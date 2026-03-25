"""
SmartCrawler — Intelligent web crawling powered by Crawl4AI.

Crawl4AI provides JS rendering, content filtering, and markdown conversion
out of the box.  SmartCrawler wraps it in a sync-friendly API that the rest
of the extraction pipeline can call.
"""

import asyncio
import traceback
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig
    CRAWL4AI_AVAILABLE = True
except ImportError:
    CRAWL4AI_AVAILABLE = False

from app.utils.scraping import get_random_user_agent


# ── Data classes ──────────────────────────────────────────────────────

@dataclass
class CrawlResult:
    """Represents the output of crawling a single page."""
    url: str
    markdown_content: str = ""
    raw_html: str = ""
    links: List[str] = field(default_factory=list)
    success: bool = False
    error: Optional[str] = None


# ── Contact-page paths to discover ────────────────────────────────────

CONTACT_PATHS = [
    "", "/contact", "/contact-us", "/contactus",
    "/about", "/about-us", "/aboutus",
    "/impressum", "/imprint",
]


class SmartCrawler:
    """Crawl websites intelligently using Crawl4AI with JS rendering."""

    def __init__(self, timeout: int = 30, verbose: bool = False):
        if not CRAWL4AI_AVAILABLE:
            raise RuntimeError(
                "crawl4ai is not installed. Run: pip install 'crawl4ai>=0.4.0'"
            )
        self.timeout = timeout
        self.verbose = verbose

    # ── Public sync API ───────────────────────────────────────────────

    def crawl_url(self, url: str) -> CrawlResult:
        """Crawl a single URL and return structured CrawlResult."""
        return self._run(self._async_crawl_one(url))

    def crawl_website(self, base_url: str) -> List[CrawlResult]:
        """Crawl the homepage + contact/about pages of a website."""
        return self._run(self._async_crawl_website(base_url))

    # ── Async implementation ──────────────────────────────────────────

    async def _async_crawl_one(self, url: str) -> CrawlResult:
        """Crawl a single URL asynchronously."""
        browser_cfg = BrowserConfig(
            headless=True,
            user_agent=get_random_user_agent(),
            verbose=self.verbose,
        )
        run_cfg = CrawlerRunConfig(
            word_count_threshold=10,
            page_timeout=self.timeout * 1000,
        )

        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=run_cfg)

                if not result.success:
                    return CrawlResult(
                        url=url,
                        success=False,
                        error=result.error_message or "Crawl failed",
                    )

                # Extract internal links for further discovery
                links = []
                if result.links and isinstance(result.links, dict):
                    for link_list in result.links.values():
                        if isinstance(link_list, list):
                            for link_obj in link_list:
                                href = link_obj.get("href", "") if isinstance(link_obj, dict) else str(link_obj)
                                if href:
                                    links.append(href)

                return CrawlResult(
                    url=url,
                    markdown_content=result.markdown or "",
                    raw_html=result.html or "",
                    links=links,
                    success=True,
                )
        except Exception as exc:
            if self.verbose:
                traceback.print_exc()
            return CrawlResult(url=url, success=False, error=str(exc))

    async def _async_crawl_website(self, base_url: str) -> List[CrawlResult]:
        """Crawl homepage + common contact pages."""
        if not base_url.startswith(("http://", "https://")):
            base_url = "https://" + base_url
        base_url = base_url.rstrip("/")

        results: List[CrawlResult] = []
        visited = set()

        browser_cfg = BrowserConfig(
            headless=True,
            user_agent=get_random_user_agent(),
            verbose=self.verbose,
        )
        run_cfg = CrawlerRunConfig(
            word_count_threshold=10,
            page_timeout=self.timeout * 1000,
        )

        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                for path in CONTACT_PATHS:
                    url = base_url + path
                    if url in visited:
                        continue
                    visited.add(url)

                    try:
                        result = await crawler.arun(url=url, config=run_cfg)
                        if result.success:
                            links = []
                            if result.links and isinstance(result.links, dict):
                                for link_list in result.links.values():
                                    if isinstance(link_list, list):
                                        for link_obj in link_list:
                                            href = link_obj.get("href", "") if isinstance(link_obj, dict) else str(link_obj)
                                            if href:
                                                links.append(href)

                            results.append(CrawlResult(
                                url=url,
                                markdown_content=result.markdown or "",
                                raw_html=result.html or "",
                                links=links,
                                success=True,
                            ))
                    except Exception:
                        continue
        except Exception as exc:
            if self.verbose:
                traceback.print_exc()

        return results

    # ── Helper ────────────────────────────────────────────────────────

    @staticmethod
    def _run(coro):
        """Run an async coroutine from synchronous code."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an existing event loop — use a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)
