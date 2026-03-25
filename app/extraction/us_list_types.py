"""
US List Types — pre-defined list-based targeting configurations.

Each entry maps a list name to its extraction parameters including
seed URLs, extraction method, and source type.
"""

import json
import re
from pathlib import Path
from typing import Optional, List
from app.extraction.gov_nonprofit_extractor import GovNonprofitExtractor


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_seed_urls(seed_file: str) -> List[str]:
    """Load seed URLs from a JSON file.

    Supports two JSON formats:
    1) ["https://...", "https://..."]
    2) [{"name": "...", "url": "https://..."}, ...]
    """
    if not seed_file:
        return []

    file_path = PROJECT_ROOT / seed_file
    if not file_path.exists():
        print(f"[US_LIST_TYPES] Seed file not found: {file_path}")
        return []

    try:
        with file_path.open('r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as exc:
        print(f"[US_LIST_TYPES] Failed to read seed file {file_path}: {exc}")
        return []

    urls: List[str] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str) and item.strip():
                urls.append(item.strip())
                continue
            if isinstance(item, dict):
                raw_url = item.get('url')
                if isinstance(raw_url, str) and raw_url.strip():
                    urls.append(raw_url.strip())

    # Dedupe while preserving order
    seen = set()
    deduped = []
    for url in urls:
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(url)

    return deduped


def _materialize_list_type_config(config: dict) -> dict:
    """Return a copy of config with seed_file expanded to seed_urls when needed."""
    if not config:
        return {}

    out = dict(config)
    if (not out.get('seed_urls')) and out.get('seed_file'):
        out['seed_urls'] = _load_seed_urls(str(out.get('seed_file')))
    return out

