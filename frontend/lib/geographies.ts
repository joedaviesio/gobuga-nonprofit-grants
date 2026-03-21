export interface GeographyOption {
  name: string;
  regions?: string[];
}

export const GEOGRAPHIES: GeographyOption[] = [
  {
    name: "New Zealand",
    regions: [
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
    ],
  },
  {
    name: "Australia",
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
    name: "United Kingdom",
    regions: [
      "England",
      "Scotland",
      "Wales",
      "Northern Ireland",
    ],
  },
  { name: "United States" },
  { name: "Canada" },
  { name: "European Union" },
  { name: "Global / International" },
  { name: "Pacific Islands" },
  { name: "Southeast Asia" },
  { name: "Africa" },
  { name: "Latin America" },
];
