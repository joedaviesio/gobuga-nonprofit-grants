"""Sector and geography code maps for case ID generation."""

SECTOR_CODES = {
    # --- Tag-aligned labels (current sign-up wizard) ---
    "arts, culture & heritage": "ART",
    "business, innovation & enterprise": "BIZ",
    "capability building": "CAP",
    "civic & local democracy": "CIV",
    "climate": "CLM",
    "community development": "COM",
    "disability services": "DIS",
    "education": "EDU",
    "environment": "ENV",
    "health & wellbeing": "HLT",
    "infrastructure": "INF",
    "māori-led kaupapa": "MRI",
    "maori-led kaupapa": "MRI",
    "pasifika": "PAS",
    "research": "RES",
    "sport & recreation": "SPT",
    "women & gender equity": "WMN",
    "youth & children": "YTH",

    # --- Legacy labels (pre-pivot sign-up — kept for existing orgs/cases) ---
    "access to justice": "LAW",
    "ai for public good": "AI",
    "digital inclusion": "DIG",
    "health and wellbeing": "HLT",
    "environment & climate": "ENV",
    "environment and climate": "ENV",
    "arts & culture": "ART",
    "arts and culture": "ART",
    "youth and children": "YTH",
    "human rights": "HRE",
    "housing & homelessness": "HSG",
    "housing and homelessness": "HSG",
    "food security": "FDS",
    "economic empowerment": "ECN",
    "technology & innovation": "TEC",
    "technology and innovation": "TEC",
}

# Keywords that map to each sector code for fuzzy matching against grant brief text
_SECTOR_KEYWORDS = {
    "ART": ["arts", "culture", "creative", "music", "theatre", "heritage", "kapa haka"],
    "BIZ": ["business", "innovation", "enterprise", "entrepreneur", "startup", "commercial"],
    "CAP": ["capability", "capacity building", "training", "professional development", "upskill"],
    "CIV": ["civic", "local democracy", "council", "community board", "local government"],
    "CLM": ["climate", "carbon", "decarbonisation", "emissions", "low-emissions"],
    "COM": ["community development", "community", "neighbourhood", "marae", "hapū"],
    "DIS": ["disability", "disabilities", "accessible", "neurodiverse", "deaf", "blind"],
    "EDU": ["education", "school", "learning", "literacy", "teaching", "scholarship", "tertiary"],
    "ENV": ["environment", "sustainability", "conservation", "biodiversity", "ecology"],
    "HLT": ["health", "wellbeing", "wellness", "medical", "mental health", "hauora"],
    "INF": ["infrastructure", "facility", "building", "venue", "playground", "marae upgrade"],
    "MRI": ["māori", "maori", "iwi", "hapū", "rūnanga", "kaupapa māori", "te ao māori"],
    "PAS": ["pasifika", "pacific", "samoan", "tongan", "fijian", "niuean", "cook islands"],
    "RES": ["research", "marsden", "rangahau", "academic", "investigator", "grant for study"],
    "SPT": ["sport", "recreation", "active recreation", "physical activity", "tū manawa"],
    "WMN": ["women", "wahine", "gender equity", "girls", "feminist"],
    "YTH": ["youth", "children", "young people", "tamariki", "rangatahi"],

    # Legacy codes
    "LAW": ["justice", "legal", "law", "court"],
    "AI": ["ai", "artificial intelligence", "machine learning"],
    "DIG": ["digital inclusion", "digital access", "internet access", "connectivity"],
    "HRE": ["human rights", "equality", "discrimination"],
    "HSG": ["housing", "homelessness", "shelter", "accommodation"],
    "FDS": ["food security", "food bank", "nutrition", "hunger"],
    "ECN": ["economic empowerment", "employment", "livelihood", "microfinance"],
    "TEC": ["technology", "tech", "digital transformation"],
}

GEO_CODES = {
    "new zealand": "NZ",
    "aotearoa": "NZ",
    "moldova": "MD",
    "republica moldova": "MD",
    "australia": "AU",
    "united kingdom": "UK",
    "united states": "US",
    "canada": "CA",
    "european union": "EU",
    "global / international": "GLB",
    "global": "GLB",
    "international": "GLB",
    "pacific islands": "PAC",
    "southeast asia": "SEA",
    "africa": "AFR",
    "latin america": "LAM",
}

# Keywords/regions that map to each geo code
_GEO_KEYWORDS = {
    "NZ": ["new zealand", "aotearoa", "auckland", "wellington", "canterbury",
            "waikato", "otago", "northland", "bay of plenty", "hawke's bay",
            "taranaki", "manawatu", "nelson", "marlborough", "southland",
            "gisborne", "tasman", "west coast"],
    "MD": ["moldova", "republica moldova", "chisinau", "chișinău", "balti",
            "bălți", "cahul", "comrat", "gagauzia", "ungheni", "orhei", "soroca"],
    "AU": ["australia", "australian", "new south wales", "victoria", "queensland",
           "western australia", "south australia", "tasmania", "sydney", "melbourne"],
    "UK": ["united kingdom", "england", "scotland", "wales", "northern ireland",
           "london", "british", "uk"],
    "US": ["united states", "usa", "american", "federal"],
    "CA": ["canada", "canadian"],
    "EU": ["european union", "europe", "eu"],
    "GLB": ["global", "international", "worldwide"],
    "PAC": ["pacific", "fiji", "samoa", "tonga", "vanuatu"],
    "SEA": ["southeast asia", "singapore", "philippines", "indonesia", "thailand", "vietnam"],
    "AFR": ["africa", "african", "kenya", "nigeria", "south africa"],
    "LAM": ["latin america", "brazil", "mexico", "colombia", "chile", "argentina"],
}

DEFAULT_SECTOR = "GEN"
DEFAULT_GEO = "INT"


def infer_sector_code(grant_brief: dict) -> str:
    """Infer a sector code from grant brief title and description."""
    text = " ".join([
        grant_brief.get("title", ""),
        grant_brief.get("description", ""),
    ]).lower()

    if not text.strip():
        return DEFAULT_SECTOR

    # Try exact sector name match first
    for sector_name, code in SECTOR_CODES.items():
        if sector_name in text:
            return code

    # Fall back to keyword matching — pick the sector with the most keyword hits
    best_code = DEFAULT_SECTOR
    best_score = 0
    for code, keywords in _SECTOR_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_code = code

    return best_code


def infer_geo_code(grant_brief: dict) -> str:
    """Infer a geography code from grant brief title and description."""
    text = " ".join([
        grant_brief.get("title", ""),
        grant_brief.get("description", ""),
    ]).lower()

    if not text.strip():
        return DEFAULT_GEO

    # Try exact geography name match first
    for geo_name, code in GEO_CODES.items():
        if geo_name in text:
            return code

    # Fall back to keyword matching
    best_code = DEFAULT_GEO
    best_score = 0
    for code, keywords in _GEO_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_code = code

    return best_code
