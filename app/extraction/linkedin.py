import re
from urllib.parse import urlparse, parse_qs, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import sync_playwright

from app.extraction.base_extractor import BaseExtractor
from app.extraction.email_scraper import EmailScraper
from app.utils.scraping import get_proxy_url, get_random_user_agent, human_delay, build_requests_session

class LinkedInExtractor(BaseExtractor):
    """Scrapes LinkedIn company profiles using Google Search and Playwright."""
    
    ENRICHMENT_WORKERS = 8
    FOREIGN_LOCATION_HINTS = {
        'india', 'west bengal', 'kolkata', 'bangladesh', 'pakistan',
        'nepal', 'sri lanka', 'nigeria', 'kenya', 'philippines',
        'indonesia', 'china', 'vietnam', 'thailand', 'uae', 'dubai',
        'saudi arabia',
    }

    def __init__(self, task_id, stop_event=None, ai_options=None):
        super().__init__(task_id, stop_event=stop_event, ai_options=ai_options)

    def extract(self):
        try:
            self.update_task_status('running')
            
            # Step 1: Use Google to find LinkedIn company pages for the keyword & location
            query = self._build_search_query()
            
            saved_info = []
            visited_urls = set()
            
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
                
                # Fetch URLs from Google Search - use adjusted target to account for filtering
                linkedin_urls = self._get_linkedin_urls_from_google(page, query, adjusted_target)
                
                if not linkedin_urls:
                    print(f"[LinkedIn] No company pages found for query: {query}")
                    self.update_task_status('completed', 0)
                    return []
                    
                # Step 2: Visit each LinkedIn URL directly to extract public details
                for url in linkedin_urls:
                    if self.should_stop or self._saved_count >= adjusted_target or self.has_reached_max_results():
                        break
                        
                    if url in visited_urls:
                        continue
                        
                    visited_urls.add(url)
                    
                    try:
                        biz = self._extract_public_profile(page, url)
                        if biz:
                            saved = self.save_business(biz)
                            if saved and hasattr(saved, 'id'):
                                saved_info.append({
                                    'business_id': saved.id,
                                    'website': biz.get('website'),
                                    'email': biz.get('email'),
                                })
                    except Exception as exc:
                        print(f"[LinkedIn] Error extracting profile {url}: {exc}")
                        
                    human_delay(1.5, 3.0) # Prevent rapid requests to LinkedIn
                    
                browser.close()

            if self.should_stop:
                self.update_task_status('stopped', self._saved_count)
                return saved_info
                
            if not saved_info:
                self.update_task_status('completed', 0)
                return []

            self.update_task_status('completed', self._saved_count)
            return saved_info
            
        except Exception as exc:
            self.update_task_status('failed')
            print(f"[LinkedIn] Failed: {exc}")
            return []

    def _get_linkedin_urls_from_google(self, page, query, max_results):
        """Perform a Google search to find linkedin.com/company/ URLs."""
        urls = []
        start = 0
        
        while len(urls) < max_results:
            if self.should_stop:
                return urls
                
            search_url = f"https://www.google.com/search?q={query}&start={start}"
            try:
                page.goto(search_url, timeout=30000, wait_until='domcontentloaded')
                human_delay(2.0, 3.5)
                
                # Dismiss consent if present
                for selector in ['button:has-text("Accept all")', 'button:has-text("Reject all")']:
                    try:
                        btn = page.query_selector(selector)
                        if btn and btn.is_visible():
                            btn.click()
                            human_delay(0.8, 1.4)
                            break
                    except Exception:
                        pass
                
                # Extract links
                links = page.query_selector_all('a')
                found_in_page = 0
                for link in links:
                    href = link.get_attribute('href') or ''
                    clean_url = self._extract_company_url_from_google_href(href)
                    if clean_url:
                        if clean_url not in urls:
                            urls.append(clean_url)
                            found_in_page += 1
                            
                if found_in_page == 0:
                    # No more results or blocked
                    break
                    
                start += 10
            except Exception as e:
                print(f"[LinkedIn] Google search error: {e}")
                break
                
        return urls[:max_results]

    def _extract_public_profile(self, page, url):
        """Scrape public details from a LinkedIn company page."""
        page.goto(url, wait_until='domcontentloaded')
        # Wait a bit for JS to render the public view
        human_delay(2.0, 3.5)
        
        # In a headless unauthenticated browser, LinkedIn shows a specific public layout.
        # We try to grab the fundamental details visible without login.
        
        website = None
        location = None
        email = None
        phone = None
        needs_website = self.wants_field('website') or self.wants_field('email') or self.wants_field('phone')
        
        try:
            # Extract basic info section
            desc_el = page.query_selector('[data-test-id="about-us__description"]')
            _ = desc_el.inner_text().strip() if desc_el else ""
            
            # The public view often has definitions <dt> and <dd>
            dts = page.query_selector_all('dt')
            dds = page.query_selector_all('dd')
            
            for i in range(min(len(dts), len(dds))):
                key = dts[i].inner_text().strip().lower()
                val = dds[i].inner_text().strip()
                
                if 'website' in key and needs_website:
                    # Clean out "View website" helper text
                    val = val.replace('View website', '').strip()
                    if val.startswith('http'):
                        website = val
                    else:
                        website = 'https://' + val
                elif self.wants_field('location') and ('headquarters' in key or 'location' in key):
                    location = val
                    
        except Exception as e:
            print(f"[LinkedIn] Error parsing profile DOM: {e}")

        if needs_website and not website:
            website = self._extract_external_website(page)
            
        profile_text = ''
        try:
            profile_text = (page.text_content('body') or '').lower()
        except Exception:
            profile_text = ''

        if not self._is_profile_geo_relevant(location, profile_text):
            return None

        if website and (self.wants_field('email') or self.wants_field('phone')):
            if self.wants_field('email'):
                try:
                    scraper = EmailScraper(timeout=6)
                    email = scraper.scrape_first_email(website)
                except Exception:
                    email = None
            if self.wants_field('phone'):
                phone = self._extract_phone_from_site(website)
            
        return {
            'website': website,
            'location': location,
            'email': email,
            'phone': phone,
            'source': 'linkedin',
        }

    def _is_profile_geo_relevant(self, extracted_location, profile_text):
        task_location = (self.task.location or '').lower()
        if not task_location:
            return True

        combined = ' '.join([
            (extracted_location or '').lower(),
            (profile_text or ''),
        ])

        wants_florida = ('florida' in task_location) or bool(re.search(r'\bfl\b', task_location))
        wants_us = wants_florida or ('united states' in task_location) or ('usa' in task_location) or bool(re.search(r'\bus\b', task_location))

        if wants_florida and not (('florida' in combined) or bool(re.search(r'\bfl\b', combined))):
            # If Florida requested but profile has no Florida signal and has foreign signal, drop it.
            if any(term in combined for term in self.FOREIGN_LOCATION_HINTS):
                return False

        if wants_us and any(term in combined for term in self.FOREIGN_LOCATION_HINTS):
            return False

        return True

    def _build_search_query(self):
        """Build a tighter Google query for US/location-specific LinkedIn company pages."""
        keyword = (self.task.keyword or '').strip()
        location = (self.task.location or '').strip()
        location_upper = location.upper()
        us_hint = ''
        if ('USA' in location_upper) or ('UNITED STATES' in location_upper) or (' FL' in f' {location_upper}') or ('FLORIDA' in location_upper):
            us_hint = ' "United States"'
        return f'site:linkedin.com/company "{keyword}" "{location}"{us_hint}'

    @staticmethod
    def _extract_company_url_from_google_href(href):
        """Extract and normalize linkedin company URL from Google result href."""
        if not href:
            return None

        candidate = href.strip()
        if candidate.startswith('/url?'):
            try:
                qs = parse_qs(urlparse(candidate).query)
                target = qs.get('q', [None])[0]
                if target:
                    candidate = unquote(target)
            except Exception:
                return None

        parsed = urlparse(candidate)
        host = (parsed.netloc or '').lower()
        path = (parsed.path or '').lower()
        if 'linkedin.com' not in host:
            return None
        if not path.startswith('/company/'):
            return None

        clean = f"{parsed.scheme or 'https'}://{parsed.netloc}{parsed.path}".rstrip('/')
        return clean

    @staticmethod
    def _extract_external_website(page):
        """Try to find company external website from any visible anchor on the page."""
        try:
            anchors = page.query_selector_all('a[href]')
            for a in anchors:
                href = (a.get_attribute('href') or '').strip()
                if not href.startswith('http'):
                    continue
                lower = href.lower()
                if 'linkedin.com' in lower or 'lnkd.in' in lower:
                    continue
                if any(skip in lower for skip in ('google.com', 'youtube.com', 'facebook.com', 'instagram.com', 'x.com', 'twitter.com')):
                    continue
                return href
        except Exception:
            return None
        return None

    def _enrich_parallel(self, saved_info):
        """Scrape company websites for email using ThreadPool."""
        from app.models import Business, db
        from flask import current_app

        ai_enabled = current_app.config.get('AI_EXTRACTION_ENABLED', False)

        if ai_enabled:
            to_enrich = [b for b in saved_info if b.get('website')]
        else:
            to_enrich = [b for b in saved_info if b.get('website') and not b.get('email')]
        if not to_enrich:
            return

        workers = min(self.ENRICHMENT_WORKERS, 2) if ai_enabled else self.ENRICHMENT_WORKERS
        print(f"[LinkedIn] Enriching {len(to_enrich)} companies (AI={ai_enabled})...")

        def _scrape_one(info):
            if self.should_stop:
                return None
                
            email = None
            phone = None
            ai_data = None
            website = info.get('website')
            if website:
                try:
                    if ai_enabled:
                        from app.ai.pipeline import extract_from_url
                        ai_data = extract_from_url(website, **self.get_ai_options())
                        if ai_data and ai_data.get('emails'):
                            email = ai_data['emails'][0]
                        if ai_data and ai_data.get('phones'):
                            phone = ai_data['phones'][0]
                    else:
                        scraper = EmailScraper(timeout=6)
                        email = scraper.scrape_first_email(website)
                except Exception:
                    pass
                if not email:
                    try:
                        scraper = EmailScraper(timeout=6)
                        email = scraper.scrape_first_email(website)
                    except Exception:
                        pass
                if not phone:
                    phone = self._extract_phone_from_site(website)

            return {
                'business_id': info['business_id'],
                'email': email,
                'phone': phone,
                'ai_data': ai_data,
            }

        results = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_scrape_one, b): b for b in to_enrich}
            for fut in as_completed(futures):
                if self.should_stop:
                    break
                try:
                    res = fut.result(timeout=30)
                    if res:
                        results.append(res)
                except Exception as e:
                    pass

        # Apply to DB
        updated = 0
        for r in results:
            bid = r.get('business_id')
            if not bid: continue
            try:
                biz = Business.query.get(bid)
                if not biz: continue
                
                if r.get('email') and not biz.email:
                    validated = self.validate_business_data({'email': r['email']})
                    if validated.get('email'):
                        biz.email = validated['email']
                        updated += 1
                if r.get('phone') and not biz.phone:
                    validated = self.validate_business_data({'phone': r['phone']})
                    if validated.get('phone'):
                        biz.phone = validated['phone']
                        updated += 1

                ai_data = r.get('ai_data')
                if ai_data:
                    new_data = {}
                    if ai_data.get('name'):
                        new_data['name'] = ai_data['name']
                    if ai_data.get('social_links'):
                        new_data['social_links'] = ai_data['social_links']
                    if ai_data.get('confidence_score'):
                        new_data['confidence_score'] = ai_data['confidence_score']
                    if new_data:
                        self._update_business(biz, new_data)
            except Exception:
                pass

        if updated:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

    @staticmethod
    def _extract_phone_from_site(website_url):
        if not website_url:
            return None
        try:
            session = build_requests_session(get_proxy_url())
            resp = session.get(website_url, timeout=8)
            if resp.status_code != 200:
                return None
            match = re.search(r'(\+?\d[\d\s\-\(\)]{6,}\d)', resp.text)
            if match:
                return match.group(1)
        except Exception:
            return None
        return None
