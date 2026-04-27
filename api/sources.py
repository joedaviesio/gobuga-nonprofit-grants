"""Country source manifests — loads `platform/sources/<country>.json`.

Each manifest seeds the watcher's source list and defines the
`must_appear_funders` spot-check used to validate sweep coverage.

Schema (NZ as reference):
- `must_appear_funders`: [{name, url, sectors}]
- `seed_sources`: [{name, url, category, scope, sectors, regions?}]
- `regions`: list of region slugs
- `currency`: ISO currency
"""

import json
import os

from api.tenant import platform_sources_path


class SourceManifestError(Exception):
    pass


def load_manifest(country: str) -> dict:
    path = platform_sources_path(country)
    if not os.path.exists(path):
        raise SourceManifestError(f"Source manifest not found: {path}")
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise SourceManifestError(f"{path} is not valid JSON: {e}") from e
    for required in ("country", "seed_sources", "must_appear_funders"):
        if required not in data:
            raise SourceManifestError(f"{path} missing field: {required}")
    return data


def list_seed_sources(country: str) -> list[dict]:
    return load_manifest(country)["seed_sources"]


def must_appear_funders(country: str) -> list[dict]:
    """Return the must-appear funder objects (name, url, sectors)."""
    return load_manifest(country)["must_appear_funders"]


def must_appear_names(country: str) -> list[str]:
    return [f["name"] for f in must_appear_funders(country)]


# --- Sector-aware filters (used by the watcher to build a tight MUST-FETCH list) ---

def must_fetch_for_sector(country: str, sector_id: str) -> list[dict]:
    """Must-appear funders whose `sectors` includes the worker's sector.
    These are the URLs the watcher is *required* to fetch before exiting."""
    return [
        f for f in must_appear_funders(country)
        if sector_id in (f.get("sectors") or [])
    ]


def seeds_for_sector(country: str, sector_id: str) -> list[dict]:
    """Seed sources relevant to a sector. Includes seeds whose `sectors`
    list includes this sector, plus universal seeds (empty `sectors` list)."""
    out = []
    for src in list_seed_sources(country):
        sectors = src.get("sectors")
        if sectors is None or len(sectors) == 0:
            out.append(src)
        elif sector_id in sectors:
            out.append(src)
    return out


def seeds_for_urban_centre(country: str, centre_regions: list[str]) -> list[dict]:
    """Seed sources whose `regions` overlaps with the urban centre's regions,
    plus universal aggregators/foundations that catch council-level grants."""
    centre = set(centre_regions)
    out = []
    for src in list_seed_sources(country):
        scope = src.get("scope", "national")
        src_regions = set(src.get("regions") or [])
        if scope == "regional" and src_regions & centre:
            out.append(src)
        elif src.get("category") in {"aggregator", "gaming"} and not src_regions:
            # Universal aggregators / national gaming trusts also surface local grants
            out.append(src)
    return out


# --- Coverage validation ---

def validate_coverage(opportunities: list[dict], country: str) -> dict:
    """Compare opportunity funders against the spot-check list."""
    expected = {f["name"].strip().lower() for f in must_appear_funders(country)}
    found_funders = {(o.get("funder") or "").strip().lower() for o in opportunities}
    found_funders.discard("")

    present = sorted(f for f in expected if f in found_funders)
    missing = sorted(f for f in expected if f not in found_funders)
    unknown = sorted(f for f in found_funders if f not in expected)
    return {
        "present": present,
        "missing": missing,
        "unknown": unknown,
        "expected_count": len(expected),
        "present_count": len(present),
        "missing_count": len(missing),
    }
