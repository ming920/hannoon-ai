"""eval/topic_harness.py 유닛테스트.

하네스의 값어치는 "반복해서 비교할 수 있는가"에 있다. 그래서 지표 조립·CSV 누적·직전
실행 비교·경고 판정을 순수 함수로 떼어 고정한다. 실제 분류기 호출(드레인)만 서브프로세스다.

특히 detect_warnings 는 이 하네스의 핵심 안전장치다 — 정답의 토픽 cannot-link 제약이
0쌍이라 모든 이벤트를 한 토픽에 몰아넣어도 must-link 충족률은 만점이 나온다. 충족률이
올랐는데 토픽 수가 급감하는 패턴을 잡아내지 못하면 하네스가 잘못된 개선을 승인하게 된다.

실행: python -m unittest tests.test_topic_harness
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from topic_harness import (  # noqa: E402
    CSV_COLUMNS,
    _coerce,
    _patch_dotenv,
    append_csv,
    collect_metrics,
    compare_runs,
    count_unassigned_events,
    detect_warnings,
    format_report,
    has_topic_review,
    read_previous_run,
    reset_topic_layer,
)

from collector.storage import ensure_db  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# DB 조작
# ══════════════════════════════════════════════════════════════════════════


def _build_conn():
    conn = ensure_db(":memory:")
    conn.execute("CREATE TABLE topics (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, topic_id INTEGER, "
        "article_count INTEGER)"
    )
    conn.execute("CREATE TABLE event_articles (event_id INTEGER, article_id INTEGER)")
    conn.execute("CREATE TABLE topic_causes (id INTEGER PRIMARY KEY, topic_id INTEGER)")
    return conn


class ResetTopicLayerTests(unittest.TestCase):
    def setUp(self):
        self.conn = _build_conn()
        self.conn.execute("INSERT INTO topics (id, title) VALUES (10, 'T')")
        self.conn.execute("INSERT INTO topic_causes (id, topic_id) VALUES (1, 10)")
        for eid in (100, 101):
            self.conn.execute(
                "INSERT INTO events (id, title, topic_id, article_count) VALUES (?, ?, ?, ?)",
                (eid, f"E{eid}", 10, 7),
            )
        self.conn.execute("INSERT INTO event_articles (event_id, article_id) VALUES (100, 1)")

    def tearDown(self):
        self.conn.close()

    def _count(self, table):
        return self.conn.query_one(f"SELECT count(*) AS n FROM {table}")["n"]

    def test_topics_and_causes_are_wiped(self):
        reset_topic_layer(self.conn)
        self.assertEqual(self._count("topics"), 0)
        self.assertEqual(self._count("topic_causes"), 0)

    def test_events_are_preserved_but_unlinked(self):
        reset_topic_layer(self.conn)
        self.assertEqual(self._count("events"), 2)
        rows = self.conn.query("SELECT topic_id FROM events")
        self.assertTrue(all(r["topic_id"] is None for r in rows))

    def test_event_articles_untouched(self):
        reset_topic_layer(self.conn)
        self.assertEqual(self._count("event_articles"), 1)

    def test_returns_preserved_event_count(self):
        self.assertEqual(reset_topic_layer(self.conn), 2)

    def test_raises_when_no_events(self):
        conn = _build_conn()
        try:
            with self.assertRaises(ValueError) as ctx:
                reset_topic_layer(conn)
            self.assertIn("classify_events", str(ctx.exception))
        finally:
            conn.close()

    def test_is_idempotent(self):
        reset_topic_layer(self.conn)
        self.assertEqual(reset_topic_layer(self.conn), 2)


class CountUnassignedTests(unittest.TestCase):
    def setUp(self):
        self.conn = _build_conn()
        # article_count: 10(자격), 3(미달), 7(자격) — 전부 미배정
        for eid, cnt in ((1, 10), (2, 3), (3, 7)):
            self.conn.execute(
                "INSERT INTO events (id, title, topic_id, article_count) VALUES (?, ?, NULL, ?)",
                (eid, "E", cnt),
            )

    def tearDown(self):
        self.conn.close()

    def test_respects_min_net(self):
        self.assertEqual(count_unassigned_events(self.conn, 5), 2)
        self.assertEqual(count_unassigned_events(self.conn, 1), 3)
        self.assertEqual(count_unassigned_events(self.conn, 20), 0)

    def test_assigned_events_are_excluded(self):
        self.conn.execute("UPDATE events SET topic_id = 9 WHERE id = 1")
        self.assertEqual(count_unassigned_events(self.conn, 5), 1)


# ══════════════════════════════════════════════════════════════════════════
# 지표 조립
# ══════════════════════════════════════════════════════════════════════════


GOLD = {
    "event_constraints": {"must_link": [[1, 2]], "cannot_link": []},
    "topic_constraints": {"must_link": [[1, 2], [1, 3]], "cannot_link": []},
}
# 기사 1,2 → 이벤트 100 → 토픽 10 / 기사 3 → 이벤트 101 → 토픽 20
SNAPSHOT = {
    "events": [
        {"id": 100, "articles": [{"id": 1}, {"id": 2}]},
        {"id": 101, "articles": [{"id": 3}]},
    ],
    "topics": [
        {"id": 10, "events": [{"id": 100, "article_ids": [1, 2]}]},
        {"id": 20, "events": [{"id": 101, "article_ids": [3]}]},
    ],
}
DIAGNOSIS = {
    "meta": {"topic_must_link_total": 2, "topic_must_link_violated": 1},
    "topic_must_link_causes": {
        "T_EVENT_PROPAGATED": 0, "T_SUBTOPIC_SPLIT": 0, "T_A_NOT_RETRIEVED": 1,
        "T_B_TRUNCATED": 0, "T_C_LLM_REJECTED": 0, "T_D_GUARDRAIL": 0,
        "T_UNKNOWN_NO_LOG": 0,
    },
}
RUBRIC = {
    "R-T1": {"violations": 3},
    "R-T2": {"violations": 0},
    "R-S1": {"ratio": 0.42},
}


def _metrics(**kwargs) -> dict:
    base = dict(
        run_id="t-001", config_tag="baseline", timestamp="2026-07-25T00:00:00",
        gold=GOLD, snapshot=SNAPSHOT, diagnosis=DIAGNOSIS, rubric=RUBRIC,
        settings={"SUBTOPIC_MODE": "embedding", "TOPIC_ASSIGN_SCORE_THRESHOLD": "0.75"},
    )
    base.update(kwargs)
    return collect_metrics(**base)


class CollectMetricsTests(unittest.TestCase):
    def setUp(self):
        self.m = _metrics()

    def test_topic_must_rate(self):
        # [1,2] 같은 토픽 → 충족 / [1,3] 다른 토픽 → 위반 → 50%
        self.assertEqual(self.m["topic_must_rate"], 0.5)

    def test_event_rate_is_recorded_for_reference(self):
        self.assertEqual(self.m["event_must_rate"], 1.0)

    def test_empty_constraint_group_is_none(self):
        self.assertIsNone(self.m["topic_cannot_rate"])

    def test_structural_counts(self):
        self.assertEqual(self.m["topics_total"], 2)
        self.assertEqual(self.m["events_total"], 2)
        self.assertEqual(self.m["events_assigned"], 2)

    def test_cause_breakdown(self):
        self.assertEqual(self.m["t_a"], 1)
        self.assertEqual(self.m["t_propagated"], 0)

    def test_rubric_values(self):
        self.assertEqual(self.m["rubric_t1"], 3)
        self.assertEqual(self.m["rubric_s1_ratio"], 0.42)

    def test_skipped_rubric_check_becomes_none(self):
        m = _metrics(rubric={"R-T1": {"skipped": True, "reason": "컬럼 없음"}})
        self.assertIsNone(m["rubric_t1"])

    def test_settings_are_recorded(self):
        self.assertEqual(self.m["subtopic_mode"], "embedding")
        self.assertEqual(self.m["assign_score"], "0.75")

    def test_all_csv_columns_present(self):
        for column in CSV_COLUMNS:
            self.assertIn(column, self.m, f"{column} 누락")


# ══════════════════════════════════════════════════════════════════════════
# 비교 / 경고
# ══════════════════════════════════════════════════════════════════════════


class TopicReviewProvenanceTests(unittest.TestCase):
    """토픽 정답이 사람 검수인지 이벤트 검수의 파생값인지 구분한다.

    2026-07-20 검수본은 `_work.topics`/`_work.edges` 가 모두 비어 있다 — 검수자는 이벤트만
    봤다. 그런데 리포트에는 "토픽 must-link 91.0%" 만 찍혀, 이벤트(84.0%)보다 높으니 토픽
    분류가 더 낫다고 읽히기 쉬웠다. 실제로는 토픽이 더 큰 바구니라 must-link 가 자동으로 더
    잘 만족되는 것뿐이다.
    """

    def test_empty_work_means_no_topic_review(self):
        self.assertFalse(has_topic_review({"_work": {"topics": {}, "edges": {}}}))

    def test_missing_work_means_no_topic_review(self):
        self.assertFalse(has_topic_review({}))
        self.assertFalse(has_topic_review(None))

    def test_topics_present_means_reviewed(self):
        self.assertTrue(has_topic_review({"_work": {"topics": {"1": {}}, "edges": {}}}))

    def test_edges_alone_also_counts(self):
        self.assertTrue(has_topic_review({"_work": {"topics": {}, "edges": {"1": {}}}}))

    def test_metric_records_provenance(self):
        self.assertFalse(_metrics()["topic_review_present"])
        reviewed = dict(GOLD, _work={"topics": {"1": {}}, "edges": {}})
        self.assertTrue(_metrics(gold=reviewed)["topic_review_present"])

    def test_warning_fires_only_when_underived(self):
        warnings = detect_warnings(_metrics(), None)
        self.assertTrue(any("파생된 값" in w for w in warnings), warnings)

        reviewed = dict(GOLD, _work={"topics": {"1": {}}, "edges": {}})
        warnings = detect_warnings(_metrics(gold=reviewed), None)
        self.assertFalse(any("파생된 값" in w for w in warnings), warnings)

    def test_report_carries_the_caveat(self):
        text = format_report(_metrics(), DIAGNOSIS, [], [])
        self.assertIn("사람이 검수한 것이 아닙니다", text)

    def test_report_drops_caveat_when_reviewed(self):
        reviewed = dict(GOLD, _work={"topics": {"1": {}}, "edges": {}})
        text = format_report(_metrics(gold=reviewed), DIAGNOSIS, [], [])
        self.assertNotIn("사람이 검수한 것이 아닙니다", text)

    def test_report_shows_pair_counts(self):
        text = format_report(_metrics(), DIAGNOSIS, [], [])
        self.assertIn("채점된 쌍", text)
        self.assertIn("표본 부족", text)  # 픽스처는 2쌍뿐이다


class CompareRunsTests(unittest.TestCase):
    def test_no_previous_run_yields_empty(self):
        self.assertEqual(compare_runs(_metrics(), None), [])

    def test_rate_increase_is_improvement(self):
        prev = {"topic_must_rate": "0.4", "topics_total": "2"}
        rows = {r["column"]: r for r in compare_runs(_metrics(), prev)}
        self.assertEqual(rows["topic_must_rate"]["verdict"], "개선")

    def test_rate_decrease_is_regression(self):
        prev = {"topic_must_rate": "0.6"}
        rows = {r["column"]: r for r in compare_runs(_metrics(), prev)}
        self.assertEqual(rows["topic_must_rate"]["verdict"], "악화")

    def test_rubric_increase_is_regression(self):
        # R-T1(중복 토픽)은 작을수록 좋다.
        prev = {"rubric_t1": "1"}
        rows = {r["column"]: r for r in compare_runs(_metrics(), prev)}
        self.assertEqual(rows["rubric_t1"]["verdict"], "악화")

    def test_rubric_decrease_is_improvement(self):
        prev = {"rubric_t1": "9"}
        rows = {r["column"]: r for r in compare_runs(_metrics(), prev)}
        self.assertEqual(rows["rubric_t1"]["verdict"], "개선")

    def test_equal_is_maintained(self):
        prev = {"topic_must_rate": "0.5"}
        rows = {r["column"]: r for r in compare_runs(_metrics(), prev)}
        self.assertEqual(rows["topic_must_rate"]["verdict"], "유지")

    def test_non_numeric_columns_are_skipped(self):
        rows = {r["column"] for r in compare_runs(_metrics(), {"subtopic_mode": "llm"})}
        self.assertNotIn("subtopic_mode", rows)
        self.assertNotIn("run_id", rows)

    def test_none_current_value_is_skipped(self):
        rows = {r["column"] for r in compare_runs(_metrics(), {"topic_cannot_rate": "0.9"})}
        self.assertNotIn("topic_cannot_rate", rows)


class DetectWarningsTests(unittest.TestCase):
    def test_missing_cannot_link_constraint_warns(self):
        warnings = detect_warnings(_metrics(), None)
        self.assertTrue(any("과병합" in w for w in warnings))

    def test_zero_topics_warns(self):
        m = _metrics(snapshot={"events": [], "topics": []})
        self.assertTrue(any("토픽이 0개" in w for w in detect_warnings(m, None)))

    def test_rate_up_but_topics_collapsed_warns(self):
        # 하네스의 핵심 안전장치: 과병합으로 점수를 산 경우를 잡아야 한다.
        prev = {"topic_must_rate": "0.3", "topics_total": "100"}
        warnings = detect_warnings(_metrics(), prev)
        self.assertTrue(any("과병합으로 점수를 샀을" in w for w in warnings))

    def test_rate_up_with_stable_topics_does_not_warn(self):
        prev = {"topic_must_rate": "0.3", "topics_total": "2"}
        warnings = detect_warnings(_metrics(), prev)
        self.assertFalse(any("과병합으로 점수를 샀을" in w for w in warnings))

    def test_all_propagated_warns_to_fix_events_first(self):
        diag = {
            "meta": {},
            "topic_must_link_causes": {
                "T_EVENT_PROPAGATED": 5, "T_SUBTOPIC_SPLIT": 0, "T_A_NOT_RETRIEVED": 0,
                "T_B_TRUNCATED": 0, "T_C_LLM_REJECTED": 0, "T_D_GUARDRAIL": 0,
                "T_UNKNOWN_NO_LOG": 0,
            },
        }
        warnings = detect_warnings(_metrics(diagnosis=diag), None)
        self.assertTrue(any("이벤트 분류를 먼저" in w for w in warnings))


# ══════════════════════════════════════════════════════════════════════════
# 누적 / 리포트
# ══════════════════════════════════════════════════════════════════════════


class CsvAccumulationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "topic_runs.csv"

    def tearDown(self):
        self.dir.cleanup()

    def test_header_written_once(self):
        append_csv(self.path, _metrics(run_id="a"))
        append_csv(self.path, _metrics(run_id="b"))
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)          # 헤더 + 2행
        self.assertTrue(lines[0].startswith("run_id,"))

    def test_none_becomes_empty_string(self):
        append_csv(self.path, _metrics())
        row = read_previous_run(self.path, exclude_run_id="없음")
        self.assertEqual(row["topic_cannot_rate"], "")

    def test_read_previous_excludes_current_run(self):
        append_csv(self.path, _metrics(run_id="a"))
        append_csv(self.path, _metrics(run_id="b"))
        self.assertEqual(read_previous_run(self.path, "b")["run_id"], "a")

    def test_read_previous_returns_none_when_only_current(self):
        append_csv(self.path, _metrics(run_id="a"))
        self.assertIsNone(read_previous_run(self.path, "a"))

    def test_missing_file_returns_none(self):
        self.assertIsNone(read_previous_run(self.path, "a"))

    def test_roundtrip_feeds_compare(self):
        append_csv(self.path, _metrics(run_id="a"))
        previous = read_previous_run(self.path, "b")
        rows = {r["column"]: r for r in compare_runs(_metrics(run_id="b"), previous)}
        self.assertEqual(rows["topic_must_rate"]["verdict"], "유지")


class CoerceTests(unittest.TestCase):
    def test_int_and_float_strings(self):
        self.assertEqual(_coerce("3"), 3)
        self.assertEqual(_coerce("0.5"), 0.5)

    def test_empty_and_none(self):
        self.assertIsNone(_coerce(""))
        self.assertIsNone(_coerce(None))

    def test_non_numeric_passthrough(self):
        self.assertEqual(_coerce("embedding"), "embedding")


class FormatReportTests(unittest.TestCase):
    def test_contains_key_sections(self):
        text = format_report(_metrics(), DIAGNOSIS, [], [])
        for heading in ("제약 충족률", "구조 지표", "위반 원인"):
            self.assertIn(heading, text)

    def test_warnings_section_appears(self):
        text = format_report(_metrics(), DIAGNOSIS, [], ["조심하세요"])
        self.assertIn("경고", text)
        self.assertIn("조심하세요", text)

    def test_comparison_section_appears(self):
        rows = compare_runs(_metrics(), {"topic_must_rate": "0.4"})
        text = format_report(_metrics(), DIAGNOSIS, rows, [])
        self.assertIn("직전 실행 대비", text)


class PatchDotenvTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / ".env"
        self.path.write_text("A=1\n# 주석\nB=2\n", encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def test_existing_key_replaced(self):
        _patch_dotenv(self.path, ["B=9"])
        self.assertIn("B=9\n", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("B=2", self.path.read_text(encoding="utf-8"))

    def test_new_key_appended(self):
        _patch_dotenv(self.path, ["C=3"])
        self.assertIn("C=3\n", self.path.read_text(encoding="utf-8"))

    def test_comments_preserved(self):
        _patch_dotenv(self.path, ["A=5"])
        self.assertIn("# 주석", self.path.read_text(encoding="utf-8"))

    def test_returns_original_for_restore(self):
        original = _patch_dotenv(self.path, ["A=5"])
        self.path.write_text(original, encoding="utf-8")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "A=1\n# 주석\nB=2\n")


# ══════════════════════════════════════════════════════════════════════════
# 통합 — 루프가 실제로 닫히는가
# ══════════════════════════════════════════════════════════════════════════


class LoopClosesTests(unittest.TestCase):
    """DB → 스냅샷 → 진단 → 지표 → CSV → 다음 실행 비교까지 한 바퀴.

    분류기 서브프로세스만 빼고 데이터 경로 전체를 통과시킨다. 이 경로가 이어지지 않으면
    "반복해서 개선한다"는 하네스의 전제가 성립하지 않는다.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.dir.name) / "topic_runs.csv"

    def tearDown(self):
        self.dir.cleanup()

    def _db(self, *, split: bool):
        """기사 1·2·3. split=True면 기사 3이 다른 토픽으로 갈린다(위반)."""
        conn = ensure_db(":memory:")
        conn.execute("CREATE TABLE topics (id INTEGER PRIMARY KEY, title TEXT, created_at TEXT)")
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, summary TEXT, "
            "core_content TEXT, created_at TEXT, article_count INTEGER, topic_id INTEGER, "
            "prev_event_id INTEGER, next_event_id INTEGER)"
        )
        conn.execute("CREATE TABLE event_articles (event_id INTEGER, article_id INTEGER)")
        for i in (1, 2, 3):
            conn.execute(
                "INSERT INTO articles (id, guid, title) VALUES (?, ?, ?)", (i, f"g{i}", f"기사{i}")
            )
        conn.execute("INSERT INTO topics (id, title) VALUES (10, '토픽')")
        if split:
            conn.execute("INSERT INTO topics (id, title) VALUES (20, '갈린 토픽')")
        for eid, aid in ((100, 1), (101, 2), (102, 3)):
            topic = 20 if (split and eid == 102) else 10
            conn.execute(
                "INSERT INTO events (id, title, article_count, topic_id) VALUES (?, ?, ?, ?)",
                (eid, f"E{eid}", 7, topic),
            )
            conn.execute(
                "INSERT INTO event_articles (event_id, article_id) VALUES (?, ?)", (eid, aid)
            )
        return conn

    def _run(self, *, run_id, split):
        from diagnose_violations import parse_topic_logs, run_diagnosis
        from extract_snapshot import build_snapshot

        gold = {
            "event_constraints": {"must_link": [], "cannot_link": []},
            "topic_constraints": {"must_link": [[1, 2], [1, 3]], "cannot_link": []},
        }
        conn = self._db(split=split)
        try:
            snapshot = build_snapshot(conn, snapshot_date="x")
        finally:
            conn.close()

        log_text = "\n".join(
            f'{{"event_id": {eid}, "level": "flat", "candidate_count": 0, '
            f'"candidates": [], "llm_decision": {{}}, "overridden": false, '
            f'"dedup_merged": false, "decided_by": "llm", "final_action": "create", '
            f'"final_topic_id": {tid}}}'
            for eid, tid in ((100, 10), (101, 10), (102, 20 if split else 10))
        )
        topic_logs, topic_order = parse_topic_logs(log_text)
        diagnosis = run_diagnosis(gold, snapshot, {}, {}, topic_logs, topic_order)
        metrics = collect_metrics(
            run_id=run_id, config_tag="t", timestamp="2026-07-25T00:00:00",
            gold=gold, snapshot=snapshot, diagnosis=diagnosis,
            rubric={"R-T1": {"violations": 0}}, settings={},
        )
        previous = read_previous_run(self.csv_path, run_id)
        append_csv(self.csv_path, metrics)
        return metrics, previous

    def test_first_run_is_baseline_then_second_run_compares(self):
        # 1회차: 기사 3이 갈려 토픽 must-link 절반만 충족
        first, prev0 = self._run(run_id="t-000", split=True)
        self.assertIsNone(prev0)
        self.assertEqual(first["topic_must_rate"], 0.5)
        self.assertEqual(first["topics_total"], 2)

        # 2회차: 갈림이 해소되어 전부 충족
        second, prev1 = self._run(run_id="t-001", split=False)
        self.assertIsNotNone(prev1)
        self.assertEqual(prev1["run_id"], "t-000")
        self.assertEqual(second["topic_must_rate"], 1.0)

        rows = {r["column"]: r for r in compare_runs(second, prev1)}
        self.assertEqual(rows["topic_must_rate"]["verdict"], "개선")
        self.assertAlmostEqual(rows["topic_must_rate"]["delta"], 0.5)

    def test_improvement_by_over_merging_is_flagged(self):
        # 토픽 수가 급감하며 충족률이 오르면 경고해야 한다 (여기선 2 → 1, 50% 감소)
        self._run(run_id="t-000", split=True)
        second, prev = self._run(run_id="t-001", split=False)
        warnings = detect_warnings(second, prev)
        self.assertTrue(any("과병합으로 점수를 샀을" in w for w in warnings))

    def test_csv_accumulates_across_runs(self):
        self._run(run_id="t-000", split=True)
        self._run(run_id="t-001", split=False)
        self._run(run_id="t-002", split=False)
        lines = self.csv_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 4)  # 헤더 + 3회


if __name__ == "__main__":
    unittest.main()
