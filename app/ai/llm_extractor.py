"""
LLMExtractor - Structured business extraction with model routing.

Primary path: LangExtract with configurable model IDs.
Fallback path: OpenAI-compatible chat API and/or onprem local model.
Last fallback: Regex extraction.
"""

import json
import re
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import requests

try:
    from onprem import LLM as OnPremLLM
    ONPREM_AVAILABLE = True
except ImportError as e:
    ONPREM_AVAILABLE = False
    # Optional dependency for local .gguf model execution
    if "llama-cpp-python" in str(e).lower():
        pass  # llama-cpp-python not installed; onprem will be skipped gracefully at runtime
    else:
        pass  # onprem module itself not installed

try:
    import langextract as lx
    LANGEXTRACT_AVAILABLE = True
except ImportError:
    LANGEXTRACT_AVAILABLE = False


# One-time local model load cache
_onprem_llm_instance: Optional[object] = None
_onprem_load_attempted = False
_langextract_examples_cache: Optional[List[Any]] = None
_openai_reachability_cache: Dict[str, bool] = {}


MODEL_NAME_ALIASES = {
    "qwen-2.5-vl-7b-instruct": "qwen2.5:7b-instruct",
    "qwen-2.5-7b-instruct": "qwen2.5:7b-instruct",
    "qwen2.5-vl-7b-instruct": "qwen2.5:7b-instruct",
    "qwen2.5:7b-instruct": "qwen2.5:7b-instruct",
    "llama-3.1-8b-instruct": "llama3.1:8b",
    "llama3.1-8b-instruct": "llama3.1:8b",
    "llama3.1:8b-instruct": "llama3.1:8b",
    "llama3.1:8b": "llama3.1:8b",
}


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str
    primary_model: str
    fallback_model: str
    model_path: str
    model_url: str
    api_base_url: str
    api_key: str
    timeout: int
    max_tokens: int
    temperature: float
    min_confidence_for_accept: float


def _read_runtime_config(overrides: Optional[Dict[str, Optional[str]]] = None) -> LLMRuntimeConfig:
    """Read runtime config from Flask config with optional per-request overrides."""
    overrides = overrides or {}

    try:
        from flask import current_app, has_app_context
        cfg = current_app.config if has_app_context() else {}
    except Exception:
        cfg = {}

    provider = (overrides.get("provider") or cfg.get("AI_LLM_PROVIDER") or "auto").strip().lower()
    primary_model = (
        overrides.get("primary_model")
        or cfg.get("AI_PRIMARY_MODEL")
        or "Qwen-2.5-VL-7B-Instruct"
    ).strip()
    fallback_model = (
        overrides.get("fallback_model")
        or cfg.get("AI_FALLBACK_MODEL")
        or "Llama-3.1-8B-Instruct"
    ).strip()

    primary_model = _normalize_model_name(primary_model)
    fallback_model = _normalize_model_name(fallback_model)

    model_path = str(cfg.get("LLM_MODEL_PATH", "") or "").strip()
    model_url = str(cfg.get("LLM_MODEL_URL", "") or "").strip()
    api_base_url = str(
        cfg.get("AI_LLM_API_BASE_URL")
        or cfg.get("LANGEXTRACT_MODEL_URL")
        or "http://localhost:11434"
    ).strip()
    api_keys_disabled = bool(cfg.get("AI_DISABLE_API_KEYS", True))
    api_key = "" if api_keys_disabled else str(cfg.get("AI_LLM_API_KEY") or "").strip()

    timeout = _as_int(cfg.get("AI_LLM_TIMEOUT"), 60)
    max_tokens = _as_int(cfg.get("AI_LLM_MAX_TOKENS"), 1024)
    temperature = _as_float(cfg.get("AI_LLM_TEMPERATURE"), 0.1)
    min_conf = _as_float(cfg.get("AI_MIN_CONFIDENCE"), 0.55)

    return LLMRuntimeConfig(
        provider=provider,
        primary_model=primary_model,
        fallback_model=fallback_model,
        model_path=model_path,
        model_url=model_url,
        api_base_url=api_base_url,
        api_key=api_key,
        timeout=max(5, timeout),
        max_tokens=max(256, max_tokens),
        temperature=max(0.0, min(temperature, 1.0)),
        min_confidence_for_accept=max(0.0, min(min_conf, 1.0)),
    )


