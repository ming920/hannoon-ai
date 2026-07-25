from __future__ import annotations

from dataclasses import dataclass

from summary_utils import normalize_summary


@dataclass
class Event:
    """토픽 분류 단계에서 사용하는 이벤트 행."""

    id: int
    category: str
    title: str
    summary: str
    # 대표 기사(제목 + 첫 문단). cause/result 추출 입력으로 사용한다.
    embedding_text: str


# ── SQL 상수 ────────────────────────────────────────────────────────────────

# 미배정 이벤트 조회: topic_id가 null이고 기사 수가 임계값 이상인 이벤트를
# (created_at, id) 기준 오름차순으로 가져온다.
# 순서를 보장해야 prev/next 체인이 시간순으로 연결된다.
FETCH_UNASSIGNED_SQL = """
SELECT id, category, title, summary, embedding_text
FROM events
WHERE topic_id IS NULL
  AND article_count >= ?
ORDER BY created_at ASC, id ASC
LIMIT ?
"""

# 이벤트에 토픽을 배정하고 배정 근거(reason)를 기록한다.
ASSIGN_TOPIC_SQL = "UPDATE events SET topic_id = ?, reason = ? WHERE id = ?"

# 같은 토픽에서 (created_at, id) 기준 직전 노드와 그 노드의 기존 next를 조회한다.
# next 컬럼을 함께 가져오므로 중간 삽입 시 별도 next 조회 없이 처리할 수 있다.
# (created_at, id) < 현재 이벤트: < 비교가 자기 자신을 자동 제외한다.
FIND_PREV_EVENT_SQL = """
SELECT id, next_event_id
FROM events
WHERE topic_id = ?
  AND (created_at, id) < (SELECT created_at, id FROM events WHERE id = ?)
ORDER BY created_at DESC, id DESC
LIMIT 1
"""

# (created_at, id) 기준 직후 노드를 조회한다.
# 엣지 케이스: 임계치 미달로 제외됐다 뒤늦게 배정된 이벤트가 기존 토픽의 가장 과거
# 노드보다도 더 과거인 경우, FIND_PREV_EVENT_SQL 결과가 None이 된다.
# 이때 이 쿼리로 기존 head(직후 노드)를 찾아 새 이벤트를 체인 앞에 삽입한다.
FIND_NEXT_EVENT_SQL = """
SELECT id
FROM events
WHERE topic_id = ?
  AND (created_at, id) > (SELECT created_at, id FROM events WHERE id = ?)
ORDER BY created_at ASC, id ASC
LIMIT 1
"""

# 현재 이벤트의 prev/next를 동시에 설정한다 (params: prev_id, next_id, current_id).
UPDATE_CHAIN_SQL = "UPDATE events SET prev_event_id = ?, next_event_id = ? WHERE id = ?"

# 직전 이벤트의 next_event_id를 설정한다 (params: new_next_id, target_id).
UPDATE_NEXT_EVENT_SQL = "UPDATE events SET next_event_id = ? WHERE id = ?"

# 직후 이벤트의 prev_event_id를 설정한다 (params: new_prev_id, target_id).
UPDATE_PREV_EVENT_SQL = "UPDATE events SET prev_event_id = ? WHERE id = ?"

SEARCH_CANDIDATE_EVENTS_SQL = """
SELECT id, category, title, core_content, summary, article_count,
       (embedding <=> ?::vector) AS distance
FROM events
WHERE embedding IS NOT NULL
  AND (updated_at + (?::int * INTERVAL '1 day') >= ?)
  AND (embedding <=> ?::vector) <= ?
ORDER BY distance ASC
LIMIT ?
"""

UPDATE_EVENT_SUMMARY_SQL = """
UPDATE events
SET summary = ?,
    updated_at = now()
WHERE id = ?
"""

# 신규 이벤트 생성 (RETURNING id로 방금 생성된 id 획득)
INSERT_NEW_EVENT_SQL = """
INSERT INTO events (
    topic_id, category, title, summary,
    core_content, embedding_text, embedding, article_count, event_image_url
) VALUES (NULL, ?::category, ?, ?, ?, ?, ?::vector, 0, ?)
RETURNING id
"""

