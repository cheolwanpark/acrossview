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

    MAX_CANDIDATES = 10
    MAX_BODY_LENGTH = 100_000  # Effectively no limit - include full article body
    # Fuzzy match threshold - 0.70 allows for spacing/punctuation differences
    # and minor paraphrasing in Korean text
    FUZZY_MATCH_THRESHOLD = 0.70

    PROMPT = """당신은 뉴스 분석 전문가입니다.

입력 텍스트의 주장/관점에 대해 **대조되는 다른 시각**을 참고 기사에서 찾아주세요.

## 핵심 원칙 (반드시 준수)

1. **대조되는 관점만**: 입력 텍스트의 주장을 **지지하거나 동조하는 내용은 절대 포함하지 마세요**.
   반드시 입력 텍스트와 **다른 입장, 우려, 반론, 비판**만 포함합니다.

2. **직접적 관련성**: different_view는 exact_text의 주장과 **직접적으로 대응**해야 합니다.
   - exact_text가 "A가 좋다"면 → different_view는 "A의 문제점" 또는 "A에 대한 우려"
   - 같은 주제라도 exact_text와 무관한 일반론은 제외

3. **근거 기반**: 참고 기사에 명확한 근거가 있는 경우에만 포함

## 입력 텍스트
{input_text}

## 참고 기사 목록
{formatted_articles}

## 출력 규칙

1. exact_text: 입력 텍스트에서 **논쟁의 여지가 있는** 문장/구절 (원문 그대로 복사)
   - 주장, 의견, 전망, 평가 등을 선택
   - 단순 사실(날짜, 수치, 고유명사)만 있는 문장은 피함

2. different_view: exact_text에 **대조되는 관점** 설명 (4-6문장)
   포함 요소:
   - 누가 이런 다른 시각을 가지는지 (전문가, 시민단체, 업계 등)
   - 왜 exact_text와 다른 시각이 존재하는지
   - 이 관점의 핵심 우려/주장
   - "~라는 의견도 있습니다", "~를 우려합니다" 등 부드러운 표현

3. quotes: 관점을 뒷받침하는 기사 인용문
   - article_id: 기사 ID 번호
   - quote: 기사 원문 **정확히 그대로** 복사 (수정 금지)

## 예시

### ✅ 좋은 예시
입력: "이번 금리 인하는 경기 활성화에 크게 기여할 것이다"
exact_text: "금리 인하는 경기 활성화에 크게 기여할 것이다"
different_view: "일부 경제학자들은 금리 인하 효과에 신중한 시각을 보입니다.
현재 가계부채가 높은 상황에서 금리 인하가 부채 증가를 부추길 수 있다는 우려가 있습니다.
또한 부동산 시장 과열을 자극할 가능성도 제기되고 있으며,
한국은행 내부에서도 물가 안정과의 균형을 고려해야 한다는 의견이 있습니다."
→ exact_text(금리인하 긍정)에 대해 different_view(금리인하 우려)로 직접 대조됨

### ❌ 나쁜 예시 (하지 마세요)

1. 입력을 지지하는 경우:
   입력: "금리 인하가 필요하다"
   different_view: "금리 인하가 경제에 도움이 될 것이다" ← 동조하는 내용

2. 무관한 내용:
   exact_text: "금리 인하가 필요하다"
   different_view: "환율 정책에 대한 논의가 있다" ← exact_text와 무관

3. 너무 짧거나 피상적:
   different_view: "반대 의견도 있음" ← 구체성 없음

## 중요
- 최대 5개의 다른 관점
- **입력을 지지/동조하는 내용 절대 금지**
- different_view는 exact_text에 **직접 대응하는 반론/우려**여야 함
- quote는 원문 그대로 (임의 변경 금지)"""

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

        Uses a word-boundary approach that checks all positions where words start,
        ensuring no matches are missed due to step-based skipping.

        Args:
            text: The text to find.
            source: The source text to search in.

        Returns:
            True if the text is found (exact or fuzzy match).
        """
        if not text or not source:
            return False

        # Normalize whitespace for consistent matching
        normalized_text = " ".join(text.split())
        normalized_source = " ".join(source.split())

        # Fast path: exact substring match
        if normalized_text in normalized_source:
            return True

        text_len = len(normalized_text)
        source_len = len(normalized_source)

        if text_len > source_len:
            return False

        # Collect all word boundary positions (where words start)
        # Quotes naturally start at word boundaries, so this catches most cases
        word_starts = [0]
        for i in range(source_len - 1):
            if normalized_source[i] == " ":
                word_starts.append(i + 1)

        # Check fuzzy match at each word boundary position
        for pos in word_starts:
            end_pos = pos + text_len
            if end_pos > source_len:
                continue

            window = normalized_source[pos:end_pos]
            # Use autojunk=False for more accurate matching
            ratio = SequenceMatcher(
                None, normalized_text, window, autojunk=False
            ).ratio()
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
