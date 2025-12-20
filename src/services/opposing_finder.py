"""Different view finder - orchestrates the full pipeline."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from .embedding import EmbeddingService
from .keyword_generator import KeywordGeneratorService
from .objection_extractor import (
    ArticleReference,
    DifferentViewExtractorService,
    DifferentViewResult,
)
from .vector_db import SearchResult, VectorDBService

logger = logging.getLogger(__name__)


@dataclass
class KeywordSearchResult:
    """Result from searching a single keyword."""

    keyword: str
    results: list[SearchResult]
    error: str | None = None


@dataclass
class DiverseViewResult:
    """Result from the diverse view finder."""

    input_summary: str
    keywords_used: list[str]
    different_views: list[DifferentViewResult]
    total_candidates_found: int = 0
    keyword_results: list[KeywordSearchResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# Callback types for observability
OnKeywordsGenerated = Callable[[str, list[str]], None]  # (topic, keywords)
OnKeywordSearchStart = Callable[[str], None]  # (keyword)
OnKeywordSearchComplete = Callable[[str, list[SearchResult]], None]  # (keyword, results)
OnRankingStart = Callable[[int], None]  # (candidate_count)


class DiverseViewFinder:
    """Orchestrates the full diverse view finding pipeline."""

    MAX_ARTICLES_PER_KEYWORD = 10
    PRE_FILTER_LIMIT = 15

    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_db: VectorDBService,
        keyword_generator: KeywordGeneratorService,
        different_view_extractor: DifferentViewExtractorService,
    ):
        """Initialize the diverse view finder.

        Args:
            embedding_service: Service for generating embeddings.
            vector_db: Service for vector search.
            keyword_generator: Service for generating diverse keywords.
            different_view_extractor: Service for extracting different views.
        """
        self.embedding = embedding_service
        self.vector_db = vector_db
        self.keyword_gen = keyword_generator
        self.extractor = different_view_extractor

    async def _search_keyword(self, keyword: str) -> KeywordSearchResult:
        """Search for articles matching a keyword.

        Args:
            keyword: Keyword to search for.

        Returns:
            KeywordSearchResult with results or error.
        """
        try:
            embedding = self.embedding.embed(
                keyword, task_type="RETRIEVAL_QUERY"
            )
            results = self.vector_db.search(
                embedding, limit=self.MAX_ARTICLES_PER_KEYWORD
            )
            return KeywordSearchResult(keyword=keyword, results=results)
        except Exception as e:
            logger.warning(f"Error searching for keyword '{keyword}': {e}")
            return KeywordSearchResult(keyword=keyword, results=[], error=str(e))

    def _deduplicate_results(
        self, keyword_results: list[KeywordSearchResult]
    ) -> list[SearchResult]:
        """Deduplicate search results, keeping the one with lowest distance.

        Args:
            keyword_results: List of keyword search results.

        Returns:
            Deduplicated list of results.
        """
        seen_ids: dict[int, SearchResult] = {}

        for kw_result in keyword_results:
            for result in kw_result.results:
                article_id = result.article_id
                if (
                    article_id not in seen_ids
                    or result.distance < seen_ids[article_id].distance
                ):
                    seen_ids[article_id] = result

        return list(seen_ids.values())

    async def find_diverse_views(
        self,
        input_text: str,
        on_keywords_generated: OnKeywordsGenerated | None = None,
        on_keyword_search_start: OnKeywordSearchStart | None = None,
        on_keyword_search_complete: OnKeywordSearchComplete | None = None,
        on_extraction_start: OnRankingStart | None = None,
    ) -> DiverseViewResult:
        """Find articles with diverse views related to the input text.

        Args:
            input_text: The text to find diverse views for.
            on_keywords_generated: Callback when keywords are generated.
            on_keyword_search_start: Callback when keyword search starts.
            on_keyword_search_complete: Callback when keyword search completes.
            on_extraction_start: Callback when extraction starts.

        Returns:
            DiverseViewResult with topic summary, keywords, and different views.
        """
        errors: list[str] = []

        # Step 1: Generate diverse keywords
        try:
            keyword_result = await self.keyword_gen.generate_diverse_keywords(
                input_text
            )
        except Exception as e:
            return DiverseViewResult(
                input_summary="키워드 생성 실패",
                keywords_used=[],
                different_views=[],
                errors=[f"키워드 생성 중 오류 발생: {e}"],
            )

        keywords = keyword_result.keywords
        if not keywords:
            return DiverseViewResult(
                input_summary=keyword_result.topic_summary,
                keywords_used=[],
                different_views=[],
                errors=["생성된 키워드가 없습니다."],
            )

        # Notify: keywords generated
        if on_keywords_generated:
            on_keywords_generated(keyword_result.topic_summary, keywords)

        # Step 2: Search for each keyword sequentially for better observability
        keyword_results: list[KeywordSearchResult] = []
        for keyword in keywords:
            if on_keyword_search_start:
                on_keyword_search_start(keyword)

            kw_result = await self._search_keyword(keyword)
            keyword_results.append(kw_result)

            if on_keyword_search_complete:
                on_keyword_search_complete(keyword, kw_result.results)

        # Step 3: Deduplicate by article_id, keep lowest distance
        deduplicated = self._deduplicate_results(keyword_results)
        total_candidates = len(deduplicated)

        if not deduplicated:
            return DiverseViewResult(
                input_summary=keyword_result.topic_summary,
                keywords_used=keywords,
                different_views=[],
                total_candidates_found=0,
                keyword_results=keyword_results,
                errors=["검색 결과가 없습니다. 기사 인덱싱이 필요할 수 있습니다."],
            )

        # Step 4: Pre-filter: top N by distance (for LLM extraction)
        candidates = sorted(deduplicated, key=lambda x: x.distance)[
            : self.PRE_FILTER_LIMIT
        ]

        # Notify: extraction start
        if on_extraction_start:
            on_extraction_start(len(candidates))

        # Step 5: LLM extraction for different views with quotes
        try:
            different_views = await self.extractor.extract_different_views(input_text, candidates)
        except Exception as e:
            errors.append(f"다른 시각 추출 중 오류: {e}")
            different_views = []

        return DiverseViewResult(
            input_summary=keyword_result.topic_summary,
            keywords_used=keywords,
            different_views=different_views,
            total_candidates_found=total_candidates,
            keyword_results=keyword_results,
            errors=errors,
        )
