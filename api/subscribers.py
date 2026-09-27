# Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
"""Subscribe list: one email address, one email after each sweep.

Storage, one file per country:

    <platform_dir>/subscribers/<country>.json
    {"country": "nz", "subscribers": {"<email>": {...}}, "purged_unsubscribed": 0}

A record holds the address (trimmed, lowercased), the UI language it was
entered in, timestamps, the `published_at` of the last dataset the person was
emailed about, and SHA-256 hashes of their tokens. Never an IP address, never
a user agent, never a name.

Tokens come from `secrets.token_urlsafe(32)` and only their hash is stored,
so a leaked file cannot confirm or unsubscribe anyone. A confirm token lasts
CONFIRM_TTL. Each email carries its own unsubscribe token; every one of them
works for as long as the subscription does.

States, derived from the timestamps:
- pending       entered, not yet confirmed. Purged PENDING_TTL after the
                last confirmation email.
- confirmed     gets the post-sweep email.
- unsubscribed  gets nothing. The record, email included, is deleted
                UNSUBSCRIBED_RETENTION later, leaving only a count.

Writes are atomic (temp file then `os.replace`) and serialised by a module
lock plus an advisory file lock, because the send command runs in its own
process beside the server.

Sending after a sweep is a deliberate, manual step. Nothing here is called
by the sweep or by `publish_pool`.

CLI:
    python -m api.subscribers status <country>
    python -m api.subscribers send <country>                 # dry run
    python -m api.subscribers send <country> --confirm [--allow-empty]
    python -m api.subscribers send <country> --to ADDRESS [--lang xx] [--allow-empty]
"""

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

# Imported at module load, not lazily: `api.email` calls load_dotenv() on
# import, and that should happen once at startup, never mid-request.
from api import email as email_mod
from api import published, tenant
from api.country_config import get_country, get_country_config


CONFIRM_TTL = timedelta(days=7)
PENDING_TTL = CONFIRM_TTL
RESEND_INTERVAL = timedelta(hours=1)
UNSUBSCRIBED_RETENTION = timedelta(days=30)
# A dated grant whose deadline falls within this many days is "closing soon".
CLOSING_SOON_DAYS = 30
MAX_NEW_IN_EMAIL = 10
# One unsubscribe token per email sent; the oldest drop off past this.
MAX_UNSUBSCRIBE_TOKENS = 50

MAX_EMAIL_CHARS = 254
MAX_LOCAL_CHARS = 64
MAX_TOKEN_CHARS = 128

_COUNTRY_RE = re.compile(r"^[a-z]{2,8}$")
# RFC 5322 atext plus dots; conservative on purpose (ASCII only, no quoting).
_LOCAL_RE = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]+")
_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_TLD_RE = re.compile(r"[a-z](?:[a-z0-9-]*[a-z0-9])?")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
# CR and LF, plus everything else `str.splitlines` treats as a line break.
_LINE_BREAKS = frozenset("\r\n\v\f\x1c\x1d\x1e\x85  ")

_lock = threading.Lock()


class InvalidEmail(ValueError):
    """The address failed validation. The message never echoes the input."""


# --- Validation ----------------------------------------------------------------

def normalise_email(raw) -> str:
    """Trimmed, lowercased address, or InvalidEmail.

    Conservative: exactly one `@`, ASCII only, a dot in the domain, no
    whitespace or control characters, RFC length caps. A carriage return,
    newline or other line break anywhere, even at the ends where `strip`
    would hide it, is rejected outright: it is a header injection attempt.
    """
    if not isinstance(raw, str) or len(raw) > MAX_EMAIL_CHARS * 2:
        raise InvalidEmail("not a valid email address")
    if any(c in _LINE_BREAKS for c in raw):
        raise InvalidEmail("not a valid email address")
    email = raw.strip().lower()
    if not email or len(email) > MAX_EMAIL_CHARS:
        raise InvalidEmail("not a valid email address")
    if any(c.isspace() or unicodedata.category(c)[0] == "C" for c in email):
        raise InvalidEmail("not a valid email address")
    if email.count("@") != 1:
        raise InvalidEmail("not a valid email address")
    local, domain = email.split("@")
    if (not local or len(local) > MAX_LOCAL_CHARS or not _LOCAL_RE.fullmatch(local)
            or local.startswith(".") or local.endswith(".") or ".." in local):
        raise InvalidEmail("not a valid email address")
    labels = domain.split(".")
    if (len(labels) < 2 or not all(_LABEL_RE.fullmatch(label) for label in labels)
            or not _TLD_RE.fullmatch(labels[-1]) or len(labels[-1]) < 2):
        raise InvalidEmail("not a valid email address")
    return email


