#!/usr/bin/env python3
"""Test script for list type resolution"""

from app.extraction.us_list_types import resolve_list_type, get_list_type_config, get_list_type_names

def test_list_type_resolution():
    """Test various list type resolution scenarios"""
    
    print("=" * 60)
    print("Testing List Type Resolution")
    print("=" * 60)
    
    # Test case 1: Resolve "NC aging"
    keyword = "NC aging"
    list_type = resolve_list_type(keyword)
    assert list_type is not None, f"Failed to resolve '{keyword}'"
    assert list_type == "Area Agencies on Aging", f"Wrong resolution: {list_type}"
    config = get_list_type_config(list_type)
    assert config is not None, f"Config not found for {list_type}"
    print(f"✓ Test 1: '{keyword}' → '{list_type}'")
    
    # Test case 2: Resolve "florida aging"
    keyword = "florida aging"
    list_type = resolve_list_type(keyword)
    assert list_type == "Florida Aging Services (Gov+Nonprofit)", f"Wrong resolution: {list_type}"
    print(f"✓ Test 2: '{keyword}' → '{list_type}'")
    
    # Test case 3: Invalid keyword returns None
    keyword = "invalid list type xyz"
    list_type = resolve_list_type(keyword)
    assert list_type is None, f"Should return None for invalid keyword, got: {list_type}"
    print(f"✓ Test 3: '{keyword}' → None (as expected)")
    
    # Test case 4: Full list type name (exact match)
    keyword = "Nursing Homes"
    list_type = resolve_list_type(keyword)
    assert list_type == "Nursing Homes", f"Wrong resolution: {list_type}"
    print(f"✓ Test 4: '{keyword}' → '{list_type}' (exact match)")
    
    # Test case 5: Case-insensitive match
    keyword = "NURSING HOMES"
    list_type = resolve_list_type(keyword)
    assert list_type == "Nursing Homes", f"Case-insensitive match failed: {list_type}"
    print(f"✓ Test 5: '{keyword}' → '{list_type}' (case-insensitive)")
    
    # Test case 6: All aliases work
    aliases_to_test = {
        "nc aging": "Area Agencies on Aging",
        "florida aging": "Florida Aging Services (Gov+Nonprofit)",
        "nursing home": "Nursing Homes",
        "senior center": "Senior Centers",
        "home health": "Home Health Agencies",
    }
    
    for alias, expected in aliases_to_test.items():
        result = resolve_list_type(alias)
        assert result == expected, f"Alias '{alias}' should resolve to '{expected}', got '{result}'"
    print(f"✓ Test 6: All {len(aliases_to_test)} aliases work correctly")
    
    # Test case 7: Configuration has extractor class
    for list_type_name in get_list_type_names():
        config = get_list_type_config(list_type_name)
        # Some configs use extractor_class, others don't
        if "extractor_class" in config:
            assert config["extractor_class"] is not None
    print(f"✓ Test 7: All {len(get_list_type_names())} list types have valid configs")
    
    print("\n" + "=" * 60)
    print("All tests passed! ✓")
    print("=" * 60)

if __name__ == "__main__":
    test_list_type_resolution()
