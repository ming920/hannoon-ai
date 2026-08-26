"""eval/extract_snapshot.py 유닛테스트.

이 스크립트는 반복 실험 루프의 끊긴 고리를 잇는다 — 지금까지 대시보드에 SQL을 손으로
붙여넣던 단계다. 따라서 가장 중요한 검증은 "추출한 스냅샷이 채점 도구에 그대로 먹히는가"
(왕복)이고, 그걸 constraint_checks의 클러스터 맵 구성으로 확인한다.

SQLite in-memory를 쓴다. 수집기 테이블(articles / article_ai_results)은 자동 생성되고,
분류기 테이블은 여기서 직접 만든다.

실행: python -m unittest tests.test_extract_snapshot
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from constraint_checks import (  # noqa: E402
    build_event_cluster_map,
    build_topic_cluster_map,
    evaluate_pairs,
)
from extract_snapshot import _jsonable, build_snapshot, summarize  # noqa: E402

from collector.storage import ensure_db  # noqa: E402


def _build_conn():
    conn = ensure_db(":memory:")
    conn.execute(
        "CREATE TABLE topics (id INTEGER PRIMARY KEY, title TEXT, created_at TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            title TEXT,
            summary TEXT,
            core_content TEXT,
            created_at TEXT,
            article_count INTEGER,
            topic_id INTEGER,
            prev_event_id INTEGER,
            next_event_id INTEGER
        )
        """
    )
    conn.execute("CREATE TABLE event_articles (event_id INTEGER, article_id INTEGER)")
    return conn


def _seed(conn):
    """토픽 2개 / 이벤트 3개 / 기사 5건짜리 최소 구조."""
    for i in range(1, 6):
        conn.execute(
            "INSERT INTO articles (id, guid, title, publisher, published_at, link) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (i, f"g{i}", f"기사 {i}", "A신문", "2026-07-05T09:00:00", f"https://x/{i}"),
        )
        conn.execute(
            "INSERT INTO article_ai_results (article_id, summary, status) VALUES (?, ?, ?)",
            (i, f"요약 {i}", "event_assigned"),
        )

    conn.execute("INSERT INTO topics (id, title, created_at) VALUES (10, '토픽 A', '2026-07-01')")
    conn.execute("INSERT INTO topics (id, title, created_at) VALUES (20, '토픽 B', '2026-07-02')")

    # 이벤트 100(토픽10): 기사 1,2 / 101(토픽10): 기사 3 / 102(토픽20): 기사 4,5
    for eid, topic_id, cnt in ((100, 10, 2), (101, 10, 1), (102, 20, 2)):
        conn.execute(
            "INSERT INTO events (id, title, summary, core_content, created_at, "
            "article_count, topic_id, prev_event_id, next_event_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, f"이벤트 {eid}", "요약", "핵심", "2026-07-05", cnt, topic_id, None, None),
        )
    for eid, aid in ((100, 1), (100, 2), (101, 3), (102, 4), (102, 5)):
        conn.execute(
            "INSERT INTO event_articles (event_id, article_id) VALUES (?, ?)", (eid, aid)
        )


class SnapshotShapeTests(unittest.TestCase):
    def setUp(self):
        self.conn = _build_conn()
        _seed(self.conn)
        self.snap = build_snapshot(self.conn, snapshot_date="2026-07-25T00:00:00")

    def tearDown(self):
        self.conn.close()

    def test_snapshot_date_is_carried(self):
        self.assertEqual(self.snap["snapshot_date"], "2026-07-25T00:00:00")

    def test_all_events_present_sorted_by_id(self):
        self.assertEqual([e["id"] for e in self.snap["events"]], [100, 101, 102])

    def test_articles_nested_under_events(self):
        by_id = {e["id"]: e for e in self.snap["events"]}
        self.assertEqual([a["id"] for a in by_id[100]["articles"]], [1, 2])
        self.assertEqual([a["id"] for a in by_id[102]["articles"]], [4, 5])

    def test_article_fields_include_ai_summary(self):
        first = self.snap["events"][0]["articles"][0]
        self.assertEqual(first["summary"], "요약 1")       # article_ai_results 쪽
        self.assertEqual(first["publisher"], "A신문")
        self.assertEqual(first["link"], "https://x/1")

    def test_topics_nest_their_events_with_article_ids(self):
        by_id = {t["id"]: t for t in self.snap["topics"]}
        self.assertEqual([e["id"] for e in by_id[10]["events"]], [100, 101])
        self.assertEqual(by_id[10]["events"][0]["article_ids"], [1, 2])
        self.assertEqual(by_id[20]["events"][0]["article_ids"], [4, 5])

    def test_topic_without_events_is_still_listed(self):
        # 계층 모드의 부모 토픽은 직접 연결된 이벤트가 없다.
        self.conn.execute("INSERT INTO topics (id, title) VALUES (30, '빈 토픽')")
        snap = build_snapshot(self.conn, snapshot_date="x")
        empty = [t for t in snap["topics"] if t["id"] == 30][0]
        self.assertEqual(empty["events"], [])


