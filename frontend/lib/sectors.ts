// Sign-up sector labels — aligned 1:1 with the controlled tag vocabulary
// (api/taxonomy.py:TAGS). Picking these at sign-up captures org context that
// drives the search-box "match my org" relevance score and any future tier
// gating. Friendly labels here; backend maps them to slugs via
// api.taxonomy.SECTOR_LABEL_TO_TAG.

export const SECTORS = [
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
