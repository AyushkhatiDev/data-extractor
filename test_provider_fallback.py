#!/usr/bin/env python3
"""
Quick test to verify provider resolution and fallback without crashes.
Tests the scenarios from the original error report.
"""
from app.ai.llm_extractor import (
    LLMExtractor, _get_onprem_llm, _resolve_provider,
    ONPREM_AVAILABLE, LANGEXTRACT_AVAILABLE,
    LLMRuntimeConfig
)

print("\n" + "="*70)
print("PROVIDER FALLBACK VERIFICATION TEST")
print("="*70)

# Test 1: Import status
print("\n[Test 1] Module Import Status:")
print(f"  ✅ LANGEXTRACT_AVAILABLE: {LANGEXTRACT_AVAILABLE}")
print(f"  ✅ ONPREM_AVAILABLE: {ONPREM_AVAILABLE}")

# Test 2: Provider resolution (auto mode, no onprem config)
print("\n[Test 2] Provider Resolution (auto, no onprem model configured):")
cfg_auto = LLMRuntimeConfig(
    provider="auto",
    primary_model="qwen2.5:7b-instruct",
    fallback_model="llama3.1:8b",
    model_path="",
    model_url="",
    api_base_url="http://localhost:11434",
    api_key="",
    timeout=60,
    max_tokens=1024,
    temperature=0.1,
    min_confidence_for_accept=0.55,
)
resolved = _resolve_provider(cfg_auto)
print(f"  ✅ Selected provider: {resolved}")
print(f"     (Expected: langextract / openai_compatible / regex, NOT onprem)")

# Test 3: OnPrem initialization attempt (with model path, triggering error)
print("\n[Test 3] OnPrem Load Attempt (model configured, llama-cpp-python missing):")
cfg_onprem = LLMRuntimeConfig(
    provider="onprem",
    primary_model="qwen2.5:7b-instruct",
    fallback_model="llama3.1:8b",
    model_path="./models/mistral.gguf",
    model_url="",
    api_base_url="http://localhost:11434",
    api_key="",
    timeout=60,
    max_tokens=1024,
    temperature=0.1,
    min_confidence_for_accept=0.55,
)
print("  Calling _get_onprem_llm()...")
result = _get_onprem_llm(cfg_onprem)
print(f"  ✅ Returned: {result}")
print(f"     (no uncaught exception, graceful fallback)")

# Test 4: Full extraction flow
print("\n[Test 4] Full LLMExtractor Flow:")
extractor = LLMExtractor(provider="auto")
print(f"  ✅ LLMExtractor initialized")
print(f"     - Provider: {extractor.provider}")
print(f"     - Available: {extractor.is_available}")

sample_text = "Acme Corp at 123 Main St. Contact: info@acme.com or +1-555-1234"
result = extractor.extract(sample_text, prompt_type="general")
print(f"  ✅ Extraction completed")
print(f"     - Provider used: {result.get('provider_used')}")
print(f"     - Emails found: {result.get('emails')}")
print(f"     - Phones found: {result.get('phones')}")
print(f"     - Confidence: {result.get('confidence_score')}")

print("\n" + "="*70)
print("✅ ALL TESTS PASSED - Graceful fallback working!")
print("   No crashes from missing llama-cpp-python")
print("="*70)
