from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field


MAX_CAUSES_PER_TOPIC = 5


@dataclass
class TopicCandidate:
    """원인/결과 임베딩 검색으로 찾은 토픽 후보."""

    topic_id: int
    category: str
    title: str
    summary: str
    distance: float
    cause_texts: list[str] = field(default_factory=list)


# ── SQL 상수 ────────────────────────────────────────────────────────────────

# 원인 임베딩 코사인 거리로 관련 토픽을 검색한다.
# 후보는 같은 category 내 토픽으로 한정한다(WHERE t.category = ?::category).
# 이는 배정 검증을 강화해 서로 다른 분야 토픽이 잘못 병합되는 것을 막기 위한
# 의도된 제약이다(커밋 acae22b "토픽 후보 검색과 배정 검증 강화" 참고).
# 벡터 바인딩은 '[f1,f2,...]' 문자열 + ?::vector 캐스트 방식을 사용한다.
# (storage.py의 PostgresConnection이 register_vector를 호출하지 않으므로)
SEARCH_SQL = """
WITH matched AS (
    SELECT t.id AS topic_id,
           t.category AS topic_category,
           t.title AS topic_title,
           t.summary AS topic_summary,
           tc.cause_text,
           (tc.cause_embedding <=> ?::vector) AS distance
    FROM topic_causes tc
    JOIN topics t ON t.id = tc.topic_id
    WHERE tc.cause_embedding IS NOT NULL
      AND t.category = ?::category
      AND (tc.cause_embedding <=> ?::vector) <= ?
),
ranked AS (
    SELECT *,
           MIN(distance) OVER (PARTITION BY topic_id) AS topic_distance,
           ROW_NUMBER() OVER (
               PARTITION BY topic_id
               ORDER BY distance ASC
           ) AS cause_rank
    FROM matched
),
topic_ranked AS (
    SELECT *,
           DENSE_RANK() OVER (
               ORDER BY topic_distance ASC, topic_id ASC
           ) AS topic_rank
    FROM ranked
)
SELECT topic_id,
       topic_category,
       topic_title,
       topic_summary,
       cause_text,
       distance
FROM topic_ranked
WHERE topic_rank <= ?
  AND cause_rank <= ?
ORDER BY topic_rank ASC, cause_rank ASC
"""

# 이벤트의 '결과(result)'를 topic_causes에 적재한다.
# 컬럼명은 cause_text이지만, 여기에는 이벤트 result를 저장한다.
# 이는 "해당 토픽에서 발생한 결과가 향후 유사 사건의 원인으로 작용한다"는
# 모델링 가정을 반영한 것이다(docs/topic_classification_overview.md 참고).
INSERT_CAUSE_SQL = """
INSERT INTO topic_causes (topic_id, cause_text, cause_embedding)
VALUES (?, ?, ?::vector)
"""


def search_candidates(
    conn,
    embedding_literal: str,
    category: str,
    max_distance: float,
    top_k: int,
) -> list[TopicCandidate]:
    """원인 임베딩과 가까운 topic_causes를 찾아 토픽 단위 후보로 묶는다."""
    if top_k <= 0:
        raise ValueError("top_k must be greater than 0.")
    rows = conn.query(
        SEARCH_SQL,
        (
            embedding_literal,
            category,
            embedding_literal,
            max_distance,
            top_k,
            MAX_CAUSES_PER_TOPIC,
        ),
    )

    seen: OrderedDict[int, TopicCandidate] = OrderedDict()
    for row in rows:
        topic_id = row["topic_id"]
        if topic_id not in seen:
            seen[topic_id] = TopicCandidate(
                topic_id=topic_id,
                category=row["topic_category"],
                title=row["topic_title"],
                summary=row["topic_summary"],
                distance=float(row["distance"]),
                cause_texts=[row["cause_text"]],
            )
        else:
            seen[topic_id].distance = min(
                seen[topic_id].distance,
                float(row["distance"]),
            )
            seen[topic_id].cause_texts.append(row["cause_text"])

    return list(seen.values())


def add_cause(conn, topic_id: int, cause_text: str, embedding_literal: str) -> None:
    """토픽 검색에 재사용할 원인/결과 문장과 임베딩을 저장한다."""
    conn.execute(INSERT_CAUSE_SQL, (topic_id, cause_text, embedding_literal))