US_LIST_TYPES = {
    "NC Area Agencies on Aging": {
        "extractor_class": GovNonprofitExtractor,
        "seed_urls": [
            "https://www.landofsky.org/",
            "https://www.tjcog.org/",
            "https://www.regiona.org/",
            "https://www.agingandfamilyservices.org/",
            "https://centralina.org/",
            "https://www.lrcog.org/",
            "https://www.midcarolina.org/",
            "https://www.ptcog.org/",
            "https://www.nencog.org/",
            "https://www.capefearcog.org/",
            "https://www.regiond.org/",
            "https://www.wpcog.org/",
            "https://www.eccog.org/",
            "https://www.mideastcom.org/",
            "https://www.kerrtarcog.org/",
            "https://elderaffairs.org/",
        ],
        "target_domains": [
            "landofsky.org",
            "tjcog.org",
            "regiona.org",
            "agingandfamilyservices.org",
            "centralina.org",
            "lrcog.org",
            "midcarolina.org",
            "ptcog.org",
            "nencog.org",
            "capefearcog.org",
            "regiond.org",
            "wpcog.org",
            "eccog.org",
            "mideastcom.org",
            "kerrtarcog.org",
            "elderaffairs.org",
        ],
        "follow_links": True,
        "max_depth": 2,
        "max_pages": 200,
        "email_patterns": [
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.(org|com|gov|net)"
        ],
        "source_type": "nonprofit_directory",
        "description": "North Carolina Area Agencies on Aging — all 16 regional AAAs",
        "output_fields": ["name", "email", "phone", "location", "organization_type", "website"],
    },
    "Area Agencies on Aging": {
        "extractor_class": GovNonprofitExtractor,
        "seed_urls": [
            "https://elderaffairs.org/",
            "https://www.ncaoaging.org/",
        ],
        "target_domains": [
            "elderaffairs.org",
            "ncaoaging.org",
        ],
        "follow_links": True,
        "max_depth": 2,
        "max_pages": 50,
        "email_patterns": [
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.org"
        ],
        "source_type": "nonprofit_directory",
        "description": "Area Agencies on Aging — national and state-level organizations",
    },
    "Nursing Homes": {
        "extractor_class": GovNonprofitExtractor,
        "seed_file": "data/nursing_homes_seed_urls.json",
        "target_domains": [
            "medicare.gov",
            "data.medicare.gov",
            "ahcancal.org",
            "leadingage.org",
            "floridahealth.gov",
            "hhs.texas.gov",
            "cdph.ca.gov",
            "profiles.health.ny.gov",
            "health.mo.gov",
            "dph.illinois.gov",
            "health.pa.gov",
            "dhhs.ne.gov",
            "dhss.delaware.gov",
            "health.ri.gov",
            "hca.wa.gov",
        ],
        "follow_links": True,
        "max_depth": 3,
        "max_pages": 300,
        "email_patterns": [
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.(gov|org|com|us)",
        ],
        "source_type": "government_nonprofit_directory",
        "description": "Nursing home and skilled nursing facility contacts from Medicare, state directories, and industry associations",
        "output_fields": ["name", "email", "phone", "website", "location", "organization_type"],
    },
    "Senior Centers": {
        "seed_urls": [
            "https://www.ncoa.org/",
        ],
        "extraction_method": "crawl_members",
        "follow_links": True,
        "source_type": "nonprofit_directory",
        "description": "Senior centers and community programs for older adults",
    },
    "Home Health Agencies": {
        "seed_urls": [
            "https://www.medicare.gov/care-compare/",
        ],
        "extraction_method": "scrape_directory",
        "search_query": "home health agencies directory contact email florida usa",
        "max_pages": 30,
        "source_type": "government_directory",
        "description": "Medicare-certified home health agencies",
    },
    "New Homeowners": {
        "seed_urls": [],  # Placeholder — public property records TBD
        "extraction_method": "public_records",
        "source_type": "property_records",
        "description": "New homeowners from public property records (coming soon)",
    },
    "Chambers of Commerce": {
        "seed_urls": [
            "https://www.uschamber.com/",
        ],
        "extraction_method": "crawl_members",
        "follow_links": True,
        "source_type": "business_directory",
        "description": "US Chambers of Commerce member directories",
    },
    "Florida Aging Services (Gov+Nonprofit)": {
        "extractor_class": GovNonprofitExtractor,
        "seed_urls": [
            "https://elderaffairs.org/",
            "https://elderaffairs.org/contact-us/",
            "https://elderaffairs.org/programs-and-services/",
            "https://ombudsman.elderaffairs.org/contact-us/",
        ],
        "target_domains": [
            "elderaffairs.org",
            "aging-solutions.org",
            "lsfnet.org",
            "coavolusia.org",
            "guardianshipprogram.org",
            "seniorsfirstinc.org",
            "legalaidpbc.org",
            "bigbendopg.org",
        ],
        "follow_links": True,
        "max_depth": 2,
        "max_pages": 30,
        "email_patterns": [
            r"[a-zA-Z0-9._%+-]+@(?:elderaffairs|aging-solutions|lsfnet|coavolusia|guardianshipprogram|seniorsfirstinc|legalaidpbc|bigbendopg)\\.org"
        ],
        "source_type": "government_nonprofit_directory",
        "description": "Florida aging-services offices, ombudsman contacts, and affiliate nonprofits",
        "output_fields": ["name", "email", "phone", "location", "division", "organization_type"],
    },
    "National Aging Organizations (Gov+Nonprofit)": {
        "extractor_class": GovNonprofitExtractor,
        "seed_urls": [
            "https://www.ncaoaging.org/",
            "https://www.ncaoaging.org/about/",
            "https://www.ncaoaging.org/contact/",
        ],
        "target_domains": [
            "ncaoaging.org",
        ],
        "follow_links": True,
        "max_depth": 2,
        "max_pages": 20,
        "email_patterns": [
            r"[a-zA-Z0-9._%+-]+@ncaoaging\\.org"
        ],
        "source_type": "nonprofit_directory",
        "description": "National Council on Aging and related organization pages",
        "output_fields": ["name", "email", "phone", "location", "division", "organization_type"],
    },
}


