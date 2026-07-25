"""새 엔티티 정의(`docs/entity_definitions.md`) 기준 루브릭 위반을 DB 스냅샷에서 산출한다.

기계 판정 가능한 기준만 다룬다. "동일 현실 사건인가", 네이밍 테스트 등은 이 스크립트로
판정할 수 없으며 `docs/entity_definitions.md`에 수동 판정으로 명시되어 있다.

사용 예:
  python eval/rubric_checks.py --database-url "postgresql://..."
  python eval/rubric_checks.py --json > rubric_report.json

  # DATABASE_URL 환경변수로도 접속 정보를 줄 수 있다 (--database-url이 우선)
  DATABASE_URL="postgresql://..." python eval/rubric_checks.py

검사 ID:
  R-E1  단일기사 이벤트 비율 (event_articles 매핑 기준)
  R-E2  동일 토픽 내 유사 제목 이벤트 쌍 (쪼개짐 의심 신호)
  R-E2U 미배정(topic_id IS NULL) 이벤트 유사 제목 쌍 (category 버킷, R-E2의 사각지대 보완)
  R-S1  단일 이벤트 서브토픽 비율
  R-S2  시간 구분형 서브토픽 제목
  R-S3  속성형(찬반 등) 서브토픽 제목
  R-S4  토픽과 동일 범위인 서브토픽 제목
  R-S5  계층 정합성 (비-leaf 토픽에 이벤트 직결, parent_topic_id 깊이 초과)
  R-T1  중복·고유사 토픽 쌍 (최상위 토픽끼리)
  R-T2  카테고리형 토픽 제목

읽는 DB 테이블·컬럼:
  - events (id, title, topic_id, category)
  - event_articles (event_id, article_id)
  - topics (id, title, parent_topic_id)

topics.parent_topic_id가 없는 스키마(서브토픽 마이그레이션 20260701120000 미적용 DB)에서는
서브토픽 검사(R-S1~R-S5)를 건너뛰고 R-T1/R-T2는 전체 토픽을 최상위로 간주해 계속 동작한다.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

# hannoon-ai/src를 경로에 추가해 collector.storage를 임포트한다.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from collector.storage import ensure_db  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# 순수 함수 — 제목 패턴 분류기 / 유사도 판정 (DB 의존 없음, import 가능)
# ══════════════════════════════════════════════════════════════════════════


def classify_member_count(count: int) -> str:
    """묶음 내 하위 항목 수를 무항목/단일/다중으로 분류한다.

    이벤트의 기사 수(R-E1), 서브토픽의 이벤트 수(R-S1) 판정에 공용으로 쓴다.
    """
    if count == 0:
        return "무항목"
    if count == 1:
        return "단일"
    return "다중"


def title_similarity(a: str | None, b: str | None) -> float:
    """difflib.SequenceMatcher 기반 두 제목의 유사도(0~1)를 반환한다."""
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


def is_similar_title(a: str | None, b: str | None, threshold: float) -> bool:
    """두 제목의 유사도가 threshold 이상인지 판정한다."""
    return title_similarity(a, b) >= threshold


def find_similar_title_pairs(
    items: list[tuple[int, str]], threshold: float
) -> list[tuple[int, str, int, str, float]]:
    """(id, title) 목록에서 유사도가 threshold 이상인 모든 쌍을 반환한다.

    O(n^2) 완전탐색이므로 그룹 크기가 매우 큰 경우 호출부에서 주의해서 써야 한다.
    반환: (id_a, title_a, id_b, title_b, ratio) 리스트, ratio 내림차순.
    """
    pairs: list[tuple[int, str, int, str, float]] = []
    for i in range(len(items)):
        id_a, title_a = items[i]
        for j in range(i + 1, len(items)):
            id_b, title_b = items[j]
            ratio = title_similarity(title_a, title_b)
            if ratio >= threshold:
                pairs.append((id_a, title_a, id_b, title_b, ratio))
    pairs.sort(key=lambda p: p[4], reverse=True)
    return pairs


# ── R-S2: 시간 구분형 제목 ───────────────────────────────────────────────────

_ORDINAL_DAYS = (
    "첫째", "둘째", "셋째", "넷째", "다섯째", "여섯째", "일곱째", "여덟째", "아홉째", "열째",
)
_TIME_SEGMENT_RE = re.compile(
    r"(?:" + "|".join(_ORDINAL_DAYS) + r")\s*(?:날|일)"
    r"|\d+\s*(?:일차|일째|주차|차)\b"
)


def classify_time_based_subtopic_title(title: str) -> bool:
    """서브토픽 제목이 단순 시간 구분(첫째 날, N일차, N차, N주차, N일째 등)인지 판정한다.

    이벤트 제목에는 이런 표현이 정상적일 수 있으므로 서브토픽 제목에만 적용한다.
    """
    return bool(_TIME_SEGMENT_RE.search(title or ""))


# ── R-S3: 속성형(찬반 등) 제목 ───────────────────────────────────────────────

_ATTRIBUTE_KEYWORDS = ("찬성", "반대", "긍정", "부정", "옹호", "비판")
_ATTRIBUTE_SUFFIXES = ("측", "파", "론", "층", "여론", "입장", "쪽", "적")
_ATTRIBUTE_RE = re.compile(
    r"(?:^|\s)(?:" + "|".join(_ATTRIBUTE_KEYWORDS) + r")"
    r"(?:" + "|".join(_ATTRIBUTE_SUFFIXES) + r")?"
    r"(?=$|\s)"
)


def classify_attribute_subtopic_title(title: str) -> bool:
    """서브토픽 제목이 찬반/논조 같은 기사 속성을 주개념으로 삼는지 판정한다.

    "부정선거"처럼 속성 키워드가 다른 명사와 붙어 복합명사를 이루는 경우는
    제외해야 하므로, 키워드가 공백/일반 접미어로 경계 지어진 경우만 매치한다.
    """
    return bool(_ATTRIBUTE_RE.search(title or ""))


# ── R-T2: 카테고리형 제목 ─────────────────────────────────────────────────────

_GENERIC_CATEGORIES = ("정치", "경제", "사회", "국제", "문화", "스포츠", "IT", "연예")
_GENERIC_SUFFIXES = ("뉴스", "소식", "이슈", "전체", "종합")
_CATEGORY_TITLE_RE = re.compile(
    r"^(?:" + "|".join(_GENERIC_CATEGORIES) + r")"
    r"\s*(?:" + "|".join(_GENERIC_SUFFIXES) + r")?$"
)


def classify_category_topic_title(title: str) -> bool:
    """토픽 제목이 일반 뉴스 카테고리명 단독(+일반 접미어)으로만 구성되는지 판정한다."""
    return bool(_CATEGORY_TITLE_RE.match((title or "").strip()))


# ── R-S5: 계층 깊이 ───────────────────────────────────────────────────────────


def compute_topic_depth(topic_id: int, parent_map: dict[int, int | None]) -> int:
    """topic_id의 계층 깊이를 계산한다. 최상위 토픽(parent None)은 깊이 1.

    parent_map에 없는 id나 순환 참조는 순환 지점에서 안전하게 멈춘다(방어적 가드,
    정상 데이터라면 발생하지 않는다).
    """
    depth = 1
    visited: set[int] = {topic_id}
    current = topic_id
    while True:
        parent = parent_map.get(current)
        if parent is None or parent in visited:
            break
        depth += 1
        visited.add(parent)
        current = parent
    return depth


# ══════════════════════════════════════════════════════════════════════════
# DB 집계 — 각 검사 ID별로 conn을 받아 위반 리포트를 반환한다.
# ══════════════════════════════════════════════════════════════════════════

DEFAULT_EXAMPLES_LIMIT = 5


def _examples(rows: list[dict], limit: int) -> list[dict]:
    return rows[:limit]


def topics_has_parent_topic_id(conn) -> bool:
    """topics 테이블에 parent_topic_id 컬럼이 존재하는지 확인한다.

    운영 DB에 서브토픽 마이그레이션(20260701120000)이 아직 적용되지 않았을 수 있어,
    서브토픽 검사를 돌리기 전에 스키마를 먼저 확인한다. 연결은 autocommit이므로
    실패한 조회가 이후 쿼리를 오염시키지 않는다.
    """
    try:
        conn.query_one("SELECT parent_topic_id FROM topics LIMIT 1")
        return True
    except Exception:
        return False


def check_single_article_events(conn, examples_limit: int = DEFAULT_EXAMPLES_LIMIT) -> dict:
    """R-E1: event_articles 매핑 기준 단일기사 이벤트 비율."""
    rows = conn.query(
        """
        SELECT e.id AS id, e.title AS title, COUNT(ea.article_id) AS article_count
        FROM events e
        LEFT JOIN event_articles ea ON ea.event_id = e.id
        GROUP BY e.id, e.title
        """
    )
    total = len(rows)
    violations = [r for r in rows if classify_member_count(r["article_count"]) == "단일"]
    zero_count = sum(1 for r in rows if classify_member_count(r["article_count"]) == "무항목")
    ratio = len(violations) / total if total else 0.0
    return {
        "total": total,
        "violations": len(violations),
        "ratio": ratio,
        "zero_article_events": zero_count,
        "examples": _examples(
            [{"id": r["id"], "title": r["title"]} for r in violations], examples_limit
        ),
    }


def check_similar_title_event_pairs(
    conn, threshold: float = 0.8, examples_limit: int = DEFAULT_EXAMPLES_LIMIT
) -> dict:
    """R-E2: 동일 토픽 내 유사 제목 이벤트 쌍 (쪼개짐 의심 신호)."""
    rows = conn.query(
        "SELECT id, title, topic_id FROM events WHERE topic_id IS NOT NULL"
    )
    groups: dict[int, list[tuple[int, str]]] = {}
    for r in rows:
        groups.setdefault(r["topic_id"], []).append((r["id"], r["title"]))

    all_pairs: list[dict] = []
    for topic_id, items in groups.items():
        for id_a, title_a, id_b, title_b, ratio in find_similar_title_pairs(items, threshold):
            all_pairs.append(
                {
                    "topic_id": topic_id,
                    "event_id_a": id_a,
                    "title_a": title_a,
                    "event_id_b": id_b,
                    "title_b": title_b,
                    "similarity": ratio,
                }
            )
    all_pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return {
        "total_events_with_topic": len(rows),
        "violations": len(all_pairs),
        "examples": _examples(all_pairs, examples_limit),
    }


def check_similar_title_unassigned_event_pairs(
    conn, threshold: float = 0.8, examples_limit: int = DEFAULT_EXAMPLES_LIMIT
) -> dict:
    """R-E2U: 미배정(topic_id IS NULL) 이벤트끼리의 유사 제목 쌍 (쪼개짐 의심 신호).

    운영 DB는 대다수 이벤트가 토픽 미배정 상태일 수 있어 R-E2(토픽 배정분만)의
    사각지대를 보완한다. 전량 쌍 비교 폭주를 막기 위해 category 버킷으로 나누고,
    SequenceMatcher의 quick_ratio 상한 프리필터로 실제 ratio 계산량을 줄인다
    (quick_ratio ≥ ratio 이므로 threshold 미만 프리필터 탈락은 누락을 만들지 않는다).
    """
    rows = conn.query("SELECT id, title, category FROM events WHERE topic_id IS NULL")
    buckets: dict[str, list[tuple[int, str]]] = {}
    for r in rows:
        buckets.setdefault(r["category"] or "무분류", []).append((r["id"], r["title"]))

    all_pairs: list[dict] = []
    for category, items in buckets.items():
        for i in range(len(items)):
            id_a, title_a = items[i]
            for j in range(i + 1, len(items)):
                id_b, title_b = items[j]
                sm = difflib.SequenceMatcher(None, title_a or "", title_b or "")
                if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                    continue
                ratio = sm.ratio()
                if ratio >= threshold:
                    all_pairs.append(
                        {
                            "category": category,
                            "event_id_a": id_a,
                            "title_a": title_a,
                            "event_id_b": id_b,
                            "title_b": title_b,
                            "similarity": ratio,
                        }
                    )
    all_pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return {
        "total": len(rows),
        "violations": len(all_pairs),
        "note": "category 버킷 내 비교만 수행 — 버킷 경계를 넘는 유사 쌍은 잡지 못한다.",
        "examples": _examples(all_pairs, examples_limit),
    }


def check_single_event_subtopics(conn, examples_limit: int = DEFAULT_EXAMPLES_LIMIT) -> dict:
    """R-S1: parent_topic_id IS NOT NULL인 leaf 서브토픽 중 이벤트 1개인 비율."""
    rows = conn.query(
        """
        SELECT t.id AS id, t.title AS title, COUNT(e.id) AS event_count
        FROM topics t
        LEFT JOIN events e ON e.topic_id = t.id
        WHERE t.parent_topic_id IS NOT NULL
        GROUP BY t.id, t.title
        """
    )
    total = len(rows)
    violations = [r for r in rows if classify_member_count(r["event_count"]) == "단일"]
    zero_count = sum(1 for r in rows if classify_member_count(r["event_count"]) == "무항목")
    ratio = len(violations) / total if total else 0.0
    return {
        "total": total,
        "violations": len(violations),
        "ratio": ratio,
        "zero_event_subtopics": zero_count,
        "examples": _examples(
            [{"id": r["id"], "title": r["title"]} for r in violations], examples_limit
        ),
    }


def check_time_based_subtopic_titles(conn, examples_limit: int = DEFAULT_EXAMPLES_LIMIT) -> dict:
    """R-S2: 시간 구분형 서브토픽 제목."""
    rows = conn.query(
        "SELECT id, title FROM topics WHERE parent_topic_id IS NOT NULL"
    )
    violations = [r for r in rows if classify_time_based_subtopic_title(r["title"])]
    total = len(rows)
    ratio = len(violations) / total if total else 0.0
    return {
        "total": total,
        "violations": len(violations),
        "ratio": ratio,
        "examples": _examples(
            [{"id": r["id"], "title": r["title"]} for r in violations], examples_limit
        ),
    }


def check_attribute_subtopic_titles(conn, examples_limit: int = DEFAULT_EXAMPLES_LIMIT) -> dict:
    """R-S3: 속성형(찬반 등) 서브토픽 제목."""
    rows = conn.query(
        "SELECT id, title FROM topics WHERE parent_topic_id IS NOT NULL"
    )
    violations = [r for r in rows if classify_attribute_subtopic_title(r["title"])]
    total = len(rows)
    ratio = len(violations) / total if total else 0.0
    return {
        "total": total,
        "violations": len(violations),
        "ratio": ratio,
        "examples": _examples(
            [{"id": r["id"], "title": r["title"]} for r in violations], examples_limit
        ),
    }


def check_topic_scope_subtopics(
    conn, threshold: float = 0.85, examples_limit: int = DEFAULT_EXAMPLES_LIMIT
) -> dict:
    """R-S4: 제목이 부모 토픽과 동일 범위(유사도 ≥ threshold)인 서브토픽."""
    rows = conn.query(
        """
        SELECT child.id AS child_id, child.title AS child_title,
               parent.id AS parent_id, parent.title AS parent_title
        FROM topics child
        JOIN topics parent ON parent.id = child.parent_topic_id
        WHERE child.parent_topic_id IS NOT NULL
        """
    )
    total = len(rows)
    violations = [
        r for r in rows if is_similar_title(r["child_title"], r["parent_title"], threshold)
    ]
    ratio = len(violations) / total if total else 0.0
    return {
        "total": total,
        "violations": len(violations),
        "ratio": ratio,
        "examples": _examples(
            [
                {
                    "subtopic_id": r["child_id"],
                    "subtopic_title": r["child_title"],
                    "parent_id": r["parent_id"],
                    "parent_title": r["parent_title"],
                    "similarity": title_similarity(r["child_title"], r["parent_title"]),
                }
                for r in violations
            ],
            examples_limit,
        ),
    }


def check_hierarchy_consistency(conn, examples_limit: int = DEFAULT_EXAMPLES_LIMIT) -> dict:
    """R-S5: 계층 정합성.

    (a) 비-leaf(서브토픽을 가진) 토픽에 이벤트가 직접 붙어 있는 경우
        (events.topic_id는 leaf만 참조해야 한다는 설계 원칙 위반).
    (b) parent_topic_id 체인 깊이가 2를 초과하는 토픽(설계상 root→leaf 2단계까지만 지원).
    """
    topic_rows = conn.query("SELECT id, parent_topic_id FROM topics")
    parent_map = {r["id"]: r["parent_topic_id"] for r in topic_rows}
    parent_ids = {p for p in parent_map.values() if p is not None}

    event_rows = conn.query(
        "SELECT id AS event_id, topic_id FROM events WHERE topic_id IS NOT NULL"
    )
    non_leaf_violations = [
        {"event_id": r["event_id"], "topic_id": r["topic_id"]}
        for r in event_rows
        if r["topic_id"] in parent_ids
    ]

    depth_violations = []
    for topic_id in parent_map:
        depth = compute_topic_depth(topic_id, parent_map)
        if depth > 2:
            depth_violations.append({"id": topic_id, "depth": depth})

    return {
        "total_topics": len(topic_rows),
        "total_events_with_topic": len(event_rows),
        "events_on_non_leaf_topic": len(non_leaf_violations),
        "events_on_non_leaf_topic_examples": _examples(non_leaf_violations, examples_limit),
        "topics_depth_exceeded": len(depth_violations),
        "topics_depth_exceeded_examples": _examples(depth_violations, examples_limit),
    }


def check_duplicate_topic_pairs(
    conn,
    threshold: float = 0.85,
    examples_limit: int = DEFAULT_EXAMPLES_LIMIT,
    has_parent_column: bool = True,
) -> dict:
    """R-T1: 최상위 토픽끼리 제목 완전 동일 또는 유사도 ≥ threshold인 쌍."""
    if has_parent_column:
        rows = conn.query("SELECT id, title FROM topics WHERE parent_topic_id IS NULL")
    else:
        # parent_topic_id 미적용 스키마 — 모든 토픽이 최상위다.
        rows = conn.query("SELECT id, title FROM topics")
    items = [(r["id"], r["title"]) for r in rows]

    # O(n^2) 완전탐색 — 루트 토픽 수가 매우 크면 실행 시간이 급격히 늘어난다.
    if len(items) > 3000:
        return {
            "total_root_topics": len(items),
            "violations": None,
            "examples": [],
            "note": "루트 토픽이 3000건을 초과해 전량 쌍 비교를 건너뜀 — 표본을 나눠 재실행하세요.",
        }

    pairs = find_similar_title_pairs(items, threshold)
    exact_count = sum(1 for p in pairs if p[1] == p[3])
    examples = [
        {
            "topic_id_a": p[0],
            "title_a": p[1],
            "topic_id_b": p[2],
            "title_b": p[3],
            "similarity": p[4],
            "exact_match": p[1] == p[3],
        }
        for p in pairs
    ]
    return {
        "total_root_topics": len(items),
        "violations": len(pairs),
        "exact_duplicate_pairs": exact_count,
        "examples": _examples(examples, examples_limit),
    }


def check_category_topic_titles(
    conn, examples_limit: int = DEFAULT_EXAMPLES_LIMIT, has_parent_column: bool = True
) -> dict:
    """R-T2: 카테고리형 토픽 제목 (일반 뉴스 카테고리명 단독 제목)."""
    if has_parent_column:
        rows = conn.query("SELECT id, title FROM topics WHERE parent_topic_id IS NULL")
    else:
        # parent_topic_id 미적용 스키마 — 모든 토픽이 최상위다.
        rows = conn.query("SELECT id, title FROM topics")
    violations = [r for r in rows if classify_category_topic_title(r["title"])]
    total = len(rows)
    ratio = len(violations) / total if total else 0.0
    return {
        "total": total,
        "violations": len(violations),
        "ratio": ratio,
        "examples": _examples(
            [{"id": r["id"], "title": r["title"]} for r in violations], examples_limit
        ),
    }


def observe_subtopic_state(conn, has_parent_column: bool = True) -> dict:
    """부가 관측: TOPIC_SUBTOPICS_ENABLED 환경 상태와 실제 서브토픽 행 존재 여부."""
    enabled_raw = os.environ.get("TOPIC_SUBTOPICS_ENABLED", "false")
    enabled = enabled_raw.strip().lower() in {"1", "true", "yes", "on"}
    if not has_parent_column:
        return {
            "topic_subtopics_enabled_env": enabled,
            "subtopic_row_count": None,
            "note": "topics.parent_topic_id 컬럼 없음 — 서브토픽 마이그레이션(20260701120000) 미적용 스키마",
        }
    row = conn.query_one(
        "SELECT COUNT(*) AS cnt FROM topics WHERE parent_topic_id IS NOT NULL"
    )
    subtopic_count = row["cnt"] if row else 0
    return {
        "topic_subtopics_enabled_env": enabled,
        "subtopic_row_count": subtopic_count,
    }


# ══════════════════════════════════════════════════════════════════════════
# 전체 실행 + 리포트
# ══════════════════════════════════════════════════════════════════════════


def run_all_checks(
    conn,
    *,
    event_title_sim_threshold: float = 0.8,
    subtopic_scope_sim_threshold: float = 0.85,
    topic_dup_sim_threshold: float = 0.85,
    examples_limit: int = DEFAULT_EXAMPLES_LIMIT,
) -> dict:
    """모든 루브릭 검사를 실행하고 검사 ID별 결과를 담은 dict를 반환한다.

    parent_topic_id가 없는 스키마에서는 서브토픽 검사(R-S1~R-S5)를 skipped로 표시하고,
    R-T1/R-T2는 전체 토픽을 최상위로 간주해 계속 수행한다.
    """
    has_parent_column = topics_has_parent_topic_id(conn)

    def _skipped() -> dict:
        return {
            "skipped": True,
            "reason": "topics.parent_topic_id 컬럼 없음 — 서브토픽 마이그레이션(20260701120000) 미적용 스키마",
        }

    return {
        "R-E1": check_single_article_events(conn, examples_limit),
        "R-E2": check_similar_title_event_pairs(
            conn, event_title_sim_threshold, examples_limit
        ),
        "R-E2U": check_similar_title_unassigned_event_pairs(
            conn, event_title_sim_threshold, examples_limit
        ),
        "R-S1": check_single_event_subtopics(conn, examples_limit)
        if has_parent_column
        else _skipped(),
        "R-S2": check_time_based_subtopic_titles(conn, examples_limit)
        if has_parent_column
        else _skipped(),
        "R-S3": check_attribute_subtopic_titles(conn, examples_limit)
        if has_parent_column
        else _skipped(),
        "R-S4": check_topic_scope_subtopics(
            conn, subtopic_scope_sim_threshold, examples_limit
        )
        if has_parent_column
        else _skipped(),
        "R-S5": check_hierarchy_consistency(conn, examples_limit)
        if has_parent_column
        else _skipped(),
        "R-T1": check_duplicate_topic_pairs(
            conn, topic_dup_sim_threshold, examples_limit, has_parent_column
        ),
        "R-T2": check_category_topic_titles(conn, examples_limit, has_parent_column),
        "observations": observe_subtopic_state(conn, has_parent_column),
    }


_CHECK_LABELS = {
    "R-E1": "단일기사 이벤트 비율",
    "R-E2": "동일 토픽 내 유사 제목 이벤트 쌍",
    "R-E2U": "미배정 이벤트 유사 제목 쌍 (category 버킷)",
    "R-S1": "단일 이벤트 서브토픽 비율",
    "R-S2": "시간 구분형 서브토픽 제목",
    "R-S3": "속성형(찬반 등) 서브토픽 제목",
    "R-S4": "토픽과 동일 범위인 서브토픽 제목",
    "R-S5": "계층 정합성",
    "R-T1": "중복·고유사 토픽 쌍",
    "R-T2": "카테고리형 토픽 제목",
}


def _fmt_ratio(v) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:.1f}%"


def print_report(results: dict) -> None:
    """콘솔에 한국어 요약 리포트를 출력한다."""
    print("═" * 60)
    print("루브릭 위반 검사 리포트")
    print("═" * 60)

    for check_id in ("R-E1", "R-E2", "R-E2U", "R-S1", "R-S2", "R-S3", "R-S4", "R-T1", "R-T2"):
        r = results[check_id]
        label = _CHECK_LABELS[check_id]
        if r.get("skipped"):
            print(f"\n[{check_id}] {label}")
            print(f"  건너뜀: {r['reason']}")
            continue
        total_key = "total" if "total" in r else "total_root_topics" if "total_root_topics" in r else "total_events_with_topic"
        total = r.get(total_key, r.get("total_events_with_topic", r.get("total_root_topics")))
        violations = r.get("violations")
        ratio = r.get("ratio")
        print(f"\n[{check_id}] {label}")
        print(f"  모집단: {total}건 / 위반: {violations if violations is not None else 'N/A'}건", end="")
        if ratio is not None:
            print(f" ({_fmt_ratio(ratio)})")
        else:
            print()
        if r.get("note"):
            print(f"  주의: {r['note']}")
        examples = r.get("examples", [])
        if examples:
            print("  상위 사례:")
            for ex in examples:
                print(f"    - {ex}")

    r5 = results["R-S5"]
    print(f"\n[R-S5] {_CHECK_LABELS['R-S5']}")
    if r5.get("skipped"):
        print(f"  건너뜀: {r5['reason']}")
        _print_observations(results)
        return
    print(
        f"  전체 토픽: {r5['total_topics']}건 / 배정된 이벤트: {r5['total_events_with_topic']}건"
    )
    print(
        f"  비-leaf 토픽에 직결된 이벤트: {r5['events_on_non_leaf_topic']}건"
    )
    for ex in r5["events_on_non_leaf_topic_examples"]:
        print(f"    - {ex}")
    print(f"  계층 깊이 초과(>2) 토픽: {r5['topics_depth_exceeded']}건")
    for ex in r5["topics_depth_exceeded_examples"]:
        print(f"    - {ex}")

    _print_observations(results)


def _print_observations(results: dict) -> None:
    obs = results["observations"]
    print("\n[부가 관측] 서브토픽 기능 상태")
    print(f"  TOPIC_SUBTOPICS_ENABLED(env): {obs['topic_subtopics_enabled_env']}")
    count = obs["subtopic_row_count"]
    print(
        "  서브토픽 행 수(parent_topic_id IS NOT NULL): "
        + (f"{count}건" if count is not None else "N/A (컬럼 없음)")
    )
    if obs.get("note"):
        print(f"  주의: {obs['note']}")
    print("\n" + "═" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="새 엔티티 정의 루브릭 위반을 DB 스냅샷에서 산출한다."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres 연결 URL (DATABASE_URL 환경변수로도 지정 가능)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="콘솔 요약 대신 전체 결과를 JSON으로 표준출력에 출력한다.",
    )
    parser.add_argument(
        "--examples-limit",
        type=int,
        default=DEFAULT_EXAMPLES_LIMIT,
        help=f"검사별 상위 사례 출력 개수 (기본: {DEFAULT_EXAMPLES_LIMIT})",
    )
    parser.add_argument(
        "--event-title-sim-threshold",
        type=float,
        default=0.8,
        help="R-E2 이벤트 제목 유사도 임계값 (기본: 0.8)",
    )
    parser.add_argument(
        "--subtopic-scope-sim-threshold",
        type=float,
        default=0.85,
        help="R-S4 서브토픽-부모 제목 유사도 임계값 (기본: 0.85)",
    )
    parser.add_argument(
        "--topic-dup-sim-threshold",
        type=float,
        default=0.85,
        help="R-T1 토픽 중복 판정 유사도 임계값 (기본: 0.85)",
    )
    args = parser.parse_args()

    if not args.database_url:
        print(
            "[오류] --database-url 또는 DATABASE_URL 환경변수가 필요합니다.",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = ensure_db("", database_url=args.database_url)
    try:
        results = run_all_checks(
            conn,
            event_title_sim_threshold=args.event_title_sim_threshold,
            subtopic_scope_sim_threshold=args.subtopic_scope_sim_threshold,
            topic_dup_sim_threshold=args.topic_dup_sim_threshold,
            examples_limit=args.examples_limit,
        )
    finally:
        conn.close()

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results)


if __name__ == "__main__":
    main()
