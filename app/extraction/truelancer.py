import json
import re
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup

from app.extraction.base_extractor import BaseExtractor
from app.extraction.email_scraper import EmailScraper
from app.utils.scraping import build_requests_session, human_delay, get_proxy_url

class TruelancerExtractor(BaseExtractor):
    """Scrapes Truelancer projects to extract client location and website."""
    
    def __init__(self, task_id, stop_event=None, ai_options=None):
        super().__init__(task_id, stop_event=stop_event, ai_options=ai_options)
        self.session = build_requests_session(get_proxy_url())
        self._seen_project_urls = set()
        
    def extract(self):
        try:
            self.update_task_status('running')
            
            query = f"{self.task.keyword} {self.task.location}".strip()
            
            page_num = 1
            empty_page_streak = 0
            while self._saved_count < self.task.max_results:
                if self.should_stop:
                    self.update_task_status('stopped', self._saved_count)
                    return
                    
                url = self._build_search_url(query, page_num)
                try:
                    resp = self.session.get(url, timeout=10)
                    if resp.status_code != 200:
                        print(f"[Truelancer] Failed to fetch page {page_num}, status code: {resp.status_code}")
                        break
                        
                    soup = BeautifulSoup(resp.text, 'html.parser')

                    projects = self._extract_projects_from_listing(soup)
                    if not projects:
                        empty_page_streak += 1
                        print(f"[Truelancer] No projects found on page {page_num}")
                        if empty_page_streak >= 2:
                            break
                        page_num += 1
                        human_delay(1.0, 2.0)
                        continue

                    empty_page_streak = 0
                    
                    new_projects_in_page = 0
                    for project in projects:
                        if self.should_stop or self._saved_count >= self.task.max_results:
                            break

                        project_url = project.get('project_url')
                        if not project_url or project_url in self._seen_project_urls:
                            continue
                        self._seen_project_urls.add(project_url)
                        new_projects_in_page += 1
                            
                        location = None
                        if self.wants_field('location'):
                            location = project.get('location') or self.task.location

                        needs_website = self.wants_field('website') or self.wants_field('email')
                        external_website = self._extract_client_website(project_url) if needs_website else None

                        website = None
                        if self.wants_field('website'):
                            # Keep project URL when no external website is exposed.
                            website = external_website or project_url

                        email = None
                        if external_website and self.wants_field('email'):
                            try:
                                scraper = EmailScraper(timeout=6)
                                email = scraper.scrape_first_email(external_website)
                            except Exception:
                                email = None
                        
                        biz = self.save_business({
                            'email': email,
                            'website': website,
                            'location': location,
                            'source': 'truelancer'
                        })
                                
                    if new_projects_in_page == 0:
                        empty_page_streak += 1
                        if empty_page_streak >= 2:
                            break
                    else:
                        empty_page_streak = 0
                        
                    page_num += 1
                    human_delay(2.0, 4.0) # Be nice to the server
                    
                except Exception as e:
                    print(f"[Truelancer] Error on page {page_num}: {e}")
                    break
                    
            if self.should_stop:
                self.update_task_status('stopped', self._saved_count)
            else:
                self.update_task_status('completed', self._saved_count)
                
        except Exception as e:
            print(f"[Truelancer] Extraction failed: {e}")
            self.update_task_status('failed')

    def _build_search_url(self, query, page_num):
        base = f"https://www.truelancer.com/freelance-jobs?search={quote_plus(query)}"
        if page_num > 1:
            return f"{base}&page={page_num}"
        return base

    def _extract_projects_from_listing(self, soup):
        projects = self._extract_projects_from_next_data(soup)
        if projects:
            return projects
        return self._extract_projects_from_links(soup)

    def _extract_projects_from_next_data(self, soup):
        script = soup.find('script', id='__NEXT_DATA__')
        if not script:
            return []

        raw_json = script.string or script.get_text() or ''
        if not raw_json:
            return []

        try:
            payload = json.loads(raw_json)
            projects = (
                payload.get('props', {})
                .get('pageProps', {})
                .get('data', {})
                .get('projects', {})
                .get('data', [])
            )
        except Exception:
            return []

        extracted = []
        for item in projects:
            if not isinstance(item, dict):
                continue

            project_url = self._normalize_project_url(item.get('link'))
            if not project_url:
                continue

            country = (item.get('countryObj') or {}).get('name')
            location = self._compose_location(
                city=item.get('city'),
                country_name=country,
                country_code=item.get('country_code'),
            )
            extracted.append({
                'project_url': project_url,
                'location': location or self.task.location,
            })

        return extracted

    def _extract_projects_from_links(self, soup):
        extracted = []
        links = soup.select('a[href*="/freelance-project/"], a[href*="truelancer.com/freelance-project/"]')

        for link in links:
            project_url = self._normalize_project_url(link.get('href'))
            if not project_url:
                continue

            location = self.task.location
            card = link.find_parent('div', class_='freelancerListItem')
            if card:
                text = card.get_text(' ', strip=True)
                parsed_location = self._extract_location_from_text(text)
                if parsed_location:
                    location = parsed_location

            extracted.append({
                'project_url': project_url,
                'location': location,
            })

        return extracted

    def _normalize_project_url(self, href):
        if not href:
            return None

        href = str(href).strip()
        if href.startswith('/'):
            href = f"https://www.truelancer.com{href}"
        elif href.startswith('http://'):
            href = 'https://' + href[len('http://'):]

        if 'truelancer.com/freelance-project/' not in href:
            return None

        return href.split('#')[0].split('?')[0]

    @staticmethod
    def _compose_location(city=None, country_name=None, country_code=None):
        parts = []
        if city:
            parts.append(str(city).strip())
        if country_name:
            parts.append(str(country_name).strip())
        elif country_code:
            parts.append(str(country_code).strip())
        return ', '.join([p for p in parts if p])

    @staticmethod
    def _extract_location_from_text(text):
        if not text:
            return None
        match = re.search(r'location\s*[:\-]\s*([A-Za-z][A-Za-z\s,.-]{2,80})', text, re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip()

    def _extract_client_website(self, project_url):
        if not project_url:
            return None
        try:
            resp = self.session.get(project_url, timeout=10)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.text, 'html.parser')
            # Look for explicit website fields or external links
            for a in soup.select('a[href]'):
                href = a.get('href', '')
                label = (a.text or '').lower()
                if self._is_external_url(href):
                    if 'website' in label or 'company' in label:
                        return href

            # Fallback: first plausible external link
            for a in soup.select('a[href]'):
                href = a.get('href', '')
                if self._is_external_url(href):
                    return href
        except Exception:
            return None
        return None

    @staticmethod
    def _is_external_url(href):
        if not href or not href.startswith('http'):
            return False

        hostname = urlparse(href).netloc.lower()
        if not hostname:
            return False

        blocked_hosts = {
            'truelancer.com',
            'www.truelancer.com',
            'api.truelancer.com',
            'static.truelancer.com',
            'fonts.googleapis.com',
            'fonts.gstatic.com',
            'facebook.com',
            'www.facebook.com',
            'twitter.com',
            'www.twitter.com',
            'x.com',
            'www.x.com',
            'linkedin.com',
            'www.linkedin.com',
            'instagram.com',
            'www.instagram.com',
            'youtube.com',
            'www.youtube.com',
            'play.google.com',
            'itunes.apple.com',
            'apps.apple.com',
        }

        if hostname in blocked_hosts:
            return False

        # Skip common tracking/messaging links
        if any(hostname.endswith(suffix) for suffix in ('.truelancer.com', '.google.com')):
            return False

        return True
