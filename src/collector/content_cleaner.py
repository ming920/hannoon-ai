from __future__ import annotations

import re
from typing import Protocol

from openai_client.client import LLMClient, parse_json_object

from .settings import DEFAULT_LLM_CLEANUP_MODEL


class CompletionClient(Protocol):
    """본문 정제 테스트에서 LLM 클라이언트를 대체하기 위한 최소 인터페이스."""

    def request(self, prompt: str, **kwargs) -> str:
        ...


BOILERPLATE_KEYWORDS = (
    "광고",
    "구독",
    "앱에서 보기",
    "관련기사",
    "관련 기사",
    "많이 본 뉴스",
    "인기뉴스",
    "제보",
    "무단전재",
    "재배포 금지",
    "저작권자",
    "copyright",
    "all rights reserved",
    "페이스북",
    "카카오톡",
    "url 복사",
    "공유하기",
    "댓글",
    "뉴스레터",
    "알림 받기",
    "기자 페이지",
)

STRONG_BOILERPLATE_KEYWORDS = (
    "무단전재",
    "재배포 금지",
    "copyright",
    "all rights reserved",
    "url 복사",
    "앱에서 보기",
)


# 기사 본문 끝에 붙는 저작권/제보/재배포 푸터의 시작 마커. baseline 진단에서 관찰된
# 연합("제보는 카카오톡 <저작권자(c)…"), 뉴시스("◎공감언론…Copyright"), 세계("기자페이지
# 바로가기 Copyright ⓒ"), 서울("Copyright ⓒ…All rights reserved") 등을 근거로 한다.
# 저작권/IP 주제 기사의 본문 후반에 '무단 전재' 같은 단어가 정상적으로 나올 수 있어,
# 마커는 되도록 푸터 정형구(저작권자 뒤 (·ⓒ·<, '재배포 금지' 등)로 좁힌다.
_FOOTER_MARKERS = re.compile(
    r"제보는|◎공감언론|저작권자\s*[(<ⓒ©]|재배포\s*금지|기자\s*페이지|"
    r"기사\s*문의|당신이 담은 순간|GoodNews|"  # 연합뉴스TV 제보 CTA 블록, 국민일보 푸터
    r"copyright\s*[(©ⓒ]|all\s+rights\s+reserved|"
    # 맨몸 ⓒ/© 는 뒤에 언론사명이 따라오는 저작권 표기일 때만 푸터로 본다.
    # (본문 중 "작품에 ⓒ 표시가 붙어" 같은 언급 오절단 방지)
    r"[ⓒ©]\s*\S{0,12}(뉴스|일보|신문|닷컴|미디어|방송|경제|데일리|타임스)",
    re.IGNORECASE,
)


def strip_boilerplate_footer(text: str) -> str:
    """기사 본문 끝에 붙는 저작권/제보/재배포 푸터를 LLM 없이 잘라낸다.

    본문 후반부(60% 이후)에서 저작권·제보 마커를 찾고, 그 앞 150자 안에 더 이른 푸터
    마커(예: '저작권자(' 앞의 '제보는')가 있으면 거기까지 포함해 끝까지 제거한다.
    마커를 후반부로 한정해 본문 중간의 우연한 언급(예: "…페이스북을 통해 밝혔다")은 보존한다.
    """
    if not text:
        return text
    search_start = max(0, int(len(text) * 0.6))
    match = _FOOTER_MARKERS.search(text, search_start)
    if not match:
        return text
    # 매치 지점이 푸터 중간일 수 있으므로 바로 앞 구간에서 더 이른 마커(푸터 시작)를 되짚는다.
    cut = match.start()
    earlier = _FOOTER_MARKERS.search(text, max(0, cut - 150), cut)
    if earlier:
        cut = earlier.start()
    # 본문과 푸터 사이의 구분자(공백·중점·대시·쉼표)만 정리하고 문장 종결 온점은 보존한다.
    trimmed = text[:cut].rstrip(" \t\n·-—,")
    # 푸터만 있고 본문이 사라지는 과잉 절단은 피한다(마커가 본문 앞쪽이면 원본 유지).
    if len(trimmed) < 100:
        return text
    return trimmed


PROMPT_TEMPLATE = """You clean extracted news article text.

Return JSON only in this exact shape:
{{"content": "cleaned article text"}}

Rules:
- Keep the original article facts, order, names, dates, quotes, and meaning.
- Do not summarize, translate, rewrite, add new facts, or add commentary.
- Remove advertising, sponsorship blocks, subscription prompts, app download prompts,
  newsletter prompts, social sharing labels, navigation text, related-article lists,
  copyright/footer text, reporter profile blurbs, and nonessential photo captions.
- If a sentence could be part of the article body, keep it.
- If the input is already clean, return it unchanged.
- Preserve the original language of the article.

Article text:
{text}
"""


class ArticleTextCleaner:
    """크롤링 본문을 LLM으로 정제하는 얇은 래퍼."""

    def __init__(self, model: str | None = None):
        self._client = LLMClient(model=model or DEFAULT_LLM_CLEANUP_MODEL)

    def clean(self, text: str) -> str:
        return clean_article_text(text, self._client)


def should_llm_cleanup(text: str) -> tuple[bool, list[str]]:
    """본문에 광고/공유 UI 등 정제가 필요한 흔적이 있는지 휴리스틱으로 판단한다."""
    normalized = " ".join(text.split())
    if not normalized:
        return False, []

    reasons: list[str] = []
    lower_text = normalized.lower()

    if len(normalized) > 8000:
        reasons.append("too_long")

    keyword_hits = [
        keyword for keyword in BOILERPLATE_KEYWORDS if keyword.lower() in lower_text
    ]
    if len(keyword_hits) >= 2:
        reasons.append("boilerplate_keywords:" + ",".join(keyword_hits[:5]))

    strong_hits = [
        keyword for keyword in STRONG_BOILERPLATE_KEYWORDS if keyword.lower() in lower_text
    ]
    if strong_hits:
        reasons.append("strong_boilerplate:" + ",".join(strong_hits[:3]))

    related_count = normalized.count("관련기사") + normalized.count("관련 기사")
    if related_count >= 2:
        reasons.append("repeated_related_articles")

    share_count = sum(
        normalized.count(keyword)
        for keyword in ("공유하기", "페이스북", "카카오톡", "URL 복사", "url 복사")
    )
    if share_count >= 2:
        reasons.append("share_ui_noise")

    return bool(reasons), reasons


def clean_article_text(text: str, client: CompletionClient) -> str:
    """LLM에 본문 정제를 요청하고 빈 결과를 방어한다."""
    prompt = PROMPT_TEMPLATE.format(text=text)
    response = client.request(
        prompt,
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = parse_json_object(response)
    cleaned = data.get("content")
    if not isinstance(cleaned, str):
        raise ValueError("LLM cleanup response is missing string field 'content'.")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        raise ValueError("LLM cleanup returned empty content.")
    return cleaned