# 기사-이벤트 관계 테이블 삽입 쿼리
INSERT_EVENT_ARTICLE_MAP_SQL = """
INSERT INTO event_articles (event_id, article_id, reason)
VALUES (?, ?, ?)
"""


def fetch_unassigned(conn, min_net: int, batch_size: int) -> list[Event]:
    """토픽이 아직 없는 이벤트 중 정상 기사 수가 기준 이상인 항목을 가져온다."""
    rows = conn.query(FETCH_UNASSIGNED_SQL, (min_net, batch_size))
    return [
        Event(
            id=row["id"],
            category=row["category"],
            title=row["title"],
            summary=row["summary"],
            embedding_text=row["embedding_text"],
        )
        for row in rows
    ]


def assign_topic(conn, event_id: int, topic_id: int, reason: str | None) -> None:
    """이벤트를 토픽에 배정한다."""
    conn.execute(ASSIGN_TOPIC_SQL, (topic_id, reason, event_id))


def find_prev_event(conn, topic_id: int, event_id: int):
    """같은 토픽에서 현재 이벤트보다 앞선 이벤트를 찾는다."""
    return conn.query_one(FIND_PREV_EVENT_SQL, (topic_id, event_id))


def find_next_event_id(conn, topic_id: int, event_id: int) -> int | None:
    """같은 토픽에서 현재 이벤트보다 뒤에 오는 이벤트 id를 찾는다."""
    row = conn.query_one(FIND_NEXT_EVENT_SQL, (topic_id, event_id))
    return row["id"] if row else None


def link_into_chain(conn, current_id: int, prev_id: int | None, next_id: int | None) -> None:
    """토픽 내부 이벤트 연결 리스트(prev/next)를 갱신한다."""
    conn.execute(UPDATE_CHAIN_SQL, (prev_id, next_id, current_id))
    if prev_id is not None:
        conn.execute(UPDATE_NEXT_EVENT_SQL, (current_id, prev_id))
    if next_id is not None:
        conn.execute(UPDATE_PREV_EVENT_SQL, (current_id, next_id))


def search_candidate_events(
    conn,
    embedding_literal: str,
    published_at_str: str,
    max_distance: float,
    top_k: int,
    window_days: int,
) -> list[dict]:
    """기사 임베딩과 가까운 기존 이벤트 후보를 pgvector 거리순으로 가져온다."""
    return conn.query(
        SEARCH_CANDIDATE_EVENTS_SQL,
        (
            embedding_literal,
            window_days,
            published_at_str,
            embedding_literal,
            max_distance,
            top_k,
        ),
    )


def create_new_event(
    conn,
    category: str,
    title: str,
    summary: str,
    core_content: str,
    embedding_text: str,
    embedding_literal: str,
    event_image_url: str | None = None,
) -> int:
    """새 이벤트를 만들고 생성된 id를 반환한다."""
    summary = normalize_summary(summary) or normalize_summary(title)
    row = conn.query_one(
        INSERT_NEW_EVENT_SQL,
        (
            category,
            title,
            summary,
            core_content,
            embedding_text,
            embedding_literal,
            event_image_url,
        ),
    )
    return row["id"]


def update_event_summary(conn, event_id: int, summary: str) -> None:
    """이벤트 요약을 공통 길이 규칙에 맞춰 갱신한다."""
    summary = normalize_summary(summary)
    if not summary:
        raise ValueError("Event summary is empty.")
    conn.execute(UPDATE_EVENT_SUMMARY_SQL, (summary, event_id))


def link_article_to_event(
    conn,
    event_id: int,
    article_id: int,
    reason: str,
) -> None:
    """기사와 이벤트를 매핑 테이블에 연결한다."""
    conn.execute(INSERT_EVENT_ARTICLE_MAP_SQL, (event_id, article_id, reason))
