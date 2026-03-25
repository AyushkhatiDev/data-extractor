"""
AIExtractionPipeline — Haystack-based pipeline orchestrating the full
AI extraction flow:  crawl → clean → LLM-extract → embed.

Can be used as a standalone pipeline or plugged into the existing
extraction routes as a new source type ('ai_extract').
"""

import json
import threading
import traceback
from typing import Dict, List, Optional

try:
    from haystack import Pipeline, component, default_from_dict, default_to_dict
    HAYSTACK_AVAILABLE = True
except ImportError:
    HAYSTACK_AVAILABLE = False

from app.ai.smart_crawler import SmartCrawler, CrawlResult, CRAWL4AI_AVAILABLE
from app.ai.content_cleaner import ContentCleaner
from app.ai.llm_extractor import LLMExtractor
from app.ai.semantic_search import SemanticSearchEngine


# ══════════════════════════════════════════════════════════════════════
#  Haystack Custom Components
# ══════════════════════════════════════════════════════════════════════

if HAYSTACK_AVAILABLE:

    @component
    class CrawlerComponent:
        """Haystack component that crawls a website."""

        @component.output_types(crawl_results=List[Dict])
        def run(self, url: str):
            crawler = SmartCrawler(timeout=30)
            results = crawler.crawl_website(url)
            return {
                "crawl_results": [
                    {
                        "url": r.url,
                        "markdown": r.markdown_content,
                        "html": r.raw_html,
                        "links": r.links,
                    }
                    for r in results
                    if r.success
                ]
            }

    @component
    class CleanerComponent:
        """Haystack component that cleans HTML content."""

        @component.output_types(cleaned_text=str)
        def run(self, crawl_results: List[Dict]):
            cleaner = ContentCleaner()
            parts = []
            for cr in crawl_results:
                html = cr.get("html", "")
                if html:
                    cleaned = cleaner.clean(html)
                    if cleaned:
                        parts.append(cleaned)

                # Also use markdown if richer
                md = cr.get("markdown", "")
                if md and len(md) > len(cleaned if html else ""):
                    parts.append(md)

            combined = "\n\n---\n\n".join(parts)
            return {"cleaned_text": combined}

    @component
    class LLMExtractorComponent:
        """Haystack component that extracts structured data via LLM."""

        def __init__(
            self,
            provider: Optional[str] = None,
            primary_model: Optional[str] = None,
            fallback_model: Optional[str] = None,
        ):
            self.provider = provider
            self.primary_model = primary_model
            self.fallback_model = fallback_model

        @component.output_types(extracted_data=Dict)
        def run(self, cleaned_text: str):
            extractor = LLMExtractor(
                provider=self.provider,
                primary_model=self.primary_model,
                fallback_model=self.fallback_model,
            )
            result = extractor.extract(cleaned_text)
            return {"extracted_data": result}


# ══════════════════════════════════════════════════════════════════════
#  Pipeline wrapper for integration with the existing app
# ══════════════════════════════════════════════════════════════════════

