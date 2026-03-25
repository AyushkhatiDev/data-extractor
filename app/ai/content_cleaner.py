"""
ContentCleaner — Extract main content from messy HTML.

Uses trafilatura for boilerplate removal (nav, footer, ads, scripts) and
falls back to BeautifulSoup if trafilatura is unavailable.  The output is
clean, plain text ready for LLM consumption.
"""

from typing import Optional

try:
    import trafilatura
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False

try:
    import html_text
    HTML_TEXT_AVAILABLE = True
except ImportError:
    HTML_TEXT_AVAILABLE = False

from bs4 import BeautifulSoup


class ContentCleaner:
    """Strip boilerplate and extract meaningful text from HTML."""

    # Tags that are almost never useful content
    JUNK_TAGS = [
        "script", "style", "noscript", "iframe", "svg",
        "nav", "header", "footer", "aside",
    ]

    def clean(self, html: str) -> str:
        """Return the main content as clean text.

        Strategy:
        1. trafilatura (best quality, ML-based boilerplate detection)
        2. html_text  (decent fallback, respects block structure)
        3. BeautifulSoup (last resort)
        """
        if not html:
            return ""

        text = self._try_trafilatura(html)
        if text and len(text) > 50:
            return text

        text = self._try_html_text(html)
        if text and len(text) > 50:
            return text

        return self._bs4_fallback(html)

    def extract_structured(self, html: str) -> dict:
        """Return main text plus any contact-related snippets found."""
        main_text = self.clean(html)

        # Also extract text specifically near "contact" headings
        contact_text = self._extract_contact_sections(html)

        return {
            "main_text": main_text,
            "contact_text": contact_text,
            "combined": f"{main_text}\n\n{contact_text}".strip(),
        }

    # ── Strategy implementations ──────────────────────────────────────

    @staticmethod
    def _try_trafilatura(html: str) -> Optional[str]:
        if not TRAFILATURA_AVAILABLE:
            return None
        try:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
            )
            return text or None
        except Exception:
            return None

    @staticmethod
    def _try_html_text(html: str) -> Optional[str]:
        if not HTML_TEXT_AVAILABLE:
            return None
        try:
            return html_text.extract_text(html) or None
        except Exception:
            return None

    def _bs4_fallback(self, html: str) -> str:
        """Last-resort plain text extraction."""
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Remove junk
        for tag_name in self.JUNK_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        # Collapse excessive blank lines
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines)

    @staticmethod
    def _extract_contact_sections(html: str) -> str:
        """Pull text from sections near 'contact' headings."""
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        parts = []
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            text = heading.get_text(strip=True).lower()
            if any(kw in text for kw in ("contact", "reach", "get in touch", "email", "address")):
                # Grab the next few siblings
                sibling = heading.find_next_sibling()
                count = 0
                while sibling and count < 5:
                    parts.append(sibling.get_text(separator=" ", strip=True))
                    sibling = sibling.find_next_sibling()
                    count += 1

        return "\n".join(parts)
