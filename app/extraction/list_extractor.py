"""
ListExtractor — Crawl seed URLs from US_LIST_TYPES and extract business data.

Uses SmartCrawler for JS-rendered page fetching, LLMExtractor for
structured extraction, and the email validation pipeline for quality scoring.
"""

import traceback
import threading
from typing import Optional
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

from app.extraction.base_extractor import BaseExtractor
from app.extraction.email_scraper import EmailScraper
from app.extraction.us_list_types import get_list_type_config


class ListExtractor(BaseExtractor):
    """Extract business data from curated seed URL lists."""

    def __init__(
        self,
        task_id: int,
        list_type: str,
        stop_event: Optional[threading.Event] = None,
        ai_options: Optional[dict] = None,
    ):
        super().__init__(task_id, stop_event=stop_event, ai_options=ai_options)
        self.list_type = list_type
        self.config = get_list_type_config(list_type)
        if not self.config:
            raise ValueError(f"Unknown list type: {list_type}")

    def extract(self):
        """Main extraction entry point."""
        configured_extractor = self.config.get('extractor_class')
        if configured_extractor and configured_extractor is not ListExtractor:
            extractor = configured_extractor(
                task_id=self.task_id,
                seed_urls=self.config.get('seed_urls', []),
                target_domains=self.config.get('target_domains', []),
                follow_links=self.config.get('follow_links', True),
                max_depth=self.config.get('max_depth', 2),
                max_pages=self.config.get('max_pages', 40),
                email_patterns=self.config.get('email_patterns', []),
                stop_event=self.stop_event,
                ai_options=self.ai_options,
            )
            extractor.extract()
            return

        self.update_task_status('running')

        try:
            from app.ai.smart_crawler import SmartCrawler
            from app.ai.llm_extractor import LLMExtractor
        except ImportError as e:
            print(f"[ListExtractor] Missing dependency: {e}")
            self.update_task_status('failed')
            return

        seed_urls = self.config.get('seed_urls', [])
        if not seed_urls:
            print(f"[ListExtractor] No seed URLs for list type: {self.list_type}")
            self.update_task_status('completed', 0)
            return

        follow_links = self.config.get('follow_links', False)
        max_pages = self.config.get('max_pages', 20)

        crawler = SmartCrawler(timeout=30, verbose=False)
        llm = LLMExtractor(
            provider=self.ai_options.get('provider'),
            primary_model=self.ai_options.get('primary_model'),
            fallback_model=self.ai_options.get('fallback_model'),
        )

        pages_crawled = 0
        visited_urls = set()

        for seed_url in seed_urls:
            if self.should_stop:
                break

            urls_to_visit = [seed_url]

            while urls_to_visit and pages_crawled < max_pages:
                if self.should_stop:
                    break

                url = urls_to_visit.pop(0)
                if url in visited_urls:
                    continue
                visited_urls.add(url)

                try:
                    # Crawl the page
                    if follow_links:
                        results = crawler.crawl_website(url)
                    else:
                        results = [crawler.crawl_url(url)]

                    for crawl_result in results:
                        if self.should_stop:
                            break
                        if not crawl_result.success or not crawl_result.markdown_content:
                            continue

                        pages_crawled += 1

                        # Extract business data via LLM
                        extracted = llm.extract(crawl_result.markdown_content)
                        if not extracted:
                            continue

                        # Build business record from extraction
                        business_data = self._build_business_data(extracted)
                        if business_data:
                            self.save_business(business_data)

                        # Queue discovered links if following
                        if follow_links and crawl_result.links:
                            for link in crawl_result.links:
                                if link not in visited_urls and link.startswith('http'):
                                    urls_to_visit.append(link)

                except Exception as exc:
                    print(f"[ListExtractor] Error crawling {url}: {exc}")
                    traceback.print_exc()
                    continue

        self._supplement_from_web_search()

        final_status = 'stopped' if self.should_stop else 'completed'
        self.update_task_status(final_status)

    def _supplement_from_web_search(self):
        """Supplement sparse list-crawl output with search-driven contact harvesting."""
        target = int(self.task.max_results or 50)
        if self._saved_count >= target:
            return

        try:
            session = requests.Session()
            scraper = EmailScraper(timeout=8)
            links = self._collect_candidate_links(session, limit=target * 4)

            for link in links:
                if self.should_stop or self._saved_count >= target:
                    break

                try:
                    details = scraper.scrape_contact_details(link)
                except Exception:
                    details = None

                if not details:
                    continue

                email = details.get('email')
                phone = details.get('phone')
                linkedin = details.get('linkedin')

                if not email and not phone:
                    continue

                host = urlparse(link).netloc.replace('www.', '')
                name = host.split('.')[0].replace('-', ' ').replace('_', ' ').title() if host else None

                data = {
                    'name': name,
                    'email': email,
                    'phone': phone,
                    'website': link,
                    'location': self.task.location,
                    'social_links': {'linkedin': linkedin} if linkedin else {},
                    'source': 'list_crawl',
                    'confidence_score': 0.62,
                }
                self.save_business(data)
        except Exception as exc:
            print(f"[ListExtractor] Search supplement failed: {exc}")

    def _collect_candidate_links(self, session, limit=200):
        """Collect website candidates from multiple search templates."""
        queries = self._build_search_queries()
        links = []
        seen = set()

        for query in queries:
            if self.should_stop:
                break

            search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            try:
                resp = session.get(search_url, timeout=20)
                if resp.status_code != 200:
                    continue
            except Exception:
                continue

            soup = BeautifulSoup(resp.text, 'lxml')
            for a in soup.select('a.result__a, a[href]'):
                href = (a.get('href') or '').strip()
                if not href.startswith('http'):
                    continue
                if any(bad in href for bad in ('duckduckgo.com', 'google.com', 'youtube.com', 'facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'linkedin.com')):
                    continue

                base = href.split('?')[0].rstrip('/')
                if base in seen:
                    continue
                seen.add(base)
                links.append(base)
                if len(links) >= limit:
                    return links

        return links

    def _build_search_queries(self):
        """Generate robust query templates for any list type."""
        base = self.config.get('search_query') or self.list_type
        location = (self.task.location or '').strip()
        keyword = (self.task.keyword or '').strip()

        parts = [p for p in [base, keyword, location] if p]
        core = ' '.join(parts)

        templates = [
            f"{core} email phone contact",
            f"{core} directory",
            f"{core} staff",
            f"{core} about us",
            f"{core} get in touch",
            f"{core} official website",
        ]

        # Keep unique order.
        uniq = []
        seen = set()
        for q in templates:
            normalized = q.strip().lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            uniq.append(q)
        return uniq

    def _build_business_data(self, extracted: dict) -> Optional[dict]:
        """Convert LLM extraction output to business save format."""
        emails = extracted.get('emails', [])
        email = emails[0] if emails else None
        email_details = extracted.get('email_details', [])

        # Get first email's details if available
        llm_validity_score = None
        email_type = None
        if email_details:
            first_detail = email_details[0] if isinstance(email_details, list) else {}
            llm_validity_score = first_detail.get('validity_score')
            email_type = first_detail.get('email_type')

        phones = extracted.get('phones', [])
        phone = phones[0] if phones else None

        data = {
            'name': extracted.get('name'),
            'email': email,
            'phone': phone,
            'website': extracted.get('website'),
            'location': extracted.get('address'),
            'owner': extracted.get('owner'),
            'social_links': extracted.get('social_links', {}),
            'confidence_score': extracted.get('confidence_score', 0.0),
            'source': 'list_crawl',
        }

        # Attach LLM email validation fields
        if llm_validity_score is not None:
            data['llm_validity_score'] = llm_validity_score
        if email_type:
            data['email_type'] = email_type

        # Only save if there's meaningful data
        if not any(data.get(f) for f in ('name', 'email', 'phone', 'website')):
            return None

        return data
