"""Article ranker service using Google GenAI."""

import asyncio
import os

from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import LLM_MAX_RETRIES, LLM_MODEL
from .vector_db import SearchResult


class RankedArticle(BaseModel):
    """A ranked article with different perspective reasoning."""

    article_id: int
    title: str
    relevance_score: float
    perspective_reason: str


class RankingResponse(BaseModel):
    """Response from the ranking model."""

    articles: list[RankedArticle]


class ArticleRankerService:
    """Service for ranking articles by perspective diversity."""

    MAX_CANDIDATES = 15  # Limit to avoid context overflow

    PROMPT = """당신은 뉴스 분석 전문가입니다.

원본 텍스트와 후보 기사들을 비교하여, **다양한 시각이나 관점**을
제시하는 기사 5개를 선택하세요.

이 작업의 목적은 독자가 하나의 주제에 대해 여러 관점을 접하고,
보다 균형 잡힌 이해를 가질 수 있도록 돕는 것입니다.

선택 기준:
1. 원본 텍스트와 다른 시각을 가진 관점
2. 같은 주제/사건에 대한 다른 해석
3. 원본에서 다루지 않은 새로운 관점 제시

원본 텍스트:
{input_text}

후보 기사 목록:
{formatted_articles}

정확히 5개의 기사를 선택하고, 각각에 대해:
- article_id: 기사 번호
- title: 기사 제목
- relevance_score: 다른 시각으로서의 관련성 (0.0-1.0)
- perspective_reason: 이 기사가 원본과 어떻게 다른 시각을 제시하는지 설명 (1-2문장)"""

    def __init__(self, api_key: str | None = None):
        """Initialize the article ranker service.

        Args:
            api_key: Optional API key. If not provided, reads from GEMINI_API_KEY env var.
        """
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable not set. "
                "See .env.example for the template."
            )
        self.client = genai.Client(api_key=api_key)
        self.model = LLM_MODEL

    def _format_articles(self, candidates: list[SearchResult]) -> str:
        """Format candidate articles for the prompt."""
        formatted = []
        for i, article in enumerate(candidates, 1):
            # Truncate body to avoid context overflow
            body_preview = (article.body or "")[:500]
            if len(article.body or "") > 500:
                body_preview += "..."

            formatted.append(
                f"[{article.article_id}] {article.media_outlet or '알 수 없음'} ({article.date or '날짜 없음'})\n"
                f"제목: {article.title or '제목 없음'}\n"
                f"내용: {body_preview}\n"
            )
        return "\n".join(formatted)

    @retry(
        stop=stop_after_attempt(LLM_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def rank_articles(
        self, input_text: str, candidates: list[SearchResult]
    ) -> list[RankedArticle]:
        """Rank candidate articles by opposition relevance.

        Args:
            input_text: The original input text.
            candidates: List of candidate articles from vector search.

        Returns:
            List of top 5 ranked articles with reasoning.
        """
        # Truncate to MAX_CANDIDATES
        candidates = candidates[: self.MAX_CANDIDATES]

        if not candidates:
            return []

        # If fewer than 5 candidates, return all of them
        if len(candidates) < 5:
            return [
                RankedArticle(
                    article_id=c.article_id,
                    title=c.title or "제목 없음",
                    relevance_score=1.0 - c.distance,  # Convert distance to score
                    perspective_reason="관련 주제에 대한 다른 시각을 제공하는 기사입니다.",
                )
                for c in candidates
            ]

        prompt = self.PROMPT.format(
            input_text=input_text,
            formatted_articles=self._format_articles(candidates),
        )

        def _sync_generate():
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RankingResponse,
                ),
            )
            return response.parsed

        # Use thread to avoid blocking event loop
        result = await asyncio.to_thread(_sync_generate)

        if isinstance(result, RankingResponse):
            return result.articles[:5]

        if isinstance(result, dict) and "articles" in result:
            return [RankedArticle(**a) for a in result["articles"][:5]]

        raise ValueError(f"Unexpected response type: {type(result)}")
