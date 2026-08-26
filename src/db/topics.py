"""topics 테이블 repository.

토픽 생성 함수와 SQL 상수를 제공한다.
"""

from dataclasses import dataclass

from summary_utils import normalize_summary, normalize_topic_title, title_similarity


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

# create 직전 중복 검사용: 동일 스코프(최상위 또는 특정 부모 아래) 내 토픽 제목을 모두 가져온다.
# 후보 검색(topic_causes.search_candidates)과 동일하게 category로 스코프를 좁혀,
# 서로 다른 분야의 동명 이슈를 잘못 병합하지 않게 한다.
FETCH_ROOT_TOPIC_TITLES_SQL = (
    "SELECT id, title FROM topics WHERE category = ?::category AND parent_topic_id IS NULL"
)
FETCH_SUBTOPIC_TITLES_SQL = (
    "SELECT id, title FROM topics WHERE category = ?::category AND parent_topic_id = ?"
)


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


def find_duplicate_topic(
    conn,
    category: str,
    title: str,
    parent_topic_id: int | None,
    threshold: float,
) -> dict | None:
    """동일 스코프(최상위 또는 parent_topic_id 아래) 내에서 title과 가장 유사한 기존
    토픽을 찾는다. create 직전 중복 생성을 막는 가드용이며, 유사도가 threshold 이상인
    토픽이 없으면 None을 반환한다.

    parent_topic_id가 None이면 최상위(root) 토픽 스코프, 값이 있으면 그 부모 아래
    서브토픽 스코프에서 검색한다. 반환값은 {"id", "title", "similarity"} 또는 None.
    """
    if not title:
        return None
    if parent_topic_id is None:
        rows = conn.query(FETCH_ROOT_TOPIC_TITLES_SQL, (category,))
    else:
        rows = conn.query(FETCH_SUBTOPIC_TITLES_SQL, (category, parent_topic_id))

    best_row = None
    best_sim = 0.0
    for row in rows:
        sim = title_similarity(title, row["title"])
        if sim >= threshold and sim > best_sim:
            best_row = row
            best_sim = sim
    if best_row is None:
        return None
    return {"id": best_row["id"], "title": best_row["title"], "similarity": best_sim}
