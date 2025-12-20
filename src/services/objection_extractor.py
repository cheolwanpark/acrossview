"""Different view extractor service using Google GenAI."""

import asyncio
import os
from dataclasses import dataclass
from difflib import SequenceMatcher

from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import LLM_MAX_RETRIES, LLM_MODEL
from .vector_db import SearchResult


class ArticleQuote(BaseModel):
    """A quote from a specific article."""

    article_id: int
    quote: str


class LLMDifferentView(BaseModel):
    """A different view as returned by LLM."""

    exact_text: str
    different_view: str
    quotes: list[ArticleQuote]


class ExtractionResponse(BaseModel):
    """Response from the extraction model."""

    different_views: list[LLMDifferentView]


@dataclass
class ArticleReference:
    """A reference to an article supporting a different view."""

    title: str
    quote: str
    url: str


@dataclass
class DifferentViewResult:
    """A different perspective on a claim in the input text."""

    exact_text: str
    different_view: str
    reference: list[ArticleReference]


class DifferentViewExtractorService:
    """Service for extracting different views with quotes from articles."""

    MAX_CANDIDATES = 15
    MAX_BODY_LENGTH = 1500  # Truncate article body to avoid context overflow
    # Fuzzy match threshold - 0.85 allows for minor spacing/punctuation differences
    # in Korean text while rejecting significant paraphrases
    FUZZY_MATCH_THRESHOLD = 0.85

    PROMPT = """당신은 뉴스 분석 전문가입니다.

사용자의 입력 텍스트를 읽고, 해당 내용에 대해 **함께 생각해볼 만한 다른 관점**을
제공된 기사들에서 찾아 정리해주세요.

이 작업의 목적은 독자가 하나의 주제에 대해 여러 관점을 접하고,
보다 균형 잡힌 이해를 가질 수 있도록 돕는 것입니다.
비판이 아닌, "이런 시각도 있다"는 정보 제공에 초점을 맞춰주세요.

## 입력 텍스트
{input_text}

## 참고 기사 목록
{formatted_articles}

## 출력 규칙
1. exact_text: 입력 텍스트에서 다른 관점을 제시할 **정확한 문장이나 구절** (원문 그대로 복사)

2. different_view: 해당 문장에 대한 다른 관점을 **풍부한 맥락과 함께 상세히** 설명 (4-6문장)
   반드시 다음 요소들을 포함해주세요:
   - 어떤 사람들/단체가 이런 시각을 가지고 있는지 (예: 전문가, 시민단체, 업계 관계자 등)
   - 왜 이런 다른 시각이 존재하는지 배경이나 이유
   - 이 관점에서 우려하거나 강조하는 핵심 포인트
   - 더 넓은 사회적/경제적 맥락과의 연결점
   - "~라는 의견도 있습니다", "~를 우려하는 시각도 있습니다", "~라고 주장합니다" 등 부드러운 표현 사용

3. quotes: 해당 관점을 뒷받침하는 기사 인용문 목록
   - article_id: 기사 ID 번호 (위의 [기사 ID: X]에서 X)
   - quote: 기사에서 발췌한 **정확한 문장** (원문 그대로 복사, 수정하지 마세요)

## 예시
좋은 예:
"이 정책에 대해 경제학자들과 시민단체에서는 다른 시각을 제시하고 있습니다.
일부 전문가들은 급격한 규제 완화가 단기적인 경제 성장에는 도움이 될 수 있지만,
장기적으로는 소비자 보호 장치가 약화될 수 있다고 우려합니다.
특히 중소기업 협회에서는 대기업에 유리한 환경이 조성되면서
시장 내 경쟁이 오히려 줄어들 수 있다는 점을 지적하고 있습니다.
또한 노동계에서는 근로자의 권익 보호가 후순위로 밀릴 수 있다는
염려를 표명하고 있으며, 이는 과거 유사한 정책 시행 당시의
부작용 사례를 근거로 들고 있습니다."

나쁜 예:
"규제 완화에 반대하는 의견도 있음"

## 중요
- 최대 5개의 다른 관점을 추출하세요
- 기사에 명확한 근거가 없으면 해당 항목은 제외하세요
- quote는 반드시 기사 원문에서 그대로 복사해야 합니다. 임의로 변경하지 마세요."""

    def __init__(self, api_key: str | None = None):
        """Initialize the different view extractor service.

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

    def _format_articles(
        self, candidates: list[SearchResult]
    ) -> tuple[str, dict[int, str]]:
        """Format candidate articles for the prompt.

        Args:
            candidates: List of candidate articles.

        Returns:
            Tuple of (formatted_string, truncated_bodies_map).
            truncated_bodies_map maps article_id to the truncated body sent to LLM.
        """
        formatted = []
        truncated_bodies: dict[int, str] = {}

        for article in candidates:
            # Truncate body to avoid context overflow
            body = article.body or ""
            truncated = body[: self.MAX_BODY_LENGTH]
            truncated_bodies[article.article_id] = truncated

            display_body = truncated
            if len(body) > self.MAX_BODY_LENGTH:
                display_body += "..."

            formatted.append(
                f"[기사 ID: {article.article_id}]\n"
                f"제목: {article.title or '제목 없음'}\n"
                f"출처: {article.media_outlet or '알 수 없음'} ({article.date or '날짜 없음'})\n"
                f"내용:\n{display_body}\n"
            )
        return "\n---\n".join(formatted), truncated_bodies

    def _validate_text_in_source(self, text: str, source: str) -> bool:
        """Check if text exists in source using fuzzy matching.

        Args:
            text: The text to find.
            source: The source text to search in.

        Returns:
            True if the text is found (exact or fuzzy match).
        """
        if not text or not source:
            return False

        # Normalize whitespace
        normalized_text = " ".join(text.split())
        normalized_source = " ".join(source.split())

        # Exact substring match (fast path)
        if normalized_text in normalized_source:
            return True

        text_len = len(normalized_text)
        if text_len > len(normalized_source):
            return False

        # For short quotes, check more positions
        # For longer quotes, use larger steps to avoid O(n*m) complexity
        if text_len < 100:
            step = max(1, text_len // 8)
        else:
            step = max(1, text_len // 4)

        for i in range(0, len(normalized_source) - text_len + 1, step):
            window = normalized_source[i : i + text_len]
            ratio = SequenceMatcher(None, normalized_text, window).ratio()
            if ratio >= self.FUZZY_MATCH_THRESHOLD:
                return True

        return False

    def _build_different_view_results(
        self,
        llm_response: ExtractionResponse,
        candidates: list[SearchResult],
        truncated_bodies: dict[int, str],
        input_text: str,
    ) -> list[DifferentViewResult]:
        """Map LLM response to final DifferentViewResult with article metadata.

        Args:
            llm_response: The response from LLM extraction.
            candidates: The candidate articles with metadata.
            truncated_bodies: Map of article_id to truncated body (what LLM saw).
            input_text: The original input text for exact_text validation.

        Returns:
            List of DifferentViewResult with validated quotes.
        """
        article_map = {c.article_id: c for c in candidates}

        results = []
        for view in llm_response.different_views:
            # Validate exact_text is actually from the input
            if not self._validate_text_in_source(view.exact_text, input_text):
                continue

            references = []
            for quote_ref in view.quotes:
                article = article_map.get(quote_ref.article_id)
                truncated_body = truncated_bodies.get(quote_ref.article_id, "")

                # Validate quote against the truncated body (what LLM actually saw)
                if article and self._validate_text_in_source(
                    quote_ref.quote, truncated_body
                ):
                    references.append(
                        ArticleReference(
                            title=article.title or "제목 없음",
                            quote=quote_ref.quote,
                            url=article.url or "",
                        )
                    )

            # Only include views with valid references
            if references:
                results.append(
                    DifferentViewResult(
                        exact_text=view.exact_text,
                        different_view=view.different_view,
                        reference=references,
                    )
                )

        return results

    @retry(
        stop=stop_after_attempt(LLM_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def extract_different_views(
        self, input_text: str, candidates: list[SearchResult]
    ) -> list[DifferentViewResult]:
        """Extract different views from input text using candidate articles.

        Args:
            input_text: The original input text.
            candidates: List of candidate articles from vector search.

        Returns:
            List of DifferentViewResult with validated quotes and references.
        """
        # Truncate to MAX_CANDIDATES
        candidates = candidates[: self.MAX_CANDIDATES]

        if not candidates:
            return []

        formatted_articles, truncated_bodies = self._format_articles(candidates)

        prompt = self.PROMPT.format(
            input_text=input_text,
            formatted_articles=formatted_articles,
        )

        def _sync_generate():
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractionResponse,
                ),
            )
            return response.parsed

        # Use thread to avoid blocking event loop
        result = await asyncio.to_thread(_sync_generate)

        if isinstance(result, ExtractionResponse):
            return self._build_different_view_results(
                result, candidates, truncated_bodies, input_text
            )

        if isinstance(result, dict) and "different_views" in result:
            parsed = ExtractionResponse(**result)
            return self._build_different_view_results(
                parsed, candidates, truncated_bodies, input_text
            )

        raise ValueError(f"Unexpected response type: {type(result)}")
