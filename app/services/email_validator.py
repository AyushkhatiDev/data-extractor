"""
Email Validation Service — local-only email quality pipeline.

Performs MX record checks, disposable domain blacklisting,
syntactic heuristic scoring, and combines them with LLM validity
scores into a final confidence and verification status.

No external API keys required.
"""

import os
import re
import threading
from typing import Dict, Optional

try:
    import dns.resolver
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False

# ── In-memory caches ──────────────────────────────────────────────────

_mx_cache: Dict[str, Optional[bool]] = {}
_mx_cache_lock = threading.Lock()

_disposable_domains: Optional[set] = None
_disposable_domains_lock = threading.Lock()


# ── Role-based keywords ──────────────────────────────────────────────

ROLE_KEYWORDS = frozenset({
    'info', 'sales', 'support', 'contact', 'admin', 'help',
    'billing', 'noreply', 'no-reply', 'webmaster', 'postmaster',
    'abuse', 'security', 'team', 'office', 'hello', 'general',
    'inquiries', 'enquiries', 'careers', 'jobs', 'hr',
    'marketing', 'press', 'media', 'feedback', 'service',
})

# ── Personal-name patterns ───────────────────────────────────────────

_PERSONAL_PATTERNS = [
    re.compile(r'^[a-z]+\.[a-z]+@', re.IGNORECASE),           # first.last@
    re.compile(r'^[a-z]+\.[a-z]+\d{0,4}@', re.IGNORECASE),   # first.last2@
    re.compile(r'^[a-z]\.[a-z]+@', re.IGNORECASE),            # f.last@
    re.compile(r'^[a-z]+_[a-z]+@', re.IGNORECASE),            # first_last@
    re.compile(r'^[a-z]{2,}\d{0,4}@', re.IGNORECASE),         # firstname@
]

TRUSTED_TLDS = frozenset({'.edu', '.gov', '.org', '.mil'})


# ── Disposable domain loader ─────────────────────────────────────────

def _get_disposable_domains() -> set:
    """Load disposable domains from file, cached after first load."""
    global _disposable_domains

    if _disposable_domains is not None:
        return _disposable_domains

    with _disposable_domains_lock:
        if _disposable_domains is not None:
            return _disposable_domains

        domains = set()
        # Look for the file relative to the project root
        candidates = [
            os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'disposable_domains.txt'),
            os.path.join(os.getcwd(), 'data', 'disposable_domains.txt'),
        ]
        for path in candidates:
            abs_path = os.path.abspath(path)
            if os.path.isfile(abs_path):
                try:
                    with open(abs_path, 'r') as f:
                        for line in f:
                            domain = line.strip().lower()
                            if domain and not domain.startswith('#'):
                                domains.add(domain)
                except OSError:
                    pass
                break

        _disposable_domains = domains
        return _disposable_domains


def reload_disposable_domains():
    """Force reload the disposable domains list from disk."""
    global _disposable_domains
    with _disposable_domains_lock:
        _disposable_domains = None
    _get_disposable_domains()


# ── Core validation functions ─────────────────────────────────────────

def check_mx_record(domain: str, timeout: float = 5.0) -> Optional[bool]:
    """
    Check if a domain has MX records via DNS lookup.

    Returns:
        True  — MX records found (domain can receive email)
        False — No MX records found
        None  — Lookup failed (timeout / DNS unavailable)
    """
    if not DNS_AVAILABLE:
        return None

    domain = domain.strip().lower()
    if not domain:
        return None

    with _mx_cache_lock:
        if domain in _mx_cache:
            return _mx_cache[domain]

    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answers = resolver.resolve(domain, 'MX')
        result = len(answers) > 0
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        result = False
    except Exception:
        result = None  # timeout or other DNS error

    with _mx_cache_lock:
        _mx_cache[domain] = result

    return result


def is_disposable_domain(domain: str) -> bool:
    """Check if the domain is in the disposable email blocklist."""
    domain = domain.strip().lower()
    return domain in _get_disposable_domains()


