import time
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from app.extraction.base_extractor import BaseExtractor
from app.extraction.email_scraper import EmailScraper
from app.utils.scraping import build_requests_session, human_delay, get_proxy_url


class FreelancerExtractor(BaseExtractor):
    """Scrapes Freelancer.com projects to extract client location and website."""

    def __init__(self, task_id, stop_event=None, ai_options=None):
        super().__init__(task_id, stop_event=stop_event, ai_options=ai_options)
        self.session = build_requests_session(get_proxy_url())

    def extract(self):
        try:
            self.update_task_status('running')

            query = f"{self.task.keyword} {self.task.location}"
            search_url = f"https://www.freelancer.com/jobs/{quote_plus(query)}"

            page_num = 1
            while self._saved_count < self.task.max_results:
                if self.should_stop:
                    self.update_task_status('stopped', self._saved_count)
                    return

                url = f"{search_url}/{page_num}"
                try:
                    resp = self.session.get(url, timeout=10)
                    if resp.status_code != 200:
                        print(f"[Freelancer] Failed to fetch page {page_num}")
                        break

                    soup = BeautifulSoup(resp.text, 'html.parser')
                    cards = soup.select('.JobSearchCard-item, .JobSearchCard-item-inner, .project-card')
                    if not cards:
                        print(f"[Freelancer] No project cards found on page {page_num}")
                        break

                    added_in_page = 0
                    for card in cards:
                        if self.should_stop or self._saved_count >= self.task.max_results:
                            break

                        location = None
                        if self.wants_field('location'):
                            loc_el = card.select_one('[data-q="location"], .JobSearchCard-primary-heading-location')
                            location = loc_el.text.strip() if loc_el else self.task.location

                        project_el = card.select_one('a[href*="/projects/"], a[href*="/jobs/"]')
                        project_url = project_el['href'] if project_el and 'href' in project_el.attrs else None
                        if project_url and not project_url.startswith('http'):
                            project_url = f"https://www.freelancer.com{project_url}"

                        needs_website = self.wants_field('website') or self.wants_field('email')
                        website = self._extract_client_website(project_url) if (needs_website and project_url) else None
                        email = None
                        if website and self.wants_field('email'):
                            try:
                                scraper = EmailScraper(timeout=6)
                                email = scraper.scrape_first_email(website)
                            except Exception:
                                email = None

                        biz = self.save_business({
                            'email': email,
                            'website': website,
                            'location': location,
                            'source': 'freelancer'
                        })
                        if biz:
                            added_in_page += 1

                    if added_in_page == 0:
                        break

                    page_num += 1
                    human_delay(2.0, 4.0)
                except Exception as e:
                    print(f"[Freelancer] Error on page {page_num}: {e}")
                    break

            if self.should_stop:
                self.update_task_status('stopped', self._saved_count)
            else:
                self.update_task_status('completed', self._saved_count)
        except Exception as e:
            print(f"[Freelancer] Extraction failed: {e}")
            self.update_task_status('failed')

    def _extract_client_website(self, project_url):
        if not project_url:
            return None
        try:
            resp = self.session.get(project_url, timeout=10)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a in soup.select('a[href]'):
                href = a.get('href', '')
                label = (a.text or '').lower()
                if href.startswith('http') and 'freelancer.com' not in href:
                    if 'website' in label or 'company' in label or 'link' in label:
                        return href
        except Exception:
            return None
        return None
