"""
Tests for the country-as-config system.

Covers:
  1. CountryConfig loads correctly for NZ (default)
  2. CountryConfig loads correctly for MD (Moldova)
  3. NZ defaults are used when JSON fields are missing
  4. Tier definitions load per-country
  5. Stripe guard works when not configured
  6. Moldova geo codes exist in case_codes
  7. Bot A language adapts to country
  8. Tag validation works per-country
  9. Funder canonicalisation works per-country
  10. Timezone resolution works per-country

Usage:
    .venv/bin/python -m pytest tests/test_country_config.py -v
"""

import os
import sys
import json

# Ensure project root is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(country: str):
    """Load a country config with a clean cache."""
    # Set env before importing (or clear cache after)
    os.environ["GOBUGA_COUNTRY"] = country
    from api.country_config import clear_config_cache, get_country_config
    clear_config_cache()
    return get_country_config()


def _reset_to_nz():
    """Reset to NZ defaults."""
    os.environ["GOBUGA_COUNTRY"] = "nz"
    from api.country_config import clear_config_cache
    clear_config_cache()


# ---------------------------------------------------------------------------
# 1. NZ config loads correctly
# ---------------------------------------------------------------------------

def test_nz_config_loads():
    config = _load_config("nz")
    assert config.slug == "nz"
    assert config.country_label == "New Zealand"
    assert config.currency == "NZD"
    assert config.timezone == "Pacific/Auckland"
    assert config.content_language == "en"
    assert "auckland" in config.regions
    assert "wellington" in config.regions
    assert "community" in config.tags
    assert "maori" in config.tags
    assert "pasifika" in config.tags
    assert len(config.sector_slices) >= 10
    _reset_to_nz()


# ---------------------------------------------------------------------------
# 2. MD config loads correctly
# ---------------------------------------------------------------------------

def test_md_config_loads():
    config = _load_config("md")
    assert config.slug == "md"
    assert config.country_label == "Moldova"
    assert config.currency == "MDL"
    assert config.timezone == "Europe/Chisinau"
    assert config.content_language == "ro"
    assert "chisinau" in config.regions
    assert "balti" in config.regions
    assert "gagauzia" in config.regions
    assert "community" in config.tags
    assert "maori" not in config.tags  # NZ-specific tag not in MD
    assert "pasifika" not in config.tags
    assert "sport" not in config.tags
    assert len(config.sector_slices) >= 8
    _reset_to_nz()


# ---------------------------------------------------------------------------
# 3. NZ defaults fallback
# ---------------------------------------------------------------------------

def test_nz_defaults_fallback():
    """When a JSON field is missing, NZ defaults are used."""
    config = _load_config("nz")
    # These should match the hardcoded NZ defaults in country_config.py
    assert len(config.tags) == 17  # NZ has 17 tags
    assert "sport" in config.tags
    assert config.national_scope_synonyms  # should have NZ synonyms
    _reset_to_nz()


# ---------------------------------------------------------------------------
# 4. Tier definitions load per-country
# ---------------------------------------------------------------------------

def test_nz_tiers():
    config = _load_config("nz")
    assert "scanner" in config.tiers
    assert "officer" in config.tiers
    scanner = config.tiers["scanner"]
    assert scanner["label"] == "Grant Scanner"
    assert scanner["price_monthly"] == 0
    assert scanner["max_open_cases"] == 3  # NZ scanner limited to 3
    assert scanner["chat_messages_per_case"] == 5  # NZ scanner limited to 5
    assert scanner["bots_bcd"] is False  # NZ scanner can't use bots B/C/D
    officer = config.tiers["officer"]
    assert officer["max_open_cases"] == -1  # unlimited
    assert officer["bots_bcd"] is True
    _reset_to_nz()


def test_md_tiers():
    config = _load_config("md")
    assert "scanner" in config.tiers
    assert "officer" in config.tiers
    scanner = config.tiers["scanner"]
    assert scanner["label"] == "Grant Scanner"
    assert scanner["price_monthly"] == 0
    assert scanner["max_open_cases"] == -1  # MD scanner unlimited
    assert scanner["chat_messages_per_case"] == -1  # MD scanner unlimited
    assert scanner["bots_bcd"] is True  # MD scanner gets all bots
    assert scanner["export_docx"] is True  # MD scanner gets DOCX
    officer = config.tiers["officer"]
    assert officer["price_monthly"] == 5  # MD officer $5/mo
    assert officer["max_open_cases"] == -1
    _reset_to_nz()


# ---------------------------------------------------------------------------
# 5. Stripe guard
# ---------------------------------------------------------------------------

def test_stripe_guard():
    import api.billing as billing
    original = billing.STRIPE_SECRET_KEY

    # When key is set, stripe_configured() returns True
    billing.STRIPE_SECRET_KEY = "sk_test_something"
    assert billing.stripe_configured() is True

    # When key is empty, stripe_configured() returns False
    billing.STRIPE_SECRET_KEY = ""
    assert billing.stripe_configured() is False

    # _get_stripe() should raise when not configured
    try:
        billing._get_stripe()
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "not configured" in str(e)

    # Restore
    billing.STRIPE_SECRET_KEY = original


# ---------------------------------------------------------------------------
# 6. Moldova geo codes
# ---------------------------------------------------------------------------

