DEMO_ADMIN_EMAIL = 'ayushiskhati305@gmail.com'
DEMO_LOCK_MESSAGE = (
    'Your extraction limit has expired. You can no longer extract data with '
    'these credentials. To extract more data, please contact the owner at '
    'ayushiskhati305@gmail.com'
)


def is_admin_user(user):
    if not user:
        return False
    return (getattr(user, 'email', '') or '').strip().lower() == DEMO_ADMIN_EMAIL


def is_demo_locked_user(user):
    if not user or is_admin_user(user):
        return False
    return int(getattr(user, 'extraction_uses', 0) or 0) >= 1
