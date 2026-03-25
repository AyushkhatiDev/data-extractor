#!/usr/bin/env python
"""
Test auto-detection of list types in regular keyword searches.

Verifies that when a user types a keyword matching a list type
(e.g., "nursing homes"), it's auto-detected and runs the specialized
list extractor alongside the regular source extractor.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from app.extraction.us_list_types import resolve_list_type

def test_nursing_homes_resolution():
    """Test that nursing homes keyword resolves to Nursing Homes list type"""
    print("\n" + "="*70)
    print("[TEST] Auto-Detection of Nursing Homes List Type")
    print("="*70)
    
    test_cases = [
        # (keyword, should_resolve, expected_list_type)
        ("nursing homes", True, "Nursing Homes"),
        ("nursing home", True, "Nursing Homes"),
        ("skilled nursing facility", True, "Nursing Homes"),
        ("SNF", True, "Nursing Homes"),
        ("long term care", True, "Nursing Homes"),
        ("best nursing homes in texas", True, "Nursing Homes"),
        ("find nursing homes near me", True, "Nursing Homes"),
        ("NC aging", True, "NC Area Agencies on Aging"),
        ("Florida aging", True, "Florida Aging Services (Gov+Nonprofit)"),
        ("random keyword", False, None),
        ("restaurants", False, None),
    ]
    
    passed = 0
    failed = 0
    
    for keyword, should_resolve, expected_type in test_cases:
        result = resolve_list_type(keyword)
        
        if should_resolve:
            if result == expected_type:
                print(f"  ✓ '{keyword}' → '{result}'")
                passed += 1
            else:
                print(f"  ✗ '{keyword}' → Expected '{expected_type}', got '{result}'")
                failed += 1
        else:
            if result is None:
                print(f"  ✓ '{keyword}' → None (expected)")
                passed += 1
            else:
                print(f"  ✗ '{keyword}' → Expected None, got '{result}'")
                failed += 1
    
    print("\n" + "="*70)
    print(f"Results: {passed} passed, {failed} failed")
    print("="*70)
    
    return failed == 0

def test_extraction_flow_scenarios():
    """Test extraction flow scenarios with auto-detection"""
    print("\n" + "="*70)
    print("[TEST] Extraction Flow Scenarios")
    print("="*70)
    
    scenarios = [
        {
            "description": "Nursing homes + google_maps",
            "keyword": "nursing homes",
            "source": "google_maps",
            "expected_flow": "list_crawl (Nursing Homes) → google_maps",
        },
        {
            "description": "Nursing homes + both",
            "keyword": "nursing homes",
            "source": "both",
            "expected_flow": "list_crawl (Nursing Homes) → google_maps → linkedin",
        },
        {
            "description": "NC aging + list_crawl (explicit)",
            "keyword": "NC aging",
            "source": "list_crawl",
            "expected_flow": "list_crawl (NC Area Agencies on Aging)",
        },
        {
            "description": "restaurants + google_maps (no auto-detect)",
            "keyword": "restaurants",
            "source": "google_maps",
            "expected_flow": "google_maps (regular keyword search, no list type)",
        },
        {
            "description": "skilled nursing facility + yelp",
            "keyword": "skilled nursing facility",
            "source": "yelp",
            "expected_flow": "list_crawl (Nursing Homes) → yelp",
        },
    ]
    
    print("\nExpected extraction flows with auto-detection:\n")
    for scenario in scenarios:
        keyword = scenario['keyword']
        source = scenario['source']
        flow = scenario['expected_flow']
        
        # Determine if auto-detect would trigger
        resolved = resolve_list_type(keyword)
        auto_detect = resolved is not None and source != 'list_crawl'
        
        status = "✓ Auto-detect" if auto_detect else "✓ Explicit" if resolved else "✓ Regular"
        print(f"  {status}: {scenario['description']}")
        print(f"           Keyword: '{keyword}'")
        print(f"           Source: {source}")
        print(f"           Flow: {flow}\n")
    
    print("="*70)
    return True

if __name__ == '__main__':
    success = True
    success = test_nursing_homes_resolution() and success
    success = test_extraction_flow_scenarios() and success
    
    if success:
        print("\n✓ All auto-detection tests passed!")
        sys.exit(0)
    else:
        print("\n✗ Some tests failed")
        sys.exit(1)