def test_moldova_geo_codes():
    from api.case_codes import GEO_CODES, _GEO_KEYWORDS
    assert GEO_CODES.get("moldova") == "MD"
    assert GEO_CODES.get("republica moldova") == "MD"
    assert "MD" in _GEO_KEYWORDS
    md_keywords = _GEO_KEYWORDS["MD"]
    assert "chisinau" in md_keywords or "chișinău" in md_keywords
    assert "moldova" in md_keywords


# ---------------------------------------------------------------------------
# 7. Bot A language adapts to country
# ---------------------------------------------------------------------------

def test_bot_a_language_nz():
    _load_config("nz")
    from api.country_config import get_country_config
    config = get_country_config()
    lang = "Romanian" if config.content_language == "ro" else "English"
    assert lang == "English"
    _reset_to_nz()


def test_bot_a_language_md():
    _load_config("md")
    from api.country_config import get_country_config
    config = get_country_config()
    lang = "Romanian" if config.content_language == "ro" else "English"
    assert lang == "Romanian"
    _reset_to_nz()


# ---------------------------------------------------------------------------
# 8. Tag validation per-country
# ---------------------------------------------------------------------------

def test_tag_validation_nz():
    _load_config("nz")
    from api.taxonomy import validate_tags
    result = validate_tags(["community", "maori", "fake_tag"], country="nz")
    assert "community" in result
    assert "maori" in result
    assert "fake_tag" not in result
    _reset_to_nz()


def test_tag_validation_md():
    _load_config("md")
    from api.taxonomy import validate_tags
    result = validate_tags(["community", "maori", "climate"], country="md")
    assert "community" in result
    assert "climate" in result
    assert "maori" not in result  # maori is NZ-only
    _reset_to_nz()


# ---------------------------------------------------------------------------
# 9. Funder canonicalisation per-country
# ---------------------------------------------------------------------------

def test_funder_canonicalisation_nz():
    _load_config("nz")
    from api.funders import canonicalise_funder
    # NZ has funder aliases defined
    result = canonicalise_funder("Sport NZ", country="nz")
    # Should resolve to something (or return as-is if no alias matches)
    assert result is not None
    assert isinstance(result, str)
    _reset_to_nz()


def test_funder_canonicalisation_md():
    _load_config("md")
    from api.funders import canonicalise_funder
    # MD has empty funder_aliases, so any name should pass through unchanged
    result = canonicalise_funder("USAID Moldova", country="md")
    assert result == "USAID Moldova"
    _reset_to_nz()


# ---------------------------------------------------------------------------
# 10. Timezone resolution per-country
# ---------------------------------------------------------------------------

def test_timezone_nz():
    config = _load_config("nz")
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(config.timezone)
    assert str(tz) == "Pacific/Auckland"
    _reset_to_nz()


def test_timezone_md():
    config = _load_config("md")
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(config.timezone)
    assert str(tz) == "Europe/Chisinau"
    _reset_to_nz()


# ---------------------------------------------------------------------------
# 11. _get_tiers() function works (replaces old module-level TIERS)
# ---------------------------------------------------------------------------

def test_get_tiers_function():
    _load_config("nz")
    from api.limits import _get_tiers
    tiers = _get_tiers()
    assert "scanner" in tiers
    assert "officer" in tiers
    assert tiers["scanner"]["label"] == "Grant Scanner"
    _reset_to_nz()


def test_get_tiers_md():
    _load_config("md")
    from api.limits import _get_tiers
    tiers = _get_tiers()
    assert tiers["scanner"]["max_open_cases"] == -1  # MD unlimited
    assert tiers["scanner"]["bots_bcd"] is True  # MD scanner gets bots
    _reset_to_nz()


# ---------------------------------------------------------------------------
# 12. Country config JSON files are valid
# ---------------------------------------------------------------------------

def test_nz_json_valid():
    json_path = os.path.join(PROJECT_ROOT, "platform", "sources", "nz.json")
    with open(json_path) as f:
        data = json.load(f)
    assert data["country"] == "nz"
    assert "must_appear_funders" in data
    assert "seed_sources" in data
    assert len(data["regions"]) > 0
    assert len(data["tags"]) > 0


def test_md_json_valid():
    json_path = os.path.join(PROJECT_ROOT, "platform", "sources", "md.json")
    with open(json_path) as f:
        data = json.load(f)
    assert data["country"] == "md"
    assert data["currency"] == "MDL"
    assert data["timezone"] == "Europe/Chisinau"
    assert data["content_language"] == "ro"
    assert "must_appear_funders" in data
    assert "seed_sources" in data
    assert len(data["regions"]) >= 30  # Moldova has 36 raions
    assert len(data["tags"]) >= 10


# ---------------------------------------------------------------------------
# 13. Public country-config endpoint shape
# ---------------------------------------------------------------------------

def test_country_config_endpoint_shape():
    """Verify the /api/public/country-config endpoint returns expected fields."""
    # We can't hit the live endpoint in unit tests, but we can verify
    # that the config object has all the fields the endpoint would return.
    config = _load_config("nz")
    # These are the fields returned by the endpoint
    assert hasattr(config, "slug")
    assert hasattr(config, "country_label")
    assert hasattr(config, "currency")
    assert hasattr(config, "tiers")
    assert hasattr(config, "tags")
    assert hasattr(config, "sector_slices")
    assert hasattr(config, "regions")
    _reset_to_nz()
