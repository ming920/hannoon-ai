from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from collector.storage import ensure_sqlite_db, save_article_analysis_result
from db import events, topics
from openai_client.client import parse_json_object
from summary_utils import TOPIC_TITLE_MAX_CHARS, normalize_summary, normalize_topic_title


class FakeConn:
    def __init__(self):
        self.query_params = None
        self.execute_params = None

    def query_one(self, sql, params=None):
        self.query_params = params
        return {"id": 123}

    def execute(self, sql, params=None):
        self.execute_params = params


class SummaryQualityTests(unittest.TestCase):
    def test_normalize_summary_limits_sentences_and_chars(self):
        text = "One. Two. Three. Four. Five. " + ("Tail text. " * 200)

        summary = normalize_summary(text, max_chars=80)

        self.assertLessEqual(len(summary), 80)
        self.assertNotIn("Five", summary)

    def test_normalize_topic_title_limits_chars_and_trailing_period(self):
        title = (
            "김승룡 소방청장이 외유성 출장 의혹 등으로 감찰을 받아 "
            "15일자로 의원 면직 처리됐다."
        )

        normalized = normalize_topic_title(title)

        self.assertLessEqual(len(normalized), TOPIC_TITLE_MAX_CHARS)
        self.assertFalse(normalized.endswith("."))

    def test_parse_json_object_accepts_control_characters(self):
        data = parse_json_object('{"summary": "line one\nline two"}')

        self.assertEqual(data["summary"], "line one\nline two")

    def test_article_result_save_normalizes_summary(self):
        long_summary = "One. Two. Three. Four. Five. " + ("Tail text. " * 200)

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "news.db")
            with ensure_sqlite_db(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO articles (id, link, guid, title, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (1, "https://example.com/a", "guid-a", "title", "ready"),
                )
                save_article_analysis_result(
                    conn,
                    article_id=1,
                    summary=long_summary,
                    keywords=["keyword"],
                )

                row = conn.query_one(
                    "SELECT summary FROM article_ai_results WHERE article_id = ?",
                    (1,),
                )

        self.assertLessEqual(len(row["summary"]), 700)
        self.assertNotIn("Five", row["summary"])

    def test_event_and_topic_repositories_normalize_summary_writes(self):
        long_summary = "One. Two. Three. Four. Five. " + ("Tail text. " * 200)

        event_conn = FakeConn()
        events.create_new_event(
            event_conn,
            category="사회",
            title="event title",
            summary=long_summary,
            core_content="core",
            embedding_text="embedding text",
            embedding_literal="[0.1]",
        )
        self.assertLessEqual(len(event_conn.query_params[2]), 700)
        self.assertNotIn("Five", event_conn.query_params[2])

        topic_conn = FakeConn()
        long_title = (
            "서울 송파경찰서는 잠실 개표소에서 핸드볼 여성 유소년 "
            "국가대표팀 선수들의 소지품을 검사한 피의자의 출석을 요구했다."
        )
        topics.create_topic(topic_conn, "사회", long_title, long_summary)
        self.assertLessEqual(len(topic_conn.query_params[1]), TOPIC_TITLE_MAX_CHARS)
        self.assertLessEqual(len(topic_conn.query_params[2]), 700)
        self.assertNotIn("Five", topic_conn.query_params[2])

        topics.update_topic(topic_conn, 123, long_title, long_summary)
        self.assertLessEqual(len(topic_conn.execute_params[0]), TOPIC_TITLE_MAX_CHARS)
        self.assertLessEqual(len(topic_conn.execute_params[1]), 700)

if __name__ == "__main__":
    unittest.main()
