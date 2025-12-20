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
    """Service for generating opposing/challenging search keywords."""

    PROMPT = """당신은 한국어 뉴스 분석 전문가입니다.

아래 텍스트의 주제/사건을 파악하고, 해당 주제에 대해 **반대되거나 도전적인 관점**을
찾기 위한 검색 키워드 3-5개를 생성하세요.

키워드 요건:
- 같은 주제/사건에 대한 반대 의견을 찾을 수 있는 키워드
- 한국어로 작성
- 각 키워드는 2-4 단어
- 원문의 관점에 도전하는 키워드여야 함

예시:
- 원문이 "금리 인하 필요성"을 주장하면 → "금리 인상 필요", "인플레이션 우려" 등
- 원문이 "재개발 찬성"이면 → "재개발 반대", "원주민 이주 문제" 등

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
    async def generate_opposing_keywords(
        self, input_text: str
    ) -> KeywordResult:
        """Generate opposing/challenging keywords for input text.

        Args:
            input_text: The text to analyze.

        Returns:
            KeywordResult with topic summary and opposing keywords.
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