class CursorAndLimitTests(unittest.TestCase):
    def setUp(self):
        self.conn = _build_conn()
        _seed(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_event_id_min_excludes_earlier_events(self):
        snap = build_snapshot(self.conn, snapshot_date="x", event_id_min=100)
        self.assertEqual([e["id"] for e in snap["events"]], [101, 102])

    def test_articles_of_excluded_events_are_dropped(self):
        snap = build_snapshot(self.conn, snapshot_date="x", event_id_min=101)
        all_article_ids = [a["id"] for e in snap["events"] for a in e["articles"]]
        self.assertEqual(sorted(all_article_ids), [4, 5])

    def test_topic_nesting_respects_event_cursor(self):
        snap = build_snapshot(self.conn, snapshot_date="x", event_id_min=101)
        by_id = {t["id"]: t for t in snap["topics"]}
        self.assertEqual(by_id[10]["events"], [])          # 100·101 중 101만 남았어야 하나
        # 101은 id > 101 조건에서 빠지므로 토픽 10에는 남는 이벤트가 없다
        self.assertEqual([e["id"] for e in by_id[20]["events"]], [102])

    def test_topic_id_min(self):
        snap = build_snapshot(self.conn, snapshot_date="x", topic_id_min=10)
        self.assertEqual([t["id"] for t in snap["topics"]], [20])

    def test_limit_events(self):
        snap = build_snapshot(self.conn, snapshot_date="x", limit_events=2)
        self.assertEqual([e["id"] for e in snap["events"]], [100, 101])

    def test_empty_database(self):
        conn = _build_conn()
        try:
            snap = build_snapshot(conn, snapshot_date="x")
            self.assertEqual(snap["events"], [])
            self.assertEqual(snap["topics"], [])
        finally:
            conn.close()


class RoundTripTests(unittest.TestCase):
    """추출한 스냅샷이 채점 도구 입력으로 그대로 쓰이는지 — 루프의 핵심 접합부."""

    def setUp(self):
        self.conn = _build_conn()
        _seed(self.conn)
        self.snap = build_snapshot(self.conn, snapshot_date="x")

    def tearDown(self):
        self.conn.close()

    def test_event_cluster_map_is_derivable(self):
        self.assertEqual(
            build_event_cluster_map(self.snap["events"]),
            {1: 100, 2: 100, 3: 101, 4: 102, 5: 102},
        )

    def test_topic_cluster_map_is_derivable(self):
        self.assertEqual(
            build_topic_cluster_map(self.snap["topics"]),
            {1: 10, 2: 10, 3: 10, 4: 20, 5: 20},
        )

    def test_constraints_score_against_extracted_snapshot(self):
        clusters = build_event_cluster_map(self.snap["events"])
        # [1,2] 같은 이벤트 → 충족 / [1,3] 다른 이벤트 → 위반
        r = evaluate_pairs([[1, 2], [1, 3]], clusters, expect_same=True)
        self.assertEqual((r["satisfied"], r["violated"]), (1, 1))

    def test_topic_level_differs_from_event_level(self):
        # 기사 1·3은 이벤트는 다르지만 토픽은 같다 — 두 층이 실제로 구분되는지 확인.
        ev = build_event_cluster_map(self.snap["events"])
        tp = build_topic_cluster_map(self.snap["topics"])
        self.assertNotEqual(ev[1], ev[3])
        self.assertEqual(tp[1], tp[3])

    def test_snapshot_is_json_serializable(self):
        import json

        dumped = json.dumps(self.snap, ensure_ascii=False)
        self.assertIn("이벤트 100", dumped)


class HelperTests(unittest.TestCase):
    def test_jsonable_converts_datetime(self):
        self.assertEqual(_jsonable(datetime(2026, 7, 25, 12, 0)), "2026-07-25T12:00:00")

    def test_jsonable_passes_through_other_types(self):
        self.assertEqual(_jsonable("문자열"), "문자열")
        self.assertEqual(_jsonable(7), 7)
        self.assertIsNone(_jsonable(None))

    def test_summarize_counts(self):
        conn = _build_conn()
        try:
            _seed(conn)
            text = summarize(build_snapshot(conn, snapshot_date="x"))
            self.assertIn("이벤트 3", text)
            self.assertIn("기사 5", text)
            self.assertIn("토픽 2", text)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