def normalise_lang(lang, country: str | None = None) -> str:
    """One of the country's UI languages; anything else is its content language."""
    config = get_country_config(country or get_country())
    return lang if isinstance(lang, str) and lang in config.ui_languages else config.content_language


# --- Paths and file I/O ------------------------------------------------------

def _country(country: str | None) -> str:
    country = country or get_country()
    if not _COUNTRY_RE.match(country):
        raise ValueError(f"invalid country slug: {country!r}")
    return country


def subscribers_path(country: str | None = None) -> str:
    return os.path.join(tenant.platform_dir(), "subscribers", f"{_country(country)}.json")


def _empty(country: str) -> dict:
    return {"country": country, "subscribers": {}, "purged_unsubscribed": 0}


def _load(country: str) -> dict:
    try:
        with open(subscribers_path(country)) as f:
            data = json.load(f)
    except FileNotFoundError:
        return _empty(country)
    if not isinstance(data, dict) or not isinstance(data.get("subscribers"), dict):
        # Refuse to carry on over a damaged file: a write would wipe the list.
        raise RuntimeError(f"subscriber file is malformed: {subscribers_path(country)}")
    data.setdefault("purged_unsubscribed", 0)
    return data


def _save(country: str, data: dict) -> None:
    path = subscribers_path(country)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def _locked(country: str):
    """Module lock for threads in this process, flock for the CLI beside it."""
    path = subscribers_path(country)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _lock, open(path + ".lock", "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _mutate(country: str, now: datetime, change):
    """Load, purge, apply `change(data)`, save; returns what `change` returned."""
    with _locked(country):
        data = _load(country)
        _purge(data, now)
        result = change(data)
        _save(country, data)
        return result


# --- Records, tokens, time -----------------------------------------------------

def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, _hash(token)


def _token_hash(token) -> str | None:
    """Hash of a well-formed token, or None for anything that cannot be one."""
    if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN_CHARS:
        return None
    if not _TOKEN_RE.fullmatch(token):
        return None
    return _hash(token)


def is_token_shaped(token) -> bool:
    """Could this string be a token we issued? Says nothing about validity."""
    return _token_hash(token) is not None


def _same(stored, digest: str) -> bool:
    return isinstance(stored, str) and hmac.compare_digest(stored, digest)


def state_of(record: dict) -> str:
    if record.get("unsubscribed_at"):
        return "unsubscribed"
    if record.get("confirmed_at"):
        return "confirmed"
    return "pending"


def _purge(data: dict, now: datetime) -> None:
    """Drop expired pending records; drop old unsubscribed ones, keeping a count."""
    subs = data["subscribers"]
    for email, record in list(subs.items()):
        state = state_of(record)
        if state == "pending":
            sent = _parse(record.get("confirm_sent_at")) or _parse(record.get("created_at"))
            if sent is None or now - sent > PENDING_TTL:
                del subs[email]
        elif state == "unsubscribed":
            left = _parse(record.get("unsubscribed_at"))
            if left is None or now - left > UNSUBSCRIBED_RETENTION:
                del subs[email]
                data["purged_unsubscribed"] = int(data.get("purged_unsubscribed", 0)) + 1


# --- Public functions ------------------------------------------------------------

def subscribe(email, lang, country=None, now=None) -> tuple[str, str | None]:
    """Record a request to subscribe. Returns (outcome, confirm_token or None).

    Outcomes:
    - `new`               first time; a confirmation should be sent
    - `resubscribed`      had unsubscribed; starts again as pending
    - `resent`            still pending and the last email is over an hour old
    - `throttled`         still pending, last email under an hour ago; nothing
    - `already_confirmed` nothing changes and nothing is sent

    A token is returned only when a confirmation email should go out. Raises
    InvalidEmail for a bad address. The file is written in every case, so the
    work done does not depend on the address's state.
    """
    country = _country(country)
    now = _now(now)
    email = normalise_email(email)
    lang = normalise_lang(lang, country)

    def change(data):
        subs = data["subscribers"]
        record = subs.get(email)
        state = state_of(record) if record else None
        if state == "confirmed":
            return "already_confirmed", None
        if state == "pending":
            sent = _parse(record.get("confirm_sent_at"))
            if sent is not None and now - sent < RESEND_INTERVAL:
                return "throttled", None
            outcome = "resent"
        else:
            outcome = "new" if record is None else "resubscribed"
            # A fresh subscription. `last_sent_publish` carries over so a
            # returning subscriber is not sent a sweep they already had.
            record = {
                "email": email,
                "lang": lang,
                "created_at": _iso(now),
                "confirmed_at": None,
                "unsubscribed_at": None,
                "last_sent_publish": (record or {}).get("last_sent_publish"),
                "confirm_token_hash": None,
                "confirm_sent_at": None,
                "unsubscribe_token_hashes": [],
            }
            subs[email] = record
        token, digest = _new_token()
        record["lang"] = lang
        record["confirm_token_hash"] = digest
        record["confirm_sent_at"] = _iso(now)
        return outcome, token

    return _mutate(country, now, change)


def issue_unsubscribe_token(email, country=None, now=None) -> str | None:
    """Mint an unsubscribe token for one email about to be sent.

    None if the address has no record or has unsubscribed. The hash is saved
    before the email goes out, so the link works the moment it arrives.
    """
    country = _country(country)
    now = _now(now)
    email = normalise_email(email)

    def change(data):
        record = data["subscribers"].get(email)
        if record is None or state_of(record) == "unsubscribed":
            return None
        token, digest = _new_token()
        hashes = list(record.get("unsubscribe_token_hashes") or []) + [digest]
        record["unsubscribe_token_hashes"] = hashes[-MAX_UNSUBSCRIBE_TOKENS:]
        return token

    return _mutate(country, now, change)


def confirm(token, country=None, now=None) -> bool:
    """Confirm the subscription the token was issued for.

    True if the token matches a pending or already confirmed record within
    CONFIRM_TTL of being sent (a second press of the button is not an
    error). False for anything else, without saying which.
    """
    country = _country(country)
    now = _now(now)
    digest = _token_hash(token)
    if digest is None:
        return False

    def change(data):
        for record in data["subscribers"].values():
            if not _same(record.get("confirm_token_hash"), digest):
                continue
            sent = _parse(record.get("confirm_sent_at"))
            if state_of(record) == "unsubscribed" or sent is None or now - sent > CONFIRM_TTL:
                return False
            if not record.get("confirmed_at"):
                record["confirmed_at"] = _iso(now)
            return True
        return False

    return _mutate(country, now, change)


def unsubscribe(token, country=None, now=None) -> bool:
    """Unsubscribe whoever the token was issued to. Idempotent.

    True if the token belongs to a record still on file (including one that
    has already unsubscribed); False otherwise.
    """
    country = _country(country)
    now = _now(now)
    digest = _token_hash(token)
    if digest is None:
        return False

    def change(data):
        for record in data["subscribers"].values():
            if not any(_same(h, digest) for h in record.get("unsubscribe_token_hashes") or []):
                continue
            if not record.get("unsubscribed_at"):
                record["unsubscribed_at"] = _iso(now)
                record["confirm_token_hash"] = None
            return True
        return False

    return _mutate(country, now, change)


def mark_sent(email, publish_id: str, country=None, now=None) -> None:
    """Record that this address has been emailed about `publish_id`."""
    country = _country(country)
    now = _now(now)
    email = normalise_email(email)

    def change(data):
        record = data["subscribers"].get(email)
        if record is not None:
            record["last_sent_publish"] = publish_id

    _mutate(country, now, change)


def _current(country: str | None, now: datetime | None) -> dict:
    """The file as it stands, with purging applied in memory only."""
    country = _country(country)
    data = _load(country)
    _purge(data, _now(now))
    return data


def subscriber_counts(country=None, now=None) -> dict:
    """{"confirmed": n, "pending": n, "unsubscribed": n}.

    `unsubscribed` includes records already purged, which survive only as a
    count.
    """
    data = _current(country, now)
    counts = {"confirmed": 0, "pending": 0, "unsubscribed": int(data.get("purged_unsubscribed", 0))}
    for record in data["subscribers"].values():
        counts[state_of(record)] += 1
    return counts


def confirmed_subscribers(country=None, now=None) -> list[dict]:
    """Confirmed records without their token hashes, oldest first."""
    data = _current(country, now)
    rows = [
        {k: record.get(k) for k in ("email", "lang", "created_at", "confirmed_at", "last_sent_publish")}
        for record in data["subscribers"].values()
        if state_of(record) == "confirmed"
    ]
    return sorted(rows, key=lambda r: (r["confirmed_at"] or "", r["email"]))


# --- URLs and email ------------------------------------------------------------

def app_url() -> str:
    """The public site, from APP_URL as in password-reset links."""
    return os.environ.get("APP_URL", "http://localhost:3002").rstrip("/")


def _email_link(path: str, **params) -> str:
    """A link to the site from an email; always tagged utm_medium=email."""
    query = urlencode({**params, "utm_medium": "email"})
    return f"{app_url()}{path}?{query}"


def confirm_url(token: str, lang: str) -> str:
    return _email_link("/api/subscribe/confirm", token=token, lang=lang)


def unsubscribe_url(token: str, lang: str) -> str:
    return _email_link("/api/subscribe/unsubscribe", token=token, lang=lang)


def send_confirmation(email: str, token: str, lang: str, country=None, sender=None) -> None:
    """Mint an unsubscribe token and send the confirmation email."""
    unsub = issue_unsubscribe_token(email, country)
    if unsub is None:
        return
    message = email_mod.build_confirmation_email(
        confirm_url=confirm_url(token, lang),
        unsubscribe_url=unsubscribe_url(unsub, lang),
        lang=lang,
        interval_months=published.SWEEP_INTERVAL_MONTHS,
        confirm_days=CONFIRM_TTL.days,
    )
    (sender or email_mod.send_list_email)(email, message)


def sweep_summary(country=None, now=None) -> dict | None:
    """What the post-sweep email says, or None if nothing has been published.

    Built from the newest changelog entry and the publish metadata. `new_count`
    and `changed_count` are that entry's; `closing_count` is live dated
    grants whose deadline is within CLOSING_SOON_DAYS of today (local time).
    The listed new grants are the first ten of that entry's new IDs that are
    live today.
    """
    country = _country(country)
    now = _now(now)
    changes = published.load_changes(country)
    meta = published.published_meta(country)
    if not changes or not meta.get("published_at"):
        return None
    latest = changes[0]
    live = {r.get("id"): r for r in published.load_published(country, today=now, include=("live",))}
    today = now.astimezone(ZoneInfo(get_country_config(country).timezone)).date()

    closing = 0
    for row in live.values():
        try:
            deadline = date.fromisoformat(str(row.get("deadline")))
        except ValueError:
            continue  # `rolling`, `TBC` or missing
        if row.get("deadline_state") == "dated" and 0 <= (deadline - today).days <= CLOSING_SOON_DAYS:
            closing += 1

    new_ids = [i for i in latest.get("new") or [] if isinstance(i, str)]
    new_grants = [
        {"id": i, "title": live[i].get("title"), "url": _email_link(f"/grants/{quote(i, safe='')}")}
        for i in new_ids if i in live
    ][:MAX_NEW_IN_EMAIL]
    return {
        "publish_id": latest.get("published_at"),
        "sweep_month": latest.get("sweep_month") or meta.get("sweep_month"),
        "next_sweep_due": meta.get("next_sweep_due"),
        "new_count": len(new_ids),
        "changed_count": len(latest.get("changed") or []),
        "closing_count": closing,
        "closing_days": CLOSING_SOON_DAYS,
        "new_grants": new_grants,
        "changes_url": _email_link("/changes"),
        "home_url": _email_link("/"),
    }


# --- Send command ------------------------------------------------------------------

def _is_empty(summary: dict) -> bool:
    return not (summary["new_count"] or summary["changed_count"] or summary["closing_count"])


def send_sweep(country, *, confirm_send=False, allow_empty=False, to=None, to_lang=None,
               now=None, sender=None, out=print) -> int:
    """The `send` command. Returns an exit code: 0 done, 1 refused.

    Dry run unless `confirm_send`. `to` sends one copy to that address and
    touches no subscriber record. Progress is saved per address, so a run
    that is interrupted or has failures can simply be run again.
    """
    country = _country(country)
    now = _now(now)
    sender = sender or email_mod.send_list_email

    summary = sweep_summary(country, now)
    if summary is None or not summary["publish_id"]:
        out(f"Refused: nothing has been published for {country}.")
        return 1
    publish_id = summary["publish_id"]
    out(f"Latest publish: {publish_id} (sweep {summary['sweep_month']})")
    out(f"New {summary['new_count']}, changed {summary['changed_count']}, "
        f"closing within {CLOSING_SOON_DAYS} days {summary['closing_count']}")
    if _is_empty(summary) and not allow_empty:
        out("Refused: this email would announce nothing new, changed or closing. "
            "Add --allow-empty to send it anyway.")
        return 1

    def build(lang, token):
        return email_mod.build_sweep_email(summary, unsubscribe_url=unsubscribe_url(token, lang), lang=lang)

    if to is not None:
        try:
            address = normalise_email(to)
        except InvalidEmail:
            out("Refused: --to is not a valid email address.")
            return 1
        lang = normalise_lang(to_lang, country)
        # A throwaway token: the preview's unsubscribe link leads to the
        # "not valid" page, and no record is created or changed.
        sender(address, build(lang, secrets.token_urlsafe(32)))
        out(f"Sent one preview ({lang}) to {address}. No subscriber record was touched.")
        return 0

    confirmed = confirmed_subscribers(country, now)
    due = [s for s in confirmed if s["last_sent_publish"] != publish_id]
    out(f"Confirmed subscribers: {len(confirmed)}; already sent this publish: "
        f"{len(confirmed) - len(due)}; to email now: {len(due)}")
    if not due:
        out("Refused: no confirmed subscriber is waiting for this publish." if confirmed
            else "Refused: there are no confirmed subscribers.")
        return 1

    if not confirm_send:
        langs = sorted({s["lang"] for s in due})
        for lang in langs:
            message = build(lang, "UNSUBSCRIBE-TOKEN")
            out(f"\n--- {lang}, recipients: {sum(1 for s in due if s['lang'] == lang)} ---")
            out(f"Subject: {message['subject']}\n")
            out(message["text"])
        out("Dry run: nothing was sent. Add --confirm to send.")
        return 0

    sent = failed = 0
    for sub in due:
        try:
            token = issue_unsubscribe_token(sub["email"], country, now)
            if token is None:
                continue  # unsubscribed since the run began
            sender(sub["email"], build(sub["lang"], token))
        except Exception as exc:  # noqa: BLE001 — one bad address must not stop the run
            failed += 1
            out(f"  failed: {sub['email']}: {type(exc).__name__}: {exc}")
            continue
        mark_sent(sub["email"], publish_id, country, now)
        sent += 1
    out(f"Sent {sent}, failed {failed}." + (" Run again to retry the failures." if failed else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = ("Usage:\n  python -m api.subscribers status <country>\n"
             "  python -m api.subscribers send <country> [--confirm] [--allow-empty] "
             "[--to ADDRESS [--lang xx]]")
    options = {}
    for name in ("--to", "--lang"):
        if name in argv:
            pos = argv.index(name)
            if pos + 1 >= len(argv):
                print(usage)
                return 2
            options[name] = argv[pos + 1]
            del argv[pos:pos + 2]
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    if flags - {"--confirm", "--allow-empty"} or len(args) != 2:
        print(usage)
        return 2

    command, country = args
    if command == "status":
        print(json.dumps(subscriber_counts(country), indent=2))
        return 0
    if command == "send":
        return send_sweep(
            country,
            confirm_send="--confirm" in flags,
            allow_empty="--allow-empty" in flags,
            to=options.get("--to"),
            to_lang=options.get("--lang"),
        )
    print(usage)
    return 2


if __name__ == "__main__":
    sys.exit(main())
