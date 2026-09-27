#!/usr/bin/env python3
"""DEVELOPMENT FIXTURE: write a fake published dataset for the public pages.

Builds a realistic, entirely invented published dataset with
`api.published.publish_pool` into a throwaway data directory, so the public
pages can be rendered and checked without running a sweep. Nothing here is
real: every funder URL is under `.example`, and the funder names only borrow
the shape of real ones.

What it contains (about 30 rows for NZ, fewer for MD), across two publishes
so that /changes has new, changed and closed entries:

- live rows with a dated deadline, and live rows with a confirmed rolling one;
- closed rows (a past dated deadline, and one the verify pass found closed);
- a stale row (rolling, verified more than ROLLING_EXPIRY_DAYS ago);
- a hostile title containing `</script><script>alert(1)</script>` and quotes;
- a very long summary; rows with no amount; a row with only a maximum.

No network, no LLM, no sweep. Refuses to write inside the project directory,
so it can never touch the real `platform/` data.

Usage:
    python scripts/dev_seed_public_fixture.py --data-dir /tmp/gobuga-fixture [--country nz|md]
"""

import argparse
import os
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOSTILE_TITLE = 'Youth "Sports" Fund </script><script>alert(1)</script> & \'friends\''
LONG_SUMMARY = " ".join(
    ["This fund supports community organisations that run programmes for local people."]
    + [f"Paragraph {n}: applicants should describe the need, the people who will benefit, "
       f"the budget, and how the work will be measured and reported to the funder." for n in range(1, 25)]
)

FIRST_PUBLISH = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
SECOND_PUBLISH = datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)

# (funder, domain) pairs. Invented domains under .example.
NZ_FUNDERS = [
    ("Foundation North", "foundationnorth.example"),
    ("Sport NZ", "sportnz.example"),
    ("Lottery Grants Board", "lottery.example"),
    ("Toi Foundation", "toi.example"),
    ("Rātā Foundation", "rata.example"),
    ("Creative New Zealand", "creativenz.example"),
    ("Eastern & Central Community Trust", "ecct.example"),
    ("Otago Community Trust", "otagoct.example"),
]
NZ_REGIONS = ["auckland", "canterbury", "otago", "wellington", "waikato", "national"]
NZ_TAGS = ["community", "sport", "arts", "youth", "environment", "health", "education", "maori"]
NZ_ELIGIBILITY = [
    "Incorporated societies and charitable trusts based in the region.",
    "Registered charities, schools and clubs.",
    "Marae, iwi and hapū organisations, and charitable trusts.",
    "Community groups, including informal groups with an umbrella organisation.",
    "Not open to individuals or companies. Incorporated societies may apply.",
]

MD_FUNDERS = [
    ("Fundația Soros Moldova", "soros.example"),
    ("Delegația UE în Moldova", "eu.example"),
    ("PNUD Moldova", "undp.example"),
    ("Fondul Ecologic Național", "fen.example"),
    ("Agenția Națională pentru Cercetare și Dezvoltare", "ancd.example"),
]
MD_REGIONS = ["chisinau", "balti", "cahul", "ungheni", "national"]
MD_TAGS = ["community", "civic", "education", "environment", "youth", "women"]
MD_ELIGIBILITY = [
    "Asociații obștești și fundații înregistrate în Republica Moldova.",
    "Organizații neguvernamentale, școli și autorități publice locale.",
    "Grupuri de inițiativă și asociații obștești din mediul rural.",
]


def _excerpt(deadline_state: str, deadline: str, country: str) -> str:
    if country == "md":
        if deadline_state == "dated":
            return f"Termenul-limită de depunere a cererilor este {deadline}."
        if deadline_state == "closed":
            return "Apelul este închis."
        return "Cererile se primesc pe tot parcursul anului."
    if deadline_state == "dated":
        return f"Applications close on {deadline}."
    if deadline_state == "closed":
        return "This round is now closed."
    return "Applications are accepted at any time throughout the year."


def _row(n: int, country: str, *, title: str, funder: tuple[str, str], deadline: str,
         state: str, verified_at: str, amount_min=None, amount_max=None,
         regions=None, tags=None, eligibility: str = "", summary: str = "",
         currency: str = "NZD") -> dict:
    name, domain = funder
    source_url = f"https://{domain}/grants/programme-{n}"
    excerpt = _excerpt(state, deadline, country)
    provenance = {
        "deadline": {"source_url": source_url, "verified_at": verified_at, "excerpt": excerpt},
        "funder": {"source_url": f"https://{domain}/about", "verified_at": verified_at,
                   "excerpt": f"{name} is the funder of this programme."},
    }
    if amount_min is not None or amount_max is not None:
        provenance["amount"] = {"source_url": source_url, "verified_at": verified_at,
                                "excerpt": f"Grants of up to {amount_max or amount_min} are available."}
    if eligibility:
        provenance["eligibility"] = {"source_url": source_url, "verified_at": verified_at,
                                     "excerpt": eligibility}
    return {
        "country": country,
        "title": title,
        "funder": name,
        "deadline": deadline,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "currency": currency,
        "region": regions or ["national"],
        "tags": tags or ["community"],
        "eligibility": eligibility,
        "summary": summary or f"{title}: funding from {name} for community projects.",
        "source_url": source_url,
        "evidence_ids": [f"EV-{country.upper()}-{n:04d}"],
        "first_seen": "2026-08-04T20:00:00+00:00",
        "last_seen": verified_at,
        "dedupe_key": f"{name.lower()}|{domain}|programme-{n}",
        "deadline_state": state,
        "verified_at": verified_at,
        "verified_by": "dev-fixture/no-llm",
        "source_excerpt": excerpt,
        "provenance": provenance,
    }


