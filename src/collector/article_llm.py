from __future__ import annotations

from dataclasses import dataclass

from openai_client.client import LLMClient
from summary_utils import clean_string, normalize_summary

from .settings import (
    DEFAULT_LLM_ARTICLE_MODEL,
    DEFAULT_LLM_SUMMARY_MODEL,
)


@dataclass(frozen=True)
class ArticleAnalysis:
    """기사 단위 LLM 분석 결과."""

    summary: str
    keywords: list[str]


class ArticleLLMAnalyzer:
    """기사 요약을 LLM으로 수행한다."""

    def __init__(
        self,
        *,
        article_model: str = DEFAULT_LLM_ARTICLE_MODEL,
        summary_model: str = DEFAULT_LLM_SUMMARY_MODEL,
    ) -> None:
        self.article_model = article_model
        self.summary_model = summary_model
        self._clients: dict[str, LLMClient] = {}

    def analyze(
        self,
        *,
        title: str,
        subtitle: str,
        category: str,
        content: str,
    ) -> ArticleAnalysis:
        content = _clean_string(content)
        summary = self._summarize(title=title, category=category, content=content)
        return ArticleAnalysis(
            summary=summary,
            keywords=[],
        )

    def _client(self, model: str) -> LLMClient:
        if model not in self._clients:
            self._clients[model] = LLMClient(model=model)
        return self._clients[model]

    def _summarize(self, *, title: str, category: str, content: str) -> str:
        prompt = f"""다음 한국어 뉴스 기사를 요약하세요.

아래 JSON 객체만 반환하세요:
{{"summary": "중립적인 한국어 요약 4문장"}}

규칙:
- 기사 본문에 있는 사실만 사용하세요.
- 광고, 관련 기사, 저작권, 기자 프로필 문구는 요약에 포함하지 마세요.
- 사실 중심의 중립적인 문장으로 작성하세요.
- 기본 4문장, 전체 400~650자 정도로 작성하세요.
- 1문장은 핵심 사건/변화(누가, 무엇을 했는지), 2문장은 배경/원인/맥락, 3문장은 현재 결과/상태/수치/영향, 4문장은 후속 쟁점/반응/예정된 절차를 담으세요.
- 경제, 국제, 사회, 정치 등 모든 카테고리에 같은 구조를 적용하되, 경제 기사는 기업·시장·수치·정책 영향, 국제 기사는 국가·기관·외교/안보 맥락, 사회 기사는 피해·기관 조치·제도 쟁점을 우선 포함하세요.
- 나중에 이벤트/토픽 임베딩에 사용할 수 있도록 사건명, 주요 주체, 대상, 지역, 날짜, 수치, 상태 변화를 가능한 한 명시하세요.

제목: {title}
카테고리: {category}
기사 본문:
{content}
"""
        data = self._client(self.summary_model).request_json(
            prompt,
            required_keys={"summary"},
            temperature=0,
        )
        summary = normalize_summary(data.get("summary"))
        if not summary:
            raise ValueError("LLM summary is empty.")
        return summary


def _clean_string(value) -> str:
    return clean_string(value)
