"""eval/reset_classifier_only.py 유닛테스트.

이 스크립트의 존재 이유는 단 하나 — `article_ai_results`의 기사 요약을 **보존**하면서
분류기 출력만 지우는 것이다. 요약은 수집기 단계가 LLM으로 만든 비싼 산출물이라,
`reset_test_db.py`처럼 통째로 지우면 제약 검사를 반복할 때마다 재생성해야 한다.

따라서 "무엇이 지워지는가"보다 **"무엇이 살아남는가"**를 더 촘촘히 검증한다.

SQLite in-memory(ensure_db의 sqlite 경로)를 쓴다. 수집기 테이블(articles /
article_ai_results)은 자동 생성되고, 분류기 테이블은 여기서 직접 만든다.

실행: python -m unittest tests.test_reset_classifier_only
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reset_classifier_only import (  # noqa: E402
    reset_classifier_output,
    verify_reset,
)

from collector.storage import ensure_db  # noqa: E402


def _build_conn():
    """분류기 테이블까지 갖춘 SQLite in-memory 연결을 만든다."""
    conn = ensure_db(":memory:")
    conn.execute(
        "CREATE TABLE topics (id INTEGER PRIMARY KEY, title TEXT, parent_topic_id INTEGER)"
    )
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            title TEXT,
            topic_id INTEGER,
            prev_event_id INTEGER,
            next_event_id INTEGER
        )
        """
    )
    conn.execute("CREATE TABLE event_articles (event_id INTEGER, article_id INTEGER)")
    conn.execute("CREATE TABLE topic_causes (id INTEGER PRIMARY KEY, topic_id INTEGER)")
    return conn


def _seed(conn):
    """기사 3건 + 그 요약, 그리고 이벤트/토픽 구조를 넣는다."""
    for i in (1, 2, 3):
        conn.execute(
            "INSERT INTO articles (id, guid, title, content) VALUES (?, ?, ?, ?)",
            (i, f"guid-{i}", f"기사 {i}", "본문"),
        )
        conn.execute(
            "INSERT INTO article_ai_results (article_id, summary, status) VALUES (?, ?, ?)",
            (i, f"기사 {i} 의 비싼 LLM 요약", "event_assigned"),
        )

    conn.execute("INSERT INTO topics (id, title) VALUES (10, '토픽')")
    # 자기참조 체인: 100 → 101
    conn.execute(
        "INSERT INTO events (id, title, topic_id, prev_event_id, next_event_id) "
        "VALUES (100, '이벤트 A', 10, NULL, 101)"
    )
    conn.execute(
        "INSERT INTO events (id, title, topic_id, prev_event_id, next_event_id) "
        "VALUES (101, '이벤트 B', 10, 100, NULL)"
    )
    conn.execute("INSERT INTO event_articles (event_id, article_id) VALUES (100, 1)")
    conn.execute("INSERT INTO event_articles (event_id, article_id) VALUES (100, 2)")
    conn.execute("INSERT INTO event_articles (event_id, article_id) VALUES (101, 3)")
    conn.execute("INSERT INTO topic_causes (id, topic_id) VALUES (1, 10)")


def _count(conn, table: str) -> int:
    return conn.query_one(f"SELECT count(*) AS n FROM {table}")["n"]


class ResetClassifierOnlyTests(unittest.TestCase):
    def setUp(self):
        self.conn = _build_conn()
        _seed(self.conn)

    def tearDown(self):
        self.conn.close()

    # ── 지워져야 하는 것 ──────────────────────────────────────────────

    def test_classifier_output_tables_are_emptied(self):
        reset_classifier_output(self.conn)
        for table in ("event_articles", "events", "topic_causes", "topics"):
            self.assertEqual(_count(self.conn, table), 0, f"{table} 이 비지 않았다")

    def test_self_referencing_events_are_deletable(self):
        # prev/next 자기참조를 먼저 NULL 처리하지 않으면 FK가 있는 DB에서 삭제가 막힌다.
        reset_classifier_output(self.conn)
        self.assertEqual(_count(self.conn, "events"), 0)

    # ── 살아남아야 하는 것 (이 스크립트의 존재 이유) ──────────────────

    def test_articles_are_untouched(self):
        reset_classifier_output(self.conn)
        self.assertEqual(_count(self.conn, "articles"), 3)

    def test_ai_result_rows_survive(self):
        reset_classifier_output(self.conn)
        self.assertEqual(_count(self.conn, "article_ai_results"), 3)

    def test_expensive_summaries_survive(self):
        reset_classifier_output(self.conn)
        rows = self.conn.query("SELECT article_id, summary FROM article_ai_results ORDER BY article_id")
        self.assertEqual(
            [r["summary"] for r in rows],
            ["기사 1 의 비싼 LLM 요약", "기사 2 의 비싼 LLM 요약", "기사 3 의 비싼 LLM 요약"],
        )

    def test_status_is_rewound_to_done(self):
        # 분류기가 event_assigned로 바꿔놓은 상태를 되돌려야 재처리 대상이 된다.
        reset_classifier_output(self.conn)
        rows = self.conn.query("SELECT status FROM article_ai_results")
        self.assertTrue(all(r["status"] == "done" for r in rows))

    def test_updated_at_is_stamped(self):
        reset_classifier_output(self.conn)
        rows = self.conn.query("SELECT updated_at FROM article_ai_results")
        self.assertTrue(all(r["updated_at"] for r in rows))

    # ── 반환값 / 검증 ────────────────────────────────────────────────

    def test_returns_pre_delete_counts(self):
        before = reset_classifier_output(self.conn)
        self.assertEqual(before["event_articles"], 3)
        self.assertEqual(before["events"], 2)
        self.assertEqual(before["topics"], 1)
        self.assertEqual(before["topic_causes"], 1)
        self.assertEqual(before["articles"], 3)
        self.assertEqual(before["article_ai_results"], 3)

    def test_verify_passes_after_reset(self):
        reset_classifier_output(self.conn)
        self.assertEqual(verify_reset(self.conn), [])

    def test_verify_flags_leftover_classifier_rows(self):
        reset_classifier_output(self.conn)
        self.conn.execute("INSERT INTO topics (id, title) VALUES (99, '남은 토픽')")
        problems = verify_reset(self.conn)
        self.assertEqual(len(problems), 1)
        self.assertIn("topics", problems[0])

    def test_verify_flags_status_not_rewound(self):
        reset_classifier_output(self.conn)
        self.conn.execute("UPDATE article_ai_results SET status = 'event_assigned' WHERE article_id = 1")
        problems = verify_reset(self.conn)
        self.assertEqual(len(problems), 1)
        self.assertIn("status", problems[0])

    # ── 멱등성 ───────────────────────────────────────────────────────

    def test_running_twice_is_safe(self):
        reset_classifier_output(self.conn)
        before = reset_classifier_output(self.conn)
        self.assertEqual(before["events"], 0)
        self.assertEqual(before["article_ai_results"], 3)  # 두 번 돌려도 요약은 그대로
        self.assertEqual(verify_reset(self.conn), [])

    def test_empty_database_is_safe(self):
        conn = _build_conn()  # 시드 없음
        try:
            before = reset_classifier_output(conn)
            self.assertEqual(before["events"], 0)
            self.assertEqual(verify_reset(conn), [])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
