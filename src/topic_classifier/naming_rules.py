"""서브토픽 제목의 불포함 규칙(S-1~S-4, docs/entity_definitions.md) 코드 가드.

패턴은 eval/rubric_checks.py의 R-S2/R-S3/R-S4 정규식·유사도 판정과 동일 알고리즘을
유지한다. src는 eval을 임포트할 수 없으므로(계층 경계) 독립적으로 구현한다.

위반 시 파이프라인(topic_classifier.pipeline._sanitize_subtopic_title)은 이 제목으로
새 서브토픽을 만들지 않고 cause 기반 명사구로 재명명을 시도한다.
"""
from __future__ import annotations

import re

from summary_utils import title_similarity


_ORDINAL_DAYS = (
    "첫째", "둘째", "셋째", "넷째", "다섯째", "여섯째", "일곱째", "여덟째", "아홉째", "열째",
)
_TIME_SEGMENT_RE = re.compile(
    r"(?:" + "|".join(_ORDINAL_DAYS) + r")\s*(?:날|일)"
    r"|\d+\s*(?:일차|일째|주차|차)\b"
)

_ATTRIBUTE_KEYWORDS = ("찬성", "반대", "긍정", "부정", "옹호", "비판")
_ATTRIBUTE_SUFFIXES = ("측", "파", "론", "층", "여론", "입장", "쪽", "적")
_ATTRIBUTE_RE = re.compile(
    r"(?:^|\s)(?:" + "|".join(_ATTRIBUTE_KEYWORDS) + r")"
    r"(?:" + "|".join(_ATTRIBUTE_SUFFIXES) + r")?"
    r"(?=$|\s)"
)

# S-1(이벤트 1:1 복제) 판정 유사도. R-T1/R-S4의 0.85보다 높게 잡아, 트리거 이벤트
# 제목과 "거의 동일"한 경우만 걸러낸다(우연히 겹치는 핵심 단어 몇 개로 오탐하지 않도록).
EVENT_COPY_SIM_THRESHOLD = 0.90
# S-4(부모와 동일 범위) 판정 유사도. eval/rubric_checks.py R-S4와 동일한 0.85.
PARENT_SCOPE_SIM_THRESHOLD = 0.85


def is_time_segment_title(title: str) -> bool:
    """S-2: 단순 시간 구분(첫째 날, N일차, N차, N주차, N일째 등) 제목인지 판정한다."""
    return bool(_TIME_SEGMENT_RE.search(title or ""))


def is_attribute_title(title: str) -> bool:
    """S-3: 찬반·논조 같은 기사 속성을 주개념으로 삼는 제목인지 판정한다."""
    return bool(_ATTRIBUTE_RE.search(title or ""))


def is_event_copy_title(title: str, event_title: str) -> bool:
    """S-1: 서브토픽 제목이 트리거 이벤트 제목의 1:1 복제(고유사)인지 판정한다."""
    return title_similarity(title, event_title) >= EVENT_COPY_SIM_THRESHOLD


def is_parent_scope_title(title: str, parent_title: str) -> bool:
    """S-4: 서브토픽 제목이 부모 토픽과 동일 범위(고유사)인지 판정한다."""
    return title_similarity(title, parent_title) >= PARENT_SCOPE_SIM_THRESHOLD


def violates_subtopic_naming(title: str, *, event_title: str, parent_title: str) -> str | None:
    """4규칙 중 하나라도 위반하면 위반 사유 코드를 반환하고, 통과하면 None을 반환한다.

    검사 순서는 임의 우선순위이며(첫 위반만 보고), 어느 하나라도 걸리면 재명명 대상이다.
    """
    if not title:
        return "empty"
    if is_time_segment_title(title):
        return "S-2 시간구분형"
    if is_attribute_title(title):
        return "S-3 속성형"
    if is_event_copy_title(title, event_title):
        return "S-1 이벤트복사형"
    if is_parent_scope_title(title, parent_title):
        return "S-4 부모동일범위형"
    return None
