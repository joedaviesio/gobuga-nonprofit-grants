# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""Routes for the subscribe list. The store lives in `api/subscribers.py`.

- `POST /api/subscribe` takes `{email, lang}` as JSON or as a plain HTML form
  post. The answer is the same whether the address is new, pending, confirmed
  or unsubscribed, so the endpoint cannot tell anyone who is on the list. An
  invalid address is the only outcome that differs. The confirmation email
  goes out after the response, so response time does not differ either.
- `GET /api/subscribe/confirm?token=` and `GET /api/subscribe/unsubscribe?token=`
  are the links in the emails. A GET changes nothing, because mail scanners
  and link previewers fetch every link in an email. It returns a small page
  with one button that POSTs the token back to the same path.
- `POST /api/subscribe/confirm` and `POST /api/subscribe/unsubscribe` do the
  work. The unsubscribe POST is also the RFC 8058 one-click target: body
  `List-Unsubscribe=One-Click`, token in the query string.

The pages are complete HTML with no script and no external asset, written in
the country's UI languages and chosen by `lang`. Every interpolated value is
escaped.

Rate limits are in memory only, per client address and per target address.
The client address is used as a bucket key and never stored.
"""

import hashlib
import html
import json
import os
import threading
import time
from collections import deque
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from api import email as email_mod
from api import subscribers
from api.country_config import get_country, get_country_config
from api.errors import PublicError
from api.published import SWEEP_INTERVAL_MONTHS

router = APIRouter()

MAX_BODY_BYTES = 4096

# (requests, window in seconds)
CLIENT_SUBSCRIBE_LIMIT = (10, 600)
TARGET_SUBSCRIBE_LIMIT = (5, 3600)
CLIENT_TOKEN_LIMIT = (30, 600)

NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
}
PAGE_HEADERS = {
    **NO_STORE_HEADERS,
    # The page URL carries a token; keep it out of any Referer.
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                               "frame-ancestors 'none'; base-uri 'none'",
}


# --- Client address and rate limits --------------------------------------------

def client_address(request: Request) -> str:
    """The client's address, for use as an in-memory bucket key only.

    Taken from `X-Forwarded-For`, counting TRUSTED_PROXY_HOPS entries from the
    right (default 1: the address our own edge proxy saw). Entries further
    left are written by the client and cannot be trusted. Falls back to the
    socket peer when the header is missing or shorter than the hop count.
    """
    try:
        hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))
    except ValueError:
        hops = 1
    forwarded = [
        part.strip()
        for header in request.headers.getlist("x-forwarded-for")
        for part in header.split(",")
        if part.strip()
    ]
    if hops > 0 and len(forwarded) >= hops:
        return forwarded[-hops][:64]
    return (request.client.host if request.client else "unknown")[:64]


class RateLimiter:
    """Sliding-window counter per key, in memory, reset on restart."""

    MAX_KEYS = 10_000

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        cutoff = now - self.window
        with self._lock:
            if len(self._hits) > self.MAX_KEYS:
                self._hits = {k: q for k, q in self._hits.items() if q and q[-1] >= cutoff}
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


client_subscribe_limiter = RateLimiter(*CLIENT_SUBSCRIBE_LIMIT)
target_subscribe_limiter = RateLimiter(*TARGET_SUBSCRIBE_LIMIT)
client_token_limiter = RateLimiter(*CLIENT_TOKEN_LIMIT)


def _target_key(email: str) -> str:
    # Hashed so the limiter's memory never holds the address itself.
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


# --- Request bodies ------------------------------------------------------------

async def _read_capped_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise PublicError(413, "payload_too_large", f"Body exceeds {MAX_BODY_BYTES} bytes")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise PublicError(413, "payload_too_large", f"Body exceeds {MAX_BODY_BYTES} bytes")
    return bytes(body)


def _media_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _form_fields(body: bytes) -> dict[str, str]:
    """First value of each field of a urlencoded body; {} if undecodable."""
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=20)
    except (UnicodeDecodeError, ValueError):
        return {}
    return {k: v[0] for k, v in parsed.items() if v}


# --- POST /api/subscribe ---------------------------------------------------------

def _form_redirect(state: str, lang=None) -> RedirectResponse:
    """Back to the /subscribe page. `lang` is carried only when it is one of
    the country's UI languages and not the default, as the page's own links do."""
    config = get_country_config(get_country())
    url = f"{subscribers.app_url()}/subscribe?state={state}"
    if isinstance(lang, str) and lang in config.ui_languages and lang != config.content_language:
        url += f"&lang={lang}"
    return RedirectResponse(url, status_code=303, headers=NO_STORE_HEADERS)


def _send_confirmation(email: str, token: str, lang: str) -> None:
    """Runs after the response has gone. Never raises."""
    try:
        subscribers.send_confirmation(email, token, lang)
    except Exception as exc:  # noqa: BLE001 — the response has already been sent
        print(f"[subscribe] confirmation email failed: {type(exc).__name__}: {exc}")


@router.post("/api/subscribe")
async def post_subscribe(request: Request, background: BackgroundTasks):
    media = _media_type(request)
    if media not in ("application/json", "application/x-www-form-urlencoded"):
        raise PublicError(415, "unsupported_media_type",
                          "Send JSON or an application/x-www-form-urlencoded form")
    is_form = media == "application/x-www-form-urlencoded"

    if not client_subscribe_limiter.allow(client_address(request)):
        if is_form:
            # The body has not been read yet, so the language is not known.
            return _form_redirect("limited")
        raise PublicError(429, "rate_limited", "Too many requests. Please try again later.")

    body = await _read_capped_body(request)
    if is_form:
        fields = _form_fields(body)
    else:
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        fields = parsed if isinstance(parsed, dict) else {}

    try:
        email = subscribers.normalise_email(fields.get("email"))
    except subscribers.InvalidEmail:
        if is_form:
            return _form_redirect("invalid", fields.get("lang"))
        raise PublicError(400, "invalid_email", "Enter a valid email address")

    if not target_subscribe_limiter.allow(_target_key(email)):
        if is_form:
            return _form_redirect("limited", fields.get("lang"))
        raise PublicError(429, "rate_limited", "Too many requests. Please try again later.")

    lang = subscribers.normalise_lang(fields.get("lang"))
    _, token = await run_in_threadpool(subscribers.subscribe, email, lang)
    if token:
        background.add_task(_send_confirmation, email, token, lang)

    # FastAPI attaches `background` to the returned response, so the email
    # is sent only after the client has its answer.
    if is_form:
        return _form_redirect("sent", lang)
    return JSONResponse({"ok": True}, headers=NO_STORE_HEADERS)


# --- Pages -----------------------------------------------------------------------
#
# English is the reference; Romanian and Russian need a native speaker's review.

_PAGE_COPY = {
    "en": {
        "brand_link": "Go to GoBuga",
        "confirm_prompt_title": "Confirm your subscription",
        "confirm_prompt_body": ("Press the button to confirm that you want one email from GoBuga "
                                "after each sweep of grant funding, roughly {interval}."),
        "confirm_button": "Confirm subscription",
        "confirm_done_title": "Subscription confirmed",
        "confirm_done_body": ("You will get one email after each sweep of grant funding. "
                              "Every email has a link to unsubscribe."),
        "confirm_invalid_title": "This link is not valid",
        "confirm_invalid_body": ("The link has expired or is not valid. Confirmation links work "
                                 "for {days}. Enter your address again and we will send a new link."),
        "email_label": "Email address",
        "resubscribe_button": "Send a new link",
        "unsub_prompt_title": "Unsubscribe",
        "unsub_prompt_body": "Press the button to stop getting GoBuga's emails after each sweep.",
        "unsub_button": "Unsubscribe",
        "unsub_done_title": "You have unsubscribed",
        "unsub_done_body": ("You will not get any more emails from this list. Your address "
                            "will be deleted within {days}."),
        "unsub_invalid_title": "This link is not valid",
        "unsub_invalid_body": ("This unsubscribe link is not valid. It may have been copied "
                               "incompletely. If you still get emails from GoBuga, use the "
                               "unsubscribe link in the most recent one."),
        "limited_title": "Too many requests",
        "limited_body": "Please wait a few minutes and try again.",
    },
    "ro": {
        "brand_link": "Mergeți la GoBuga",
        "confirm_prompt_title": "Confirmați abonarea",
        "confirm_prompt_body": ("Apăsați butonul pentru a confirma că doriți să primiți un e-mail "
                                "de la GoBuga după fiecare actualizare a listei de granturi, "
                                "aproximativ {interval}."),
        "confirm_button": "Confirmați abonarea",
        "confirm_done_title": "Abonare confirmată",
        "confirm_done_body": ("Veți primi un e-mail după fiecare actualizare a listei de granturi. "
                              "Fiecare e-mail conține un link de dezabonare."),
        "confirm_invalid_title": "Acest link nu este valid",
        "confirm_invalid_body": ("Linkul a expirat sau nu este valid. Linkurile de confirmare sunt "
                                 "valabile {days}. Introduceți din nou adresa și vă vom trimite "
                                 "un link nou."),
        "email_label": "Adresa de e-mail",
        "resubscribe_button": "Trimiteți un link nou",
        "unsub_prompt_title": "Dezabonare",
        "unsub_prompt_body": ("Apăsați butonul pentru a nu mai primi e-mailurile GoBuga trimise "
                              "după fiecare actualizare."),
        "unsub_button": "Dezabonare",
        "unsub_done_title": "V-ați dezabonat",
        "unsub_done_body": ("Nu veți mai primi e-mailuri de pe această listă. Adresa "
                            "dumneavoastră va fi ștearsă în cel mult {days}."),
        "unsub_invalid_title": "Acest link nu este valid",
        "unsub_invalid_body": ("Acest link de dezabonare nu este valid. Poate nu a fost copiat "
                               "complet. Dacă încă primiți e-mailuri de la GoBuga, folosiți linkul "
                               "de dezabonare din cel mai recent."),
        "limited_title": "Prea multe cereri",
        "limited_body": "Vă rugăm să așteptați câteva minute și să încercați din nou.",
    },
    "ru": {
        "brand_link": "Перейти на GoBuga",
        "confirm_prompt_title": "Подтвердите подписку",
        "confirm_prompt_body": ("Нажмите кнопку, чтобы подтвердить, что вы хотите получать одно "
                                "письмо от GoBuga после каждого обновления списка грантов, "
                                "примерно {interval}."),
        "confirm_button": "Подтвердить подписку",
        "confirm_done_title": "Подписка подтверждена",
        "confirm_done_body": ("Вы будете получать одно письмо после каждого обновления списка "
                              "грантов. В каждом письме есть ссылка для отписки."),
        "confirm_invalid_title": "Ссылка недействительна",
        "confirm_invalid_body": ("Срок действия ссылки истёк или она недействительна. Ссылки для "
                                 "подтверждения действуют {days}. Введите адрес ещё раз, и мы "
                                 "пришлём новую ссылку."),
        "email_label": "Адрес электронной почты",
        "resubscribe_button": "Прислать новую ссылку",
        "unsub_prompt_title": "Отписка",
        "unsub_prompt_body": ("Нажмите кнопку, чтобы больше не получать письма GoBuga после "
                              "каждого обновления."),
        "unsub_button": "Отписаться",
        "unsub_done_title": "Вы отписались",
        "unsub_done_body": ("Вы больше не будете получать письма из этой рассылки. Ваш адрес "
                            "будет удалён в течение {days}."),
        "unsub_invalid_title": "Ссылка недействительна",
        "unsub_invalid_body": ("Эта ссылка для отписки недействительна. Возможно, она скопирована "
                               "не полностью. Если вы всё ещё получаете письма от GoBuga, "
                               "воспользуйтесь ссылкой для отписки в последнем из них."),
        "limited_title": "Слишком много запросов",
        "limited_body": "Пожалуйста, подождите несколько минут и попробуйте снова.",
    },
}


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def page_lang(lang) -> str:
    """The `lang` asked for if the country offers it and we have copy for it,
    else the country's content language, else English."""
    config = get_country_config(get_country())
    if isinstance(lang, str) and lang in config.ui_languages and lang in _PAGE_COPY:
        return lang
    return config.content_language if config.content_language in _PAGE_COPY else "en"


