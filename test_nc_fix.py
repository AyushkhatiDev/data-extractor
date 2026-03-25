#!/usr/bin/env python3
"""Quick diagnostic test for NC aging resolution"""

from app.extraction.us_list_types import resolve_list_type, get_list_type_config, get_list_type_names

# Test keyword resolution
test_keywords = ["NC aging", "nc aging", "NC Area Agencies on Aging", "florida aging"]

print("=" * 70)
print("Testing List Type Resolution After Fix")
print("=" * 70)

for keyword in test_keywords:
    resolved = resolve_list_type(keyword)
    print(f"\nKeyword: '{keyword}'")
    print(f"  → Resolved to: {resolved}")
    
    if resolved:
        config = get_list_type_config(resolved)
        if config:
            seed_urls = config.get('seed_urls', [])
            print(f"  → Seed URLs: {len(seed_urls)} urls")
            if seed_urls:
                print(f"    First 3:")
                for url in seed_urls[:3]:
                    print(f"      - {url}")
            print(f"  → Max pages: {config.get('max_pages', 'default')}")
            print(f"  → Max depth: {config.get('max_depth', 'default')}")
            print(f"  → Follow links: {config.get('follow_links', 'default')}")
            extractor = config.get('extractor_class')
            print(f"  → Extractor: {extractor.__name__ if extractor else 'None'}")

print("\n" + "=" * 70)
print(f"All available list types ({len(get_list_type_names())}):")
for lt in sorted(get_list_type_names()):
    print(f"  - {lt}")
print("=" * 70)
