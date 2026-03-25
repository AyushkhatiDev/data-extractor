import os

import undetected_chromedriver as uc
from selenium import webdriver

from app.utils.scraping import get_proxy_url, get_random_user_agent


def get_driver(headless=False, proxy_url=None, user_agent=None):
    options = webdriver.ChromeOptions()
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--start-maximized')

    if headless:
        options.add_argument('--headless=new')

    ua = user_agent or get_random_user_agent()
    if ua:
        options.add_argument(f'--user-agent={ua}')

    proxy = proxy_url or get_proxy_url()
    if proxy:
        options.add_argument(f'--proxy-server={proxy}')

    return uc.Chrome(options=options)