def _resolve_provider(cfg: LLMRuntimeConfig) -> str:
    """Resolve provider with graceful fallback when dependencies are missing."""
    provider = cfg.provider or "auto"
    allowed = {"auto", "langextract", "openai_compatible", "onprem", "regex"}
    if provider not in allowed:
        provider = "auto"

    if provider == "auto":
        if LANGEXTRACT_AVAILABLE:
            return "langextract"
        if cfg.api_base_url and _is_openai_compatible_reachable(cfg.api_base_url, timeout=2):
            return "openai_compatible"
        if ONPREM_AVAILABLE and (cfg.model_path or cfg.model_url):
            return "onprem"
        return "regex"

    if provider == "langextract" and not LANGEXTRACT_AVAILABLE:
        print("[LLMExtractor] langextract not installed, falling back")
        if cfg.api_base_url and _is_openai_compatible_reachable(cfg.api_base_url, timeout=2):
            return "openai_compatible"
        if ONPREM_AVAILABLE and (cfg.model_path or cfg.model_url):
            return "onprem"
        return "regex"

    if provider == "openai_compatible" and (
        not cfg.api_base_url or not _is_openai_compatible_reachable(cfg.api_base_url, timeout=2)
    ):
        print("[LLMExtractor] OpenAI-compatible endpoint unavailable")
        return "regex"

    if provider == "onprem" and (not ONPREM_AVAILABLE or not (cfg.model_path or cfg.model_url)):
        print("[LLMExtractor] onprem not available or model path/url missing")
        return "regex"

    return provider


def _get_onprem_llm(cfg: LLMRuntimeConfig) -> Optional[object]:
    """Lazy-load onprem local LLM exactly once.
    
    Returns None if:
    - onprem module not available
    - llama-cpp-python dependency missing (required for local .gguf models)
    - No model source configured (model_path or model_url)
    - Initialization fails for any reason
    """
    global _onprem_llm_instance, _onprem_load_attempted

    if _onprem_load_attempted:
        return _onprem_llm_instance

    _onprem_load_attempted = True

    if not ONPREM_AVAILABLE:
        return None

    # Validate model source before attempting load
    if not cfg.model_path and not cfg.model_url:
        print("[LLMExtractor] [WARN] onprem: No LLM_MODEL_PATH or LLM_MODEL_URL configured; skipping")
        return None

    try:
        if cfg.model_path:
            _onprem_llm_instance = OnPremLLM(cfg.model_path, n_gpu_layers=-1)
        elif cfg.model_url:
            _onprem_llm_instance = OnPremLLM(cfg.model_url, n_gpu_layers=-1)

        print("[LLMExtractor] [INFO] onprem model loaded successfully")
        return _onprem_llm_instance
    except ImportError as exc:
        # Typically: llama-cpp-python not installed
        if "llama-cpp-python" in str(exc).lower():
            print("[LLMExtractor] [WARN] onprem: llama-cpp-python not installed; local .gguf inference unavailable")
            print("[LLMExtractor]       Falling back to other providers (langextract/openai_compatible/regex)")
        else:
            print(f"[LLMExtractor] [WARN] onprem: Import error: {exc}")
        return None
    except ValueError as exc:
        # Typically: llama-cpp-python missing at runtime; we catch this explicitly to avoid noisy tracebacks
        if "llama-cpp-python" in str(exc).lower():
            print("[LLMExtractor] [WARN] onprem: llama-cpp-python required but not installed")
            print("[LLMExtractor]       For local .gguf support, run: pip install llama-cpp-python")
        else:
            print(f"[LLMExtractor] [WARN] onprem: Configuration error: {exc}")
        return None
    except Exception as exc:
        print(f"[LLMExtractor] [WARN] onprem: Failed to load model: {type(exc).__name__}: {exc}")
        return None


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible chat client (works with local model servers too)."""

    def __init__(self, base_url: str, api_key: str = "", timeout: int = 25):
        self.base_url = (base_url or "").strip()
        self.api_key = api_key or ""
        self.timeout = timeout

    def _chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def prompt(self, prompt: str, model: str, max_tokens: int, temperature: float) -> str:
        if not self.base_url or not model:
            return ""

        if not _is_openai_compatible_reachable(self.base_url, timeout=1):
            return ""

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You extract business contact data into strict JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        resp = requests.post(
            self._chat_completions_url(),
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return ""

        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return "\n".join(parts).strip()
        return str(content or "").strip()


def _is_openai_compatible_reachable(base_url: str, timeout: int = 2) -> bool:
    """Quick reachability probe for local/API model servers with one-time cache."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return False

    cached = _openai_reachability_cache.get(base)
    if cached is not None:
        return cached

    probe_urls = []
    if base.endswith("/chat/completions"):
        api_root = base.rsplit("/", 2)[0]
        probe_urls = [f"{api_root}/models", f"{api_root.replace('/v1', '')}/api/tags"]
    elif base.endswith("/v1"):
        probe_urls = [f"{base}/models", f"{base[:-3]}/api/tags"]
    else:
        probe_urls = [f"{base}/v1/models", f"{base}/api/tags"]

    for url in probe_urls:
        try:
            resp = requests.get(url, timeout=max(1, timeout))
            # Any HTTP response means the endpoint is reachable.
            if resp.status_code >= 100:
                _openai_reachability_cache[base] = True
                return True
        except requests.RequestException:
            continue

    _openai_reachability_cache[base] = False
    return False


