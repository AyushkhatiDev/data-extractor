import os
import random
import time
from typing import Optional

import requests

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
]


def get_random_user_agent():
    return random.choice(USER_AGENTS)


def human_delay(min_seconds=2.0, max_seconds=5.0):
    time.sleep(random.uniform(min_seconds, max_seconds))


def get_proxy_url():
    return os.getenv('PROXY_URL') or None


def build_requests_session(proxy_url: Optional[str] = None):
    session = requests.Session()
    session.headers.update({
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.8',
    })
    if proxy_url:
        session.proxies.update({
            'http': proxy_url,
            'https': proxy_url,
        })
    return session


def human_scroll_playwright(page, scroll_container_selector=None, steps=6):
    """Small, incremental scrolls to mimic human behavior."""
    try:
        if scroll_container_selector:
            container = page.query_selector(scroll_container_selector)
            if not container:
                return
            for _ in range(steps):
                container.evaluate('el => el.scrollTop = el.scrollTop + 400')
                time.sleep(random.uniform(0.4, 1.0))
        else:
            for _ in range(steps):
                page.mouse.wheel(0, random.randint(300, 700))
                time.sleep(random.uniform(0.4, 1.0))
    except Exception:
        return