def _copy(lang: str, key: str) -> str:
    text = _PAGE_COPY[lang][key]
    if "{interval}" in text:
        text = text.replace("{interval}", email_mod.interval_phrase(SWEEP_INTERVAL_MONTHS, lang))
    if "{days}" in text:
        days = (subscribers.CONFIRM_TTL if key.startswith("confirm")
                else subscribers.UNSUBSCRIBED_RETENTION).days
        text = text.replace("{days}", email_mod.count_of(days, lang, "day"))
    return text


def _page(lang: str, title_key: str, body_key: str, extra: str = "", status: int = 200) -> HTMLResponse:
    title = _copy(lang, title_key)
    document = (
        "<!doctype html>\n"
        f'<html lang="{_esc(lang)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>{_esc(title)} · GoBuga</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:34rem;margin:3rem auto;"
        "padding:0 1rem;line-height:1.5;color:#1a1a1a;background:#fff}"
        "button,input{font:inherit;padding:.5rem .8rem}</style></head>"
        f"<body><main><h1>{_esc(title)}</h1><p>{_esc(_copy(lang, body_key))}</p>{extra}"
        f'<p><a href="{_esc(subscribers.app_url() + "/")}">{_esc(_copy(lang, "brand_link"))}</a></p>'
        "</main></body></html>\n"
    )
    return HTMLResponse(document, status_code=status, headers=PAGE_HEADERS)


