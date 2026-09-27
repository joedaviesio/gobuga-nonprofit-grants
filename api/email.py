"""Email delivery via Resend. Falls back to stdout in dev (no API key)."""

import html
import os

from dotenv import load_dotenv

load_dotenv()


def send_text_email(to_email: str, subject: str, text: str):
    """Send a plain-text email. Logs to stdout if RESEND_API_KEY is not set."""
    api_key = os.getenv("RESEND_API_KEY")
    from_addr = os.getenv("RESEND_FROM", "gobuga <noreply@gobuga.org>")

    if not api_key:
        print(f"[EMAIL] No RESEND_API_KEY — would send to {to_email}: {subject}")
        return

    import resend

    resend.api_key = api_key
    resend.Emails.send({
        "from": from_addr,
        "to": [to_email],
        "subject": subject,
        "text": text,
    })


def send_reset_email(to_email: str, reset_url: str):
    """Send a password reset email. Logs to stdout if RESEND_API_KEY is not set."""
    api_key = os.getenv("RESEND_API_KEY")
    from_addr = os.getenv("RESEND_FROM", "gobuga <noreply@gobuga.org>")

    if not api_key:
        print(f"[EMAIL] No RESEND_API_KEY — reset link for {to_email}:\n  {reset_url}")
        return

    import resend

    resend.api_key = api_key
    resend.Emails.send({
        "from": from_addr,
        "to": [to_email],
        "subject": "Reset your gobuga password",
        "html": (
            f"<p>Hi,</p>"
            f"<p>We received a request to reset your password.</p>"
            f'<p><a href="{reset_url}">Click here to set a new password</a></p>'
            f"<p>This link expires in 15 minutes. If you didn't request this, you can ignore this email.</p>"
            f"<p>— GoBuga</p>"
        ),
        "text": (
            f"Hi,\n\n"
            f"We received a request to reset your password.\n\n"
            f"Reset your password: {reset_url}\n\n"
            f"This link expires in 15 minutes. If you didn't request this, you can ignore this email.\n\n"
            f"— GoBuga"
        ),
    })


# --- Subscribe list ---------------------------------------------------------
#
# Two emails: the confirmation sent when someone enters an address, and the
# update sent by hand after each sweep (`python -m api.subscribers send`).
# Both are built by pure functions that return {subject, text, html, headers}
# and are sent by `send_list_email`. No tracking pixel, no link wrapping, no
# open tracking: every link is the plain URL it looks like.
#
# The English is the reference. The Romanian and Russian need a native
# speaker's review.

_MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
    "ro": ("ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie",
           "august", "septembrie", "octombrie", "noiembrie", "decembrie"),
    "ru": ("январь", "февраль", "март", "апрель", "май", "июнь", "июль",
           "август", "сентябрь", "октябрь", "ноябрь", "декабрь"),
}

_NUMBER_WORDS = {
    "en": {2: "two", 3: "three", 4: "four", 6: "six"},
    "ro": {2: "două", 3: "trei", 4: "patru", 6: "șase"},
    "ru": {2: "два", 3: "три", 4: "четыре", 6: "шесть"},
}

# Grant titles come from funder websites; a runaway one is cut here.
MAX_TITLE_CHARS = 200

