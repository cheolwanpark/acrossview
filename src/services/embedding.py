"""Embedding service using Google GenAI with rate limit handling."""

import asyncio
import logging
import os
import random
import time
from collections.abc import Callable
from typing import Literal

from google import genai
from google.genai import errors, types

from ..config import (
    EMBEDDING_BATCH_DELAY,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
)

logger = logging.getLogger(__name__)

TaskType = Literal["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT"]


class RateLimitConfig:
    """Configuration for rate limiting."""

    def __init__(
        self,
        max_retries: int = 5,
        initial_delay: float = 2.0,
        backoff_multiplier: float = 2.0,
        max_delay: float = 60.0,
        jitter_factor: float = 0.2,
        min_request_interval: float = 1.0,
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.backoff_multiplier = backoff_multiplier
        self.max_delay = max_delay
        self.jitter_factor = jitter_factor
        self.min_request_interval = min_request_interval


class EmbeddingService:
    """Service for generating text embeddings using Google GenAI."""

    def __init__(
        self,
        api_key: str | None = None,
        rate_limit_config: RateLimitConfig | None = None,
    ):
        """Initialize the embedding service.

        Args:
            api_key: Optional API key. If not provided, reads from GEMINI_API_KEY env var.
            rate_limit_config: Configuration for rate limiting. Uses defaults if not provided.

        Raises:
            ValueError: If no API key is provided or found in environment.
        """
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable not set. "
                "Please create a .env file with your API key. "
                "See .env.example for the template."
            )
        self.client = genai.Client(api_key=api_key)
        self.model = EMBEDDING_MODEL
        self.dimensions = EMBEDDING_DIM
        self.batch_size = EMBEDDING_BATCH_SIZE
        self.batch_delay = EMBEDDING_BATCH_DELAY
        self.rate_config = rate_limit_config or RateLimitConfig()
        self._last_request_time = 0.0

    def _add_jitter(self, delay: float) -> float:
        """Add random jitter to delay to prevent thundering herd."""
        jitter = delay * self.rate_config.jitter_factor
        return delay + random.uniform(-jitter, jitter)

    def _wait_for_rate_limit(self) -> None:
        """Enforce minimum interval between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_config.min_request_interval:
            sleep_time = self.rate_config.min_request_interval - elapsed
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    async def _wait_for_rate_limit_async(self) -> None:
        """Enforce minimum interval between requests (async version)."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_config.min_request_interval:
            sleep_time = self.rate_config.min_request_interval - elapsed
            await asyncio.sleep(sleep_time)
        self._last_request_time = time.time()

    def embed(
        self, text: str, task_type: TaskType = "RETRIEVAL_QUERY"
    ) -> list[float]:
        """Generate embedding for a single text with rate limit handling.

        Args:
            text: Text to embed.
            task_type: Type of task for optimization.
                - RETRIEVAL_QUERY: For search queries.
                - RETRIEVAL_DOCUMENT: For document indexing.

        Returns:
            List of float values representing the embedding.
        """
        delay = self.rate_config.initial_delay

        for attempt in range(self.rate_config.max_retries):
            try:
                self._wait_for_rate_limit()

                response = self.client.models.embed_content(
                    model=self.model,
                    contents=text,
                    config=types.EmbedContentConfig(
                        output_dimensionality=self.dimensions,
                        task_type=task_type,
                    ),
                )
                return list(response.embeddings[0].values)

            except errors.APIError as e:
                if e.code == 429:  # Rate limit exceeded
                    if attempt < self.rate_config.max_retries - 1:
                        jittered_delay = self._add_jitter(delay)
                        logger.warning(
                            f"Rate limited (429). Retrying in {jittered_delay:.1f}s "
                            f"(attempt {attempt + 1}/{self.rate_config.max_retries})"
                        )
                        time.sleep(jittered_delay)
                        delay = min(
                            delay * self.rate_config.backoff_multiplier,
                            self.rate_config.max_delay,
                        )
                    else:
                        logger.error(
                            f"Rate limit exceeded after {self.rate_config.max_retries} retries"
                        )
                        raise
                else:
                    logger.error(f"API error {e.code}: {e.message}")
                    raise

        raise RuntimeError("Failed to embed after all retries")

    async def embed_batch(
        self,
        texts: list[str],
        task_type: TaskType = "RETRIEVAL_DOCUMENT",
        batch_size: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[list[float]]:
        """Batch embed multiple texts with rate limiting.

        Args:
            texts: List of texts to embed.
            task_type: Type of task for optimization.
            batch_size: Number of texts per batch. Defaults to config value.
            on_progress: Optional callback for progress updates.
                Called with (processed_count, total_count).

        Returns:
            List of embeddings, one per input text.
        """
        batch_size = batch_size or self.batch_size
        all_embeddings: list[list[float]] = []
        total = len(texts)
        consecutive_errors = 0
        max_consecutive_errors = 3

        for i in range(0, total, batch_size):
            batch = texts[i : i + batch_size]

            try:
                embeddings = await self._embed_batch_with_retry(batch, task_type)
                all_embeddings.extend(embeddings)
                consecutive_errors = 0  # Reset on success

                if on_progress:
                    on_progress(len(all_embeddings), total)

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Batch error at index {i}: {e}")

                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        f"Too many consecutive errors ({consecutive_errors}). "
                        "Stopping to avoid quota exhaustion."
                    )
                    raise RuntimeError(
                        f"Stopped after {consecutive_errors} consecutive errors. "
                        f"Processed {len(all_embeddings)}/{total} texts."
                    )

                # Add placeholder embeddings for failed batch
                all_embeddings.extend([[] for _ in batch])

                if on_progress:
                    on_progress(len(all_embeddings), total)

            # Rate limiting between batches - longer delay after errors
            if i + batch_size < total:
                delay = self.batch_delay * (2 ** consecutive_errors)
                await asyncio.sleep(delay)

        return all_embeddings

    async def _embed_batch_with_retry(
        self, texts: list[str], task_type: TaskType
    ) -> list[list[float]]:
        """Embed a batch of texts with retry logic and rate limit handling.

        Uses asyncio.to_thread to avoid blocking the event loop.
        """
        delay = self.rate_config.initial_delay

        for attempt in range(self.rate_config.max_retries):
            try:
                await self._wait_for_rate_limit_async()

                def _sync_embed():
                    response = self.client.models.embed_content(
                        model=self.model,
                        contents=texts,
                        config=types.EmbedContentConfig(
                            output_dimensionality=self.dimensions,
                            task_type=task_type,
                        ),
                    )
                    return [list(e.values) for e in response.embeddings]

                return await asyncio.to_thread(_sync_embed)

            except errors.APIError as e:
                if e.code == 429:  # Rate limit exceeded
                    if attempt < self.rate_config.max_retries - 1:
                        jittered_delay = self._add_jitter(delay)
                        logger.warning(
                            f"Rate limited (429). Retrying in {jittered_delay:.1f}s "
                            f"(attempt {attempt + 1}/{self.rate_config.max_retries})"
                        )
                        await asyncio.sleep(jittered_delay)
                        delay = min(
                            delay * self.rate_config.backoff_multiplier,
                            self.rate_config.max_delay,
                        )
                    else:
                        logger.error(
                            f"Rate limit exceeded after {self.rate_config.max_retries} retries"
                        )
                        raise
                elif e.code == 503:  # Service unavailable
                    if attempt < self.rate_config.max_retries - 1:
                        jittered_delay = self._add_jitter(delay)
                        logger.warning(
                            f"Service unavailable (503). Retrying in {jittered_delay:.1f}s"
                        )
                        await asyncio.sleep(jittered_delay)
                        delay = min(
                            delay * self.rate_config.backoff_multiplier,
                            self.rate_config.max_delay,
                        )
                    else:
                        raise
                else:
                    logger.error(f"API error {e.code}: {e.message}")
                    raise

        raise RuntimeError("Failed to embed batch after all retries")
