"""분류 결과를 DB에서 뽑아 제약 검사 입력 스냅샷(JSON)으로 저장한다.

`data/constraints/README.md`의 추출 SQL을 대시보드에 손으로 붙여넣던 단계를 자동화한다.
이 단계가 수동이면 반복 실험 루프가 매번 끊긴다.

원본 SQL은 `json_build_object`/`json_agg`로 DB 안에서 JSON을 조립하지만, 여기서는 평범한
SELECT로 행을 읽고 파이썬에서 조립한다. 이유가 둘 있다.
  - `?` 플레이스홀더만 쓰는 storage.py 규약을 지키면서 SQLite에서도 돌아간다(테스트 가능).
  - 중첩 json_agg는 실패 시 원인 파악이 어렵다.

읽기 전용 SELECT만 수행한다.

산출 형식(제약 검사·진단 도구가 읽는 것):
    {
      "snapshot_date": "...",
      "events": [{id, title, summary, core_content, created_at, article_count,
                  articles: [{id, title, publisher, published_at, summary, link}]}],
      "topics": [{id, title, created_at,
                  events: [{id, title, created_at, prev_event_id, next_event_id,
                            core_content, article_ids: [...]}]}]
    }

판정에 실제로 쓰이는 필드는 events[].id / events[].articles[].id / topics[].id /
topics[].events[].article_ids 넷뿐이다. 나머지는 사람이 읽기 위한 것이다.

사용 예:
    python eval/extract_snapshot.py --database-url "postgresql://..." --out snapshot.json
    python eval/extract_snapshot.py --out snapshot.json --event-id-min 1200   # 구간 누적
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from collector.storage import ensure_db  # noqa: E402

FETCH_EVENTS_SQL = """
SELECT id, title, summary, core_content, created_at, article_count,
       topic_id, prev_event_id, next_event_id
FROM events
WHERE id > ?
ORDER BY id ASC
LIMIT ?
"""

FETCH_EVENT_ARTICLES_SQL = """
SELECT ea.event_id AS event_id,
       ar.id AS id,
       ar.title AS title,
       ar.publisher AS publisher,
       ar.published_at AS published_at,
       air.summary AS summary,
       ar.link AS link
FROM event_articles ea
JOIN articles ar ON ar.id = ea.article_id
LEFT JOIN article_ai_results air ON air.article_id = ar.id
ORDER BY ea.event_id ASC, ar.id ASC
"""

FETCH_TOPICS_SQL = """
SELECT id, title, created_at
FROM topics
WHERE id > ?
ORDER BY id ASC
LIMIT ?
"""


def _jsonable(value):
    """DB 드라이버가 돌려주는 날짜/시각 객체를 JSON 직렬화 가능한 문자열로 바꾼다."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row_to_dict(row, keys: tuple[str, ...]) -> dict:
    """SQLite Row / psycopg dict_row 양쪽에서 같은 방식으로 값을 꺼낸다.

    sqlite3.Row에는 .get()이 없으므로 키 접근만 쓴다.
    """
    return {key: _jsonable(row[key]) for key in keys}


_EVENT_KEYS = ("id", "title", "summary", "core_content", "created_at", "article_count")
_ARTICLE_KEYS = ("id", "title", "publisher", "published_at", "summary", "link")
_TOPIC_EVENT_KEYS = (
    "id", "title", "created_at", "prev_event_id", "next_event_id", "core_content",
)