_LIST_COPY = {
    "en": {
        "hello": "Hello,",
        "confirm_subject": "Confirm your GoBuga email subscription",
        "confirm_intro": ("Someone entered this email address on GoBuga to get updates "
                          "about grant funding. If that was you, please confirm."),
        "confirm_what": ("What you would get: one email after each sweep of grant funding, "
                         "roughly {interval}. Each email says how many grants are new, "
                         "changed or closing soon, with links to them. Nothing else."),
        "confirm_action": "Confirm your subscription:",
        "confirm_button": "Confirm your subscription",
        "confirm_expiry": ("This link works for {days}. If you do nothing, you will not hear "
                           "from us again and your address will be deleted."),
        "confirm_not_you": ("Did not ask for this? You can ignore this email, or remove "
                            "your address now:"),
        "remove_link": "Remove my address",
        "sweep_subject": "GoBuga grant update: {month}",
        "sweep_intro": "GoBuga has finished its {month} sweep of grant funding. Here is what changed.",
        "count_new": "New grants",
        "count_changed": "Changed grants",
        "count_closing": "Closing in the next {days}",
        "new_heading": "New grants:",
        "more_new": "Plus {n} more on the changes page.",
        "changes_link": "Every change from this sweep:",
        "changes_button": "See every change",
        "next_sweep": "The next sweep is due in {month}.",
        "why_sweep": ("You are getting this because you subscribed to GoBuga's email "
                      "updates. To stop them:"),
        "unsubscribe": "Unsubscribe",
    },
    "ro": {
        "hello": "Bună ziua,",
        "confirm_subject": "Confirmați abonarea la e-mailurile GoBuga",
        "confirm_intro": ("Cineva a introdus această adresă de e-mail pe GoBuga pentru a primi "
                          "noutăți despre granturi. Dacă ați fost dumneavoastră, vă rugăm să "
                          "confirmați."),
        "confirm_what": ("Ce veți primi: un e-mail după fiecare actualizare a listei de granturi, "
                         "aproximativ {interval}. Fiecare e-mail arată câte granturi sunt noi, "
                         "modificate sau aproape de termen, cu linkuri către ele. Nimic altceva."),
        "confirm_action": "Confirmați abonarea:",
        "confirm_button": "Confirmați abonarea",
        "confirm_expiry": ("Linkul este valabil {days}. Dacă nu faceți nimic, nu veți mai primi "
                           "nimic de la noi, iar adresa dumneavoastră va fi ștearsă."),
        "confirm_not_you": ("Nu ați cerut acest lucru? Puteți ignora acest e-mail sau puteți "
                            "elimina adresa acum:"),
        "remove_link": "Eliminați adresa mea",
        "sweep_subject": "Actualizare granturi GoBuga: {month}",
        "sweep_intro": ("GoBuga a încheiat actualizarea listei de granturi din {month}. "
                        "Iată ce s-a schimbat."),
        "count_new": "Granturi noi",
        "count_changed": "Granturi modificate",
        "count_closing": "Se închid în următoarele {days}",
        "new_heading": "Granturi noi:",
        "more_new": "Încă {n} pe pagina de modificări.",
        "changes_link": "Toate modificările din această actualizare:",
        "changes_button": "Vedeți toate modificările",
        "next_sweep": "Următoarea actualizare este prevăzută pentru {month}.",
        "why_sweep": ("Primiți acest e-mail pentru că v-ați abonat la noutățile GoBuga. "
                      "Pentru a vă dezabona:"),
        "unsubscribe": "Dezabonare",
    },
    "ru": {
        "hello": "Здравствуйте!",
        "confirm_subject": "Подтвердите подписку на рассылку GoBuga",
        "confirm_intro": ("Кто-то указал этот адрес электронной почты на GoBuga, чтобы получать "
                          "новости о грантах. Если это были вы, пожалуйста, подтвердите подписку."),
        "confirm_what": ("Что вы будете получать: одно письмо после каждого обновления списка "
                         "грантов, примерно {interval}. В каждом письме указано, сколько грантов "
                         "появилось, изменилось или скоро закрывается, со ссылками на них. "
                         "Больше ничего."),
        "confirm_action": "Подтвердить подписку:",
        "confirm_button": "Подтвердить подписку",
        "confirm_expiry": ("Ссылка действительна {days}. Если вы ничего не сделаете, мы больше "
                           "не будем вам писать, а ваш адрес будет удалён."),
        "confirm_not_you": ("Вы не подписывались? Просто проигнорируйте это письмо или удалите "
                            "адрес сейчас:"),
        "remove_link": "Удалить мой адрес",
        "sweep_subject": "Обновление грантов GoBuga: {month}",
        "sweep_intro": ("GoBuga завершила обновление списка грантов ({month}). "
                        "Вот что изменилось."),
        "count_new": "Новые гранты",
        "count_changed": "Изменённые гранты",
        "count_closing": "Закрываются в ближайшие {days}",
        "new_heading": "Новые гранты:",
        "more_new": "Ещё {n} — на странице изменений.",
        "changes_link": "Все изменения этого обновления:",
        "changes_button": "Все изменения",
        "next_sweep": "Следующее обновление ожидается: {month}.",
        "why_sweep": ("Вы получили это письмо, потому что подписались на новости GoBuga. "
                      "Чтобы отписаться:"),
        "unsubscribe": "Отписаться",
    },
}

