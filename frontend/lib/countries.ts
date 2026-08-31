// Country config — driven by the backend's /api/public/country-config endpoint.
//
// Each deployment serves a single country (GOBUGA_COUNTRY env var on the backend).
// The frontend fetches the deployment's config once at startup and caches it.
// Hardcoded NZ defaults serve as a synchronous fallback until the fetch resolves.

import { getPublicCountryConfig, type PublicCountryConfig } from "@/lib/api";

// --- NZ defaults (synchronous fallback) ---

const NZ_SECTOR_LABELS: string[] = [
  "Arts, culture & heritage",
  "Business, innovation & enterprise",
  "Capability building",
  "Civic & local democracy",
  "Climate",
  "Community development",
  "Disability services",
  "Education",
  "Environment",
  "Health & wellbeing",
  "Infrastructure",
  "Māori-led kaupapa",
  "Pasifika",
  "Research",
  "Sport & recreation",
  "Women & gender equity",
  "Youth & children",
];

const NZ_REGIONS: string[] = [
  "Northland",
  "Auckland",
  "Waikato",
  "Bay of Plenty",
  "Gisborne",
  "Hawke's Bay",
  "Taranaki",
  "Manawatū-Whanganui",
  "Wellington",
  "Tasman",
  "Nelson",
  "Marlborough",
  "West Coast",
  "Canterbury",
  "Otago",
  "Southland",
];

const NZ_SECTOR_LABEL_TO_TAG: Record<string, string> = {
  "Arts, culture & heritage": "arts",
  "Business, innovation & enterprise": "business",
  "Capability building": "capability",
  "Civic & local democracy": "civic",
  "Climate": "climate",
  "Community development": "community",
  "Disability services": "disability",
  "Education": "education",
  "Environment": "environment",
  "Health & wellbeing": "health",
  "Infrastructure": "infrastructure",
  "Māori-led kaupapa": "maori",
  "Pasifika": "pasifika",
  "Research": "research",
  "Sport & recreation": "sport",
  "Women & gender equity": "women",
  "Youth & children": "youth",
};

const NZ_TAGS: string[] = [
  "community", "sport", "civic", "arts", "environment", "education",
  "health", "youth", "maori", "pasifika", "research", "capability",
  "infrastructure", "business", "women", "disability", "climate",
];

const NZ_REGION_SLUGS: string[] = [
  "national", "auckland", "wellington", "canterbury", "otago", "waikato",
  "bay-of-plenty", "northland", "taranaki", "manawatu-whanganui",
  "hawkes-bay", "tasman", "nelson", "marlborough", "west-coast",
  "southland", "gisborne",
];

const NZ_DEFAULT: DeploymentConfig = {
  country: "nz",
  countryLabel: "New Zealand",
  currency: "NZD",
  contentLanguage: "en",
  uiLanguages: ["en"],
  sectorLabels: NZ_SECTOR_LABELS,
  regions: NZ_REGIONS,
  sectorLabelToTag: NZ_SECTOR_LABEL_TO_TAG,
  tags: NZ_TAGS,
  regionSlugs: NZ_REGION_SLUGS,
  tiers: {},
};

// --- Deployment config type ---

export interface DeploymentConfig {
  country: string;
  countryLabel: string;
  currency: string;
  contentLanguage: string;
  uiLanguages: string[];
  sectorLabels: string[];
  regions: string[];
  sectorLabelToTag: Record<string, string>;
  tags: string[];
  regionSlugs: string[];
  tiers: Record<string, unknown>;
}

// --- Cached config ---

let _cached: DeploymentConfig = NZ_DEFAULT;
let _fetchPromise: Promise<DeploymentConfig> | null = null;

function _mapPublicConfig(raw: PublicCountryConfig): DeploymentConfig {
  // Build sector labels from sector_slices
  const sectorLabels = raw.sector_slices.map((s) => s.label);

  // Build label-to-tag map: for each sector slice, map its label to its id
  const sectorLabelToTag: Record<string, string> = {};
  for (const slice of raw.sector_slices) {
    sectorLabelToTag[slice.label] = slice.id;
  }

  return {
    country: raw.country,
    countryLabel: raw.country_label,
    currency: raw.currency,
    contentLanguage: raw.content_language ?? "en",
    uiLanguages: raw.ui_languages?.length ? raw.ui_languages : ["en"],
    sectorLabels,
    regions: raw.regions.filter((r) => r !== "national").map(
      (r) => r.charAt(0).toUpperCase() + r.slice(1).replace(/-/g, " ")
    ),
    sectorLabelToTag,
    tags: raw.tags,
    regionSlugs: raw.regions,
    tiers: raw.tiers,
  };
}

/**
 * Fetch the deployment's country config from the backend.
 * Caches the result. Returns immediately with the cached value if already fetched.
 */
export async function loadDeploymentConfig(): Promise<DeploymentConfig> {
  if (_fetchPromise) return _fetchPromise;
  _fetchPromise = getPublicCountryConfig()
    .then((raw) => {
      _cached = _mapPublicConfig(raw);
      return _cached;
    })
    .catch((err) => {
      console.warn("[countries] Failed to load deployment config, using NZ defaults:", err);
      return _cached;
    });
  return _fetchPromise;
}

/** Synchronous access to the cached config (NZ defaults until loadDeploymentConfig resolves). */
export function getDeploymentConfig(): DeploymentConfig {
  return _cached;
}

// --- Backward-compatible exports ---
// These are used by setup/page.tsx and settings/page.tsx.
// In the country-as-config world each deployment IS one country, so
// ENABLED_COUNTRIES has exactly one entry: the deployment's country.

export interface CountryConfig {
  slug: string;
  name: string;
  enabled: boolean;
  sectors: string[];
  regions?: string[];
}

export function getEnabledCountries(): CountryConfig[] {
  const c = _cached;
  return [{
    slug: c.country,
    name: c.countryLabel,
    enabled: true,
    sectors: c.sectorLabels,
    regions: c.regions,
  }];
}

/** @deprecated Use getEnabledCountries() for dynamic data */
export const ENABLED_COUNTRIES: CountryConfig[] = [{
  slug: NZ_DEFAULT.country,
  name: NZ_DEFAULT.countryLabel,
  enabled: true,
  sectors: NZ_DEFAULT.sectorLabels,
  regions: NZ_DEFAULT.regions,
}];

export function findCountry(name: string | null | undefined): CountryConfig | undefined {
  if (!name) return undefined;
  const countries = getEnabledCountries();
  return countries.find((c) => c.name === name || c.slug === name);
}

// Sector label → tag slug mapping (deployment-aware)
export const SECTOR_LABEL_TO_TAG: Record<string, string> = NZ_SECTOR_LABEL_TO_TAG;

export function getSectorLabelToTag(): Record<string, string> {
  return _cached.sectorLabelToTag;
}

export function orgSectorsToTags(labels: string[] | null | undefined): string[] {
  if (!labels) return [];
  const labelMap = _cached.sectorLabelToTag;
  const out: string[] = [];
  const seen = new Set<string>();
  for (const label of labels) {
    const slug = labelMap[label];
    if (slug && !seen.has(slug)) {
      seen.add(slug);
      out.push(slug);
    }
  }
  return out;
}