def build_snapshot(conn, *, snapshot_date: str, event_id_min: int = 0,
                   topic_id_min: int = 0, limit_events: int = 1_000_000,
                   limit_topics: int = 1_000_000) -> dict:
    """DB에서 이벤트·토픽 구조를 읽어 스냅샷 dict를 만든다 (읽기 전용)."""
    event_rows = conn.query(FETCH_EVENTS_SQL, (event_id_min, limit_events))
    events_by_id = {}
    for row in event_rows:
        event = _row_to_dict(row, _EVENT_KEYS)
        event["articles"] = []
        events_by_id[row["id"]] = {
            "payload": event,
            "topic_id": row["topic_id"],
            "prev_event_id": _jsonable(row["prev_event_id"]),
            "next_event_id": _jsonable(row["next_event_id"]),
            "article_ids": [],
        }

    # 기사 매핑은 한 번에 읽고 파이썬에서 이벤트별로 나눈다(이벤트당 쿼리 N+1 방지).
    for row in conn.query(FETCH_EVENT_ARTICLES_SQL):
        holder = events_by_id.get(row["event_id"])
        if holder is None:
            continue  # id 구간 밖의 이벤트에 속한 기사
        holder["payload"]["articles"].append(_row_to_dict(row, _ARTICLE_KEYS))
        holder["article_ids"].append(row["id"])

    topics = []
    for row in conn.query(FETCH_TOPICS_SQL, (topic_id_min, limit_topics)):
        topic_id = row["id"]
        nested = []
        for eid, holder in events_by_id.items():
            if holder["topic_id"] != topic_id:
                continue
            payload = holder["payload"]
            nested.append({
                "id": payload["id"],
                "title": payload["title"],
                "created_at": payload["created_at"],
                "prev_event_id": holder["prev_event_id"],
                "next_event_id": holder["next_event_id"],
                "core_content": payload["core_content"],
                "article_ids": list(holder["article_ids"]),
            })
        nested.sort(key=lambda e: e["id"])
        topics.append({
            "id": topic_id,
            "title": _jsonable(row["title"]),
            "created_at": _jsonable(row["created_at"]),
            "events": nested,
        })

    return {
        "snapshot_date": snapshot_date,
        "events": [h["payload"] for h in events_by_id.values()],
        "topics": topics,
    }


def summarize(snapshot: dict) -> str:
    """추출 결과를 한 줄로 요약한다."""
    events = snapshot.get("events") or []
    topics = snapshot.get("topics") or []
    articles = sum(len(e.get("articles") or []) for e in events)
    topic_articles = sum(
        len(ev.get("article_ids") or []) for t in topics for ev in t.get("events") or []
    )
    return (
        f"이벤트 {len(events):,}개 / 기사 {articles:,}건 / "
        f"토픽 {len(topics):,}개 (토픽에 연결된 기사 {topic_articles:,}건)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="분류 결과를 제약 검사 입력 스냅샷 JSON으로 추출한다 (읽기 전용)."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres 연결 URL (미지정 시 DATABASE_URL 환경변수 사용)",
    )
    parser.add_argument("--out", required=True, help="저장할 JSON 경로")
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="스냅샷 표기 시각 (기본: 현재 시각 ISO8601)",
    )
    parser.add_argument(
        "--event-id-min", type=int, default=0,
        help="이 id를 초과하는 이벤트만 추출 (구간 누적용 커서, 기본 0)",
    )
    parser.add_argument(
        "--topic-id-min", type=int, default=0,
        help="이 id를 초과하는 토픽만 추출 (기본 0)",
    )
    parser.add_argument("--limit-events", type=int, default=1_000_000)
    parser.add_argument("--limit-topics", type=int, default=1_000_000)
    args = parser.parse_args()

    if not args.database_url:
        print("오류: --database-url 또는 DATABASE_URL 환경변수를 지정하세요.", file=sys.stderr)
        sys.exit(1)

    snapshot_date = args.snapshot_date or datetime.now().isoformat(timespec="seconds")
    conn = ensure_db("", database_url=args.database_url)
    try:
        snapshot = build_snapshot(
            conn,
            snapshot_date=snapshot_date,
            event_id_min=args.event_id_min,
            topic_id_min=args.topic_id_min,
            limit_events=args.limit_events,
            limit_topics=args.limit_topics,
        )
    finally:
        conn.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    print(f"[추출] {summarize(snapshot)} → {out}")

    if not snapshot["events"]:
        print(
            "경고: 이벤트가 0건입니다. 분류기를 아직 돌리지 않았거나 --event-id-min 이 "
            "너무 큽니다.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
