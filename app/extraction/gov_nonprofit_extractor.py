"""
GovNonprofitExtractor — targeted crawler for government/nonprofit directories.

Crawls curated seed URLs, discovers relevant program/directory/contact pages,
extracts contact records, and stores normalized Business rows.
"""

import re
import traceback
from collections import deque
from html import unescape
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.ai.llm_extractor import LLMExtractor
from app.extraction.base_extractor import BaseExtractor
from app.services.email_validator import validate_email_record


EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")

CONTACT_KEYWORDS = (
    "contact",
    "directory",
    "staff",
    "team",
    "office",
    "program",
    "services",
    "about",
    "regional",
    "affiliate",
    "ombudsman",
)

FACILITY_KEYWORDS = (
    "facility",
    "nursing",
    "care",
    "home",
    "provider",
    "profile",
    "licensed",
    "licensing",
    "long-term",
    "long term",
)

SKIP_EXTENSIONS = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".zip",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
)


class GovNonprofitExtractor(BaseExtractor):
    """Specialized extraction for government and nonprofit directory sites."""

    def __init__(
        self,
        task_id: int,
        seed_urls: List[str],
        target_domains: Optional[List[str]] = None,
        follow_links: bool = True,
        max_depth: int = 2,
        max_pages: int = 40,
        email_patterns: Optional[List[str]] = None,
        stop_event=None,
        ai_options: Optional[dict] = None,
    ):
        super().__init__(task_id, stop_event=stop_event, ai_options=ai_options)
        self.seed_urls = [u for u in (seed_urls or []) if isinstance(u, str) and u.strip()]
        self.target_domains = [d.strip().lower() for d in (target_domains or []) if d and d.strip()]
        self.follow_links = bool(follow_links)
        self.max_depth = max(0, int(max_depth or 0))
        self.max_pages = max(1, int(max_pages or 1))
        self.max_llm_pages = max(0, int(self.ai_options.get('max_llm_pages', 8)))
        self.is_nursing_home_mode = (str(self.ai_options.get('list_type') or '').strip().lower() == 'nursing homes')
        self._pages_crawled = 0
        self._llm_calls = 0
        self.email_patterns = self._compile_patterns(email_patterns or [])
        self.llm = LLMExtractor(
            provider=self.ai_options.get('provider'),
            primary_model=self.ai_options.get('primary_model'),
            fallback_model=self.ai_options.get('fallback_model'),
        )
        self.enable_validation = bool(self.ai_options.get('enable_validation', True))
        self.http = requests.Session()
        self.http.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                )
            }
        )

    def extract(self):
        self.update_task_status('running')

        if not self.seed_urls:
            print(f"[GovNonprofitExtractor] No seed URLs provided; marking complete")
            self.update_task_status('completed', 0)
            return

        visited: Set[str] = set()
        seen_contacts: Set[str] = set()
        failed_seeds = []
        successful_seeds = 0

        print(f"[GovNonprofitExtractor] Starting extraction with {len(self.seed_urls)} seed URLs")
        print(f"[GovNonprofitExtractor] max_pages={self.max_pages}, max_depth={self.max_depth}, follow_links={self.follow_links}")

        try:
            for seed_idx, seed in enumerate(self.seed_urls, 1):
                if self.should_stop:
                    print(f"[GovNonprofitExtractor] Extraction stopped by user")
                    break
                
                print(f"[GovNonprofitExtractor] [{seed_idx}/{len(self.seed_urls)}] Processing seed: {seed}")
                try:
                    self._crawl_seed(seed, visited, seen_contacts)
                    successful_seeds += 1
                except Exception as seed_exc:
                    print(f"[GovNonprofitExtractor] Error crawling seed {seed}: {seed_exc}")
                    failed_seeds.append((seed, str(seed_exc)))
                    # Continue to next seed instead of failing entire extraction
                    traceback.print_exc()

            print(f"[GovNonprofitExtractor] Crawling complete: {successful_seeds}/{len(self.seed_urls)} seeds successful")
            print(f"[GovNonprofitExtractor] Crawled {self._pages_crawled} pages, extracted {self._saved_count} contacts")
            
            if failed_seeds:
                print(f"[GovNonprofitExtractor] Failed seeds ({len(failed_seeds)}):")
                for url, error in failed_seeds:
                    print(f"  - {url}: {error}")

            final_status = 'stopped' if self.should_stop else 'completed'
            self.update_task_status(final_status)
        except Exception as exc:
            traceback.print_exc()
            print(f"[GovNonprofitExtractor] Extraction failed: {exc}")
            self.update_task_status('failed')

    def _crawl_seed(self, seed_url: str, visited: Set[str], seen_contacts: Set[str]):
        """Crawl a single seed URL and its discovered links."""
        queue = deque([(seed_url, 0)])
        seed_contacts = 0

        while queue and not self.should_stop and self._pages_crawled < self.max_pages:
            url, depth = queue.popleft()
            norm = self._normalize_url(url)
            if not norm or norm in visited:
                continue
            if depth > self.max_depth:
                continue
            if not self._is_allowed_url(norm):
                continue

            visited.add(norm)
            
            try:
                html = self._fetch(norm)
                if not html:
                    print(f"[GovNonprofitExtractor]   ✗ Failed to fetch: {norm}")
                    continue
            except Exception as fetch_exc:
                print(f"[GovNonprofitExtractor]   ✗ Error fetching {norm}: {fetch_exc}")
                continue
            
            self._pages_crawled += 1
            print(f"[GovNonprofitExtractor]   ✓ Fetched page {self._pages_crawled}: {norm}")

            try:
                soup = BeautifulSoup(html, 'lxml')
                page_text = self._page_text(soup)

                contacts = self._extract_from_page(soup, norm, page_text)
                for contact in contacts:
                    sig = self._contact_signature(contact)
                    if sig in seen_contacts:
                        continue
                    seen_contacts.add(sig)
                    self.save_business(contact)
                    seed_contacts += 1

                if contacts:
                    print(f"[GovNonprofitExtractor]     → Extracted {len(contacts)} contacts")
            except Exception as extract_exc:
                print(f"[GovNonprofitExtractor]   ! Error extracting from {norm}: {extract_exc}")
                traceback.print_exc()
                continue

            if not self.follow_links or depth >= self.max_depth:
                continue

            try:
                for link in self._discover_links(soup, norm):
                    if link not in visited:
                        queue.append((link, depth + 1))
            except Exception as link_exc:
                print(f"[GovNonprofitExtractor]   ! Error discovering links from {norm}: {link_exc}")
                continue

        print(f"[GovNonprofitExtractor] Seed crawl complete: {seed_contacts} new contacts extracted")

    def _extract_from_page(self, soup: BeautifulSoup, page_url: str, page_text: str) -> List[Dict]:
        contacts: List[Dict] = []

        # 1) Collect explicit mailto contacts.
        for a in soup.select('a[href^="mailto:"]'):
            raw = (a.get('href') or '').replace('mailto:', '').split('?')[0].strip()
            email = self._normalize_email(raw)
            if not email:
                continue

            contact = {
                'email': email,
                'name': self._clean_text(a.get_text(' ', strip=True)) or self._guess_name_from_email(email),
                'phone': self._extract_nearby_phone(a),
                'website': self._extract_homepage(page_url),
                'location': None,
                'owner': None,
                'organization_type': self._infer_org_type(page_url),
                'parent_organization': self._infer_parent_org(page_url),
                'division': self._infer_division(page_url, soup),
                'source_url': page_url,
                'extraction_method': 'mailto',
                'source': 'list_crawl',
                'confidence_score': 0.8,
            }

            if self.enable_validation:
                contact.update(validate_email_record(email, llm_score=0.85, email_type='role_based'))
            contacts.append(contact)

        # 2) Regex email extraction from page text.
        candidates = set(EMAIL_RE.findall(page_text))
        for pattern in self.email_patterns:
            candidates.update(pattern.findall(page_text))

        for raw_email in candidates:
            email = self._normalize_email(raw_email)
            if not email:
                continue
            if any(c.get('email') == email for c in contacts):
                continue

            contact = {
                'email': email,
                'name': self._guess_name_from_context(page_text, email),
                'phone': self._extract_phone_from_context(page_text, email),
                'website': self._extract_homepage(page_url),
                'location': None,
                'owner': None,
                'organization_type': self._infer_org_type(page_url),
                'parent_organization': self._infer_parent_org(page_url),
                'division': self._infer_division(page_url, soup),
                'source_url': page_url,
                'extraction_method': 'regex',
                'source': 'list_crawl',
                'confidence_score': 0.65,
            }

            if self.enable_validation:
                contact.update(validate_email_record(email, llm_score=0.6, email_type='unknown'))
            contacts.append(contact)

        # 3) Optional LLM enrichment for directory-style pages.
        should_use_llm = (
            self.llm.is_available
            and self._llm_calls < self.max_llm_pages
            and self._looks_like_contact_page(page_url, page_text)
            and (len(contacts) < 2 or not any(c.get('name') and c.get('email') for c in contacts))
        )
        if should_use_llm:
            self._llm_calls += 1
            llm_result = self.llm.extract(page_text[:9000], prompt_type='gov_nonprofit')
            llm_contacts = self._contacts_from_llm(llm_result, page_url)
            for contact in llm_contacts:
                if any(c.get('email') == contact.get('email') and c.get('email') for c in contacts):
                    continue
                contacts.append(contact)

        # 4) Fallback: keep pages that expose contact signals without an email address.
        if not contacts:
            phone_match = PHONE_RE.search(page_text)
            heading = None
            h1 = soup.find('h1')
            if h1:
                heading = self._clean_text(h1.get_text(' ', strip=True))
            if phone_match or heading:
                contacts.append(
                    {
                        'name': heading or self._infer_division(page_url, soup) or self._infer_parent_org(page_url),
                        'email': None,
                        'phone': phone_match.group(0) if phone_match else None,
                        'website': self._extract_homepage(page_url),
                        'location': None,
                        'owner': None,
                        'organization_type': self._infer_org_type(page_url),
                        'parent_organization': self._infer_parent_org(page_url),
                        'division': self._infer_division(page_url, soup),
                        'source_url': page_url,
                        'extraction_method': 'phone_or_heading',
                        'source': 'list_crawl',
                        'confidence_score': 0.35,
                    }
                )

        return contacts

    def _contacts_from_llm(self, llm_result: Dict, page_url: str) -> List[Dict]:
        if not llm_result:
            return []

        contacts: List[Dict] = []
        emails = llm_result.get('emails') or []
        email_details = llm_result.get('email_details') or []
        phone = (llm_result.get('phones') or [None])[0]

        detail_map = {}
        for detail in email_details:
            if isinstance(detail, dict):
                e = self._normalize_email(detail.get('email') or '')
                if e:
                    detail_map[e] = detail

        for raw_email in emails:
            email = self._normalize_email(raw_email)
            if not email:
                continue

            detail = detail_map.get(email, {})
            llm_score = detail.get('validity_score', llm_result.get('confidence_score', 0.55))
            email_type = detail.get('email_type', 'unknown')

            contact = {
                'name': llm_result.get('name') or self._guess_name_from_email(email),
                'email': email,
                'phone': phone,
                'website': llm_result.get('website') or self._extract_homepage(page_url),
                'location': llm_result.get('address'),
                'owner': llm_result.get('owner'),
                'organization_type': self._infer_org_type(page_url),
                'parent_organization': self._infer_parent_org(page_url),
                'division': self._infer_division(page_url),
                'source_url': page_url,
                'extraction_method': 'llm',
                'source': 'list_crawl',
                'confidence_score': float(llm_result.get('confidence_score') or 0.7),
                'llm_validity_score': float(llm_score or 0.0),
                'email_type': email_type,
            }

            if self.enable_validation:
                contact.update(validate_email_record(email, llm_score=llm_score, email_type=email_type))

            contacts.append(contact)

        # Keep non-email records for directories where phone/address are published but email is absent.
        if not contacts and any(
            llm_result.get(field)
            for field in ('name', 'address', 'website', 'owner')
        ):
            contact = {
                'name': llm_result.get('name') or self._infer_parent_org(page_url),
                'email': None,
                'phone': phone,
                'website': llm_result.get('website') or self._extract_homepage(page_url),
                'location': llm_result.get('address'),
                'owner': llm_result.get('owner'),
                'organization_type': self._infer_org_type(page_url),
                'parent_organization': self._infer_parent_org(page_url),
                'division': self._infer_division(page_url),
                'source_url': page_url,
                'extraction_method': 'llm_no_email',
                'source': 'list_crawl',
                'confidence_score': float(llm_result.get('confidence_score') or 0.5),
            }
            contacts.append(contact)

        return contacts

    def _discover_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        links = []
        contact_like_links = []
        facility_like_links = []
        for a in soup.find_all('a', href=True):
            href = (a.get('href') or '').strip()
            if not href:
                continue
            absolute = self._normalize_url(urljoin(base_url, href))
            if not absolute or not self._is_allowed_url(absolute):
                continue

            text = self._clean_text(a.get_text(' ', strip=True)).lower()
            if self._looks_like_contact_page(absolute, text):
                contact_like_links.append(absolute)
                continue

            if self.is_nursing_home_mode:
                probe = f"{absolute} {text}".lower()
                if any(k in probe for k in FACILITY_KEYWORDS):
                    facility_like_links.append(absolute)
                    continue

            if self._same_registered_domain(base_url, absolute) and self._is_relevant_path(absolute):
                links.append(absolute)

            if self.is_nursing_home_mode and self._same_registered_domain(base_url, absolute):
                # For directory-heavy nursing home sources, allow broader same-domain discovery.
                links.append(absolute)

        # Keep order while deduping.
        seen = set()
        out = []
        limit = 80 if self.is_nursing_home_mode else 35
        for link in contact_like_links + facility_like_links + links:
            if link in seen:
                continue
            seen.add(link)
            out.append(link)
            if len(out) >= limit:
                break
        return out

    def _fetch(self, url: str) -> Optional[str]:
        try:
            resp = self.http.get(url, timeout=20, allow_redirects=True)
            if resp.status_code >= 400:
                return None
            ctype = (resp.headers.get('Content-Type') or '').lower()
            if 'text/html' not in ctype and 'application/xhtml+xml' not in ctype:
                return None
            return resp.text
        except Exception:
            return None

    def _is_allowed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False
        if parsed.fragment:
            return False

        lowered = url.lower()
        if any(lowered.endswith(ext) for ext in SKIP_EXTENSIONS):
            return False

        if not self.target_domains:
            return True

        host = (parsed.netloc or '').lower()
        host = host.split(':')[0]
        return any(host == d or host.endswith(f".{d}") for d in self.target_domains)

    @staticmethod
    def _normalize_url(url: str) -> Optional[str]:
        if not url:
            return None
        try:
            parsed = urlparse(url.strip())
            if not parsed.scheme:
                return None
            netloc = parsed.netloc.lower()
            path = parsed.path or '/'
            if path != '/' and path.endswith('/'):
                path = path[:-1]
            return f"{parsed.scheme.lower()}://{netloc}{path}"
        except Exception:
            return None

    @staticmethod
    def _normalize_email(email: str) -> Optional[str]:
        if not email:
            return None
        email = unescape(email).strip().lower()
        email = email.replace(' [at] ', '@').replace('(at)', '@').replace('[at]', '@')
        email = email.replace(' [dot] ', '.').replace('(dot)', '.').replace('[dot]', '.')
        email = email.replace(' ', '')
        if not EMAIL_RE.fullmatch(email):
            return None
        return email

    @staticmethod
    def _extract_homepage(page_url: str) -> str:
        parsed = urlparse(page_url)
        return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else page_url

    @staticmethod
    def _clean_text(value: Optional[str]) -> str:
        return re.sub(r"\s+", " ", (value or "")).strip()

    @staticmethod
    def _page_text(soup: BeautifulSoup) -> str:
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(' ', strip=True)).strip()

    @staticmethod
    def _contact_signature(contact: Dict) -> str:
        email = (contact.get('email') or '').lower()
        phone = (contact.get('phone') or '').lower()
        website = (contact.get('website') or '').lower()
        source_url = (contact.get('source_url') or '').lower()
        name = (contact.get('name') or '').lower()
        return f"{email}|{phone}|{website}|{source_url}|{name}"

    def _guess_name_from_email(self, email: str) -> str:
        local = (email or '').split('@')[0]
        local = re.sub(r"[._-]+", " ", local)
        return local.title().strip() or 'Unknown'

    def _guess_name_from_context(self, text: str, email: str) -> str:
        if not text or not email:
            return self._guess_name_from_email(email)

        idx = text.lower().find(email.lower())
        if idx < 0:
            return self._guess_name_from_email(email)

        start = max(0, idx - 120)
        snippet = text[start:idx + len(email)]
        match = re.search(r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s*(?:<|\(|\[)?\s*" + re.escape(email), snippet)
        if match:
            return match.group(1)
        return self._guess_name_from_email(email)

    def _extract_phone_from_context(self, text: str, email: str) -> Optional[str]:
        if not text or not email:
            return None
        idx = text.lower().find(email.lower())
        if idx < 0:
            return None
        snippet = text[max(0, idx - 140): idx + 140]
        found = PHONE_RE.search(snippet)
        return found.group(0) if found else None

    def _extract_nearby_phone(self, node) -> Optional[str]:
        block = node.parent.get_text(' ', strip=True) if node and node.parent else ''
        found = PHONE_RE.search(block)
        return found.group(0) if found else None

    def _infer_org_type(self, page_url: str) -> str:
        host = (urlparse(page_url).netloc or '').lower()
        if host.endswith('.gov'):
            return 'government_agency'
        if host.endswith('.org'):
            return 'nonprofit'
        return 'organization'

    def _infer_parent_org(self, page_url: str) -> Optional[str]:
        host = (urlparse(page_url).netloc or '').lower()
        if not host:
            return None
        host = host.replace('www.', '')
        return host

    def _infer_division(self, page_url: str, soup: Optional[BeautifulSoup] = None) -> Optional[str]:
        path = (urlparse(page_url).path or '').strip('/').replace('-', ' ')
        if path:
            parts = [p for p in path.split('/') if p]
            if parts:
                return parts[-1].title()

        if soup is not None:
            h1 = soup.find('h1')
            if h1:
                title = self._clean_text(h1.get_text(' ', strip=True))
                if title:
                    return title[:200]
        return None

    def _looks_like_contact_page(self, url: str, text: str) -> bool:
        probe = f"{url} {text}".lower()
        return any(k in probe for k in CONTACT_KEYWORDS)

    @staticmethod
    def _is_relevant_path(url: str) -> bool:
        path = (urlparse(url).path or '').lower()
        if path in ('', '/'):
            return True
        return any(k in path for k in CONTACT_KEYWORDS)

    @staticmethod
    def _same_registered_domain(a: str, b: str) -> bool:
        host_a = (urlparse(a).netloc or '').lower().split(':')[0]
        host_b = (urlparse(b).netloc or '').lower().split(':')[0]
        if not host_a or not host_b:
            return False

        parts_a = host_a.split('.')
        parts_b = host_b.split('.')
        if len(parts_a) < 2 or len(parts_b) < 2:
            return host_a == host_b

        reg_a = '.'.join(parts_a[-2:])
        reg_b = '.'.join(parts_b[-2:])
        return reg_a == reg_b

    @staticmethod
    def _compile_patterns(patterns: List[str]) -> List[re.Pattern]:
        compiled = []
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                continue
        return compiled
