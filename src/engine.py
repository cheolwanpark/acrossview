"""Async crawl engine with per-domain rate limiting."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import httpx
from tqdm.asyncio import tqdm

from .config import (
    DEFAULT_CONCURRENT,
    RATE_LIMIT_PER_DOMAIN,
    MAX_RETRIES,
    HEADERS,
    REQUEST_TIMEOUT,
)
from .crawler.base import BaseCrawler, CrawlResult
from .database import Database
from .logger import logger


@dataclass
class CrawlTask:
    """A single crawl task."""

    article_id: int
    url: str
    domain: str


class RateLimiter:
    """Per-domain rate limiter using asyncio."""

    def __init__(self, rate_limit: float = RATE_LIMIT_PER_DOMAIN):
        """Initialize rate limiter.

        Args:
            rate_limit: Minimum seconds between requests to same domain
        """
        self.rate_limit = rate_limit
        self._last_request: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, domain: str) -> None:
        """Wait if needed to respect rate limit for domain.

        Args:
            domain: Domain to rate limit
        """
        async with self._locks[domain]:
            now = asyncio.get_event_loop().time()
            last = self._last_request.get(domain, 0)
            elapsed = now - last

            if elapsed < self.rate_limit:
                await asyncio.sleep(self.rate_limit - elapsed)

            self._last_request[domain] = asyncio.get_event_loop().time()


class CrawlEngine:
    """Async crawl engine with rate limiting and retries."""

    def __init__(
        self,
        db: Database,
        concurrent: int = DEFAULT_CONCURRENT,
        rate_limit: float = RATE_LIMIT_PER_DOMAIN,
    ):
        """Initialize crawl engine.

        Args:
            db: Database instance
            concurrent: Maximum concurrent requests
            rate_limit: Seconds between requests to same domain
        """
        self.db = db
        self.concurrent = concurrent
        self.rate_limiter = RateLimiter(rate_limit)
        self._semaphore = asyncio.Semaphore(concurrent)
        self._client: httpx.AsyncClient | None = None
        self._crawler: BaseCrawler | None = None
        self._pbar_lock = asyncio.Lock()  # Lock for thread-safe progress bar updates

        # Stats
        self.success_count = 0
        self.failed_count = 0
        self.blocked_count = 0

    async def __aenter__(self) -> "CrawlEngine":
        """Enter async context."""
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        self._crawler = BaseCrawler(self._client)
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context."""
        if self._client:
            await self._client.aclose()

    def _should_retry(self, result: CrawlResult) -> bool:
        """Check if result should be retried."""
        return not result.success and result.retryable

    async def _crawl_with_retry(self, url: str) -> CrawlResult:
        """Crawl URL with retry logic.

        Args:
            url: URL to crawl

        Returns:
            CrawlResult
        """
        attempts = 0
        last_result: CrawlResult | None = None

        while attempts < MAX_RETRIES:
            attempts += 1
            result = await self._crawler.crawl(url)

            if result.success or not result.retryable:
                return result

            last_result = result

            if attempts < MAX_RETRIES:
                # Exponential backoff: 2^attempts seconds
                wait_time = 2**attempts
                logger.debug(f"Retry {attempts}/{MAX_RETRIES} for {url} after {wait_time}s")
                await asyncio.sleep(wait_time)

        return last_result or CrawlResult.from_error("Max retries exceeded")

    async def _process_task(self, task: CrawlTask, pbar: tqdm) -> None:
        """Process a single crawl task.

        Args:
            task: Crawl task to process
            pbar: Progress bar to update
        """
        async with self._semaphore:
            # Rate limit per domain
            await self.rate_limiter.acquire(task.domain)

            # Crawl with retry
            result = await self._crawl_with_retry(task.url)

            # Update database
            if result.success:
                await self.db.update_crawl_result(
                    article_id=task.article_id,
                    success=True,
                    title=result.title,
                    body=result.body,
                    published_at=result.published_at,
                )
                self.success_count += 1
            elif not result.retryable:
                # Permanent failure (403, 404, parse error)
                await self.db.mark_blocked(task.article_id, result.error or "Unknown error")
                self.blocked_count += 1
            else:
                # Temporary failure
                await self.db.update_crawl_result(
                    article_id=task.article_id,
                    success=False,
                    error_message=result.error,
                )
                self.failed_count += 1

            # Update progress bar with lock for thread-safety
            async with self._pbar_lock:
                pbar.update(1)
                pbar.set_postfix(
                    success=self.success_count,
                    failed=self.failed_count,
                    blocked=self.blocked_count,
                )

    async def crawl_pending(
        self,
        domain: str | None = None,
        limit: int | None = None,
    ) -> dict[str, int]:
        """Crawl all pending URLs.

        Args:
            domain: Filter by domain (optional)
            limit: Maximum URLs to crawl (optional)

        Returns:
            Statistics dictionary
        """
        # Reset stats
        self.success_count = 0
        self.failed_count = 0
        self.blocked_count = 0

        # Get pending URLs
        pending = await self.db.get_pending_urls(domain=domain, limit=limit)
        if not pending:
            logger.info("No pending URLs to crawl")
            return {"success": 0, "failed": 0, "blocked": 0}

        logger.info(f"Starting crawl of {len(pending)} URLs (concurrent={self.concurrent})")

        # Create tasks
        tasks = [
            CrawlTask(
                article_id=row["id"],
                url=row["url"],
                domain=row["domain"],
            )
            for row in pending
        ]

        # Process with progress bar
        with tqdm(total=len(tasks), desc="Crawling", unit="url") as pbar:
            # Create all task coroutines
            coroutines = [self._process_task(task, pbar) for task in tasks]

            # Run concurrently
            await asyncio.gather(*coroutines)

        logger.info(
            f"Crawl complete: {self.success_count} success, "
            f"{self.failed_count} failed, {self.blocked_count} blocked"
        )

        return {
            "success": self.success_count,
            "failed": self.failed_count,
            "blocked": self.blocked_count,
        }

    async def retry_failed(
        self,
        max_attempts: int = MAX_RETRIES,
        limit: int | None = None,
    ) -> dict[str, int]:
        """Retry previously failed URLs.

        Args:
            max_attempts: Maximum total attempts per URL
            limit: Maximum URLs to retry (optional)

        Returns:
            Statistics dictionary
        """
        # Reset stats
        self.success_count = 0
        self.failed_count = 0
        self.blocked_count = 0

        # Get failed URLs that can be retried
        failed = await self.db.get_failed_urls(max_attempts=max_attempts, limit=limit)
        if not failed:
            logger.info("No failed URLs to retry")
            return {"success": 0, "failed": 0, "blocked": 0}

        logger.info(f"Retrying {len(failed)} failed URLs")

        # Create tasks
        tasks = [
            CrawlTask(
                article_id=row["id"],
                url=row["url"],
                domain=row["domain"],
            )
            for row in failed
        ]

        # Process with progress bar
        with tqdm(total=len(tasks), desc="Retrying", unit="url") as pbar:
            coroutines = [self._process_task(task, pbar) for task in tasks]
            await asyncio.gather(*coroutines)

        logger.info(
            f"Retry complete: {self.success_count} success, "
            f"{self.failed_count} still failed, {self.blocked_count} blocked"
        )

        return {
            "success": self.success_count,
            "failed": self.failed_count,
            "blocked": self.blocked_count,
        }
