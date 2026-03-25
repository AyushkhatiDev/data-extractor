"""
Playwright-based Yelp scraper for US business data.

Extracts business data from Yelp search results including:
name, phone, website, location, email, and owner (from "About the Business").
"""

import re
import json
import traceback

import requests
from playwright.sync_api import sync_playwright

from app.extraction.base_extractor import BaseExtractor
from app.extraction.email_scraper import EmailScraper
from app.utils.scraping import get_proxy_url, get_random_user_agent, human_delay, human_scroll_playwright


class YelpExtractor(BaseExtractor):
    """Scrapes Yelp.com search results using Playwright."""

    # CSS selectors ---------------------------------------------------
    # Yelp search results page
    SEL_BIZ_LINK = 'a[href*="/biz/"]'
    SEL_BIZ_CARD = '[data-testid="serp-ia-card"]'
    SEL_NEXT_PAGE = 'a[aria-label="Next"], a.next-link, a[href*="start="]'

    # Yelp detail page
    SEL_BIZ_NAME = 'h1'
    SEL_RATING = '[aria-label*="star rating"]'
    SEL_REVIEW_COUNT = 'a[href*="#reviews"]'
    SEL_PHONE = 'p:has(> a[href^="tel:"])'
    SEL_PHONE_LINK = 'a[href^="tel:"]'
    SEL_WEBSITE = 'a[href*="/biz_redir"]'
    SEL_ADDRESS = 'address'
    SEL_CATEGORIES = 'span a[href*="/c/"]'
    SEL_PRICE = 'span.priceRange, span[class*="priceRange"]'
    SEL_HOURS_TABLE = 'table.hours-table, table[class*="hour"]'
    SEL_HOURS_ROW = 'table.hours-table tr, table[class*="hour"] tr'
    SEL_ABOUT_OWNER = '[class*="aboutBusiness"], [data-testid*="about"]'

    # Timing ----------------------------------------------------------
    SCROLL_PAUSE = 2.0
    DETAIL_PAUSE = 2.5

    def __init__(self, task_id, stop_event=None, ai_options=None):
        super().__init__(task_id, stop_event=stop_event, ai_options=ai_options)

    # ==================================================================
    #  Main entry point
    # ==================================================================
    def extract(self):
        """Scrape Yelp search results page by page."""
        try:
            self.update_task_status('running')
            keyword = self.task.keyword
            location = self.task.location

            print(f"[Yelp] Starting: '{keyword}' in '{location}'")
            print(f"[Yelp] Max results: {self.task.max_results}")

            # Account for filtering losses: extractors typically lose ~40-50% of records
            # to location relevance, field selection, and deduplication checks.
            # Request 1.5x more to account for these filters.
            FILTER_BUFFER = 1.5
            adjusted_target = max(self.task.max_results, int(self.task.max_results * FILTER_BUFFER))

            with sync_playwright() as pw:
                proxy = get_proxy_url()
                browser = pw.chromium.launch(
                    headless=True,
                    proxy={'server': proxy} if proxy else None,
                )
                ctx = browser.new_context(
                    viewport={'width': 1280, 'height': 900},
                    locale='en-US',
                    user_agent=get_random_user_agent(),
                )
                page = ctx.new_page()
                page.set_default_timeout(15000)

                start = 0
                page_num = 1

                while self._saved_count < adjusted_target:
                    if self.should_stop:
                        print(f"[Yelp] Stopped by user at {self._saved_count}")
                        break

                    search_url = self._build_search_url(keyword, location, start)
                    print(f"[Yelp] Page {page_num}: {search_url}")

                    try:
                        page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
                        human_delay(2.5, 4.0)
                    except Exception as exc:
                        print(f"[Yelp] Failed to load page {page_num}: {exc}")
                        break

                    # Collect business links from search results
                    biz_links = self._collect_business_links(page)
                    if not biz_links:
                        print(f"[Yelp] No results on page {page_num}")
                        break

                    print(f"[Yelp] Found {len(biz_links)} listings on page {page_num}")

                    for biz_url in biz_links:
                        if self.should_stop or self._saved_count >= adjusted_target:
                            break

                        try:
                            biz_data = self._extract_detail_page(ctx, biz_url)
                            if biz_data:
                                saved = self.save_business(biz_data)
                                if saved:
                                    print(f"  [{self._saved_count}/{self.task.max_results}] "
                                          f"{biz_data.get('name') or biz_url}")
                        except Exception as exc:
                            print(f"  [Yelp] Error extracting {biz_url}: {exc}")

                    # Next page
                    start += 10
                    page_num += 1

                    # Safety cap on pages
                    if page_num > 20:
                        print("[Yelp] Reached max page limit")
                        break

                browser.close()

            if self.should_stop:
                self.update_task_status('stopped', self._saved_count)
            else:
                self.update_task_status('completed', self._saved_count)

            print(f"[Yelp] Done — {self._saved_count} records saved")

        except Exception as exc:
            self.update_task_status('failed')
            print(f"[Yelp] Failed: {exc}")
            traceback.print_exc()

    # ==================================================================
    #  URL builders
    # ==================================================================
    @staticmethod
    def _build_search_url(keyword, location, start=0):
        """Build Yelp search URL."""
        from urllib.parse import quote_plus
        url = (
            f"https://www.yelp.com/search"
            f"?find_desc={quote_plus(keyword)}"
            f"&find_loc={quote_plus(location)}"
        )
        if start > 0:
            url += f"&start={start}"
        return url

    # ==================================================================
    #  Search results parsing
    # ==================================================================
    def _collect_business_links(self, page):
        """Collect unique /biz/ links from the search results page."""
        links = page.query_selector_all(self.SEL_BIZ_LINK)
        seen = set()
        result = []

        for link in links:
            href = link.get_attribute('href') or ''
            # Filter to actual business pages (not ads, categories, etc.)
            if '/biz/' not in href:
                continue
            # Normalize
            if href.startswith('/'):
                href = f"https://www.yelp.com{href}"
            # Strip query params for dedup
            base = href.split('?')[0]
            if base not in seen:
                seen.add(base)
                result.append(base)

        return result

    # ==================================================================
    #  Detail page extraction
    # ==================================================================
    def _extract_detail_page(self, ctx, biz_url):
        """Open a Yelp business page and extract all data."""
        detail_page = None
        try:
            detail_page = ctx.new_page()
            detail_page.set_default_timeout(12000)
            detail_page.goto(biz_url, timeout=20000, wait_until='domcontentloaded')
            human_delay(1.5, 2.5)

            data = {}

            # ── Name ──────────────────────────────────────────────
            if self.wants_field('name'):
                name_el = detail_page.query_selector(self.SEL_BIZ_NAME)
                if name_el:
                    data['name'] = name_el.inner_text().strip()

            # ── Phone ─────────────────────────────────────────────
            if self.wants_field('phone'):
                data['phone'] = self._extract_phone(detail_page)

            # ── Website ───────────────────────────────────────────
            website = None
            if self.wants_field('website') or self.wants_field('email'):
                website = self._extract_website(detail_page)
                data['website'] = website

            # ── Email (from website) ──────────────────────────────
            if website and self.wants_field('email'):
                try:
                    scraper = EmailScraper(timeout=6)
                    data['email'] = scraper.scrape_first_email(website)
                except Exception:
                    data['email'] = None

            # ── Location / Address ────────────────────────────────
            if self.wants_field('location'):
                data['location'] = self._extract_address(detail_page)

            # ── Owner ─────────────────────────────────────────────
            if self.wants_field('owner'):
                data['owner'] = self._extract_owner(detail_page)

            data['source'] = 'yelp'

            detail_page.close()
            return data

        except Exception as exc:
            if detail_page:
                try:
                    detail_page.close()
                except Exception:
                    pass
            raise exc

    # ==================================================================
    #  Individual field extractors
    # ==================================================================
    @staticmethod
    def _extract_phone(page):
        """Extract phone number."""
        try:
            phone_link = page.query_selector('a[href^="tel:"]')
            if phone_link:
                href = phone_link.get_attribute('href') or ''
                phone = href.replace('tel:', '').strip()
                if phone:
                    return phone
                return phone_link.inner_text().strip()

            sidebar = page.query_selector('[class*="sidebar"]')
            if sidebar:
                txt = sidebar.inner_text()
                phones = re.findall(
                    r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', txt
                )
                if phones:
                    return phones[0]
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_website(page):
        """Extract website URL from Yelp's redirect link."""
        try:
            link = page.query_selector('a[href*="/biz_redir"]')
            if link:
                href = link.get_attribute('href') or ''
                if 'url=' in href:
                    from urllib.parse import unquote, parse_qs, urlparse
                    parsed = urlparse(href)
                    params = parse_qs(parsed.query)
                    if 'url' in params:
                        return unquote(params['url'][0])
                txt = link.inner_text().strip()
                if txt and '.' in txt:
                    if not txt.startswith('http'):
                        txt = 'https://' + txt
                    return txt

            link2 = page.query_selector('a[aria-label*="website" i]')
            if link2:
                txt = link2.inner_text().strip()
                if txt and '.' in txt:
                    if not txt.startswith('http'):
                        txt = 'https://' + txt
                    return txt
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_address(page):
        """Extract business address."""
        try:
            addr_el = page.query_selector('address')
            if addr_el:
                return addr_el.inner_text().strip().replace('\n', ', ')

            addr_el2 = page.query_selector('[aria-label*="Map"], [class*="mapBox"]')
            if addr_el2:
                txt = addr_el2.inner_text().strip()
                lines = [l.strip() for l in txt.split('\n') if l.strip()]
                if lines:
                    return ', '.join(lines[:3])
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_owner(page):
        """Extract owner name from 'About the Business' section."""
        try:
            about_section = page.query_selector(
                '[class*="aboutBusiness"], '
                '[data-testid*="about"], '
                'section:has(h2:text-is("About the Business"))'
            )
            if about_section:
                text = about_section.inner_text()
                lines = text.split('\n')
                for i, line in enumerate(lines):
                    line_lower = line.lower().strip()
                    if any(kw in line_lower for kw in
                           ['business owner', 'owner', 'founder', 'proprietor']):
                        if ':' in line:
                            name = line.split(':', 1)[1].strip()
                            if name and len(name) < 80:
                                return name
                        for j in range(max(0, i - 1), min(len(lines), i + 3)):
                            candidate = lines[j].strip()
                            if (candidate and
                                    candidate.lower() not in
                                    ('business owner', 'owner', 'about the business') and
                                    len(candidate) < 60 and
                                    not candidate.startswith(('http', 'www'))):
                                if re.match(r'^[A-Z][a-zA-Z\s.\'-]{2,}$', candidate):
                                    return candidate
        except Exception:
            pass
        return None
