"""tier_source: why an org holds a paid tier (stripe | licence | dev).

Pure-Python, no server. Covers inference for legacy records, the dev toggle
marking itself, Stripe reconcile/webhook paths writing the field, licensed
orgs surviving an empty Stripe record, and the audit script's table.
"""

import json
import os

import pytest

import api.auth as auth
import api.billing as billing
import api.limits as limits
import api.tenant as tenant


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tenant, "PLATFORM_DIR", str(tmp_path / "platform"))
    monkeypatch.setattr(tenant, "ORGS_DIR", str(tmp_path / "orgs"))
    return tmp_path


def _write_orgs(data_dir, orgs: dict):
    pdir = data_dir / "platform"
    pdir.mkdir(exist_ok=True)
    (pdir / "orgs.json").write_text(json.dumps(orgs))


def _read_orgs(data_dir):
    return json.loads((data_dir / "platform" / "orgs.json").read_text())


# --- inference ---

@pytest.mark.parametrize("org, expected", [
    (None, None),
    ({"plan": "free"}, None),
    ({"plan": "free", "tier_source": "licence"}, None),          # free is free
    ({"plan": "starter", "stripe_subscription_id": "sub_1"}, "stripe"),
    ({"plan": "professional", "stripe_subscription_id": "sub_1"}, "stripe"),
    ({"plan": "starter"}, None),                                  # unexplained
    ({"plan": "starter", "tier_source": "licence"}, "licence"),
    ({"plan": "starter", "tier_source": "dev"}, "dev"),
    ({"plan": "starter", "tier_source": "bogus", "stripe_subscription_id": "sub_1"}, "stripe"),
])
def test_tier_source_for(org, expected):
    assert limits.tier_source_for(org) == expected


def test_dev_toggle_marks_itself(data_dir):
    _write_orgs(data_dir, {"o1": {"name": "Dev Org", "plan": "free"}})
    assert limits.toggle_tier("o1") == "officer"
    org = _read_orgs(data_dir)["o1"]
    assert org["plan"] == "starter"
    assert org["tier_source"] == "dev"
    assert limits.get_tier_source("o1") == "dev"


# --- Stripe paths ---

class _FakeStripe:
    def __init__(self, subs):
        self._subs = subs
        outer = self

        class Subscription:
            @staticmethod
            def list(customer, limit):
                return type("R", (), {"data": outer._subs})()
        self.Subscription = Subscription


def _active_sub(price_id="price_starter", sub_id="sub_live"):
    return {"id": sub_id, "status": "active",
            "items": {"data": [{"price": {"id": price_id}}]}}


@pytest.fixture
def stripe_env(monkeypatch):
    monkeypatch.setattr(billing, "PRICE_TO_PLAN", {"price_starter": "starter"})


def test_reconcile_upgrade_sets_stripe_source(data_dir, stripe_env, monkeypatch):
    _write_orgs(data_dir, {"o1": {"name": "Paying", "plan": "free", "stripe_customer_id": "cus_1"}})
    monkeypatch.setattr(billing, "_get_stripe", lambda: _FakeStripe([_active_sub()]))
    billing.reconcile_from_stripe("o1")
    org = _read_orgs(data_dir)["o1"]
    assert org["plan"] == "starter"
    assert org["stripe_subscription_id"] == "sub_live"
    assert org["tier_source"] == "stripe"


def test_reconcile_downgrade_clears_source(data_dir, stripe_env, monkeypatch):
    _write_orgs(data_dir, {"o1": {"name": "Lapsed", "plan": "starter", "stripe_customer_id": "cus_1",
                                  "stripe_subscription_id": "sub_old", "tier_source": "stripe"}})
    monkeypatch.setattr(billing, "_get_stripe", lambda: _FakeStripe([]))
    billing.reconcile_from_stripe("o1")
    org = _read_orgs(data_dir)["o1"]
    assert org["plan"] == "free"
    assert org["stripe_subscription_id"] is None
    assert org["tier_source"] is None


