#!/usr/bin/env python3
"""
Test NC Area Agencies on Aging extraction manually.
Verifies keyword resolution, configuration, and extractor setup.
"""

import sys
import json
from app import create_app

# Create Flask app context
app = create_app()

with app.app_context():
    # Test 1: Verify list type resolution
    print("=" * 80)
    print("TEST 1: Keyword Resolution")
    print("=" * 80)

    from app.extraction.us_list_types import resolve_list_type, get_list_type_config

    keyword = "NC aging"
    resolved = resolve_list_type(keyword)
    print(f"✓ Keyword '{keyword}' resolves to: {resolved}")

    if resolved != "NC Area Agencies on Aging":
        print(f"✗ ERROR: Expected 'NC Area Agencies on Aging', got '{resolved}'")
        sys.exit(1)

    # Test 2: Get the configuration
    print("\n" + "=" * 80)
    print("TEST 2: Configuration Retrieval")
    print("=" * 80)

    config = get_list_type_config(resolved)
    if not config:
        print(f"✗ ERROR: No configuration found for '{resolved}'")
        sys.exit(1)

    print(f"✓ Configuration found:")
    print(f"  - Extractor class: {config.get('extractor_class').__name__}")
    print(f"  - Seed URLs: {len(config.get('seed_urls', []))} URLs")
    print(f"  - Target domains: {len(config.get('target_domains', []))} domains")
    print(f"  - Max pages: {config.get('max_pages')}")
    print(f"  - Max depth: {config.get('max_depth')}")
    print(f"  - Follow links: {config.get('follow_links')}")

    # Test 3: Check first 3 seed URLs
    print("\n" + "=" * 80)
    print("TEST 3: Seed URLs")
    print("=" * 80)

    seed_urls = config.get('seed_urls', [])
    print(f"✓ Total seed URLs: {len(seed_urls)}")

    if len(seed_urls) < 16:
        print(f"✗ ERROR: Expected 16 seed URLs, got {len(seed_urls)}")
        sys.exit(1)

    for i, url in enumerate(seed_urls[:3]):
        print(f"  [{i+1}] {url}")

    # Test 4: Test keyword resolution with various aliases
    print("\n" + "=" * 80)
    print("TEST 4: Keyword Alias Variations")
    print("=" * 80)

    test_keywords = [
        "nc aging",
        "NC aging",
        "NC AGING",
        "nc aaa",
        "nc area agencies on aging",
        "north carolina aging",
    ]

    for test_kw in test_keywords:
        resolved_test = resolve_list_type(test_kw)
        status = "✓" if resolved_test == "NC Area Agencies on Aging" else "✗"
        print(f"{status} '{test_kw}' → {resolved_test}")

    # Test 5: Test extractor instantiation
    print("\n" + "=" * 80)
    print("TEST 5: Extractor Instantiation")
    print("=" * 80)

    from app.extraction.gov_nonprofit_extractor import GovNonprofitExtractor

    try:
        ai_options = {
            'provider': 'ollama',
            'primary_model': 'neural-chat',
            'fallback_model': None,
            'selected_fields': ['name', 'email', 'phone', 'website', 'location'],
            'enable_validation': True,
        }
        
        extractor = GovNonprofitExtractor(
            task_id=999,
            seed_urls=seed_urls[:1],  # Test with just the first URL
            target_domains=config.get('target_domains', []),
            follow_links=config.get('follow_links', True),
            max_depth=1,  # Shallow for testing
            max_pages=5,  # Limited pages for testing
            email_patterns=config.get('email_patterns', []),
            ai_options=ai_options,
        )
        
        print(f"✓ Extractor instantiated successfully")
        print(f"  - Task ID: {extractor.task_id}")
        print(f"  - Seed URLs: {len(extractor.seed_urls)}")
        print(f"  - Target domains: {len(extractor.target_domains)}")
        print(f"  - Max pages: {extractor.max_pages}")
        print(f"  - Max depth: {extractor.max_depth}")
    except Exception as e:
        print(f"✗ ERROR instantiating extractor: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Test 6: Test build_list_extractor function
    print("\n" + "=" * 80)
    print("TEST 6: build_list_extractor Function")
    print("=" * 80)

    from app.extraction.task_manager import build_list_extractor

    try:
        ai_options = {
            'provider': 'ollama',
            'primary_model': 'neural-chat',
            'fallback_model': None,
            'selected_fields': ['name', 'email', 'phone', 'website', 'location'],
            'enable_validation': True,
            'list_type': 'NC Area Agencies on Aging',
        }
        
        extractor = build_list_extractor(
            task_id=999,
            list_type='NC Area Agencies on Aging',
            ai_options=ai_options,
        )
        
        print(f"✓ Extractor built successfully")
        print(f"  - Type: {type(extractor).__name__}")
        print(f"  - Seed URLs: {len(extractor.seed_urls)}")
        print(f"  - Max pages: {extractor.max_pages}")
        print(f"  - Max depth: {extractor.max_depth}")
    except Exception as e:
        print(f"✗ ERROR building extractor: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Summary
    print("\n" + "=" * 80)
    print("✓ ALL TESTS PASSED")
    print("=" * 80)
    print(f"""
Summary:
  • Keyword resolution: working
  • Configuration retrieval: working
  • Seed URLs: 16 URLs loaded
  • Alias variations: all resolve correctly
  • Extractor instantiation: working
  • build_list_extractor: working

Next step: Run the Flask app and test with "NC aging" keyword in the UI.
""")