LIST_LANGS: tuple[str, ...] = tuple(_LIST_COPY)


def list_lang(lang) -> str:
    """A language the list emails are written in; anything else is English."""
    return lang if isinstance(lang, str) and lang in _LIST_COPY else "en"


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def count_of(n: int, lang: str, unit: str) -> str:
    """"7 days", "7 zile", "30 de zile", "7 дней". unit: day | month."""
    lang = list_lang(lang)
    if lang == "ru":
        forms = {"day": ("день", "дня", "дней"), "month": ("месяц", "месяца", "месяцев")}[unit]
        return f"{n} {_ru_plural(n, *forms)}"
    if lang == "ro":
        one, many = {"day": ("zi", "zile"), "month": ("lună", "luni")}[unit]
        if n == 1:
            return f"1 {one}"
        # Romanian puts "de" before the noun from 20 up, and again after each
        # hundred's first nineteen (101-119 take none).
        return f"{n} {many}" if 0 < n % 100 < 20 else f"{n} de {many}"
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def interval_phrase(months: int, lang: str) -> str:
    """"every two months", "o dată la două luni", "раз в два месяца"."""
    lang = list_lang(lang)
    word = _NUMBER_WORDS[lang].get(months)
    if lang == "ru":
        if months == 1:
            return "раз в месяц"
        if word:
            return f"раз в {word} {_ru_plural(months, 'месяц', 'месяца', 'месяцев')}"
        return f"раз в {count_of(months, 'ru', 'month')}"
    if lang == "ro":
        if months == 1:
            return "o dată pe lună"
        return f"o dată la {word} luni" if word else f"o dată la {count_of(months, 'ro', 'month')}"
    if months == 1:
        return "every month"
    return f"every {word} months" if word else f"every {months} months"


def month_label(value, lang: str) -> str:
    """`2026-11` -> "November 2026" in the given language; anything else as is."""
    text = str(value or "")
    parts = text.split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and 1 <= int(parts[1]) <= 12:
        return f"{_MONTHS[list_lang(lang)][int(parts[1]) - 1]} {parts[0]}"
    return text


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _html_doc(lang: str, subject: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="{_esc(lang)}"><head><meta charset="utf-8">'
        f"<title>{_esc(subject)}</title></head>"
        f'<body style="font-family:sans-serif;line-height:1.5;max-width:36rem">{body}</body></html>'
    )


def _p(text: str) -> str:
    return f"<p>{_esc(text)}</p>"


def _a(url: str, label: str) -> str:
    return f'<a href="{_esc(url)}">{_esc(label)}</a>'


def _list_headers(unsubscribe_url: str) -> dict:
    """RFC 2369 List-Unsubscribe plus the RFC 8058 one-click header."""
    return {
        "List-Unsubscribe": f"<{unsubscribe_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def build_confirmation_email(*, confirm_url: str, unsubscribe_url: str, lang,
                             interval_months: int, confirm_days: int) -> dict:
    """The double opt-in email. Returns {subject, text, html, headers}."""
    lang = list_lang(lang)
    c = _LIST_COPY[lang]
    what = c["confirm_what"].format(interval=interval_phrase(interval_months, lang))
    expiry = c["confirm_expiry"].format(days=count_of(confirm_days, lang, "day"))
    subject = c["confirm_subject"]
    text = (
        f"{c['hello']}\n\n{c['confirm_intro']}\n\n{what}\n\n"
        f"{c['confirm_action']}\n{confirm_url}\n\n{expiry}\n\n"
        f"{c['confirm_not_you']}\n{unsubscribe_url}\n\nGoBuga\n"
    )
    body = (
        _p(c["hello"]) + _p(c["confirm_intro"]) + _p(what)
        + f"<p><strong>{_a(confirm_url, c['confirm_button'])}</strong></p>"
        + _p(expiry)
        + f"<p>{_esc(c['confirm_not_you'])} {_a(unsubscribe_url, c['remove_link'])}</p>"
        + _p("GoBuga")
    )
    return {"subject": subject, "text": text, "html": _html_doc(lang, subject, body),
            "headers": _list_headers(unsubscribe_url)}


