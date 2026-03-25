import re
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from app.extraction.base_extractor import BaseExtractor
from app.extraction.email_scraper import EmailScraper
from app.utils.scraping import build_requests_session, human_delay, get_proxy_url

class IndeedExtractor(BaseExtractor):
    """Scrapes company info from Indeed job listings."""
    
    ENRICHMENT_WORKERS = 8
    
    def __init__(self, task_id, stop_event=None, ai_options=None):
        super().__init__(task_id, stop_event=stop_event, ai_options=ai_options)
        self.session = build_requests_session(get_proxy_url())
        
    def extract(self):
        try:
            self.update_task_status('running')
            
            # Indeed job search parameters
            search_url = f"https://www.indeed.com/jobs?q={quote_plus(self.task.keyword)}&l={quote_plus(self.task.location)}"
            
            page_offset = 0
            saved_info = []
            
            while self._saved_count < self.task.max_results:
                if self.should_stop:
                    self.update_task_status('stopped', self._saved_count)
                    return
                    
                url = f"{search_url}&start={page_offset}"
                try:
                    resp = self.session.get(url, timeout=10)
                    if resp.status_code != 200:
                        print(f"[Indeed] Failed to fetch offset {page_offset}")
                        break
                        
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    
                    job_cards = soup.select('.job_seen_beacon, .result')
                    if not job_cards:
                        print(f"[Indeed] No job cards found at offset {page_offset}")
                        break
                        
                    added_in_page = 0
                    for card in job_cards:
                        if self.should_stop or self._saved_count >= self.task.max_results:
                            break
                            
                        # Location
                        location = None
                        if self.wants_field('location'):
                            loc_el = card.select_one('.companyLocation, [data-testid="text-location"]')
                            location = loc_el.text.strip() if loc_el else self.task.location
                        
                        # Job detail link (for company website if available)
                        job_link_el = card.select_one('a[data-jk], a[data-testid="job-title"]')
                        job_href = job_link_el['href'] if job_link_el and 'href' in job_link_el.attrs else None
                        if job_href and not job_href.startswith('http'):
                            job_href = f"https://www.indeed.com{job_href}"

                        needs_website = self.wants_field('website') or self.wants_field('email') or self.wants_field('phone')
                        website_url = self._extract_company_website(job_href) if needs_website else None
                        email, phone = None, None
                        if website_url:
                            if self.wants_field('email'):
                                try:
                                    scraper = EmailScraper(timeout=6)
                                    email = scraper.scrape_first_email(website_url)
                                except Exception:
                                    pass
                            if self.wants_field('phone'):
                                phone = self._extract_phone_from_site(website_url)
                        
                        biz_data = {
                            'email': email,
                            'phone': phone,
                            'website': website_url,
                            'location': location,
                            'source': 'indeed'
                        }
                        biz = self.save_business(biz_data)
                        if biz:
                            added_in_page += 1
                            saved_info.append({
                                'business_id': biz.id,
                                'website': website_url,
                                'email': email,
                            })
                                
                    if added_in_page == 0:
                        break
                        
                    page_offset += 10
                    human_delay(2.0, 4.0)
                    
                except Exception as e:
                    print(f"[Indeed] Error on offset {page_offset}: {e}")
                    break
                    
            if self.should_stop:
                self.update_task_status('stopped', self._saved_count)
                return

            self.update_task_status('completed', self._saved_count)
            return
                
        except Exception as e:
            print(f"[Indeed] Extraction failed: {e}")
            self.update_task_status('failed')
            
    def _enrich_parallel(self, saved_info):
        """Find emails/phone from company websites when missing"""
        from app.models import Business, db
        from flask import current_app

        ai_enabled = current_app.config.get('AI_EXTRACTION_ENABLED', False)

        if ai_enabled:
            # We want to enrich everyone if AI enabled (to get descriptions)
            to_enrich = saved_info.copy()
        else:
            # Otherwise just those needing email/phone
            to_enrich = saved_info.copy()  # It's already mostly website-only since indeed rarely gives email

        if not to_enrich:
            return

        workers = min(self.ENRICHMENT_WORKERS, 2) if ai_enabled else self.ENRICHMENT_WORKERS
        print(f"[Indeed] Enriching {len(to_enrich)} companies for emails... (AI={ai_enabled})")
            
        def _enrich_one(info):
            if self.should_stop:
                return None
            try:
                website_url = info.get('website')
                email = None
                phone = None
                ai_data = None
                if website_url:
                    if ai_enabled:
                        from app.ai.pipeline import extract_from_url
                        ai_data = extract_from_url(website_url, **self.get_ai_options())
                        if ai_data:
                            if ai_data.get('emails'): email = ai_data['emails'][0]
                            if ai_data.get('phones'): phone = ai_data['phones'][0]
                    else:
                        scraper = EmailScraper(timeout=6)
                        email = scraper.scrape_first_email(website_url)
                        phone = self._extract_phone_from_site(website_url)

                    if not email:
                        try:
                            scraper = EmailScraper(timeout=6)
                            email = scraper.scrape_first_email(website_url)
                        except Exception:
                            pass
                    if not phone:
                        phone = self._extract_phone_from_site(website_url)
                    
                return {
                    'business_id': info['business_id'],
                    'website': website_url,
                    'email': email,
                    'phone': phone,
                    'ai_data': ai_data
                }
            except Exception:
                return None
                
        # Run parallel
        results = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_enrich_one, b): b for b in to_enrich}
            for fut in as_completed(futures):
                if self.should_stop:
                    break
                try:
                    res = fut.result(timeout=60)
                    if res:
                        results.append(res)
                except Exception:
                    pass
                    
        # Apply to DB
        updated = 0
        for r in results:
            bid = r.get('business_id')
            if not bid: continue
            try:
                biz = Business.query.get(bid)
                if not biz: continue
                
                new_data = {}
                if r.get('website'):
                    validated_data = self.validate_business_data({'website': r['website']})
                    if validated_data.get('website') and (not biz.website or 'indeed.com' in biz.website):
                        new_data['website'] = validated_data['website']

                if r.get('email'): new_data['email'] = r['email']
                if r.get('phone'): new_data['phone'] = r['phone']

                ai_data = r.get('ai_data')
                if ai_data:
                    if ai_data.get('name'): new_data['name'] = ai_data['name']
                    if ai_data.get('social_links'): new_data['social_links'] = ai_data['social_links']
                    if ai_data.get('confidence_score'): new_data['confidence_score'] = ai_data['confidence_score']

                if new_data:
                    self._update_business(biz, new_data)
                    updated += 1
            except Exception:
                pass
                
        print(f"[Indeed] Updated {updated}/{len(to_enrich)} records with enrichment")

    def _extract_company_website(self, job_url):
        if not job_url:
            return None
        try:
            resp = self.session.get(job_url, timeout=10)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.text, 'html.parser')
            # Indeed job pages often include "Company website"
            for a in soup.select('a'):
                href = a.get('href', '')
                label = (a.text or '').lower()
                if 'company website' in label and href.startswith('http'):
                    return href
                if href.startswith('http') and 'indeed.com' not in href and 'google.com' not in href:
                    if any(k in label for k in ['website', 'company']):
                        return href
        except Exception:
            return None
        return None

    def _extract_phone_from_site(self, website_url):
        if not website_url:
            return None
        try:
            resp = self.session.get(website_url, timeout=8)
            if resp.status_code != 200:
                return None
            match = re.search(r'(\+?\d[\d\s\-\(\)]{6,}\d)', resp.text)
            if match:
                return match.group(1)
        except Exception:
            return None
        return None
