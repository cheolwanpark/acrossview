"""Base crawler with trafilatura for robust article extraction."""

from dataclasses import dataclass
from typing import Any

import httpx
import trafilatura
from bs4 import BeautifulSoup

from ..config import HEADERS, REQUEST_TIMEOUT, PERMANENT_FAILURE_CODES, RETRYABLE_STATUS_CODES
from ..logger import logger


@dataclass
class CrawlResult:
    """Result of a crawl operation."""

    success: bool
    title: str | None = None
    body: str | None = None
    published_at: str | None = None
    error: str | None = None
    status_code: int | None = None
    retryable: bool = True

    @classmethod
    def from_error(
        cls, error: str, status_code: int | None = None, retryable: bool = True
    ) -> "CrawlResult":
        """Create a failed result from an error."""
        return cls(
            success=False,
            error=error,
            status_code=status_code,
            retryable=retryable,
        )


class BaseCrawler:
    """Base crawler using trafilatura for article extraction."""

    def __init__(self, client: httpx.AsyncClient):
        """Initialize crawler with HTTP client.

        Args:
            client: Shared async HTTP client
        """
        self.client = client

    async def fetch(self, url: str) -> tuple[str | None, int | None, str | None]:
        """Fetch URL content.

        Args:
            url: URL to fetch

        Returns:
            Tuple of (html_content, status_code, error_message)
        """
        try:
            response = await self.client.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )

            if response.status_code in PERMANENT_FAILURE_CODES:
                return None, response.status_code, f"HTTP {response.status_code}"

            if response.status_code in RETRYABLE_STATUS_CODES:
                return None, response.status_code, f"HTTP {response.status_code} (retryable)"

            response.raise_for_status()
            return response.text, response.status_code, None

        except httpx.TimeoutException:
            return None, None, "Request timeout"
        except httpx.NetworkError as e:
            return None, None, f"Network error: {e}"
        except httpx.HTTPStatusError as e:
            return None, e.response.status_code, f"HTTP {e.response.status_code}"
        except Exception as e:
            return None, None, f"Unexpected error: {e}"

    def extract_title(self, html: str) -> str | None:
        """Extract title from HTML using BeautifulSoup.

        Args:
            html: HTML content

        Returns:
            Extracted title or None
        """
        try:
            soup = BeautifulSoup(html, "lxml")

            # Try common title selectors for Korean news sites
            selectors = [
                "h1.headline",
                "h1.title",
                "h1.article-title",
                "h1.news-title",
                "h1.tit_article",
                "h1.article_title",
                "h1#article_title",
                "h1.view_tit",
                "h1.art_tit",
                "article h1",
                "header h1",
                "h1",
            ]

            for selector in selectors:
                elem = soup.select_one(selector)
                if elem:
                    title = elem.get_text(strip=True)
                    if title and len(title) > 5:  # Avoid short garbage
                        return title

            # Fallback to <title> tag
            if soup.title and soup.title.string:
                return soup.title.string.strip()

            return None
        except Exception as e:
            logger.debug(f"Title extraction failed: {e}")
            return None

    def extract_body(self, html: str, url: str) -> str | None:
        """Extract article body using trafilatura.

        Args:
            html: HTML content
            url: Original URL for context

        Returns:
            Extracted body text or None
        """
        try:
            # trafilatura.extract returns clean text
            body = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
                favor_precision=True,
                deduplicate=True,
            )

            if body and len(body) > 50:  # Minimum content threshold
                return body

            return None
        except Exception as e:
            logger.debug(f"Body extraction failed: {e}")
            return None

    def extract_published_date(self, html: str) -> str | None:
        """Extract published date from HTML.

        Args:
            html: HTML content

        Returns:
            Published date string or None
        """
        try:
            soup = BeautifulSoup(html, "lxml")

            # Try meta tags first
            meta_selectors = [
                ("meta[property='article:published_time']", "content"),
                ("meta[name='article:published_time']", "content"),
                ("meta[property='og:article:published_time']", "content"),
                ("meta[name='pubdate']", "content"),
                ("meta[name='date']", "content"),
                ("time[datetime]", "datetime"),
            ]

            for selector, attr in meta_selectors:
                elem = soup.select_one(selector)
                if elem and elem.get(attr):
                    return elem.get(attr)

            # Try common date selectors
            date_selectors = [
                "span.date",
                "span.datetime",
                "time.date",
                "p.date",
                "div.date",
                "span.article_date",
                "span.view_date",
            ]

            for selector in date_selectors:
                elem = soup.select_one(selector)
                if elem:
                    text = elem.get_text(strip=True)
                    if text:
                        return text

            return None
        except Exception as e:
            logger.debug(f"Date extraction failed: {e}")
            return None

    async def crawl(self, url: str) -> CrawlResult:
        """Crawl a URL and extract article content.

        Args:
            url: URL to crawl

        Returns:
            CrawlResult with extracted content or error
        """
        # Fetch HTML
        html, status_code, error = await self.fetch(url)

        if error:
            retryable = status_code not in PERMANENT_FAILURE_CODES if status_code else True
            return CrawlResult.from_error(error, status_code, retryable)

        if not html:
            return CrawlResult.from_error("Empty response", status_code, retryable=True)

        # Extract content
        title = self.extract_title(html)
        body = self.extract_body(html, url)
        published_at = self.extract_published_date(html)

        if not body:
            return CrawlResult.from_error(
                "Failed to extract article body",
                status_code,
                retryable=False,  # Don't retry parsing failures
            )

        return CrawlResult(
            success=True,
            title=title,
            body=body,
            published_at=published_at,
            status_code=status_code,
        )