def syntactic_heuristics(email: str) -> float:
    """
    Compute a heuristic score for the email based on syntactic patterns.

    Score adjustments:
        +0.2  personal pattern (first.last@, etc.)
        -0.3  role-based keyword (info@, sales@, etc.)
        +0.1  trusted TLD (.edu, .gov, .org, .mil)
        -0.5  disposable domain
    """
    if not email or '@' not in email:
        return 0.0

    email = email.strip().lower()
    local_part = email.split('@')[0]
    domain = email.split('@')[1]

    score = 0.0

    # Personal pattern bonus
    for pattern in _PERSONAL_PATTERNS:
        if pattern.match(email):
            score += 0.2
            break

    # Role-based penalty
    # Check if the local part itself is a role keyword, or starts with one
    local_base = local_part.split('.')[0].split('_')[0].split('+')[0]
    if local_base in ROLE_KEYWORDS:
        score -= 0.3

    # Trusted TLD bonus
    for tld in TRUSTED_TLDS:
        if domain.endswith(tld):
            score += 0.1
            break

    # Disposable domain penalty
    if is_disposable_domain(domain):
        score -= 0.5

    return round(score, 2)


def combine_scores(
    llm_score: float = 0.0,
    mx_valid: Optional[bool] = None,
    disposable: bool = False,
    heuristic_score: float = 0.0,
) -> Dict:
    """
    Combine multiple validation signals into a final confidence and status.

    Weights:
        40% LLM validity score
        30% MX record check
        20% Syntactic heuristics
        10% Disposable domain penalty

    Returns:
        {
            "final_confidence": float (0.0 – 1.0),
            "verification_status": str  (verified | likely_valid | unverified | invalid)
        }
    """
    # Normalize inputs
    llm_score = max(0.0, min(float(llm_score or 0.0), 1.0))

    # MX score: True → 1.0, False → 0.0, None → 0.5 (unknown)
    if mx_valid is True:
        mx_score = 1.0
    elif mx_valid is False:
        mx_score = 0.0
    else:
        mx_score = 0.5

    # Disposable penalty: 0.0 if disposable, 1.0 if not
    disposable_score = 0.0 if disposable else 1.0

    # Heuristic score is already -0.5 to +0.3, normalize to 0–1 range
    # Map [-0.5, +0.3] → [0.0, 1.0]
    heuristic_normalized = max(0.0, min((heuristic_score + 0.5) / 0.8, 1.0))

    # Weighted combination
    final = (
        0.40 * llm_score
        + 0.30 * mx_score
        + 0.20 * heuristic_normalized
        + 0.10 * disposable_score
    )
    final = round(max(0.0, min(final, 1.0)), 3)

    # Determine verification status
    if disposable:
        status = 'invalid'
    elif mx_valid is False:
        status = 'invalid'
    elif final >= 0.75 and mx_valid is True:
        status = 'verified'
    elif final >= 0.55:
        status = 'likely_valid'
    elif final >= 0.30:
        status = 'unverified'
    else:
        status = 'invalid'

    return {
        'final_confidence': final,
        'verification_status': status,
    }


def validate_email_record(
    email: str,
    llm_score: float = 0.0,
    email_type: str = 'unknown',
) -> Dict:
    """
    Run the full validation pipeline on a single email address.

    Returns a dict with all validation fields ready to store on a Business record:
        llm_validity_score, email_type, mx_valid, disposable_domain,
        heuristic_score, final_confidence, verification_status
    """
    if not email or '@' not in email:
        return {
            'llm_validity_score': 0.0,
            'email_type': 'unknown',
            'mx_valid': None,
            'disposable_domain': False,
            'heuristic_score': 0.0,
            'final_confidence': 0.0,
            'verification_status': 'invalid',
        }

    email = email.strip().lower()
    domain = email.split('@')[1]

    mx = check_mx_record(domain)
    disposable = is_disposable_domain(domain)
    heuristic = syntactic_heuristics(email)

    combined = combine_scores(
        llm_score=llm_score,
        mx_valid=mx,
        disposable=disposable,
        heuristic_score=heuristic,
    )

    return {
        'llm_validity_score': round(float(llm_score or 0.0), 3),
        'email_type': email_type or 'unknown',
        'mx_valid': mx,
        'disposable_domain': disposable,
        'heuristic_score': heuristic,
        'final_confidence': combined['final_confidence'],
        'verification_status': combined['verification_status'],
    }