def nz_rows(second: bool) -> list[dict]:
    """The NZ candidate rows for the first publish, or the second."""
    v1 = "2026-08-04T22:00:00+00:00"
    v2 = "2026-09-24T22:00:00+00:00"
    v = v2 if second else v1
    rows = []
    n = 0

    def add(**kw):
        nonlocal n
        n += 1
        kw.setdefault("verified_at", v)
        rows.append(_row(n, "nz", **kw))

    dated = ["2026-10-09", "2026-10-15", "2026-10-31", "2026-11-14", "2026-11-30", "2026-12-12",
             "2027-01-31", "2027-02-28", "2027-03-15", "2027-04-30"]
    for i, deadline in enumerate(dated):
        tag = NZ_TAGS[i % len(NZ_TAGS)]
        add(title=f"{tag.capitalize()} Grant Round {i + 1}",
            funder=NZ_FUNDERS[i % len(NZ_FUNDERS)], deadline=deadline, state="dated",
            amount_min=1000 * (i + 1), amount_max=(20_000 if i != 3 or not second else 25_000) * (i + 1),
            regions=[NZ_REGIONS[i % len(NZ_REGIONS)]], tags=[tag, "community" if tag != "community" else "youth"],
            eligibility=NZ_ELIGIBILITY[i % len(NZ_ELIGIBILITY)])
    for i in range(5):
        add(title=f"Rolling {NZ_TAGS[(i + 2) % len(NZ_TAGS)].capitalize()} Support Fund {i + 1}",
            funder=NZ_FUNDERS[(i + 3) % len(NZ_FUNDERS)], deadline="rolling", state="rolling-confirmed",
            amount_max=5000 * (i + 1) if i % 2 == 0 else None,
            regions=[NZ_REGIONS[(i + 1) % len(NZ_REGIONS)]], tags=[NZ_TAGS[(i + 2) % len(NZ_TAGS)]],
            eligibility=NZ_ELIGIBILITY[(i + 1) % len(NZ_ELIGIBILITY)])
    # Past dated deadlines: closed on the day, before the second publish.
    for i, deadline in enumerate(["2026-08-20", "2026-09-01", "2026-09-15"]):
        add(title=f"Closed Arts Round {i + 1}", funder=NZ_FUNDERS[(i + 5) % len(NZ_FUNDERS)],
            deadline=deadline, state="dated", amount_min=500, amount_max=10_000,
            regions=["canterbury"], tags=["arts", "youth"], eligibility=NZ_ELIGIBILITY[1])
    # The verify pass found this round closed at the second publish.
    add(title="Environment Restoration Fund", funder=NZ_FUNDERS[7],
        deadline="2026-12-01" if not second else "TBC", state="dated" if not second else "closed",
        amount_max=50_000, regions=["otago"], tags=["environment"], eligibility=NZ_ELIGIBILITY[0])
    # Stale: rolling, last verified in June, more than 75 days before today.
    add(title="Stale Rolling Health Fund", funder=NZ_FUNDERS[2], deadline="rolling",
        state="rolling-confirmed", verified_at="2026-06-10T00:00:00+00:00", amount_max=15_000,
        regions=["national"], tags=["health"], eligibility=NZ_ELIGIBILITY[3])
    add(title=HOSTILE_TITLE, funder=NZ_FUNDERS[1], deadline="2026-11-20", state="dated",
        amount_min=2000, amount_max=8000, regions=["auckland"], tags=["sport", "youth"],
        eligibility='Clubs "and" <b>schools</b> & societies.',
        summary='A summary with <em>markup</em>, "quotes" and </script> in it.')
    add(title="Capability Building Programme With A Very Long Summary", funder=NZ_FUNDERS[0],
        deadline="2026-12-20", state="dated", amount_min=10_000, amount_max=100_000,
        regions=["auckland", "waikato"], tags=["community", "education"], summary=LONG_SUMMARY,
        eligibility=NZ_ELIGIBILITY[0])
    # No amounts at all.
    add(title="Marae Development Fund", funder=NZ_FUNDERS[4], deadline="2027-05-31", state="dated",
        regions=["national"], tags=["maori", "community"], eligibility=NZ_ELIGIBILITY[2])
    add(title="Neighbourhood Events Fund", funder=NZ_FUNDERS[6], deadline="rolling",
        state="rolling-confirmed", regions=["wellington"], tags=["community", "arts"],
        eligibility="")
    if second:
        # New in the second publish.
        new_deadlines = ["2026-10-05", "2026-11-05", "2026-12-05", "2027-01-05", "2027-02-05", "2027-03-05"]
        for i, deadline in enumerate(new_deadlines):
            add(title=f"New Climate Action Grant {i + 1}", funder=NZ_FUNDERS[i],
                deadline=deadline, state="dated",
                amount_min=None, amount_max=30_000, regions=[NZ_REGIONS[i]],
                tags=["environment", "community"], eligibility=NZ_ELIGIBILITY[i % len(NZ_ELIGIBILITY)])
    return rows


