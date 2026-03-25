import re
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from app.utils.helpers import extract_email_from_html
from app.utils.scraping import get_random_user_agent, get_proxy_url
from app.utils.validators import validate_email


class EmailScraper:
    """Scrapes email addresses and LinkedIn URLs from business websites."""

    EMAIL_REGEX = re.compile(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        re.IGNORECASE,
    )

    LINKEDIN_REGEX = re.compile(
        r'https?://(?:www\.)?linkedin\.com/(?:company|in)/[a-zA-Z0-9\-_.%]+/?',
        re.IGNORECASE,
    )

    PHONE_REGEX = re.compile(
        r'(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',
        re.IGNORECASE,
    )

    # Pages that commonly contain contact info
    CONTACT_PATHS = [
        '', '/contact', '/contact-us', '/contactus',
        '/about', '/about-us', '/aboutus',
        '/team', '/staff', '/directory', '/leadership',
        '/get-in-touch', '/reach-us', '/support',
        '/impressum', '/imprint',
    ]

    EXCLUDED_EMAIL_DOMAINS = {
        'example.com', 'sentry.io', 'wixpress.com', 'googleapis.com',
        'w3.org', 'schema.org', 'facebook.com', 'twitter.com',
        'instagram.com', 'youtube.com', 'google.com', 'apple.com',
        'microsoft.com', 'jquery.com', 'cloudflare.com',
        'domain.com', 'example.org', 'example.net', 'email.com',
    }

    EXCLUDED_EMAIL_PREFIXES = {
        'noreply', 'no-reply', 'mailer-daemon', 'postmaster',
        'webmaster', 'root', 'abuse', 'hostmaster',
    }

    def __init__(self, timeout=8):
        self.timeout = timeout
        self.session = requests.Session()
        proxy = get_proxy_url()
        self.session.headers.update({
            'User-Agent': (
                get_random_user_agent()
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        if proxy:
            self.session.proxies.update({'http': proxy, 'https': proxy})

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------
    def scrape_emails(self, website_url):
        """Return the best email found on the website, or None."""
        pages = self._fetch_pages(website_url)
        found = set()
        for html in pages:
            found.update(self._extract_emails_from_html(html))
        return self._pick_best_email(found)

    def find_linkedin_url(self, website_url):
        """Return the first LinkedIn company/profile URL found, or None."""
        pages = self._fetch_pages(website_url)
        for html in pages:
            urls = self._extract_linkedin_from_html(html)
            if urls:
                return urls[0]
        return None

    def scrape_all(self, website_url):
        """Return (email, linkedin_url) tuple from a website."""
        pages = self._fetch_pages(website_url)
        emails = set()
        linkedin = None
        for html in pages:
            emails.update(self._extract_emails_from_html(html))
            if not linkedin:
                li = self._extract_linkedin_from_html(html)
                if li:
                    linkedin = li[0]
        return self._pick_best_email(emails), linkedin

    def scrape_contact_details(self, website_url):
        """Return dict containing best available email/phone/linkedin."""
        pages = self._fetch_pages(website_url)
        emails = set()
        phones = set()
        linkedin = None

        for html in pages:
            emails.update(self._extract_emails_from_html(html))
            phones.update(self._extract_phones_from_html(html))
            if not linkedin:
                li = self._extract_linkedin_from_html(html)
                if li:
                    linkedin = li[0]

        return {
            'email': self._pick_best_email(emails),
            'phone': self._pick_best_phone(phones),
            'linkedin': linkedin,
        }

    def scrape_first_email(self, website_url):
        """Return the first valid email found across contact pages."""
        pages = self._fetch_pages(website_url)
        for html in pages:
            email = extract_email_from_html(html)
            if email and self._valid_email(email):
                return email.lower().strip('.')
        return None

    # ------------------------------------------------------------------
    #  Page fetching
    # ------------------------------------------------------------------
    def _fetch_pages(self, website_url):
        """Download homepage + contact pages, return list of HTML strings."""
        if not website_url:
            return []

        if not website_url.startswith(('http://', 'https://')):
            website_url = 'https://' + website_url

        htmls = []
        tried = set()

        for path in self.CONTACT_PATHS:
            url = urljoin(website_url.rstrip('/') + '/', path.lstrip('/'))
            if url in tried:
                continue
            tried.add(url)

            try:
                resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                if resp.status_code == 200 and 'text/html' in resp.headers.get('content-type', ''):
                    htmls.append(resp.text)
            except Exception:
                continue

            # Homepage is enough if we already found good data
            if htmls and path == '':
                # Quick check – if homepage has email AND linkedin, skip rest
                quick_emails = self._extract_emails_from_html(htmls[0])
                quick_li = self._extract_linkedin_from_html(htmls[0])
                if quick_emails and quick_li:
                    break

        return htmls

    # ------------------------------------------------------------------
    #  Email extraction
    # ------------------------------------------------------------------
    def _extract_emails_from_html(self, html_text):
        """Extract emails from both mailto: links and raw text."""
        found = set()

        # 1) Parse mailto: links with BeautifulSoup (most reliable)
        try:
            soup = BeautifulSoup(html_text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if href.startswith('mailto:'):
                    email = href.replace('mailto:', '').split('?')[0].strip()
                    if self._valid_email(email):
                        found.add(email.lower())
        except Exception:
            # Fallback: regex-only if parser fails
            pass

        # 2) Regex across the full text for any emails parser missed
        for email in self.EMAIL_REGEX.findall(html_text):
            email = email.lower().strip('.')
            if self._valid_email(email):
                found.add(email)

        return found

    def _extract_phones_from_html(self, html_text):
        """Extract candidate phone numbers and normalize rough formatting."""
        if not html_text:
            return set()

        out = set()
        for match in self.PHONE_REGEX.findall(html_text):
            raw = (match or '').strip()
            digits = re.sub(r'\D', '', raw)
            # Keep US-style lengths only to reduce noise.
            if len(digits) == 10:
                out.add(raw)
            elif len(digits) == 11 and digits.startswith('1'):
                out.add(raw)
        return out

    def _valid_email(self, email):
        """Return True if the email looks like a real business email."""
        if not email or '@' not in email:
            return False
        email = email.lower().strip('.')
        if not validate_email(email):
            return False

        domain = email.split('@')[-1]
        prefix = email.split('@')[0]

        if domain in self.EXCLUDED_EMAIL_DOMAINS:
            return False
        if prefix in self.EXCLUDED_EMAIL_PREFIXES:
            return False
        # Must have at least one dot in domain
        if '.' not in domain:
            return False
        return True

    def _pick_best_email(self, emails):
        """Pick the most likely business-contact email."""
        if not emails:
            return None
        priority = ['info', 'contact', 'hello', 'admin', 'office', 'support', 'sales', 'enquiry', 'inquiry']
        for prefix in priority:
            for email in emails:
                if email.startswith(prefix + '@'):
                    return email
        return sorted(emails)[0]

    @staticmethod
    def _pick_best_phone(phones):
        """Pick the most plausible phone candidate."""
        if not phones:
            return None
        ranked = sorted(
            phones,
            key=lambda p: len(re.sub(r'\D', '', p)),
            reverse=True,
        )
        return ranked[0]

    # ------------------------------------------------------------------
    #  LinkedIn extraction
    # ------------------------------------------------------------------
    def _extract_linkedin_from_html(self, html_text):
        """Extract LinkedIn company/profile URLs from HTML."""
        found = []

        # 1) BeautifulSoup: look for <a> links pointing to linkedin.com
        try:
            soup = BeautifulSoup(html_text, 'lxml')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'linkedin.com/company/' in href or 'linkedin.com/in/' in href:
                    clean = self._clean_linkedin_url(href)
                    if clean and clean not in found:
                        found.append(clean)
        except Exception:
            pass

        # 2) Regex fallback
        for match in self.LINKEDIN_REGEX.findall(html_text):
            clean = self._clean_linkedin_url(match)
            if clean and clean not in found:
                found.append(clean)

        return found

    @staticmethod
    def _clean_linkedin_url(url):
        """Normalise a LinkedIn URL."""
        url = url.strip().rstrip('/')
        # Remove tracking params
        url = url.split('?')[0].rstrip('/')
        if '/company/' in url or '/in/' in url:
            return url
        return None


def discover_email(website_url, timeout=8):
    """Reusable helper to fetch a website and return the first valid email."""
    scraper = EmailScraper(timeout=timeout)
    return scraper.scrape_first_email(website_url)
