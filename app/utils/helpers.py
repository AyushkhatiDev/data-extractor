import re

from app.utils.validators import validate_email


def extract_email_from_html(html):
    """Return the first valid email address found in an HTML string."""
    if not html:
        return None
    emails = re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        html,
    )
    for email in emails:
        candidate = (email or '').strip().lower().strip('.')
        if validate_email(candidate):
            return candidate
    return None