# Prompt for JSON extraction via generation models
EXTRACTION_PROMPT = """You are a strict data extraction assistant.
Extract business details from the text and return ONLY valid JSON.

Schema:
{{
  "name": "business name or null",
  "emails": ["email1@example.com"],
  "email_details": [
    {{
      "email": "email1@example.com",
      "email_type": "personal",
      "validity_score": 0.9,
      "reasoning": "Follows first.last pattern on a real company domain"
    }}
  ],
  "phones": ["+1-555-0123"],
  "address": "full address or null",
  "website": "https://example.com or null",
  "owner": "owner or founder name or null",
  "social_links": {{
    "linkedin": "url or null",
    "twitter": "url or null",
    "facebook": "url or null",
    "instagram": "url or null"
  }}
}}

For each email address extracted, also provide an entry in "email_details" with:
- "email": the full email address
- "email_type": classify as "personal" (e.g. first.last@domain), "role_based" (e.g. info@, sales@), "generic" (e.g. webmaster@, contact@), "obfuscated" (e.g. name [at] domain [dot] com), or "unknown"
- "validity_score": a number from 0 to 1 indicating how likely this email is to be deliverable. Base this on syntax, presence of a real name, domain reputation, and whether it appears to be a placeholder.
- "reasoning": brief justification for the score.

Rules:
- Prefer exact text evidence over guesses.
- Do not invent fields.
- Keep emails/phones deduplicated.
- Decode obfuscated emails (e.g. name [at] domain [dot] com → name@domain.com).

TEXT:
{text}

JSON:"""


LANGEXTRACT_PROMPT = """Extract business contact data with source-grounded entities.
Return entities for these classes when present:
- name
- email
- phone
- address
- website
- social_link (include attribute platform: linkedin/twitter/facebook/instagram)
- owner (owner or founder name)

Only extract what is supported by the provided text evidence.
"""


GOV_NONPROFIT_PROMPT = """You are extracting contacts from government/nonprofit directory pages.
Return ONLY valid JSON using this schema:
{
    "name": "organization or office name or null",
    "emails": ["email@example.org"],
    "email_details": [
        {
            "email": "email@example.org",
            "email_type": "personal|role_based|generic|obfuscated|unknown",
            "validity_score": 0.0,
            "reasoning": "short reason"
        }
    ],
    "phones": ["+1-555-555-5555"],
    "address": "full address or null",
    "website": "https://example.org or null",
    "owner": "person in charge or null",
    "division": "program, regional office, or division name or null",
    "parent_organization": "parent agency/nonprofit name or null",
    "organization_type": "government_agency|nonprofit|organization|unknown",
    "social_links": {
        "linkedin": "url or null",
        "twitter": "url or null",
        "facebook": "url or null",
        "instagram": "url or null"
    }
}

Rules:
- Extract explicit evidence first (mailto, staff listings, contact sections).
- Do not invent contacts or emails.
- Deduplicate emails and phones.
"""


GOV_LANGEXTRACT_PROMPT = """Extract government/nonprofit directory contacts with source-grounded entities.
Return entities for these classes when present:
- name
- email
- phone
- address
- website
- owner
- division
- parent_organization
- organization_type
- social_link (attribute platform: linkedin/twitter/facebook/instagram)

Only extract values clearly supported by the text.
"""


