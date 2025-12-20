"""Keyword generator service using Google GenAI."""

import asyncio
import os

from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import LLM_MAX_RETRIES, LLM_MODEL


class KeywordResult(BaseModel):
    """Result from keyword generation."""

    topic_summary: str
    keywords: list[str]


class KeywordGeneratorService:
    """Service for generating diverse perspective search keywords."""

    PROMPT = """당신은 한국어 뉴스 분석 전문가입니다.

아래 텍스트의 주제/사건을 파악하고, 해당 주제에 대해 **다양한 시각이나 관점**을
찾기 위한 검색 키워드 3-5개를 생성하세요.

이 작업의 목적은 하나의 주제에 대해 여러 관점을 가진 기사를 찾아,
독자가 균형 잡힌 시각을 가질 수 있도록 돕는 것입니다.

키워드 요건:
- 같은 주제/사건에 대한 다른 의견을 찾을 수 있는 키워드
- 한국어로 작성
- 각 키워드는 2-4 단어
- 원문과 다른 시각에서 바라보는 키워드

예시:
- 원문이 "금리 인하 필요성"을 주장하면 → "금리 인상 신중론", "물가 안정 우선" 등
- 원문이 "재개발 긍정론"이면 → "재개발 신중론", "원주민 정착 지원" 등

입력 텍스트:
{input_text}"""

    def __init__(self, api_key: str | None = None):
        """Initialize the keyword generator service.

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

    @retry(
        stop=stop_after_attempt(LLM_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def generate_diverse_keywords(
        self, input_text: str
    ) -> KeywordResult:
        """Generate keywords for finding diverse perspectives on input text.

        Args:
            input_text: The text to analyze.

        Returns:
            KeywordResult with topic summary and keywords for diverse views.
        """
        prompt = self.PROMPT.format(input_text=input_text)

        def _sync_generate():
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=KeywordResult,
                ),
            )
            return response.parsed

        # Use thread to avoid blocking event loop
        result = await asyncio.to_thread(_sync_generate)

        if isinstance(result, KeywordResult):
            # Enforce 3-5 keywords limit
            if len(result.keywords) > 5:
                result = KeywordResult(
                    topic_summary=result.topic_summary,
                    keywords=result.keywords[:5],
                )
            return result

        # Fallback: try to construct from dict
        if isinstance(result, dict):
            keywords = result.get("keywords", [])[:5]  # Limit to 5
            return KeywordResult(
                topic_summary=result.get("topic_summary", ""),
                keywords=keywords,
            )

        raise ValueError(f"Unexpected response type: {type(result)}")
