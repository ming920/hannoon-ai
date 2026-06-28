"""topics 테이블 repository.

토픽 생성 함수와 SQL 상수를 제공한다.
"""

from dataclasses import dataclass

from summary_utils import normalize_summary, normalize_topic_title


@dataclass
class Topic:
    """topics 테이블 행을 표현하는 dataclass."""
    id: int
    category: str
    title: str
    summary: str
    # 계층 분류용 부모 토픽 참조. None이면 최상위 토픽, 값이 있으면 서브토픽(leaf)이다.
    parent_topic_id: int | None = None


# 새 토픽을 삽입하고 생성된 id를 반환한다.
# category는 public.category enum 타입이므로 ?::category 캐스트가 필요하다.
# PostgresCursor의 자동 RETURNING id 처리는 'INSERT INTO ARTICLES'에만 적용되므로,
# topics INSERT는 명시적으로 RETURNING id를 붙이고 query_one으로 id를 받는다.
# parent_topic_id가 NULL이면 최상위 토픽, 값이 있으면 서브토픽이다.
INSERT_TOPIC_SQL = """
INSERT INTO topics (category, title, summary, parent_topic_id)
VALUES (?::category, ?, ?, ?)
RETURNING id
"""

# 기존 토픽의 제목·요약을 갱신한다.
# updated_at은 DB 트리거가 처리하므로 여기서 다루지 않는다.
UPDATE_TOPIC_SQL = "UPDATE topics SET title = ?, summary = ? WHERE id = ?"


def create_topic(
    conn,
    category: str,
    title: str,
    summary: str,
    parent_topic_id: int | None = None,
) -> int:
    """새 토픽을 생성하고 생성된 id를 반환한다. 트랜잭션 내에서 호출한다.

    parent_topic_id를 넘기면 해당 부모 아래 서브토픽으로 생성한다(기본은 최상위 토픽).
    """
    title = normalize_topic_title(title) or normalize_topic_title(summary)
    if not title:
        raise ValueError("Topic title is empty.")
    summary = normalize_summary(summary) or normalize_summary(title)
    row = conn.query_one(INSERT_TOPIC_SQL, (category, title, summary, parent_topic_id))
    return row["id"]


def update_topic(conn, topic_id: int, title: str, summary: str) -> None:
    """기존 토픽의 제목·요약을 갱신한다. 트랜잭션 내에서 호출한다."""
    title = normalize_topic_title(title)
    if not title:
        raise ValueError("Topic title is empty.")
    summary = normalize_summary(summary)
    if not summary:
        raise ValueError("Topic summary is empty.")
    conn.execute(UPDATE_TOPIC_SQL, (title, summary, topic_id))