# Keyword aliases for flexible user input
LIST_TYPE_ALIASES = {
    # NC Area Agencies on Aging aliases
    "nc aging": "NC Area Agencies on Aging",
    "nc area agencies": "NC Area Agencies on Aging",
    "nc area agencies on aging": "NC Area Agencies on Aging",
    "north carolina area agencies on aging": "NC Area Agencies on Aging",
    "north carolina aging": "NC Area Agencies on Aging",
    "nc aaa": "NC Area Agencies on Aging",
    # Generic Area Agencies on Aging aliases
    "area agencies on aging": "Area Agencies on Aging",
    "aging": "Area Agencies on Aging",
    # Nursing Homes aliases
    "nursing home": "Nursing Homes",
    "nursing homes": "Nursing Homes",
    "nursing facility": "Nursing Homes",
    "nursing facilities": "Nursing Homes",
    "skilled nursing": "Nursing Homes",
    "skilled nursing facility": "Nursing Homes",
    "skilled nursing facilities": "Nursing Homes",
    "snf": "Nursing Homes",
    "long term care": "Nursing Homes",
    "long-term care": "Nursing Homes",
    "medicare care compare": "Nursing Homes",
    "care facilities": "Nursing Homes",
    # Senior Centers aliases
    "senior center": "Senior Centers",
    "senior centers": "Senior Centers",
    "ncoa": "Senior Centers",
    # Home Health Agencies aliases
    "home health": "Home Health Agencies",
    "home health agencies": "Home Health Agencies",
    "home health care": "Home Health Agencies",
    # Florida Aging Services aliases
    "fl aging": "Florida Aging Services (Gov+Nonprofit)",
    "fl aging services": "Florida Aging Services (Gov+Nonprofit)",
    "florida aging": "Florida Aging Services (Gov+Nonprofit)",
    "florida aging services": "Florida Aging Services (Gov+Nonprofit)",
    "fl elder affairs": "Florida Aging Services (Gov+Nonprofit)",
    "florida elder affairs": "Florida Aging Services (Gov+Nonprofit)",
    # National Aging Organizations aliases
    "national aging": "National Aging Organizations (Gov+Nonprofit)",
    "national aging organizations": "National Aging Organizations (Gov+Nonprofit)",
    "ncoa organizations": "National Aging Organizations (Gov+Nonprofit)",
    # Chambers of Commerce aliases
    "chamber of commerce": "Chambers of Commerce",
    "chambers of commerce": "Chambers of Commerce",
    "us chamber of commerce": "Chambers of Commerce",
}


def resolve_list_type(keyword: str) -> Optional[str]:
    """
    Resolve a user-provided keyword to an official list type name.
    
    First tries direct match in US_LIST_TYPES.
    Then checks aliases in LIST_TYPE_ALIASES.
    Returns None if no match found.
    """
    if not keyword or not isinstance(keyword, str):
        return None
    
    keyword_lower = keyword.lower().strip()
    keyword_normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s-]", " ", keyword_lower)).strip()
    
    # Direct match (case-insensitive)
    for name in US_LIST_TYPES:
        name_normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s-]", " ", name.lower())).strip()
        if name_normalized == keyword_normalized:
            return name
    
    # Alias match
    if keyword_normalized in LIST_TYPE_ALIASES:
        return LIST_TYPE_ALIASES[keyword_normalized]

    # Phrase match for inputs like "best nursing homes in texas"
    for alias, target in sorted(LIST_TYPE_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        pattern = r"\b" + re.escape(alias) + r"\b"
        if re.search(pattern, keyword_normalized):
            return target
    
    # No match found
    return None


def get_list_type_config(list_type_name: str) -> dict:
    """Retrieve configuration for a named list type, or None."""
    return _materialize_list_type_config(US_LIST_TYPES.get(list_type_name))


def get_list_type_names() -> list:
    """Return sorted list of available list type names."""
    return sorted(US_LIST_TYPES.keys())
