"""
Tests for the email validation service.

Run with:
    python -m pytest tests/test_email_validator.py -v
"""

import sys
import os

# Ensure project root is on sys.path so `app` is importable without install
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.email_validator import (
    syntactic_heuristics,
    is_disposable_domain,
    combine_scores,
    check_mx_record,
    validate_email_record,
)
from app.utils.validators import validate_email


# ── syntactic_heuristics tests ────────────────────────────────────────

class TestSyntacticHeuristics:
    def test_personal_email_positive_score(self):
        """Personal emails (first.last@) should get a positive heuristic boost."""
        score = syntactic_heuristics("john.doe@company.com")
        assert score > 0, f"Expected positive score for personal email, got {score}"

    def test_role_email_negative_score(self):
        """Role-based emails (info@, sales@) should get a negative adjustment."""
        score = syntactic_heuristics("info@company.com")
        assert score < 0, f"Expected negative score for role email, got {score}"

    def test_support_email_negative(self):
        score = syntactic_heuristics("support@bigcorp.com")
        assert score < 0

    def test_edu_domain_bonus(self):
        """Emails on .edu domains should get a positive TLD bonus."""
        score = syntactic_heuristics("student@university.edu")
        assert score >= 0.1

    def test_gov_domain_bonus(self):
        score = syntactic_heuristics("employee@agency.gov")
        assert score >= 0.1

    def test_personal_beats_role(self):
        """A personal email should score higher than a role-based one."""
        personal = syntactic_heuristics("john.doe@company.com")
        role = syntactic_heuristics("info@company.com")
        assert personal > role

    def test_empty_email_returns_zero(self):
        assert syntactic_heuristics("") == 0.0
        assert syntactic_heuristics(None) == 0.0

    def test_no_at_sign_returns_zero(self):
        assert syntactic_heuristics("notanemail") == 0.0


# ── is_disposable_domain tests ────────────────────────────────────────

class TestDisposableDomain:
    def test_known_disposable_detected(self):
        """Domains in the blocklist should be detected."""
        assert is_disposable_domain("mailinator.com") is True
        assert is_disposable_domain("yopmail.com") is True

    def test_legitimate_domain_not_flagged(self):
        """Legitimate domains should NOT be flagged as disposable."""
        assert is_disposable_domain("gmail.com") is False
        assert is_disposable_domain("company.com") is False

    def test_case_insensitive(self):
        assert is_disposable_domain("MAILINATOR.COM") is True
        assert is_disposable_domain("Yopmail.Com") is True


# ── combine_scores tests ─────────────────────────────────────────────

class TestCombineScores:
    def test_high_scores_produce_verified(self):
        """High LLM + valid MX should produce 'verified' status."""
        result = combine_scores(
            llm_score=0.9, mx_valid=True, disposable=False, heuristic_score=0.2
        )
        assert result['verification_status'] == 'verified'
        assert result['final_confidence'] >= 0.75

    def test_disposable_produces_invalid(self):
        """Disposable domain should always produce 'invalid'."""
        result = combine_scores(
            llm_score=0.9, mx_valid=True, disposable=True, heuristic_score=0.2
        )
        assert result['verification_status'] == 'invalid'

    def test_no_mx_produces_invalid(self):
        """No MX record should produce 'invalid'."""
        result = combine_scores(
            llm_score=0.5, mx_valid=False, disposable=False, heuristic_score=0.0
        )
        assert result['verification_status'] == 'invalid'

    def test_moderate_scores_produce_likely_valid(self):
        result = combine_scores(
            llm_score=0.7, mx_valid=True, disposable=False, heuristic_score=0.0
        )
        assert result['verification_status'] in ('verified', 'likely_valid')

    def test_low_scores_produce_unverified_or_invalid(self):
        result = combine_scores(
            llm_score=0.2, mx_valid=None, disposable=False, heuristic_score=-0.3
        )
        assert result['verification_status'] in ('unverified', 'invalid')

    def test_confidence_is_bounded(self):
        result = combine_scores(llm_score=1.0, mx_valid=True, disposable=False, heuristic_score=0.3)
        assert 0.0 <= result['final_confidence'] <= 1.0

        result2 = combine_scores(llm_score=0.0, mx_valid=False, disposable=True, heuristic_score=-0.5)
        assert 0.0 <= result2['final_confidence'] <= 1.0


# ── validate_email_record tests ───────────────────────────────────────

class TestValidateEmailRecord:
    def test_returns_all_fields(self):
        """validate_email_record should return all expected fields."""
        result = validate_email_record("test@example.com", llm_score=0.5)
        expected_keys = {
            'llm_validity_score', 'email_type', 'mx_valid',
            'disposable_domain', 'heuristic_score',
            'final_confidence', 'verification_status',
        }
        assert expected_keys == set(result.keys())

    def test_disposable_email_flagged(self):
        result = validate_email_record("user@mailinator.com", llm_score=0.8)
        assert result['disposable_domain'] is True
        assert result['verification_status'] == 'invalid'

    def test_personal_email_scores_higher(self):
        personal = validate_email_record("john.doe@company.com", llm_score=0.8, email_type='personal')
        generic = validate_email_record("info@company.com", llm_score=0.8, email_type='role_based')
        assert personal['heuristic_score'] > generic['heuristic_score']

    def test_empty_email_returns_invalid(self):
        result = validate_email_record("", llm_score=0.5)
        assert result['verification_status'] == 'invalid'

    def test_no_at_sign_returns_invalid(self):
        result = validate_email_record("notanemail", llm_score=0.5)
        assert result['verification_status'] == 'invalid'


class TestCoreEmailValidator:
    def test_rejects_asset_like_email(self):
        assert validate_email("chosen-sprite@2x.png") is False
        assert validate_email("white-pattern-fade-top@2x.png") is False

    def test_rejects_placeholder_email(self):
        assert validate_email("user@domain.com") is False

    def test_accepts_real_business_email(self):
        assert validate_email("ahalstead@rlcommunities.com") is True


# ── check_mx_record tests (requires network) ─────────────────────────

class TestCheckMxRecord:
    def test_valid_domain_has_mx(self):
        """Well-known domain should have MX records."""
        result = check_mx_record("gmail.com")
        # May be None if dnspython isn't installed
        if result is not None:
            assert result is True

    def test_invalid_domain_no_mx(self):
        """Non-existent domain should not have MX records."""
        result = check_mx_record("this-domain-does-not-exist-xyz123.com")
        if result is not None:
            assert result is False

    def test_empty_domain(self):
        assert check_mx_record("") is None