def _button_form(action: str, lang: str, token: str, button_key: str) -> str:
    return (
        f'<form method="post" action="{_esc(action)}?lang={_esc(lang)}">'
        f'<input type="hidden" name="token" value="{_esc(token)}">'
        f'<button type="submit">{_esc(_copy(lang, button_key))}</button></form>'
    )


def _subscribe_form(lang: str) -> str:
    return (
        '<form method="post" action="/api/subscribe">'
        f'<p><label for="email">{_esc(_copy(lang, "email_label"))}</label><br>'
        '<input id="email" name="email" type="email" required autocomplete="email" maxlength="254"></p>'
        f'<input type="hidden" name="lang" value="{_esc(lang)}">'
        f'<button type="submit">{_esc(_copy(lang, "resubscribe_button"))}</button></form>'
    )


async def _token_and_lang(request: Request) -> tuple[str, str]:
    """Token and lang from a POST: form body first, then the query string.

    RFC 8058 one-click posts carry the token in the query string and
    `List-Unsubscribe=One-Click` in the body, urlencoded or multipart; a
    multipart body is not parsed, since nothing needed is in it.
    """
    body = await _read_capped_body(request)
    fields = _form_fields(body) if _media_type(request) == "application/x-www-form-urlencoded" else {}
    token = fields.get("token") or request.query_params.get("token") or ""
    lang = page_lang(fields.get("lang") or request.query_params.get("lang"))
    return token, lang


