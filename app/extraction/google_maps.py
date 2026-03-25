"""
Playwright-based Google Maps scraper.

It focuses on fast listing extraction and completes the task as soon as
scraping finishes (or max_results is reached), without a long post-scrape
enrichment phase.

No Google Places API key required.
"""

import re
import traceback
import requests
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import sync_playwright

from app.extraction.base_extractor import BaseExtractor
from app.extraction.email_scraper import EmailScraper
from app.utils.helpers import extract_email_from_html
from app.utils.scraping import get_proxy_url, get_random_user_agent, human_delay, human_scroll_playwright


class GoogleMapsExtractor(BaseExtractor):
    """Scrapes Google Maps search results using Playwright (Firefox)."""

    # CSS selectors -------------------------------------------------
    SEL_PLACE_LINK = 'a[href*="/maps/place/"]'
    SEL_FEED = 'div[role="feed"]'
    SEL_ADDRESS = 'button[data-item-id="address"]'
    SEL_PHONE = 'button[data-item-id^="phone:tel:"]'
    SEL_WEBSITE = 'a[data-item-id="authority"]'
    SEL_BACK = 'button[aria-label="Back"]'

    # Extended detail selectors -------------------------------------
    SEL_NAME = 'h1'
    SEL_RATING = 'div.F7nice span[aria-hidden="true"]'
    SEL_REVIEW_COUNT = 'div.F7nice span[aria-label*="review"]'
    SEL_CATEGORY = 'button[jsaction*="category"]'
    SEL_PRICE = 'span[aria-label*="Price"]'
    SEL_HOURS_TABLE = 'table.eK4R0e'
    SEL_HOURS_ROW = 'table.eK4R0e tr'

    # US state abbreviations for smart query formatting
    US_STATES = {
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
        'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
        'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
        'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
        'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
        'DC',
    }

    # Timing --------------------------------------------------------
    SCROLL_PAUSE = 2.0
    DETAIL_PAUSE = 2.5
    MAX_STALE_SCROLLS = 5      # give up scrolling after N fruitless attempts

    # Multi-threading -----------------------------------------------
    ENRICHMENT_WORKERS = 8     # parallel threads for email/LinkedIn

    def __init__(self, task_id, stop_event=None, ai_options=None):
        super().__init__(task_id, stop_event=stop_event, ai_options=ai_options)

    # ==================================================================
    #  US-optimized search query
    # ==================================================================
    def _build_search_query(self):
        """Build a search query, appending 'United States' if no US state found."""
        keyword = self.task.keyword
        location = self.task.location
        loc_upper = location.upper().replace(',', ' ')
        loc_words = loc_upper.split()
        has_us_state = any(w in self.US_STATES for w in loc_words)
        has_us_keyword = any(
            term in loc_upper
            for term in ['UNITED STATES', 'USA', 'U.S.', 'US']
        )
        if has_us_state or has_us_keyword:
            return f"{keyword} in {location}"
        return f"{keyword} in {location}, United States"

    # ==================================================================
    #  Main entry point
    # ==================================================================
    def extract(self):
        """Scrape Google Maps listings and finish immediately after scraping."""
        try:
            self.update_task_status('running')
            query = self._build_search_query()
            search_url = (
                "https://www.google.com/maps/search/"
                + requests.utils.quote(query)
            )
            print(f"[GoogleMaps] Starting: {query}")
            print(f"[GoogleMaps] Max results: {self.task.max_results}")

            # ── Phase 1: Scrape listings with Playwright ─────────────
            saved_info = []  # [{business_id, website, email}]

            # Account for filtering losses: extractors typically lose ~40-50% of records
            # to location relevance, field selection, and deduplication checks.
            # Request 1.5x more to account for these filters.
            FILTER_BUFFER = 1.5
            adjusted_target = max(self.task.max_results, int(self.task.max_results * FILTER_BUFFER))

            with sync_playwright() as pw:
                proxy = get_proxy_url()
                browser = pw.chromium.launch(
                    headless=True,
                    proxy={'server': proxy} if proxy else None
                )
                ctx = browser.new_context(
                    viewport={'width': 1280, 'height': 900},
                    locale='en-US',
                    user_agent=get_random_user_agent(),
                )
                page = ctx.new_page()
                page.set_default_timeout(15000)

                page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
                human_delay(2.5, 4.0)
                self._dismiss_consent(page)

                # Pre-scroll to load a big batch of results
                self._initial_scroll(page, adjusted_target)

                # Click-extract loop with visited-href tracking
                visited = set()          # normalised hrefs we've already clicked
                no_new_rounds = 0

                while self._saved_count < adjusted_target:
                    if self.should_stop:
                        print(f"[GoogleMaps] Stopped by user at count {self._saved_count}")
                        self.update_task_status('stopped', self._saved_count)
                        browser.close()
                        return saved_info

                    links = page.query_selector_all(self.SEL_PLACE_LINK)

                    # Find the next unvisited link
                    target = None
                    for lnk in links:
                        href = self._norm_href(lnk.get_attribute('href'))
                        if href and href not in visited:
                            target = lnk
                            visited.add(href)
                            break

                    if target is None:
                        # All visible links already visited → try loading more
                        if self._scroll_for_more(page, len(links)):
                            no_new_rounds = 0
                            continue
                        no_new_rounds += 1
                        if no_new_rounds >= 2:
                            print(f"[GoogleMaps] No more results available")
                            break
                        continue

                    no_new_rounds = 0
                    try:
                        biz = self._click_and_extract(page, target)
                        if biz:
                            saved = self.save_business(biz)
                            if saved and hasattr(saved, 'id'):
                                saved_info.append({
                                    'business_id': saved.id,
                                    'website': biz.get('website'),
                                    'email': biz.get('email'),
                                })
                                print(f"  [{self._saved_count}/{self.task.max_results}] "
                                      f"{biz.get('name') or biz.get('website') or 'listing'}")
                    except Exception as exc:
                        print(f"  Extraction error: {exc}")
                        # Try to recover navigation
                        self._go_back(page)

                browser.close()

            if self.should_stop:
                self.update_task_status('stopped', self._saved_count)
                return saved_info

            if not saved_info:
                self.update_task_status('completed', 0)
                return []

            self.update_task_status('completed', self._saved_count)
            print(f"[GoogleMaps] Complete – {self._saved_count} businesses saved (requested: {self.task.max_results})")
            return saved_info

        except Exception as exc:
            self.update_task_status('failed')
            print(f"[GoogleMaps] Failed: {exc}")
            traceback.print_exc()
            return []

    # ==================================================================
    #  Scrolling
    # ==================================================================
    def _initial_scroll(self, page, adjusted_target=None):
        """Pre-scroll the results feed to load a large batch."""
        feed = page.query_selector(self.SEL_FEED)
        if not feed:
            return

        human_scroll_playwright(page, self.SEL_FEED, steps=4)

        # Use adjusted target if provided (accounting for filtering), otherwise default
        if adjusted_target is None:
            adjusted_target = self.task.max_results + 10
        else:
            adjusted_target = adjusted_target + 10  # Small buffer

        prev = 0
        stale = 0

        for _ in range(60):                      # safety cap
            feed.evaluate('el => el.scrollTop = el.scrollHeight')
            human_delay(1.5, 2.8)

            links = page.query_selector_all(self.SEL_PLACE_LINK)
            count = len(links)

            if count >= adjusted_target:
                print(f"[GoogleMaps] Pre-loaded {count} listings (target: {adjusted_target})")
                return

            if count == prev:
                stale += 1
                if stale >= self.MAX_STALE_SCROLLS:
                    # Last-resort nudge: scroll last element into view
                    if links:
                        try:
                            links[-1].scroll_into_view_if_needed()
                            human_delay(0.8, 1.4)
                            feed.evaluate('el => el.scrollTop = el.scrollHeight')
                            human_delay(1.5, 2.8)
                            new_c = len(page.query_selector_all(self.SEL_PLACE_LINK))
                            if new_c > count:
                                stale = 0
                                prev = new_c
                                continue
                        except Exception:
                            pass
                    print(f"[GoogleMaps] Pre-scroll done at {count} listings")
                    return
            else:
                stale = 0

            prev = count

    def _scroll_for_more(self, page, current_link_count):
        """Try to load more results beyond what we already see.
        Returns True if new links appeared."""
        feed = page.query_selector(self.SEL_FEED)
        if not feed:
            return False

        # Nudge: scroll last link into view first
        links = page.query_selector_all(self.SEL_PLACE_LINK)
        if links:
            try:
                links[-1].scroll_into_view_if_needed()
                human_delay(0.4, 0.9)
            except Exception:
                pass

        for _ in range(3):
            feed.evaluate('el => el.scrollTop = el.scrollHeight')
            human_delay(1.5, 2.8)
            new_count = len(page.query_selector_all(self.SEL_PLACE_LINK))
            if new_count > current_link_count:
                return True

        return False

    # ==================================================================
    #  Detail extraction (Phase 1) — ENHANCED
    # ==================================================================
    def _click_and_extract(self, page, link):
        """Click a result link, scrape the detail panel, navigate back."""
        href = link.get_attribute('href') or ''

        try:
            link.scroll_into_view_if_needed()
            human_delay(0.2, 0.6)
        except Exception:
            pass

        link.click()
        human_delay(1.8, 3.0)

        # ── Business Name ──────────────────────────────────────────
        name = None
        if self.wants_field('name'):
            name = self._get_text(page, self.SEL_NAME)

        # ── Address ────────────────────────────────────────────────
        address = None
        if self.wants_field('location'):
            address = self._strip_prefix(
                self._get_aria(page, self.SEL_ADDRESS), 'Address:')

        # ── Phone ──────────────────────────────────────────────────
        phone = None
        if self.wants_field('phone'):
            phone = self._strip_prefix(
                self._get_aria(page, self.SEL_PHONE), 'Phone:')

        # ── Website ────────────────────────────────────────────────
        website = None
        if self.wants_field('website') or self.wants_field('email'):
            website_el = page.query_selector(self.SEL_WEBSITE)
            website = website_el.get_attribute('href') if website_el else None

        # ── Email (from website) ───────────────────────────────────
        email = None
        if website and self.wants_field('email'):
            email = self._extract_email_from_website(page.context, website)

        # ── Rating & Review Count ──────────────────────────────────
        # (removed per user request)

        # ── Categories ─────────────────────────────────────────────
        # (removed per user request)

        # ── Price Level ────────────────────────────────────────────
        # (removed per user request)

        # ── Hours ──────────────────────────────────────────────────
        # (removed per user request)

        self._go_back(page)

        return {
            'name': name,
            'location': address,
            'phone': phone,
            'website': website,
            'email': email,
            'source': 'google_maps',
        }


    # ==================================================================
    #  Multi-threaded enrichment (Phase 2)
    # ==================================================================
    def _enrich_parallel(self, saved_info):
        """Scrape websites for email + AI fields using a thread pool,
        then bulk-update DB records in the main thread."""
        from app.models import Business, db
        from flask import current_app

        # Enrich ALL webxsites if AI is enabled, to get descriptions/socials
        ai_enabled = current_app.config.get('AI_EXTRACTION_ENABLED', False)
        
        if ai_enabled:
            to_enrich = [b for b in saved_info if b.get('website')]
        else:
            to_enrich = [b for b in saved_info if b.get('website') and not b.get('email')]
            
        if not to_enrich:
            return

        # Use fewer workers for AI to avoid RAM overload from headless browsers
        workers = min(self.ENRICHMENT_WORKERS, 2) if ai_enabled else self.ENRICHMENT_WORKERS

        print(f"[GoogleMaps] Enriching {len(to_enrich)} websites "
              f"({workers} threads, AI={ai_enabled})...")

        def _scrape_one(info):
            """HTTP-only work — thread-safe, no Flask/DB required."""
            if self.should_stop:
                return None
                
            website = info.get('website')
            ai_data = None
            email = None

            if website:
                try:
                    if ai_enabled:
                        from app.ai.pipeline import extract_from_url
                        ai_data = extract_from_url(website, **self.get_ai_options())
                        if ai_data and ai_data.get('emails'):
                            email = ai_data['emails'][0]
                    else:
                        scraper = EmailScraper(timeout=6)
                        email = scraper.scrape_first_email(website)
                except Exception as e:
                    print(f"  Enrichment error for {website}: {e}")

                # Keep enrichment resilient even when AI returns partial results.
                if not email:
                    try:
                        scraper = EmailScraper(timeout=6)
                        email = scraper.scrape_first_email(website)
                    except Exception:
                        pass

            return {
                'business_id': info['business_id'],
                'email': email,
                'ai_data': ai_data
            }

        # Run scraping in parallel
        results = []
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_scrape_one, b): b for b in to_enrich}
            for fut in as_completed(futures):
                if self.should_stop:
                    print("[GoogleMaps] Cancelling enrichment pool...")
                    break
                try:
                    res = fut.result(timeout=60)
                    if res:
                        results.append(res)
                except Exception as e:
                    pass

        # Apply to DB in main thread
        updated = 0
        for r in results:
            bid = r.get('business_id')
            if not bid:
                continue
            try:
                biz = Business.query.get(bid)
                if not biz:
                    continue
                
                new_data = {}
                if r.get('email'):
                    new_data['email'] = r['email']
                
                ai_data = r.get('ai_data')
                if ai_data:
                    if ai_data.get('name'): new_data['name'] = ai_data['name']
                    if ai_data.get('social_links'): new_data['social_links'] = ai_data['social_links']
                    if ai_data.get('confidence_score'): new_data['confidence_score'] = ai_data['confidence_score']
                    if ai_data.get('phones'): new_data['phone'] = ai_data['phones'][0]

                if new_data:
                    self._update_business(biz, new_data)
                    updated += 1
            except Exception as e:
                pass

        print(f"[GoogleMaps] Updated {updated}/{len(to_enrich)} records with enrichment")

    # ==================================================================
    #  Consent / cookie dialog
    # ==================================================================
    @staticmethod
    def _dismiss_consent(page):
        """Try to dismiss Google's cookie-consent or sign-in overlays."""
        for selector in [
            'button:has-text("Accept all")',
            'button:has-text("Reject all")',
            'button[aria-label="Accept all"]',
            'form[action*="consent"] button',
        ]:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    human_delay(0.8, 1.4)
                    return
            except Exception:
                continue

    # ==================================================================
    #  Navigation
    # ==================================================================
    def _go_back(self, page):
        """Navigate back from the detail panel to the results list."""
        back_btn = page.query_selector(self.SEL_BACK)
        if back_btn:
            try:
                back_btn.click()
                human_delay(1.0, 1.8)
                return
            except Exception:
                pass
        page.go_back()
        human_delay(1.0, 1.8)

    # ==================================================================
    #  DOM helpers
    # ==================================================================
    @staticmethod
    def _get_text(page, selector):
        el = page.query_selector(selector)
        if el:
            txt = el.inner_text().strip()
            return txt if txt else None
        return None

    @staticmethod
    def _get_aria(page, selector):
        el = page.query_selector(selector)
        if el:
            lbl = (el.get_attribute('aria-label') or '').strip()
            return lbl if lbl else None
        return None

    @staticmethod
    def _strip_prefix(text, prefix):
        if not text:
            return None
        text = text.strip()
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
        return text or None

    @staticmethod
    def _norm_href(href):
        """Normalise a place link href for dedup (strip query params)."""
        if not href:
            return None
        return href.split('?')[0]

    # ==================================================================
    #  Parsing helpers
    # ==================================================================
    @staticmethod
    def _extract_place_id(href):
        if not href:
            return None
        m = re.search(r'/maps/place/([^/]+)', href)
        if m:
            return unquote(m.group(1))[:200]
        return None

    # ==================================================================
    #  Website email discovery
    # ==================================================================
    @staticmethod
    def _extract_email_from_website(ctx, website_url):
        # Prefer contact-page aware scraping first.
        try:
            scraper = EmailScraper(timeout=8)
            email = scraper.scrape_emails(website_url)
            if email:
                return email
        except Exception:
            pass

        page = None
        try:
            page = ctx.new_page()
            page.set_default_timeout(12000)
            page.goto(website_url, wait_until='domcontentloaded', timeout=20000)
            human_delay(1.0, 2.0)
            html = page.content()
            page.close()
            return extract_email_from_html(html)
        except Exception:
            try:
                if page:
                    page.close()
            except Exception:
                pass
            return None
