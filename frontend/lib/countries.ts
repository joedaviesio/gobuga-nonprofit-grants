// Country-scoped sign-up data. Each country bundles the sector chip set and
// sub-national regions used in the setup wizard and settings editor.
//
// `enabled: false` countries are kept here so we can switch them on later
// without having to re-derive the data — they just don't appear in any UI
// picker today. To enable a country, fill its `sectors` and (if applicable)
// `regions`, then flip `enabled` to true.

export interface CountryConfig {
  slug: string;
  name: string;
  enabled: boolean;
  sectors: string[];
  regions?: string[];
}

// NZ sector labels — aligned 1:1 with the controlled tag vocabulary
// (api/taxonomy.py:TAGS). Backend maps these labels to slugs via
// api.taxonomy.SECTOR_LABEL_TO_TAG.
const NZ_SECTORS: string[] = [
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

export const COUNTRIES: CountryConfig[] = [
  {
    slug: "nz",
    name: "New Zealand",
    enabled: true,
    sectors: NZ_SECTORS,
    regions: NZ_REGIONS,
  },
  // Disabled — sectors/regions retained so we can switch these on later.
  {
    slug: "au",
    name: "Australia",
    enabled: false,
    sectors: [],
    regions: [
      "New South Wales",
      "Victoria",
      "Queensland",
      "Western Australia",
      "South Australia",
      "Tasmania",
      "Australian Capital Territory",
      "Northern Territory",
    ],
  },
  {
    slug: "uk",
    name: "United Kingdom",
    enabled: false,
    sectors: [],
    regions: ["England", "Scotland", "Wales", "Northern Ireland"],
  },
  { slug: "us", name: "United States", enabled: false, sectors: [] },
  { slug: "ca", name: "Canada", enabled: false, sectors: [] },
  { slug: "eu", name: "European Union", enabled: false, sectors: [] },
  { slug: "intl", name: "Global / International", enabled: false, sectors: [] },
  { slug: "pacific", name: "Pacific Islands", enabled: false, sectors: [] },
  { slug: "sea", name: "Southeast Asia", enabled: false, sectors: [] },
  { slug: "africa", name: "Africa", enabled: false, sectors: [] },
  { slug: "latam", name: "Latin America", enabled: false, sectors: [] },
];

export const ENABLED_COUNTRIES: CountryConfig[] = COUNTRIES.filter((c) => c.enabled);

export function findCountry(name: string | null | undefined): CountryConfig | undefined {
  if (!name) return undefined;
  return COUNTRIES.find((c) => c.name === name);
}

// Mirrors api.taxonomy.SECTOR_LABEL_TO_TAG — used to translate the
// org's stored sector labels into the controlled tag slugs that drive
// the opportunity feed's filter chips.
export const SECTOR_LABEL_TO_TAG: Record<string, string> = {
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

export function orgSectorsToTags(labels: string[] | null | undefined): string[] {
  if (!labels) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const label of labels) {
    const slug = SECTOR_LABEL_TO_TAG[label];
    if (slug && !seen.has(slug)) {
      seen.add(slug);
      out.push(slug);
    }
  }
  return out;
}