# --- Confirm and unsubscribe -------------------------------------------------------

CONFIRM_PATH = "/api/subscribe/confirm"
UNSUBSCRIBE_PATH = "/api/subscribe/unsubscribe"


@router.get(CONFIRM_PATH)
def get_confirm(token: str = "", lang: str = ""):
    """Shows the confirm button. Reads nothing, writes nothing."""
    lang = page_lang(lang)
    if not subscribers.is_token_shaped(token):
        return _page(lang, "confirm_invalid_title", "confirm_invalid_body", _subscribe_form(lang), 400)
    return _page(lang, "confirm_prompt_title", "confirm_prompt_body",
                 _button_form(CONFIRM_PATH, lang, token, "confirm_button"))


@router.post(CONFIRM_PATH)
async def post_confirm(request: Request):
    token, lang = await _token_and_lang(request)
    if not client_token_limiter.allow(client_address(request)):
        return _page(lang, "limited_title", "limited_body", status=429)
    if await run_in_threadpool(subscribers.confirm, token):
        return _page(lang, "confirm_done_title", "confirm_done_body")
    return _page(lang, "confirm_invalid_title", "confirm_invalid_body", _subscribe_form(lang), 400)


@router.get(UNSUBSCRIBE_PATH)
def get_unsubscribe(token: str = "", lang: str = ""):
    """Shows the unsubscribe button. Reads nothing, writes nothing."""
    lang = page_lang(lang)
    if not subscribers.is_token_shaped(token):
        return _page(lang, "unsub_invalid_title", "unsub_invalid_body", status=400)
    return _page(lang, "unsub_prompt_title", "unsub_prompt_body",
                 _button_form(UNSUBSCRIBE_PATH, lang, token, "unsub_button"))


@router.post(UNSUBSCRIBE_PATH)
async def post_unsubscribe(request: Request):
    token, lang = await _token_and_lang(request)
    if not client_token_limiter.allow(client_address(request)):
        return _page(lang, "limited_title", "limited_body", status=429)
    if await run_in_threadpool(subscribers.unsubscribe, token):
        return _page(lang, "unsub_done_title", "unsub_done_body")
    return _page(lang, "unsub_invalid_title", "unsub_invalid_body", status=400)