def md_rows(second: bool) -> list[dict]:
    v = "2026-09-24T22:00:00+00:00" if second else "2026-08-04T22:00:00+00:00"
    rows = []
    n = 0

    def add(**kw):
        nonlocal n
        n += 1
        kw.setdefault("verified_at", v)
        kw.setdefault("currency", "MDL")
        rows.append(_row(n, "md", **kw))

    for i, deadline in enumerate(["2026-10-20", "2026-11-15", "2026-12-01", "2027-01-20", "2027-03-01"]):
        add(title=f"Program de granturi pentru comunități {i + 1}", funder=MD_FUNDERS[i % len(MD_FUNDERS)],
            deadline=deadline, state="dated", amount_min=10_000 * (i + 1), amount_max=200_000 * (i + 1),
            regions=[MD_REGIONS[i % len(MD_REGIONS)]], tags=[MD_TAGS[i % len(MD_TAGS)]],
            eligibility=MD_ELIGIBILITY[i % len(MD_ELIGIBILITY)])
    for i in range(3):
        add(title=f"Fond de sprijin continuu {i + 1}", funder=MD_FUNDERS[(i + 2) % len(MD_FUNDERS)],
            deadline="rolling", state="rolling-confirmed", amount_max=50_000 if i else None,
            regions=["national"], tags=[MD_TAGS[(i + 3) % len(MD_TAGS)]], eligibility=MD_ELIGIBILITY[i])
    add(title="Apel închis pentru tineret", funder=MD_FUNDERS[0], deadline="2026-09-10", state="dated",
        amount_max=100_000, regions=["cahul"], tags=["youth"], eligibility=MD_ELIGIBILITY[0])
    add(title="Fond vechi neverificat", funder=MD_FUNDERS[1], deadline="rolling", state="rolling-confirmed",
        verified_at="2026-06-10T00:00:00+00:00", regions=["national"], tags=["civic"],
        eligibility=MD_ELIGIBILITY[1])
    add(title=HOSTILE_TITLE, funder=MD_FUNDERS[2], deadline="2026-11-25", state="dated",
        amount_max=40_000, regions=["chisinau"], tags=["youth"], eligibility=MD_ELIGIBILITY[2])
    if second:
        add(title="Granturi noi pentru femei antreprenoare", funder=MD_FUNDERS[3], deadline="2026-12-15",
            state="dated", amount_max=150_000, regions=["balti"], tags=["women", "business"],
            eligibility=MD_ELIGIBILITY[0])
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, help="Throwaway GOBUGA_DATA_DIR to write into")
    parser.add_argument("--country", default="nz", choices=("nz", "md"))
    args = parser.parse_args(argv)

    data_dir = os.path.realpath(args.data_dir)
    looks_real = any(os.path.exists(os.path.join(data_dir, p))
                     for p in ("orgs", "platform/users.json", "platform/sessions.json", "platform/cycles"))
    if data_dir == PROJECT_ROOT or data_dir.startswith(PROJECT_ROOT + os.sep) or looks_real:
        print("Refusing: --data-dir must be an empty throwaway directory outside the project, "
              "never real data.", file=sys.stderr)
        return 2
    # api.tenant reads GOBUGA_DATA_DIR at import, so set it before importing.
    os.environ["GOBUGA_DATA_DIR"] = data_dir
    os.environ["GOBUGA_COUNTRY"] = args.country
    sys.path.insert(0, PROJECT_ROOT)
    from api import published, tenant

    assert tenant.platform_dir().startswith(data_dir), "data dir not honoured"
    build = nz_rows if args.country == "nz" else md_rows
    silent = lambda *a, **k: None  # noqa: E731 — no director email from a fixture
    for month, now, second in (("2026-08", FIRST_PUBLISH, False), ("2026-09", SECOND_PUBLISH, True)):
        report = published.publish_pool(args.country, month, build(second), now=now, force=True,
                                        forced_by="dev-fixture", notifier=silent)
        c = report["counts"]
        print(f"{args.country} {month}: {c['published_total']} rows ({c['live']} live, "
              f"{c['closed_total']} closed, {c['stale']} stale); {c['new']} new, "
              f"{c['changed']} changed, {c['closed']} closed")
    print(f"Wrote {published.published_dir(args.country)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
