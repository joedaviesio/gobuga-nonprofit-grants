"""Phase 7, open the workspace: free and unlimited, one daily LLM budget,
checkout frozen, portal and webhook alive, one optional sign-up screen.

In-process (fastapi TestClient) against a throwaway data dir. No network:
every LLM entry point and Stripe are replaced with fakes. That matters here,
because api/billing.py calls load_dotenv(), which can pick up a real .env
(with live Stripe and Anthropic keys) from a parent directory.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.billing as billing
import api.bots as bots
import api.llm_budget as llm_budget
import api.limits as limits
import api.tenant as tenant
import orchestrator.main as orchestrator_main
import orchestrator.tools as orchestrator_tools
from api import server
from api.auth import get_org, update_org
from api.country_config import clear_config_cache
from api.usage_log import log_usage


def _no_network(*args, **kwargs):
    raise AssertionError("a test reached a paid or external call")


def _fake_bot_a(org_id, case_id, model=None):
    """Stands in for Bot A: records one LLM call, like the real one does."""
    log_usage(org_id, "bot_a", {"api_calls": 1, "model": "fake"}, case_id=case_id)
    return {"summary": "fake", "usage": {}}


def _fake_chat(org_id, case_id, message, model=None):
    from api.case_manager import append_message
    append_message(org_id, case_id, "officer", message)
    append_message(org_id, case_id, "assistant", "ok")
    log_usage(org_id, "chat", {"api_calls": 1, "model": "fake"}, case_id=case_id)
    return {"response": "ok"}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    monkeypatch.setenv("GOBUGA_COUNTRY", "nz")
    monkeypatch.setenv("STARTUP_SWEEP_DISABLED", "1")
    monkeypatch.delenv("DAILY_LLM_CALL_BUDGET", raising=False)
    monkeypatch.delenv("BILLING_CHECKOUT_ENABLED", raising=False)
    clear_config_cache()
    # Stripe off and unreachable unless a test fakes it
    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(billing, "_get_stripe", _no_network)
    # LLM entry points: Bot A is faked (create_case runs it), the rest must
    # never be reached by these tests
    monkeypatch.setattr(bots, "bot_a_generate_summary", _fake_bot_a)
    monkeypatch.setattr(server, "bot_a_generate_summary", _fake_bot_a)
    monkeypatch.setattr(server, "chat", _fake_chat)
    for name in ("bot_b_parse_document", "bot_c_fill_sections", "bot_d_analyze_gaps",
                 "bot_d_process_answer", "bot_d_process_answer_stream",
                 "ingest_file_to_databank"):
        monkeypatch.setattr(server, name, _no_network)
    monkeypatch.setattr(orchestrator_main, "run_cycle", _no_network)
    monkeypatch.setattr(orchestrator_tools, "handle_web_fetch", _no_network)
    yield tmp_path
    clear_config_cache()


@pytest.fixture
def client(data_dir):
    return TestClient(server.app)


def _register(client, name="Test Org", website_url=""):
    r = client.post("/api/auth/register", json={
        "email": f"{name.replace(' ', '-').lower()}@gobuga-test.org",
        "password": "TestPass123!",
        "org_name": name,
        "website_url": website_url,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    return data["org_id"], {"Authorization": f"Bearer {data['token']}"}


def _open_case(client, headers, n):
    return client.post("/api/cases", headers=headers,
                       json={"grant_id": f"grant-{n}", "grant_brief": {"title": f"Grant {n}"}})


def _spend(org_id, calls, when=None):
    """Write `calls` LLM calls into the org's usage log for the local day of
    `when` (default: now)."""
    date = llm_budget.local_date(when)
    logs = tenant.org_logs_dir(org_id)
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, f"usage-{date}.jsonl"), "a") as f:
        f.write(json.dumps({"org_id": org_id, "caller": "test", "api_calls": calls}) + "\n")


# --- 1. The free tier is unlimited --------------------------------------------

def test_free_account_opens_more_than_three_cases(client):
    org_id, headers = _register(client)
    assert limits.get_tier_key(org_id) == "scanner"
    for n in range(1, 7):
        r = _open_case(client, headers, n)
        assert r.status_code == 200, f"case {n}: {r.status_code} {r.text}"
    open_cases = [c for c in client.get("/api/cases", headers=headers).json() if c["status"] == "open"]
    assert len(open_cases) == 6


def test_free_account_sends_more_than_five_chat_messages(client):
    org_id, headers = _register(client)
    case_id = _open_case(client, headers, 1).json()["case_id"]
    for n in range(1, 9):
        r = client.post(f"/api/cases/{case_id}/chat", headers=headers, json={"message": f"message {n}"})
        assert r.status_code == 200, f"message {n}: {r.status_code} {r.text}"
    conversation = client.get(f"/api/cases/{case_id}/conversation", headers=headers).json()
    assert len([m for m in conversation if m["role"] == "officer"]) == 8


def test_free_account_has_every_feature(client):
    org_id, _ = _register(client)
    assert limits.check_feature_access(org_id, "bots_bcd")["allowed"] is True
    assert limits.check_feature_access(org_id, "export_docx")["allowed"] is True
    assert limits.check_case_limit(org_id)["allowed"] is True
    opps = [{"id": f"o{i}", "priority": p} for i, p in enumerate(["high"] * 5 + ["medium"] * 5 + ["low"] * 5)]
    assert limits.filter_opportunities_for_tier(opps, org_id) == opps


@pytest.mark.parametrize("country", ["nz", "md"])
def test_free_tier_is_unlimited_in_every_country_and_keeps_the_cheap_model(country, monkeypatch):
    monkeypatch.setenv("GOBUGA_COUNTRY", country)
    clear_config_cache()
    try:
        scanner = limits._get_tiers()["scanner"]
        assert scanner["opportunities_per_cycle"] is None
        assert scanner["max_open_cases"] == -1
        assert scanner["chat_messages_per_case"] == -1
        assert scanner["bots_bcd"] is True and scanner["export_docx"] is True
        assert scanner["model"] == "claude-haiku-4-5-20251001"
    finally:
        clear_config_cache()


def test_code_defaults_match_the_manifests():
    """Behaviour must not depend on whether the manifest or the default is read."""
    from api.country_config import _DEFAULT_TIERS
    scanner = _DEFAULT_TIERS["scanner"]
    assert scanner["opportunities_per_cycle"] is None
    assert scanner["max_open_cases"] == -1
    assert scanner["chat_messages_per_case"] == -1
    assert scanner["bots_bcd"] is True and scanner["export_docx"] is True
    assert scanner["model"] == "claude-haiku-4-5-20251001"


def test_tailored_ranking_is_open_to_a_free_account(client):
    org_id, headers = _register(client)
    assert get_org(org_id)["tailored_enabled"] is False  # the old toggle no longer gates
    r = client.get("/api/tailored/access", headers=headers)
    assert r.status_code == 200
    assert r.json()["allowed"] is True and r.json()["reason"] is None


def test_tailored_cooldown_still_applies_to_everyone(client):
    """The 7-day cooldown is a cost guard, kept for every account."""
    org_id, _ = _register(client)
    limits.trigger_cycle(org_id)
    access = limits.check_tailored_access(org_id)
    assert access["allowed"] is False and access["reason"] == "cooldown"
    assert "officer" not in access["message"].lower() and "upgrade" not in access["message"].lower()


def test_limit_messages_never_say_upgrade(client, monkeypatch):
    """If limits are ever reinstated, the messages must not sell an upgrade."""
    org_id, headers = _register(client)
    case_id = _open_case(client, headers, 1).json()["case_id"]
    client.post(f"/api/cases/{case_id}/chat", headers=headers, json={"message": "hi"})
    capped = {"scanner": {"label": "Grant Scanner", "opportunities_per_cycle": {"high": 1},
                          "max_open_cases": 1, "chat_messages_per_case": 1,
                          "bots_bcd": False, "export_docx": False, "model": "m"}}
    monkeypatch.setattr(limits, "_get_tiers", lambda: capped)
    messages = [
        limits.check_case_limit(org_id)["message"],
        limits.check_chat_limit(org_id, case_id)["message"],
        limits.check_feature_access(org_id, "bots_bcd")["message"],
    ]
    for m in messages:
        assert m, "expected the capped tier to refuse"
        assert "upgrade" not in m.lower() and "officer" not in m.lower(), m


def test_officer_account_is_unchanged(client):
    free_id, _ = _register(client, "Free Org")
    officer_id, headers = _register(client, "Paying Org")
    update_org(officer_id, {"plan": "starter", "tier_source": "stripe", "stripe_subscription_id": "sub_1"})
    assert limits.get_tier_key(officer_id) == "officer"
    officer = limits.get_tier(officer_id)
    assert officer["model"] == "claude-sonnet-5"
    assert officer["max_open_cases"] == -1 and officer["bots_bcd"] is True
    assert limits.get_model_overrides(officer_id)["grant_watcher"] == "claude-sonnet-5"
    # The free account did not get the paid model
    assert limits.get_model_overrides(free_id)["grant_watcher"] == "claude-haiku-4-5-20251001"
    session = client.post("/api/auth/verify", headers=headers).json()
    assert session["tier"] == "officer" and session["tier_source"] == "stripe"


def test_no_prices_in_public_config_or_session(client):
    _, headers = _register(client)
    assert "tiers" not in client.get("/api/public/country-config").json()
    assert "tiers" not in client.post("/api/auth/verify", headers=headers).json()


# --- 2. Daily LLM budget -------------------------------------------------------

def test_budget_default_and_env(monkeypatch):
    monkeypatch.delenv("DAILY_LLM_CALL_BUDGET", raising=False)
    assert llm_budget.daily_llm_call_budget() == 200
    monkeypatch.setenv("DAILY_LLM_CALL_BUDGET", "35")
    assert llm_budget.daily_llm_call_budget() == 35
    monkeypatch.setenv("DAILY_LLM_CALL_BUDGET", "lots")
    assert llm_budget.daily_llm_call_budget() == 200


def test_budget_counts_the_usage_log_and_blocks_at_the_limit(data_dir, monkeypatch):
    monkeypatch.setenv("DAILY_LLM_CALL_BUDGET", "5")
    _spend("org1", 3)
    log_usage("org1", "chat", {"api_calls": 1})  # the real logger counts too
    check = llm_budget.check_llm_budget("org1")
    assert check == {"allowed": True, "used": 4, "limit": 5, "message": None}
    _spend("org1", 1)
    check = llm_budget.check_llm_budget("org1")
    assert check["allowed"] is False and check["used"] == 5
    assert check["message"] == llm_budget.BUDGET_EXHAUSTED_MESSAGE
    assert llm_budget.check_llm_budget("other-org")["allowed"] is True  # per org


def test_budget_resets_on_the_next_local_day(data_dir, monkeypatch):
    """The day is the country's, not UTC's. NZ is UTC+13 on 28 Sep 2026."""
    monkeypatch.setenv("DAILY_LLM_CALL_BUDGET", "2")
    late_evening = datetime(2026, 9, 28, 10, 30, tzinfo=timezone.utc)  # 23:30 NZDT, 28 Sep
    after_midnight = late_evening + timedelta(hours=1)                 # 00:30 NZDT, 29 Sep
    assert llm_budget.local_date(late_evening) == "2026-09-28"
    assert llm_budget.local_date(after_midnight) == "2026-09-29"
    assert after_midnight.date().isoformat() == "2026-09-28"           # still the 28th in UTC
    _spend("org1", 2, when=late_evening)
    assert llm_budget.check_llm_budget("org1", now=late_evening)["allowed"] is False
    assert llm_budget.check_llm_budget("org1", now=after_midnight)["allowed"] is True


@pytest.mark.parametrize("value", ["0", "-1"])
def test_budget_disabled_by_zero_or_negative(data_dir, monkeypatch, value):
    monkeypatch.setenv("DAILY_LLM_CALL_BUDGET", value)
    _spend("org1", 10_000)
    assert llm_budget.check_llm_budget("org1") == {"allowed": True, "used": 0, "limit": -1, "message": None}


def test_budget_exempts_sweep_orgs(data_dir, monkeypatch):
    monkeypatch.setenv("DAILY_LLM_CALL_BUDGET", "1")
    sweep_org = "_sweep:nz:2026-09:120000"
    log_usage(sweep_org, "grant_watcher", {"api_calls": 50})
    assert llm_budget.check_llm_budget(sweep_org)["allowed"] is True


def _guarded_routes(case_id):
    """Every route that starts LLM work on behalf of an org."""
    return [
        ("post", "/api/cases", {"grant_id": "g", "grant_brief": {}}),
        ("post", "/api/reports/open-case", {"opportunity_id": "x", "cycle_date": "2026-09-28"}),
        ("post", "/api/opportunities/open-case", {"opportunity_id": "x"}),
        ("post", f"/api/cases/{case_id}/chat", {"message": "hi"}),
        ("post", "/api/cycle/run", {}),
        ("post", "/api/tailored/run", None),
        ("post", f"/api/cases/{case_id}/bot/summary", None),
        ("post", f"/api/cases/{case_id}/bot/parse", {"filename": "f.docx"}),
        ("post", f"/api/cases/{case_id}/bot/fill", None),
        ("post", f"/api/cases/{case_id}/bot/parse-and-fill", {"filename": "f.docx"}),
        ("post", f"/api/cases/{case_id}/bot/questions", None),
        ("post", f"/api/cases/{case_id}/bot/answer", {"message": "hi"}),
        ("post", f"/api/cases/{case_id}/bot/answer/stream", {"message": "hi"}),
        ("post", f"/api/cases/{case_id}/bot/ingest", {"filename": "f.pdf"}),
    ]


def test_every_llm_route_returns_429_when_the_budget_is_spent(client, monkeypatch):
    monkeypatch.setenv("DAILY_LLM_CALL_BUDGET", "3")
    org_id, headers = _register(client)
    case_id = _open_case(client, headers, 1).json()["case_id"]  # spends 1 via fake Bot A
    _spend(org_id, 2)
    cases_before = len(client.get("/api/cases", headers=headers).json())

    for method, path, body in _guarded_routes(case_id):
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        r = getattr(client, method)(path, **kwargs)
        assert r.status_code == 429, f"{path}: {r.status_code} {r.text}"
        assert r.json() == {"detail": llm_budget.BUDGET_EXHAUSTED_MESSAGE}, path

    # Nothing was started or recorded by the refused requests
    assert len(client.get("/api/cases", headers=headers).json()) == cases_before
    assert limits.get_cycle_timer(org_id) is None  # tailored run did not start the cooldown
    assert server._cycle_state["running"] is False


def test_budget_message_is_plain(client):
    m = llm_budget.BUDGET_EXHAUSTED_MESSAGE.lower()
    assert "daily limit reached" in m and "resets tomorrow" in m and "nothing is lost" in m


def test_disabled_budget_lets_llm_routes_through(client, monkeypatch):
    monkeypatch.setenv("DAILY_LLM_CALL_BUDGET", "0")
    org_id, headers = _register(client)
    _spend(org_id, 10_000)
    assert _open_case(client, headers, 1).status_code == 200


# --- 3. Billing: checkout frozen, portal and webhook unchanged ----------------

class _FakeStripe:
    """The slice of the stripe module that api/billing.py touches."""

    def __init__(self, event=None):
        self.created_sessions = []
        outer = self

        class Customer:
            @staticmethod
            def create(metadata):
                return type("C", (), {"id": "cus_new"})()

        class _CheckoutSession:
            @staticmethod
            def create(**kwargs):
                outer.created_sessions.append(kwargs)
                return type("S", (), {"url": "https://checkout.example/s", "id": "cs_1"})()

        class _PortalSession:
            @staticmethod
            def create(customer, return_url):
                return type("P", (), {"url": f"https://portal.example/{customer}"})()

        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret):
                if sig != "good":
                    raise outer.error.SignatureVerificationError("bad signature")
                return event

        class error:
            class SignatureVerificationError(Exception):
                pass

        self.Customer = Customer
        self.checkout = type("checkout", (), {"Session": _CheckoutSession})
        self.billing_portal = type("billing_portal", (), {"Session": _PortalSession})
        self.Webhook = Webhook
        self.error = error


@pytest.fixture
def fake_stripe(monkeypatch):
    fake = _FakeStripe()
    monkeypatch.setattr(billing, "STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(billing, "STRIPE_STARTER_PRICE_ID", "price_starter")
    monkeypatch.setattr(billing, "_get_stripe", lambda: fake)
    return fake


def test_checkout_is_frozen(client, fake_stripe):
    _, headers = _register(client)
    r = client.post("/api/billing/checkout", headers=headers, json={"plan": "starter"})
    assert r.status_code == 410
    assert "closed" in r.json()["detail"].lower()
    assert fake_stripe.created_sessions == []


@pytest.mark.parametrize("value", ["0", "", "true"])
def test_checkout_stays_frozen_unless_flag_is_exactly_1(client, fake_stripe, monkeypatch, value):
    _, headers = _register(client)
    monkeypatch.setenv("BILLING_CHECKOUT_ENABLED", value)
    assert client.post("/api/billing/checkout", headers=headers, json={"plan": "starter"}).status_code == 410


def test_checkout_works_when_flag_is_on(client, fake_stripe, monkeypatch):
    org_id, headers = _register(client)
    monkeypatch.setenv("BILLING_CHECKOUT_ENABLED", "1")
    r = client.post("/api/billing/checkout", headers=headers, json={"plan": "starter"})
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://checkout.example/s", "session_id": "cs_1"}
    assert fake_stripe.created_sessions[0]["metadata"] == {"org_id": org_id, "plan": "starter"}
    assert get_org(org_id)["stripe_customer_id"] == "cus_new"


def test_checkout_with_flag_on_but_no_stripe_is_503_as_before(client, monkeypatch):
    _, headers = _register(client)
    monkeypatch.setenv("BILLING_CHECKOUT_ENABLED", "1")
    assert client.post("/api/billing/checkout", headers=headers, json={"plan": "starter"}).status_code == 503


def test_portal_still_works_for_a_paying_org(client, fake_stripe):
    org_id, headers = _register(client)
    update_org(org_id, {"plan": "starter", "stripe_customer_id": "cus_paying",
                        "stripe_subscription_id": "sub_1", "tier_source": "stripe"})
    r = client.get("/api/billing/portal", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://portal.example/cus_paying"}


def test_portal_without_a_customer_is_400_as_before(client, fake_stripe):
    _, headers = _register(client)
    assert client.get("/api/billing/portal", headers=headers).status_code == 400


def test_portal_without_stripe_is_503_as_before(client):
    _, headers = _register(client)
    assert client.get("/api/billing/portal", headers=headers).status_code == 503


def test_webhook_still_syncs_a_cancellation(client, fake_stripe, monkeypatch):
    org_id, _ = _register(client)
    update_org(org_id, {"plan": "starter", "stripe_subscription_id": "sub_x", "tier_source": "stripe"})
    event = {"type": "customer.subscription.deleted", "id": "evt_1", "data": {"object": {"id": "sub_x"}}}
    monkeypatch.setattr(billing, "_get_stripe", lambda: _FakeStripe(event))
    r = client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "good"})
    assert r.status_code == 200 and r.json() == {"received": True}
    org = get_org(org_id)
    assert org["plan"] == "free" and org["stripe_subscription_id"] is None


def test_webhook_still_rejects_a_bad_signature(client, fake_stripe, monkeypatch):
    monkeypatch.setattr(billing, "_get_stripe", lambda: _FakeStripe({}))
    r = client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "forged"})
    assert r.status_code == 400


# --- 4. One optional sign-up screen --------------------------------------------

def test_setup_accepts_an_empty_submission(client):
    org_id, headers = _register(client, "Harbour Rowing Club")
    r = client.post("/api/org/setup", headers=headers, json={})
    assert r.status_code == 200, r.text
    org = get_org(org_id)
    assert org["setup_complete"] is True
    assert org["name"] == "Harbour Rowing Club"          # kept from registration
    assert org["country"] == "nz"                         # kept from registration
    assert org["sectors"] == [] and org["geographies"] == []
    assert org["seeding_complete"] is False               # the screen's own step sets it
    profile = open(tenant.org_profile_path(org_id)).read()
    assert profile.startswith("# Harbour Rowing Club")
    prompt = open(os.path.join(tenant.org_prompts_dir(org_id), "grant_watcher.md")).read()
    assert "{{" not in prompt


def test_setup_accepts_a_partial_submission_and_keeps_the_rest(client):
    org_id, headers = _register(client, "Harbour Rowing Club")
    r = client.post("/api/org/setup", headers=headers,
                    json={"sectors": ["Sport & recreation"], "geographies": ["New Zealand > Canterbury"]})
    assert r.status_code == 200, r.text
    r = client.post("/api/org/setup", headers=headers, json={"sectors": ["Youth & children"]})
    assert r.status_code == 200, r.text
    org = get_org(org_id)
    assert org["name"] == "Harbour Rowing Club"
    assert org["sectors"] == ["Youth & children"]
    assert org["geographies"] == ["New Zealand > Canterbury"]  # untouched by the second call


def test_setup_with_a_website_scrapes_it_through_the_fake_fetch(client, monkeypatch):
    org_id, headers = _register(client)
    fetched = []
    monkeypatch.setattr(orchestrator_tools, "handle_web_fetch",
                        lambda args: fetched.append(args["url"]) or "About us: we row.")
    r = client.post("/api/org/setup", headers=headers, json={"website": "rowing.example.org"})
    assert r.status_code == 200, r.text
    assert fetched == ["https://rowing.example.org"]
    org = get_org(org_id)
    assert org["website"] == org["website_url"] == "https://rowing.example.org"
    scrape = os.path.join(tenant.org_data_dir(org_id), "org", "website-scrape.md")
    assert "we row" in open(scrape).read()


def test_setup_org_takes_the_fetch_function_as_a_parameter(data_dir):
    from api.auth import _save_orgs
    from api.org_setup import setup_org
    _save_orgs({"o1": {"name": "Org", "country": "nz", "website_url": "https://org.example"}})
    fetched = []
    setup_org("o1", {}, fetch=lambda args: fetched.append(args["url"]) or "text")
    assert fetched == ["https://org.example"]


def test_register_without_a_website_is_accepted(client):
    org_id, _ = _register(client, website_url="")
    assert get_org(org_id)["website_url"] == ""


@pytest.mark.parametrize("setup_complete, seeding_complete", [
    (False, False),   # new account
    (True, False),    # part-way through the old two-step flow
    (False, True),    # seeding flagged without setup (only reachable through the API)
])
def test_the_one_screen_finishes_onboarding_from_any_state(client, setup_complete, seeding_complete):
    """Whatever state an account is in, submitting the screen (setup, then
    seeding-complete) leaves it with both flags set, so the gate sends it to
    the feed and never back to the screen."""
    org_id, headers = _register(client)
    update_org(org_id, {"setup_complete": setup_complete, "seeding_complete": seeding_complete})
    assert client.post("/api/org/setup", headers=headers, json={}).status_code == 200
    assert client.post("/api/org/seeding-complete", headers=headers).status_code == 200
    session = client.post("/api/auth/verify", headers=headers).json()
    assert session["setup_complete"] is True and session["seeding_complete"] is True


def test_legacy_org_without_seeding_flag_is_treated_as_done(client):
    org_id, headers = _register(client)
    from api.auth import _load_orgs, _save_orgs
    orgs = _load_orgs()
    orgs[org_id]["setup_complete"] = True
    del orgs[org_id]["seeding_complete"]
    _save_orgs(orgs)
    session = client.post("/api/auth/verify", headers=headers).json()
    assert session["setup_complete"] is True and session["seeding_complete"] is True


def test_profile_edit_no_longer_sends_a_finished_org_back_to_seeding(client):
    org_id, headers = _register(client)
    client.post("/api/org/setup", headers=headers, json={})
    client.post("/api/org/seeding-complete", headers=headers)
    r = client.patch("/api/org/profile", headers=headers, json={"sectors": ["Education"]})
    assert r.status_code == 200, r.text
    org = get_org(org_id)
    assert org["sectors"] == ["Education"]
    assert org["seeding_complete"] is True and org["setup_complete"] is True


# --- Audit additions: cost and tenancy guards on the legacy cycle and chat ----

def test_cycle_run_needs_a_trigger_and_runs_once_per_trigger(client, monkeypatch):
    runs = []
    monkeypatch.setattr(orchestrator_main, "run_cycle",
                        lambda org_id, *a, **kw: runs.append(org_id))
    org_id, headers = _register(client)

    r = client.post("/api/cycle/run", headers=headers, json={})
    assert r.status_code == 409 and "Start a cycle" in r.json()["detail"]

    limits.trigger_cycle(org_id)
    assert client.post("/api/cycle/run", headers=headers, json={}).status_code == 200
    for _ in range(50):
        if not server._cycle_state["running"]:
            break
        time.sleep(0.02)
    r = client.post("/api/cycle/run", headers=headers, json={})
    assert r.status_code == 409 and "already run" in r.json()["detail"]
    assert runs == [org_id]


def test_cycle_status_does_not_show_another_orgs_run(client, monkeypatch):
    _, mine = _register(client, "Mine")
    other_id, theirs = _register(client, "Theirs")
    monkeypatch.setitem(server._cycle_state, "org_id", other_id)
    monkeypatch.setitem(server._cycle_state, "running", False)
    monkeypatch.setitem(server._cycle_state, "error", "their private failure text")
    assert client.get("/api/cycle/status", headers=mine).json() == {"status": "idle"}
    assert client.get("/api/cycle/status", headers=theirs).json()["status"] == "error"


def test_chat_uses_the_orgs_tier_model(client, monkeypatch):
    seen = []

    def fake_chat(org_id, case_id, message, model=None):
        seen.append(model)
        return {"response": "ok"}
    monkeypatch.setattr(server, "chat", fake_chat)
    org_id, headers = _register(client)
    case_id = _open_case(client, headers, 1).json()["case_id"]
    client.post(f"/api/cases/{case_id}/chat", headers=headers, json={"message": "hi"})
    assert seen == [limits.get_tier(org_id)["model"]]
    assert "haiku" in seen[0]
