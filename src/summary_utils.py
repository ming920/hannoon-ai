from __future__ import annotations

import difflib
import re


SUMMARY_MAX_SENTENCES = 4
SUMMARY_MAX_CHARS = 700
TOPIC_TITLE_MAX_CHARS = 30
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?\u3002\uff01\uff1f])\s+")
_TITLE_TRAILING_RE = re.compile(r"[\s.,!?;:\u3002\uff01\uff1f\uff0c\uff1b\uff1a\u3001]+$")


def clean_string(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def normalize_summary(
    value,
    *,
    max_sentences: int = SUMMARY_MAX_SENTENCES,
    max_chars: int = SUMMARY_MAX_CHARS,
) -> str:
    text = clean_string(value)
    if not text:
        return ""

    if max_sentences > 0:
        sentences = [
            part.strip()
            for part in _SENTENCE_BOUNDARY_RE.split(text)
            if part.strip()
        ]
        if sentences:
            text = " ".join(sentences[:max_sentences])

    if max_chars <= 0 or len(text) <= max_chars:
        return text

    clipped = text[:max_chars].rstrip()
    boundary = max(
        clipped.rfind(". "),
        clipped.rfind("! "),
        clipped.rfind("? "),
        clipped.rfind("\u3002"),
        clipped.rfind("\uff01"),
        clipped.rfind("\uff1f"),
    )
    if boundary >= max_chars // 2:
        return clipped[: boundary + 1].rstrip()
    return clipped[: max_chars - 3].rstrip() + "..."


def normalize_topic_title(
    value,
    *,
    max_chars: int = TOPIC_TITLE_MAX_CHARS,
) -> str:
    text = clean_string(value).strip(" \"'`")
    if not text:
        return ""

    text = _TITLE_TRAILING_RE.sub("", text)
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    clipped = text[:max_chars].rstrip()
    boundary = max(
        clipped.rfind(" "),
        clipped.rfind(","),
        clipped.rfind("\uff0c"),
        clipped.rfind("\u3001"),
        clipped.rfind("\u00b7"),
    )
    if boundary >= int(max_chars * 0.65):
        clipped = clipped[:boundary].rstrip()
    return _TITLE_TRAILING_RE.sub("", clipped)


def title_similarity(a: str | None, b: str | None) -> float:
    """difflib.SequenceMatcher 기반 두 제목의 유사도(0~1)를 반환한다.

    eval/rubric_checks.py의 동명 함수와 동일 알고리즘이다. src는 eval을 임포트할 수
    없으므로(계층 경계) 여기서 독립적으로 구현하고, 토픽 중복 방지·서브토픽 명명
    가드(topic_classifier)와 eval 쪽 루브릭 검사가 같은 판정 기준을 공유하게 한다.
    """
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()