def _build_langextract_examples() -> List[Any]:
    """Build few-shot examples once for stable LangExtract behavior."""
    global _langextract_examples_cache

    if _langextract_examples_cache is not None:
        return _langextract_examples_cache

    if not LANGEXTRACT_AVAILABLE:
        _langextract_examples_cache = []
        return _langextract_examples_cache

    try:
        sample_text = (
            "Bluebird Dental Clinic is located at 480 Market St, San Francisco, CA. "
            "For appointments email hello@bluebirddental.com or call +1 (415) 555-0189. "
            "Website: https://bluebirddental.com. "
            "LinkedIn: https://www.linkedin.com/company/bluebirddental. "
            "Bluebird Dental Clinic provides cosmetic and family dental care."
        )

        _langextract_examples_cache = [
            lx.data.ExampleData(
                text=sample_text,
                extractions=[
                    lx.data.Extraction(
                        extraction_class="name",
                        extraction_text="Bluebird Dental Clinic",
                        attributes={},
                    ),
                    lx.data.Extraction(
                        extraction_class="address",
                        extraction_text="480 Market St, San Francisco, CA",
                        attributes={},
                    ),
                    lx.data.Extraction(
                        extraction_class="email",
                        extraction_text="hello@bluebirddental.com",
                        attributes={},
                    ),
                    lx.data.Extraction(
                        extraction_class="phone",
                        extraction_text="+1 (415) 555-0189",
                        attributes={},
                    ),
                    lx.data.Extraction(
                        extraction_class="website",
                        extraction_text="https://bluebirddental.com",
                        attributes={},
                    ),
                    lx.data.Extraction(
                        extraction_class="social_link",
                        extraction_text="https://www.linkedin.com/company/bluebirddental",
                        attributes={"platform": "linkedin"},
                    ),
                    lx.data.Extraction(
                        extraction_class="description",
                        extraction_text="provides cosmetic and family dental care",
                        attributes={},
                    ),
                ],
            )
        ]
    except Exception:
        _langextract_examples_cache = []

    return _langextract_examples_cache