class AIExtractionPipeline:
    """Full AI extraction pipeline that integrates with the existing
    BaseExtractor-style task system."""

    def __init__(self, task_id: int, stop_event: threading.Event = None, ai_options=None):
        from app.models import ExtractionTask
        self.task_id = task_id
        self.task = ExtractionTask.query.get(task_id)
        self.stop_event = stop_event
        self._saved_count = 0
        self.ai_options = ai_options or {}

    @property
    def should_stop(self):
        if self.stop_event and self.stop_event.is_set():
            return True
        return False

    def run_extraction(self):
        """Execute the full pipeline for the task's keyword + location."""
        from app.models import ExtractionTask, db
        from app.extraction.base_extractor import BaseExtractor

        try:
            self.task.status = "running"
            db.session.commit()

            keyword = self.task.keyword
            location = self.task.location

            print(f"[AI Pipeline] Starting: '{keyword}' in '{location}'")

            # Step 1: Generate search URLs to crawl
            urls_to_crawl = self._generate_search_urls(keyword, location)
            if not urls_to_crawl:
                self.task.status = "completed"
                self.task.total_records = 0
                db.session.commit()
                return

            # Step 2: Crawl, clean, extract for each URL
            for url in urls_to_crawl:
                if self.should_stop or self._saved_count >= self.task.max_results:
                    break

                try:
                    result = self._process_single_url(url)
                    if result:
                        self._save_result(result, url)
                except Exception as exc:
                    print(f"[AI Pipeline] Error processing {url}: {exc}")
                    continue

            # Step 3: Generate embeddings for search
            if not self.should_stop:
                try:
                    engine = SemanticSearchEngine()
                    if engine.is_available:
                        count = engine.embed_and_store_for_task(self.task_id)
                        print(f"[AI Pipeline] Generated {count} embeddings")
                except Exception as exc:
                    print(f"[AI Pipeline] Embedding error (non-fatal): {exc}")

            # Update final status
            if self.should_stop:
                self.task.status = "stopped"
            else:
                self.task.status = "completed"
            self.task.total_records = self._saved_count
            from datetime import datetime
            self.task.completed_at = datetime.utcnow()
            db.session.commit()

            print(f"[AI Pipeline] Done — {self._saved_count} records saved")

        except Exception as exc:
            traceback.print_exc()
            self.task.status = "failed"
            db.session.commit()
            print(f"[AI Pipeline] Failed: {exc}")

    def _process_single_url(self, url: str) -> Optional[Dict]:
        """Run the full crawl→clean→extract pipeline on one URL."""
        if HAYSTACK_AVAILABLE:
            return self._process_with_haystack(url)
        else:
            return self._process_direct(url)

    def _process_with_haystack(self, url: str) -> Optional[Dict]:
        """Use Haystack pipeline for orchestration."""
        try:
            pipe = Pipeline()
            pipe.add_component("crawler", CrawlerComponent())
            pipe.add_component("cleaner", CleanerComponent())
            pipe.add_component(
                "extractor",
                LLMExtractorComponent(
                    provider=self.ai_options.get("provider"),
                    primary_model=self.ai_options.get("primary_model"),
                    fallback_model=self.ai_options.get("fallback_model"),
                ),
            )

            pipe.connect("crawler.crawl_results", "cleaner.crawl_results")
            pipe.connect("cleaner.cleaned_text", "extractor.cleaned_text")

            result = pipe.run({"crawler": {"url": url}})
            return result.get("extractor", {}).get("extracted_data")
        except Exception as exc:
            print(f"[AI Pipeline] Haystack pipeline error: {exc}")
            return self._process_direct(url)

    def _process_direct(self, url: str) -> Optional[Dict]:
        """Direct pipeline without Haystack (fallback)."""
        # Crawl
        crawler = SmartCrawler(timeout=30)
        crawl_results = crawler.crawl_website(url)

        if not crawl_results:
            return None

        # Clean
        cleaner = ContentCleaner()
        texts = []
        for cr in crawl_results:
            if cr.raw_html:
                cleaned = cleaner.clean(cr.raw_html)
                if cleaned:
                    texts.append(cleaned)
            if cr.markdown_content:
                texts.append(cr.markdown_content)

        combined_text = "\n\n".join(texts)
        if not combined_text.strip():
            return None

        # Extract
        extractor = LLMExtractor(
            provider=self.ai_options.get("provider"),
            primary_model=self.ai_options.get("primary_model"),
            fallback_model=self.ai_options.get("fallback_model"),
        )
        return extractor.extract(combined_text)

    def _save_result(self, result: Dict, source_url: str):
        """Save an extracted result to the database."""
        from app.models import Business, db

        selected_fields_raw = self.ai_options.get("selected_fields")
        if isinstance(selected_fields_raw, str):
            selected_fields = {v.strip().lower() for v in selected_fields_raw.split(",") if v and v.strip()}
        elif isinstance(selected_fields_raw, (list, tuple, set)):
            selected_fields = {str(v).strip().lower() for v in selected_fields_raw if str(v).strip()}
        else:
            selected_fields = {
                'name', 'email', 'phone', 'website', 'location', 'owner',
            }

        if not selected_fields:
            selected_fields = {
                'name', 'email', 'phone', 'website', 'location', 'owner',
            }

        email = None
        if result.get("emails"):
            email = result["emails"][0]

        phone = None
        if result.get("phones"):
            phone = result["phones"][0]

        social_links_json = None
        if result.get("social_links"):
            social_links_json = json.dumps(result["social_links"])

        name = result.get("name") if 'name' in selected_fields else None
        email_val = email if 'email' in selected_fields else None
        phone_val = phone if 'phone' in selected_fields else None
        website_val = (result.get("website") or source_url) if 'website' in selected_fields else None
        location_val = (result.get("address") or self.task.location) if 'location' in selected_fields else None
        owner_val = result.get("owner") if 'owner' in selected_fields else None

        if all(v in (None, '', [], {}) for v in [
            name, email_val, phone_val, website_val, location_val, owner_val
        ]):
            return

        try:
            business = Business(
                task_id=self.task_id,
                name=name,
                email=email_val,
                phone=phone_val,
                website=website_val,
                location=location_val,
                social_links=social_links_json,
                confidence_score=result.get("confidence_score", 0.0),
                owner=owner_val,
                source="ai_extract",
            )
            db.session.add(business)
            db.session.commit()

            self._saved_count += 1
            self.task.total_records = self._saved_count
            db.session.commit()

            print(f"  [{self._saved_count}/{self.task.max_results}] "
                  f"{result.get('name') or source_url}")
        except Exception as exc:
            db.session.rollback()
            print(f"[AI Pipeline] Save error: {exc}")

    def _generate_search_urls(self, keyword: str, location: str) -> List[str]:
        """Generate URLs to crawl based on keyword + location.

        Uses Google search to find relevant business websites.
        """
        import requests
        from urllib.parse import quote_plus
        from bs4 import BeautifulSoup
        from app.utils.scraping import build_requests_session, get_proxy_url, human_delay

        urls = []
        session = build_requests_session(get_proxy_url())

        query = f"{keyword} {location} contact"
        search_url = f"https://www.google.com/search?q={quote_plus(query)}&num=20"

        try:
            resp = session.get(search_url, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    # Google wraps results in /url?q=...
                    if href.startswith("/url?q="):
                        real_url = href.split("/url?q=")[1].split("&")[0]
                        if (
                            real_url.startswith("http")
                            and "google.com" not in real_url
                            and "youtube.com" not in real_url
                            and "wikipedia.org" not in real_url
                        ):
                            urls.append(real_url)
        except Exception as exc:
            print(f"[AI Pipeline] Search error: {exc}")

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for u in urls:
            base = u.split("?")[0].rstrip("/")
            if base not in seen:
                seen.add(base)
                unique.append(u)

        return unique[:self.task.max_results]


# ── Convenience function for route handlers ───────────────────────────

def extract_from_url(
    url: str,
    provider: Optional[str] = None,
    primary_model: Optional[str] = None,
    fallback_model: Optional[str] = None,
) -> Dict:
    """Quick one-shot extraction from a single URL (no task/DB)."""
    crawler = SmartCrawler(timeout=30)
    crawl_results = crawler.crawl_website(url)

    cleaner = ContentCleaner()
    texts = []
    for cr in crawl_results:
        if cr.raw_html:
            cleaned = cleaner.clean(cr.raw_html)
            if cleaned:
                texts.append(cleaned)

    combined = "\n\n".join(texts) if texts else ""

    extractor = LLMExtractor(
        provider=provider,
        primary_model=primary_model,
        fallback_model=fallback_model,
    )
    return extractor.extract(combined)