def build_sweep_email(summary: dict, *, unsubscribe_url: str, lang) -> dict:
    """The update sent after a sweep. Returns {subject, text, html, headers}.

    `summary` comes from `api.subscribers.sweep_summary`: the counts, up to
    ten new grants as {id, title, url}, `changes_url` and `home_url` (every
    URL already carrying utm_medium=email), `closing_days`, `sweep_month`
    and `next_sweep_due`.
    """
    lang = list_lang(lang)
    c = _LIST_COPY[lang]
    month = month_label(summary.get("sweep_month"), lang)
    subject = c["sweep_subject"].format(month=month)
    intro = c["sweep_intro"].format(month=month)
    counts = [
        (c["count_new"], summary.get("new_count", 0)),
        (c["count_changed"], summary.get("changed_count", 0)),
        (c["count_closing"].format(days=count_of(summary.get("closing_days", 0), lang, "day")),
         summary.get("closing_count", 0)),
    ]
    grants = [
        {"title": (str(g.get("title") or "").strip() or str(g.get("id") or ""))[:MAX_TITLE_CHARS],
         "url": g["url"]}
        for g in summary.get("new_grants") or []
    ]
    more = max(0, int(summary.get("new_count", 0)) - len(grants))
    next_due = summary.get("next_sweep_due")
    next_line = c["next_sweep"].format(month=month_label(next_due, lang)) if next_due else ""
    changes_url, home_url = summary["changes_url"], summary["home_url"]

    text = f"{c['hello']}\n\n{intro}\n\n" + "".join(f"{k}: {v}\n" for k, v in counts) + "\n"
    if grants:
        text += c["new_heading"] + "\n"
        text += "".join(f"- {g['title']}\n  {g['url']}\n" for g in grants)
        if more:
            text += c["more_new"].format(n=more) + "\n"
        text += "\n"
    text += f"{c['changes_link']}\n{changes_url}\n\n"
    if next_line:
        text += next_line + "\n\n"
    text += f"{c['why_sweep']}\n{unsubscribe_url}\n\nGoBuga\n{home_url}\n"

    body = _p(c["hello"]) + _p(intro)
    body += "<ul>" + "".join(f"<li>{_esc(k)}: {_esc(v)}</li>" for k, v in counts) + "</ul>"
    if grants:
        body += _p(c["new_heading"])
        body += "<ul>" + "".join(f"<li>{_a(g['url'], g['title'])}</li>" for g in grants) + "</ul>"
        if more:
            body += _p(c["more_new"].format(n=more))
    body += f"<p>{_a(changes_url, c['changes_button'])}</p>"
    if next_line:
        body += _p(next_line)
    body += f"<p>{_esc(c['why_sweep'])} {_a(unsubscribe_url, c['unsubscribe'])}</p>"
    body += f"<p>{_a(home_url, 'GoBuga')}</p>"
    return {"subject": subject, "text": text, "html": _html_doc(lang, subject, body),
            "headers": _list_headers(unsubscribe_url)}


def send_list_email(to_email: str, message: dict):
    """Send a list email built above. Logs to stdout if RESEND_API_KEY is not set.

    Raises whatever Resend raises; callers decide what a failure means.
    """
    api_key = os.getenv("RESEND_API_KEY")
    from_addr = os.getenv("RESEND_FROM", "gobuga <noreply@gobuga.org>")

    if not api_key:
        print(f"[EMAIL] No RESEND_API_KEY — would send to {to_email}: {message['subject']}\n"
              f"{message['text']}")
        return

    import resend

    resend.api_key = api_key
    resend.Emails.send({
        "from": from_addr,
        "to": [to_email],
        "subject": message["subject"],
        "html": message["html"],
        "text": message["text"],
        "headers": dict(message["headers"]),
    })