class LLMExtractor:
    """Extract structured business data from cleaned text."""

    def __init__(
        self,
        provider: Optional[str] = None,
        primary_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
    ):
        overrides = {
            "provider": provider,
            "primary_model": primary_model,
            "fallback_model": fallback_model,
        }
        self.cfg = _read_runtime_config(overrides=overrides)
        self.provider = _resolve_provider(self.cfg)

        self.openai_client: Optional[OpenAICompatibleClient] = None
        self._openai_timed_out_recently = False
        if self.cfg.api_base_url:
            self.openai_client = OpenAICompatibleClient(
                base_url=self.cfg.api_base_url,
                api_key=self.cfg.api_key,
                timeout=self.cfg.timeout,
            )

        uses_onprem_path = self.provider in {"langextract", "openai_compatible", "onprem"}
        has_onprem_model = bool(self.cfg.model_path or self.cfg.model_url)
        self.onprem_llm = _get_onprem_llm(self.cfg) if (ONPREM_AVAILABLE and uses_onprem_path and has_onprem_model) else None

    @property
    def is_available(self) -> bool:
        if self.provider == "langextract" and LANGEXTRACT_AVAILABLE:
            return True
        if self.openai_client is not None:
            return True
        if self.onprem_llm is not None:
            return True
        return False

    def extract(self, text: str, prompt_type: str = "general") -> Dict:
        """Extract structured fields with multi-provider fallback."""
        if not text or len(text.strip()) < 20:
            return self._empty_result()

        text = text[:9000]
        candidates: List[Tuple[Dict, float, str, str]] = []

        provider_order = [self.provider]
        if self.provider == "langextract":
            provider_order.extend(["openai_compatible", "onprem"])
        elif self.provider == "openai_compatible":
            provider_order.append("onprem")

        for idx, provider in enumerate(provider_order):
            if idx == 0 or self._best_score(candidates) < self.cfg.min_confidence_for_accept:
                self._try_provider_models(text, candidates, provider, prompt_type=prompt_type)

        if candidates:
            best_result, best_score, best_provider, best_model = max(candidates, key=lambda x: x[1])
            best_result["confidence_score"] = round(best_score, 2)
            best_result["provider_used"] = best_provider
            best_result["model_used"] = best_model
            return best_result

        # Regex fallback (always available)
        result = self._regex_extract(text)
        result["confidence_score"] = max(0.1, self._score_confidence(result) * 0.6)
        result["provider_used"] = "regex"
        result["model_used"] = "regex"
        return result

    def _try_provider_models(
        self,
        text: str,
        candidates: List[Tuple[Dict, float, str, str]],
        provider: str,
        prompt_type: str = "general",
    ):
        """Try primary model, then fallback model when needed."""
        if provider == "langextract":
            if not LANGEXTRACT_AVAILABLE:
                return

            primary = self._extract_with_langextract(text, self.cfg.primary_model, prompt_type=prompt_type)
            self._append_candidate(candidates, primary, provider, self.cfg.primary_model)

            if self._should_try_fallback(primary):
                fallback = self._extract_with_langextract(text, self.cfg.fallback_model, prompt_type=prompt_type)
                self._append_candidate(candidates, fallback, provider, self.cfg.fallback_model)
            return

        if provider == "openai_compatible":
            if self.openai_client is None:
                return

            primary = self._extract_with_openai_client(text, self.cfg.primary_model, prompt_type=prompt_type)
            self._append_candidate(candidates, primary, provider, self.cfg.primary_model)

            # Avoid back-to-back long timeouts against the same endpoint.
            if self._openai_timed_out_recently:
                return

            if self._should_try_fallback(primary):
                fallback = self._extract_with_openai_client(text, self.cfg.fallback_model, prompt_type=prompt_type)
                self._append_candidate(candidates, fallback, provider, self.cfg.fallback_model)
            return

        if provider == "onprem":
            if self.onprem_llm is None:
                return

            primary = self._extract_with_onprem(text, prompt_type=prompt_type)
            self._append_candidate(candidates, primary, provider, "onprem-default")

    def _should_try_fallback(self, result: Optional[Dict]) -> bool:
        """Fallback when primary result is missing or low-confidence."""
        if not self.cfg.fallback_model:
            return False
        if self.cfg.fallback_model == self.cfg.primary_model:
            return False
        if not result:
            return True
        return self._score_confidence(result) < self.cfg.min_confidence_for_accept

    def _append_candidate(
        self,
        candidates: List[Tuple[Dict, float, str, str]],
        result: Optional[Dict],
        provider: str,
        model_name: str,
    ):
        """Normalize and score candidate before adding it."""
        if not result or not self._has_data(result):
            return

        normalized = self._normalize_result(result)
        score = self._score_confidence(normalized)
        candidates.append((normalized, score, provider, model_name))

    # ── LangExtract path ───────────────────────────────────────────────

    def _extract_with_langextract(self, text: str, model_id: str, prompt_type: str = "general") -> Optional[Dict]:
        if not LANGEXTRACT_AVAILABLE or not model_id:
            return None

        try:
            kwargs = {
                "text_or_documents": text,
                "prompt_description": self._select_langextract_prompt(prompt_type),
                "examples": _build_langextract_examples(),
                "model_id": model_id,
                "fence_output": False,
                "use_schema_constraints": False,
                "extraction_passes": 2,
                "max_char_buffer": 1200,
            }

            if self.cfg.api_base_url:
                kwargs["model_url"] = self.cfg.api_base_url
            if self.cfg.api_key:
                kwargs["api_key"] = self.cfg.api_key

            doc = lx.extract(**kwargs)
            parsed = self._parse_langextract_response(doc)
            return self._merge_regex_signals(parsed, text)
        except Exception as exc:
            print(f"[LLMExtractor] LangExtract inference error ({model_id}): {exc}")
            return None

    @classmethod
    def _parse_langextract_response(cls, response: Any) -> Optional[Dict]:
        """Convert LangExtract output to extractor schema."""
        extractions = cls._collect_langextract_extractions(response)
        if not extractions:
            return None

        result = cls._empty_result()

        for ext in extractions:
            ext_class = (cls._ext_value(ext, "extraction_class") or "").strip().lower()
            ext_text = (cls._ext_value(ext, "extraction_text") or "").strip()
            attrs = cls._ext_value(ext, "attributes") or {}
            if not isinstance(attrs, dict):
                attrs = {}

            if ext_class in {"name", "business_name", "company_name", "organization"}:
                if not result["name"] and ext_text:
                    result["name"] = ext_text
                continue

            if ext_class in {"email", "emails"}:
                for e in _ensure_list(attrs.get("email")) + _ensure_list(ext_text):
                    if e and "@" in e:
                        result["emails"].append(e)
                continue

            if ext_class in {"phone", "phones", "telephone"}:
                for p in _ensure_list(attrs.get("phone")) + _ensure_list(ext_text):
                    if p and any(ch.isdigit() for ch in p):
                        result["phones"].append(p)
                continue

            if ext_class in {"address", "location", "headquarters"}:
                if not result["address"] and ext_text:
                    result["address"] = ext_text
                continue

            if ext_class in {"website", "url", "site"}:
                url = attrs.get("url") or ext_text
                if url and not result["website"]:
                    result["website"] = url
                continue

            if ext_class in {"description", "about", "summary"}:
                if not result["description"] and ext_text:
                    result["description"] = ext_text
                continue

            if ext_class in {"social_link", "social", "linkedin", "twitter", "facebook", "instagram"}:
                url = attrs.get("url") or ext_text
                platform = (attrs.get("platform") or ext_class).lower()
                cls._apply_social_link(result, platform, url)

        return result

    @staticmethod
    def _collect_langextract_extractions(response: Any) -> List[Any]:
        """Best-effort extraction collector across LangExtract return types."""
        if response is None:
            return []

        if isinstance(response, list):
            out: List[Any] = []
            for item in response:
                out.extend(LLMExtractor._collect_langextract_extractions(item))
            return out

        exts = getattr(response, "extractions", None)
        if isinstance(exts, list):
            return exts

        if isinstance(response, dict):
            if isinstance(response.get("extractions"), list):
                return response["extractions"]

            docs = response.get("annotated_documents") or response.get("documents") or []
            out = []
            for d in docs:
                out.extend(LLMExtractor._collect_langextract_extractions(d))
            return out

        docs_attr = getattr(response, "annotated_documents", None) or getattr(response, "documents", None)
        if isinstance(docs_attr, list):
            out = []
            for d in docs_attr:
                out.extend(LLMExtractor._collect_langextract_extractions(d))
            return out

        return []

    @staticmethod
    def _ext_value(extraction: Any, key: str):
        """Read extraction field from object or dict."""
        if isinstance(extraction, dict):
            return extraction.get(key)
        return getattr(extraction, key, None)

    # ── Text generation paths (OpenAI-compatible + onprem) ────────────

    def _extract_with_openai_client(self, text: str, model: str, prompt_type: str = "general") -> Optional[Dict]:
        if not self.openai_client or not model:
            return None

        self._openai_timed_out_recently = False
        try:
            prompt = self._build_generation_prompt(text, prompt_type)
            raw = self.openai_client.prompt(
                prompt=prompt,
                model=model,
                max_tokens=self.cfg.max_tokens,
                temperature=self.cfg.temperature,
            )
            parsed = self._parse_llm_response(raw)
            return self._merge_regex_signals(parsed, text)
        except requests.exceptions.ReadTimeout as exc:
            self._openai_timed_out_recently = True
            print(f"[LLMExtractor] OpenAI-compatible timeout ({model}) after {self.cfg.timeout}s: {exc}")
            return None
        except Exception as exc:
            print(f"[LLMExtractor] OpenAI-compatible inference error ({model}): {exc}")
            return None

    def _extract_with_onprem(self, text: str, prompt_type: str = "general") -> Optional[Dict]:
        if not self.onprem_llm:
            return None

        try:
            prompt = self._build_generation_prompt(text, prompt_type)
            response = self.onprem_llm.prompt(
                prompt,
                max_tokens=self.cfg.max_tokens,
                temperature=self.cfg.temperature,
            )

            if isinstance(response, dict):
                raw = response.get("text", "") or response.get("output", "")
            else:
                raw = str(response)

            parsed = self._parse_llm_response(raw)
            return self._merge_regex_signals(parsed, text)
        except Exception as exc:
            print(f"[LLMExtractor] onprem inference error: {exc}")
            return None

    @staticmethod
    def _parse_llm_response(raw: str) -> Optional[Dict]:
        """Parse JSON from model output, tolerating markdown fences."""
        if not raw:
            return None

        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"```\s*$", "", raw)
            raw = raw.strip()

        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None

        try:
            data = json.loads(match.group())
            emails = _ensure_list(data.get("emails"))
            email_details = data.get("email_details") or []

            # Build email_details from raw emails if LLM didn't provide them
            if emails and not email_details:
                email_details = [
                    {"email": e, "email_type": "unknown", "validity_score": 0.5, "reasoning": ""}
                    for e in emails
                ]

            return {
                "name": data.get("name"),
                "emails": emails,
                "email_details": email_details,
                "phones": _ensure_list(data.get("phones")),
                "address": data.get("address"),
                "website": data.get("website"),
                "description": data.get("description"),
                "social_links": data.get("social_links") or {},
                "owner": data.get("owner"),
                "division": data.get("division"),
                "parent_organization": data.get("parent_organization"),
                "organization_type": data.get("organization_type"),
                "rating": _as_float(data.get("rating"), None),
                "hours": data.get("hours"),
                "categories": data.get("categories"),
                "price_level": data.get("price_level"),
                "confidence_score": 0.0,
            }
        except (json.JSONDecodeError, TypeError):
            return None

    # ── Post-processing + regex fallback ──────────────────────────────

    @classmethod
    def _merge_regex_signals(cls, result: Optional[Dict], text: str) -> Optional[Dict]:
        """Fill missing fields from regex signals without overriding strong model output."""
        if result is None:
            return None

        regex_data = cls._regex_extract(text)

        if not result.get("emails") and regex_data.get("emails"):
            result["emails"] = regex_data["emails"][:3]
        if not result.get("phones") and regex_data.get("phones"):
            result["phones"] = regex_data["phones"][:3]
        if not result.get("website") and regex_data.get("website"):
            result["website"] = regex_data["website"]
        if not result.get("social_links"):
            result["social_links"] = regex_data.get("social_links") or {}
        else:
            for platform, url in (regex_data.get("social_links") or {}).items():
                if url and not result["social_links"].get(platform):
                    result["social_links"][platform] = url

        return result

    @classmethod
    def _normalize_result(cls, result: Dict) -> Dict:
        """Normalize field formats and deduplicate list fields."""
        emails = cls._dedupe_keep_order(_ensure_list(result.get("emails")))
        email_details = result.get("email_details") or []

        # Build default email_details if missing
        if emails and not email_details:
            email_details = [
                {"email": e, "email_type": "unknown", "validity_score": 0.5, "reasoning": ""}
                for e in emails
            ]

        out = {
            "name": result.get("name"),
            "emails": emails,
            "email_details": email_details,
            "phones": cls._dedupe_keep_order(_ensure_list(result.get("phones"))),
            "address": result.get("address"),
            "website": result.get("website"),
            "description": result.get("description"),
            "social_links": result.get("social_links") or {},
            "owner": result.get("owner"),
            "division": result.get("division"),
            "parent_organization": result.get("parent_organization"),
            "organization_type": result.get("organization_type"),
            "rating": result.get("rating"),
            "hours": result.get("hours"),
            "categories": result.get("categories"),
            "price_level": result.get("price_level"),
            "confidence_score": 0.0,
        }

        if not isinstance(out["social_links"], dict):
            out["social_links"] = {}

        # Normalize social dict shape
        for key in ("linkedin", "twitter", "facebook", "instagram"):
            out["social_links"].setdefault(key, None)

        return out

    @staticmethod
    def _build_generation_prompt(text: str, prompt_type: str) -> str:
        if (prompt_type or '').strip().lower() in {'gov_nonprofit', 'government_nonprofit'}:
            return GOV_NONPROFIT_PROMPT + "\n\nTEXT:\n" + text + "\n\nJSON:"
        return EXTRACTION_PROMPT.format(text=text)

    @staticmethod
    def _select_langextract_prompt(prompt_type: str) -> str:
        if (prompt_type or '').strip().lower() in {'gov_nonprofit', 'government_nonprofit'}:
            return GOV_LANGEXTRACT_PROMPT
        return LANGEXTRACT_PROMPT

    @staticmethod
    def _dedupe_keep_order(items: List[str]) -> List[str]:
        seen = set()
        out = []
        for item in items:
            key = item.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item.strip())
        return out

    @staticmethod
    def _apply_social_link(result: Dict, platform_hint: str, url: str):
        """Map social URL to the right platform key."""
        if not url:
            return

        lower_url = url.lower()
        platform = platform_hint.lower() if platform_hint else ""

        if "linkedin" in platform or "linkedin.com" in lower_url:
            result["social_links"]["linkedin"] = url
        elif "twitter" in platform or "x" == platform or "x.com" in lower_url or "twitter.com" in lower_url:
            result["social_links"]["twitter"] = url
        elif "facebook" in platform or "facebook.com" in lower_url:
            result["social_links"]["facebook"] = url
        elif "instagram" in platform or "instagram.com" in lower_url:
            result["social_links"]["instagram"] = url

    @staticmethod
    def _best_score(candidates: List[Tuple[Dict, float, str, str]]) -> float:
        if not candidates:
            return 0.0
        return max(score for _, score, _, _ in candidates)

    @staticmethod
    def _regex_extract(text: str) -> Dict:
        """Regex-only extraction for guaranteed baseline output."""
        emails = list(set(re.findall(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text
        )))

        phones = list(set(re.findall(
            r"(?:\+?\d{1,3}[\s\-]?)?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}", text
        )))

        websites = re.findall(
            r"https?://(?:www\.)?[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
            text,
            re.IGNORECASE,
        )
        website = websites[0] if websites else None

        linkedin_urls = re.findall(
            r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[a-zA-Z0-9\-_.%]+/?",
            text,
            re.IGNORECASE,
        )
        twitter_urls = re.findall(
            r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[a-zA-Z0-9_]+/?",
            text,
            re.IGNORECASE,
        )
        facebook_urls = re.findall(
            r"https?://(?:www\.)?facebook\.com/[a-zA-Z0-9.\-]+/?",
            text,
            re.IGNORECASE,
        )
        instagram_urls = re.findall(
            r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9_.\-]+/?",
            text,
            re.IGNORECASE,
        )

        social = {
            "linkedin": linkedin_urls[0] if linkedin_urls else None,
            "twitter": twitter_urls[0] if twitter_urls else None,
            "facebook": facebook_urls[0] if facebook_urls else None,
            "instagram": instagram_urls[0] if instagram_urls else None,
        }

        return {
            "name": None,
            "emails": emails[:5],
            "phones": phones[:5],
            "address": None,
            "website": website,
            "description": None,
            "social_links": social,
            "confidence_score": 0.0,
        }

    @staticmethod
    def _has_data(result: Dict) -> bool:
        return bool(
            result.get("emails")
            or result.get("phones")
            or result.get("name")
            or result.get("address")
            or result.get("website")
        )

    @staticmethod
    def _score_confidence(result: Dict) -> float:
        """Estimate confidence from field completeness."""
        score = 0.0
        weights = {
            "name": 0.15,
            "emails": 0.25,
            "phones": 0.15,
            "address": 0.15,
            "website": 0.10,
            "description": 0.10,
            "social_links": 0.10,
        }

        for field, weight in weights.items():
            val = result.get(field)
            if val:
                if isinstance(val, list) and len(val) > 0:
                    score += weight
                elif isinstance(val, dict):
                    if any(v for v in val.values()):
                        score += weight
                elif isinstance(val, str) and val.strip():
                    score += weight

        return round(min(score, 1.0), 2)

    @staticmethod
    def _empty_result() -> Dict:
        return {
            "name": None,
            "emails": [],
            "email_details": [],
            "phones": [],
            "address": None,
            "website": None,
            "description": None,
            "owner": None,
            "division": None,
            "parent_organization": None,
            "organization_type": None,
            "social_links": {
                "linkedin": None,
                "twitter": None,
                "facebook": None,
                "instagram": None,
            },
            "confidence_score": 0.0,
        }


def _ensure_list(val):
    """Ensure a value is a list."""
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val else []
    if isinstance(val, list):
        return [v for v in val if v]
    return []


def _as_int(val, default: int) -> int:
    try:
        return int(val)
    except Exception:
        return default


def _as_float(val, default: float) -> float:
    try:
        return float(val)
    except Exception:
        return default


def _normalize_model_name(model_name: str) -> str:
    """Map friendly model names to local-runtime IDs."""
    cleaned = (model_name or "").strip()
    if not cleaned:
        return ""

    return MODEL_NAME_ALIASES.get(cleaned.lower(), cleaned)
