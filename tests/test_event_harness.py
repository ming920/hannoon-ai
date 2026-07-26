"""eval/event_harness.py 유닛테스트.

토픽 하네스와 같은 뼈대지만 실패 양상이 양방향이라 경고 판정이 더 중요하다.
  모두 한 이벤트로 병합 → must-link 100%, cannot-link 0%
  모두 쪼갬              → cannot-link 100%, must-link 0%
둘 중 어느 쪽으로 치우쳐도 한쪽 숫자는 좋아 보이므로, detect_warnings 가 이를 잡아내지
못하면 하네스가 잘못된 개선을 승인한다.

count_pending_articles 는 분류기의 대상 선정 조건과 **정확히 같아야** 한다. 어긋나면
드레인이 영원히 안 끝나거나 너무 일찍 끝난다.

실행: python -m unittest tests.test_event_harness
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from event_harness import (  # noqa: E402
    CSV_COLUMNS,
    append_csv,
    collect_metrics,
    compare_runs,
    count_pending_articles,
    detect_warnings,
    format_report,
)
from harness_common import read_previous_run  # noqa: E402

from collector.storage import ensure_db  # noqa: E402


class CountPendingArticlesTests(unittest.TestCase):
    """분류기(pipeline.py)의 done_articles 쿼리 조건과 일치해야 한다."""

    def setUp(self):
        self.conn = ensure_db(":memory:")

    def tearDown(self):
        self.conn.close()

    def _add(self, aid, *, status, content):
        self.conn.execute(
            "INSERT INTO articles (id, guid, title, content) VALUES (?, ?, ?, ?)",
            (aid, f"g{aid}", "제목", content),
        )
        self.conn.execute(
            "INSERT INTO article_ai_results (article_id, summary, status) VALUES (?, ?, ?)",
            (aid, "요약", status),
        )

    def test_counts_only_done_status(self):
        self._add(1, status="done", content="본문")
        self._add(2, status="event_assigned", content="본문")
        self.assertEqual(count_pending_articles(self.conn), 1)

    def test_excludes_empty_content(self):
        self._add(1, status="done", content="")
        self._add(2, status="done", content=None)
        self._add(3, status="done", content="본문")
        self.assertEqual(count_pending_articles(self.conn), 1)

    def test_article_without_ai_result_is_excluded(self):
        self.conn.execute(
            "INSERT INTO articles (id, guid, title, content) VALUES (9, 'g9', '제목', '본문')"
        )
        self.assertEqual(count_pending_articles(self.conn), 0)

    def test_empty_database(self):
        self.assertEqual(count_pending_articles(self.conn), 0)


# ══════════════════════════════════════════════════════════════════════════
# 지표
# ══════════════════════════════════════════════════════════════════════════


GOLD = {
    "event_constraints": {"must_link": [[1, 2], [1, 3]], "cannot_link": [[1, 4]]},
    "topic_constraints": {"must_link": [], "cannot_link": []},
}
# 기사 1,2 → 이벤트 100 / 기사 3 → 101(단일) / 기사 4 → 102(단일)
SNAPSHOT = {
    "events": [
        {"id": 100, "articles": [{"id": 1}, {"id": 2}]},
        {"id": 101, "articles": [{"id": 3}]},
        {"id": 102, "articles": [{"id": 4}]},
    ],
    "topics": [],
}
DIAGNOSIS = {
    "meta": {"must_link_total": 2, "must_link_violated": 1},
    "must_link_causes": {
        "A_NOT_RETRIEVED": 1, "B_TRUNCATED": 0, "C_LLM_REJECTED": 0,
        "D_GUARDRAIL": 0, "UNKNOWN_NO_LOG": 0,
    },
}
RUBRIC = {"R-E1": {"ratio": 0.67}, "R-E2": {"violations": 2}}


def _metrics(**kwargs) -> dict:
    base = dict(
        run_id="e-001", config_tag="baseline", timestamp="2026-07-25T00:00:00",
        gold=GOLD, snapshot=SNAPSHOT, diagnosis=DIAGNOSIS, rubric=RUBRIC,
        settings={"EVENT_DISTANCE_THRESHOLD": "0.50", "EVENT_ASSIGN_SCORE_THRESHOLD": "0.80"},
    )
    base.update(kwargs)
    return collect_metrics(**base)


class CollectMetricsTests(unittest.TestCase):
    def setUp(self):
        self.m = _metrics()

    def test_must_rate(self):
        # [1,2] 같은 이벤트 충족 / [1,3] 다른 이벤트 위반 → 50%
        self.assertEqual(self.m["event_must_rate"], 0.5)

    def test_cannot_rate(self):
        # [1,4] 다른 이벤트 → 충족
        self.assertEqual(self.m["event_cannot_rate"], 1.0)

    def test_structural_counts(self):
        self.assertEqual(self.m["events_total"], 3)
        self.assertEqual(self.m["articles_assigned"], 4)
        self.assertEqual(self.m["single_article_events"], 2)

    def test_cause_breakdown(self):
        self.assertEqual(self.m["c_a"], 1)
        self.assertEqual(self.m["c_c"], 0)

    def test_rubric_values(self):
        self.assertEqual(self.m["rubric_e1_ratio"], 0.67)
        self.assertEqual(self.m["rubric_e2"], 2)

    def test_skipped_rubric_becomes_none(self):
        m = _metrics(rubric={"R-E1": {"skipped": True, "reason": "x"}})
        self.assertIsNone(m["rubric_e1_ratio"])

    def test_settings_recorded(self):
        self.assertEqual(self.m["distance_threshold"], "0.50")

    def test_all_csv_columns_present(self):
        for column in CSV_COLUMNS:
            self.assertIn(column, self.m, f"{column} 누락")


class DetectWarningsTests(unittest.TestCase):
    def test_zero_events_warns_and_stops(self):
        m = _metrics(snapshot={"events": [], "topics": []})
        warnings = detect_warnings(m, None)
        self.assertEqual(len(warnings), 1)
        self.assertIn("이벤트가 0개", warnings[0])

    def test_mostly_single_article_events_warns_over_split(self):
        # 3개 중 2개가 단일 → 67%, 임계 70% 미만이라 경고 없음
        self.assertFalse(any("과분할" in w for w in detect_warnings(_metrics(), None)))
        # 4개 중 3개 단일 → 75%
        snap = {
            "events": [
                {"id": 100, "articles": [{"id": 1}, {"id": 2}]},
                {"id": 101, "articles": [{"id": 3}]},
                {"id": 102, "articles": [{"id": 4}]},
                {"id": 103, "articles": [{"id": 5}]},
            ],
            "topics": [],
        }
        warnings = detect_warnings(_metrics(snapshot=snap), None)
        self.assertTrue(any("과분할" in w for w in warnings))

    def test_rate_up_but_events_collapsed_warns_over_merge(self):
        prev = {"events_total": "100", "event_must_rate": "0.2", "event_cannot_rate": "1.0"}
        warnings = detect_warnings(_metrics(), prev)
        self.assertTrue(any("과병합으로 점수를 샀을" in w for w in warnings))

    def test_must_up_cannot_down_tradeoff_warns(self):
        prev = {"events_total": "3", "event_must_rate": "0.2", "event_cannot_rate": "1.0"}
        m = _metrics(
            gold={"event_constraints": {"must_link": [[1, 2], [1, 3]],
                                        "cannot_link": [[1, 2]]},
                  "topic_constraints": {"must_link": [], "cannot_link": []}}
        )
        # cannot_link [1,2]는 같은 이벤트라 위반 → 0.0 (이전 1.0에서 하락)
        self.assertEqual(m["event_cannot_rate"], 0.0)
        warnings = detect_warnings(m, prev)
        self.assertTrue(any("트레이드오프" in w for w in warnings))

    def test_stable_run_has_no_tradeoff_warning(self):
        prev = {"events_total": "3", "event_must_rate": "0.5", "event_cannot_rate": "1.0"}
        warnings = detect_warnings(_metrics(), prev)
        self.assertFalse(any("트레이드오프" in w for w in warnings))


class CompareAndAccumulateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "event_runs.csv"

    def tearDown(self):
        self.dir.cleanup()

    def test_no_previous_is_empty(self):
        self.assertEqual(compare_runs(_metrics(), None), [])

    def test_must_rate_improvement(self):
        rows = {r["column"]: r for r in compare_runs(_metrics(), {"event_must_rate": "0.2"})}
        self.assertEqual(rows["event_must_rate"]["verdict"], "개선")

    def test_rubric_e1_lower_is_better(self):
        rows = {r["column"]: r for r in compare_runs(_metrics(), {"rubric_e1_ratio": "0.3"})}
        self.assertEqual(rows["rubric_e1_ratio"]["verdict"], "악화")

    def test_events_total_is_neutral_direction(self):
        # 이벤트 개수는 많다고 좋은 것도 적다고 좋은 것도 아니다.
        rows = {r["column"]: r for r in compare_runs(_metrics(), {"events_total": "10"})}
        self.assertEqual(rows["events_total"]["verdict"], "변동")

    def test_csv_roundtrip_feeds_next_run(self):
        append_csv(self.path, _metrics(run_id="e-000"))
        previous = read_previous_run(self.path, "e-001")
        self.assertEqual(previous["run_id"], "e-000")
        rows = {r["column"]: r for r in compare_runs(_metrics(run_id="e-001"), previous)}
        self.assertEqual(rows["event_must_rate"]["verdict"], "유지")

    def test_header_written_once(self):
        append_csv(self.path, _metrics(run_id="a"))
        append_csv(self.path, _metrics(run_id="b"))
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)


class FormatReportTests(unittest.TestCase):
    def test_sections_present(self):
        text = format_report(_metrics(), DIAGNOSIS, [], [])
        for heading in ("제약 충족률", "구조 지표", "must-link 위반 원인"):
            self.assertIn(heading, text)

    def test_single_article_percentage_rendered(self):
        text = format_report(_metrics(), DIAGNOSIS, [], [])
        self.assertIn("66.7%", text)  # 3개 중 2개

    def test_zero_events_does_not_divide_by_zero(self):
        m = _metrics(snapshot={"events": [], "topics": []})
        text = format_report(m, DIAGNOSIS, [], [])
        self.assertIn("이벤트 개수 | 0", text)


if __name__ == "__main__":
    unittest.main()