def test_reconcile_leaves_licensed_org_alone(data_dir, stripe_env, monkeypatch):
    """A licensed org that once had a Stripe customer must not be downgraded
    just because Stripe has no active subscription for it."""
    _write_orgs(data_dir, {"o1": {"name": "Licensed", "plan": "starter",
                                  "stripe_customer_id": "cus_1", "tier_source": "licence"}})
    called = []
    monkeypatch.setattr(billing, "_get_stripe", lambda: called.append(1) or _FakeStripe([]))
    billing.reconcile_from_stripe("o1")
    org = _read_orgs(data_dir)["o1"]
    assert org["plan"] == "starter"
    assert org["tier_source"] == "licence"
    assert called == []


def test_webhook_checkout_completed_sets_stripe_source(data_dir, stripe_env, monkeypatch):
    _write_orgs(data_dir, {"o1": {"name": "New", "plan": "free"}})
    event = {"type": "checkout.session.completed", "id": "evt_1",
             "data": {"object": {"metadata": {"org_id": "o1", "plan": "starter"}, "subscription": "sub_new"}}}

    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            return event

    class error:
        class SignatureVerificationError(Exception):
            pass

    monkeypatch.setattr(billing, "_get_stripe", lambda: type("S", (), {"Webhook": Webhook, "error": error}))
    billing.handle_webhook(b"{}", "sig")
    org = _read_orgs(data_dir)["o1"]
    assert org["plan"] == "starter" and org["tier_source"] == "stripe"


def test_webhook_subscription_deleted_clears_source(data_dir, stripe_env, monkeypatch):
    _write_orgs(data_dir, {"o1": {"name": "Gone", "plan": "starter",
                                  "stripe_subscription_id": "sub_x", "tier_source": "stripe"}})
    event = {"type": "customer.subscription.deleted", "id": "evt_2", "data": {"object": {"id": "sub_x"}}}

    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            return event

    class error:
        class SignatureVerificationError(Exception):
            pass

    monkeypatch.setattr(billing, "_get_stripe", lambda: type("S", (), {"Webhook": Webhook, "error": error}))
    billing.handle_webhook(b"{}", "sig")
    org = _read_orgs(data_dir)["o1"]
    assert org["plan"] == "free" and org["tier_source"] is None


# --- audit script ---

def test_audit_lists_only_unexplained_and_non_stripe_paid_orgs(data_dir):
    from scripts.audit_tier_grants import audit, main
    _write_orgs(data_dir, {
        "free1": {"name": "Free", "plan": "free", "created": "2026-01-01T00:00:00"},
        "pay1": {"name": "Paying", "plan": "starter", "stripe_subscription_id": "sub_1", "created": "2026-02-01T00:00:00"},
        "lic1": {"name": "Bowen", "plan": "starter", "tier_source": "licence", "created": "2026-03-01T00:00:00",
                 "updated": "2026-09-01T00:00:00"},
        "dev1": {"name": "Toggled", "plan": "starter", "tier_source": "dev", "created": "2026-04-01T00:00:00"},
        "unk1": {"name": "Mystery", "plan": "professional", "created": "2026-05-01T00:00:00"},
    })
    cases = data_dir / "orgs" / "unk1" / "cases" / "c1"
    cases.mkdir(parents=True)
    (cases / "case.json").write_text("{}")

    rows = audit(str(data_dir))
    assert [r["org_id"] for r in rows] == ["lic1", "dev1", "unk1"]
    by_id = {r["org_id"]: r for r in rows}
    assert by_id["lic1"]["tier_source"] == "licence"
    assert by_id["lic1"]["last_activity"] == "2026-09-01"
    assert by_id["dev1"]["tier_source"] == "dev"
    assert by_id["unk1"]["tier_source"] == "UNEXPLAINED"
    assert by_id["unk1"]["last_activity"] != "-"   # from the case file mtime

    assert [r["org_id"] for r in audit(str(data_dir), include_all=True)] == ["pay1", "lic1", "dev1", "unk1"]

    # --mark-licence is the only write, and only on request
    assert main(["--data-dir", str(data_dir), "--mark-licence", "unk1", "nope"]) == 0
    assert _read_orgs(data_dir)["unk1"]["tier_source"] == "licence"
    assert audit(str(data_dir))[2]["tier_source"] == "licence"
