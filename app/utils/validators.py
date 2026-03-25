import re
import validators


PLACEHOLDER_DOMAINS = {
    'domain.com', 'example.com', 'example.org', 'example.net',
    'email.com', 'test.com', 'yourdomain.com',
}

ASSET_TLDS = {
    'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'css', 'js', 'ico',
}

INVALID_LOCAL_PARTS = {
    'user', 'username', 'email', 'test', 'demo', 'sample',
}

def validate_email(email):
    """Validate email format and reject obvious placeholders/assets."""
    if not email:
        return False

    email = str(email).strip().lower()
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if re.match(pattern, email) is None:
        return False

    local, domain = email.split('@', 1)
    if local in INVALID_LOCAL_PARTS:
        return False
    if domain in PLACEHOLDER_DOMAINS:
        return False

    tld = domain.rsplit('.', 1)[-1]
    if tld in ASSET_TLDS:
        return False

    # Reject common sprite/image filename local-parts and pixel density tokens.
    if re.search(r'@\d+x\.', email):
        return False
    if any(token in local for token in ('sprite', 'logo', 'pattern', 'icon', 'image', 'banner')):
        return False

    return True

def validate_phone(phone):
    """Validate phone number format (basic)"""
    pattern = r'^[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,5}[-\s\.]?[0-9]{1,5}$'
    return re.match(pattern, phone) is not None

def validate_url(url):
    """Validate URL format"""
    return validators.url(url)

def extract_emails_from_text(text):
    """Extract all email addresses from text"""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return re.findall(pattern, text)