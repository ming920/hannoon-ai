"""eval/constraint_checks.py 유닛테스트.

제약 판정은 전부 순수 함수라 DB 없이 검증한다(결과 스냅샷/정답 JSON은 dict 리터럴로 구성).

실행: python -m unittest tests.test_constraint_checks
"""
from __future__ import annotations

import os
import sys
import unittest

# eval/ 디렉터리를 경로에 추가한다(constraint_checks.py 임포트용).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from constraint_checks import (  # noqa: E402
    build_event_cluster_map,
    build_topic_cluster_map,
    collect_review_memos,
    compare_with_baseline,
    evaluate_pairs,
    extract_baseline,
    run_all_checks,
    satisfaction_rate,
)


# ══════════════════════════════════════════════════════════════════════════
# 클러스터 맵 구성
# ══════════════════════════════════════════════════════════════════════════


class TestBuildEventClusterMap(unittest.TestCase):
    def test_maps_article_id_to_event_id(self):
        events = [
            {"id": 101, "articles": [{"id": 1}, {"id": 2}]},
            {"id": 102, "articles": [{"id": 3}]},
        ]
        self.assertEqual(build_event_cluster_map(events), {1: 101, 2: 101, 3: 102})

    def test_empty_and_none_are_safe(self):
        self.assertEqual(build_event_cluster_map([]), {})
        self.assertEqual(build_event_cluster_map(None), {})

    def test_event_without_articles_key_is_skipped(self):
        self.assertEqual(build_event_cluster_map([{"id": 101}]), {})

    def test_event_without_id_is_skipped(self):
        self.assertEqual(build_event_cluster_map([{"articles": [{"id": 1}]}]), {})


class TestBuildTopicClusterMap(unittest.TestCase):
    def test_maps_article_id_to_topic_id_through_events(self):
        topics = [
            {"id": 5, "events": [{"id": 100, "article_ids": [1, 2]},
                                 {"id": 101, "article_ids": [3]}]},
            {"id": 6, "events": [{"id": 102, "article_ids": [4]}]},
        ]
        self.assertEqual(build_topic_cluster_map(topics), {1: 5, 2: 5, 3: 5, 4: 6})

    def test_empty_and_none_are_safe(self):
        self.assertEqual(build_topic_cluster_map([]), {})
        self.assertEqual(build_topic_cluster_map(None), {})


# ══════════════════════════════════════════════════════════════════════════
# 제약 판정
# ══════════════════════════════════════════════════════════════════════════


class TestEvaluatePairsMustLink(unittest.TestCase):
    CLUSTERS = {1: 101, 2: 101, 3: 102}

    def test_same_cluster_is_satisfied(self):
        r = evaluate_pairs([[1, 2]], self.CLUSTERS, expect_same=True)
        self.assertEqual((r["satisfied"], r["violated"], r["unknown"]), (1, 0, 0))
        self.assertEqual(r["rate"], 1.0)

    def test_different_cluster_is_violation(self):
        r = evaluate_pairs([[1, 3]], self.CLUSTERS, expect_same=True)
        self.assertEqual((r["satisfied"], r["violated"], r["unknown"]), (0, 1, 0))
        self.assertEqual(r["rate"], 0.0)
        self.assertEqual(r["violation_examples"][0]["clusters"], [101, 102])

    def test_missing_article_counts_as_unknown(self):
        r = evaluate_pairs([[1, 999]], self.CLUSTERS, expect_same=True)
        self.assertEqual((r["satisfied"], r["violated"], r["unknown"]), (0, 0, 1))
        self.assertIsNone(r["rate"])
        self.assertEqual(r["unknown_examples"][0]["missing"], [999])

    def test_unknown_is_excluded_from_rate_denominator(self):
        # 충족 1, 위반 1, 불가 2 → 충족률은 1/(1+1) = 50%
        pairs = [[1, 2], [1, 3], [1, 999], [998, 999]]
        r = evaluate_pairs(pairs, self.CLUSTERS, expect_same=True)
        self.assertEqual((r["satisfied"], r["violated"], r["unknown"]), (1, 1, 2))
        self.assertEqual(r["rate"], 0.5)
        self.assertEqual(r["total"], 4)


class TestEvaluatePairsCannotLink(unittest.TestCase):
    CLUSTERS = {1: 101, 2: 101, 3: 102}

    def test_different_cluster_is_satisfied(self):
        r = evaluate_pairs([[1, 3]], self.CLUSTERS, expect_same=False)
        self.assertEqual((r["satisfied"], r["violated"]), (1, 0))

    def test_same_cluster_is_violation(self):
        r = evaluate_pairs([[1, 2]], self.CLUSTERS, expect_same=False)
        self.assertEqual((r["satisfied"], r["violated"]), (0, 1))
        self.assertEqual(r["violation_examples"][0]["clusters"], [101, 101])


class TestEvaluatePairsEdgeCases(unittest.TestCase):
    def test_no_pairs_gives_none_rate(self):
        r = evaluate_pairs([], {1: 101}, expect_same=True)
        self.assertEqual((r["satisfied"], r["violated"], r["unknown"]), (0, 0, 0))
        self.assertIsNone(r["rate"])

    def test_none_pairs_is_safe(self):
        r = evaluate_pairs(None, {1: 101}, expect_same=True)
        self.assertEqual(r["total"], 0)

    def test_malformed_pair_is_skipped(self):
        r = evaluate_pairs([[1], []], {1: 101}, expect_same=True)
        self.assertEqual((r["satisfied"], r["violated"], r["unknown"]), (0, 0, 0))

    def test_examples_limit_caps_output(self):
        clusters = {i: i for i in range(20)}  # 전부 다른 클러스터 → must-link 전부 위반
        pairs = [[i, i + 1] for i in range(10)]
        r = evaluate_pairs(pairs, clusters, expect_same=True, examples_limit=3)
        self.assertEqual(r["violated"], 10)
        self.assertEqual(len(r["violation_examples"]), 3)


class TestSatisfactionRate(unittest.TestCase):
    def test_zero_denominator_is_none(self):
        self.assertIsNone(satisfaction_rate(0, 0))

    def test_normal_ratio(self):
        self.assertAlmostEqual(satisfaction_rate(42, 3), 42 / 45)

    def test_all_violated_is_zero(self):
        self.assertEqual(satisfaction_rate(0, 5), 0.0)


# ══════════════════════════════════════════════════════════════════════════
# 기준선 비교
# ══════════════════════════════════════════════════════════════════════════


def _results(em=None, ec=None, tm=None, tc=None) -> dict:
    """compare_with_baseline 입력용 최소 결과 dict."""
    rates = {"C-EM": em, "C-EC": ec, "C-TM": tm, "C-TC": tc}
    return {cid: {"rate": rate, "satisfied": 0, "violated": 0, "unknown": 0}
            for cid, rate in rates.items()}


class TestCompareWithBaseline(unittest.TestCase):
    BASELINE = {"rates": {"C-EM": 0.90, "C-EC": 1.00, "C-TM": 0.80, "C-TC": 0.70}}

    def test_identical_rates_pass_as_maintained(self):
        ok, rows = compare_with_baseline(_results(0.90, 1.00, 0.80, 0.70), self.BASELINE)
        self.assertTrue(ok)
        self.assertTrue(all(r["status"] == "유지" for r in rows))

    def test_improvement_passes(self):
        ok, rows = compare_with_baseline(_results(0.95, 1.00, 0.80, 0.70), self.BASELINE)
        self.assertTrue(ok)
        self.assertEqual(rows[0]["status"], "개선")

    def test_any_drop_fails(self):
        ok, rows = compare_with_baseline(_results(0.89, 1.00, 0.80, 0.70), self.BASELINE)
        self.assertFalse(ok)
        self.assertEqual(rows[0]["status"], "회귀")

    def test_tolerance_absorbs_small_drop(self):
        # 1%p 하락, 허용 2%p → 통과
        ok, _ = compare_with_baseline(
            _results(0.89, 1.00, 0.80, 0.70), self.BASELINE, tolerance=0.02
        )
        self.assertTrue(ok)

    def test_tolerance_does_not_absorb_large_drop(self):
        ok, _ = compare_with_baseline(
            _results(0.85, 1.00, 0.80, 0.70), self.BASELINE, tolerance=0.02
        )
        self.assertFalse(ok)

    def test_none_rate_is_not_comparable_and_does_not_fail(self):
        ok, rows = compare_with_baseline(_results(None, 1.00, 0.80, 0.70), self.BASELINE)
        self.assertTrue(ok)
        self.assertEqual(rows[0]["status"], "비교불가")

    def test_missing_baseline_key_is_not_comparable(self):
        ok, rows = compare_with_baseline(_results(0.90, 1.00, 0.80, 0.70), {"rates": {}})
        self.assertTrue(ok)
        self.assertTrue(all(r["status"] == "비교불가" for r in rows))


# ══════════════════════════════════════════════════════════════════════════
# 통합 — run_all_checks / 부가 기능
# ══════════════════════════════════════════════════════════════════════════


GOLD = {
    "schema_version": "constraints-v1",
    "snapshot_date": "2026-07-07T22:28:12",
    "reviewer": "정예은_오재민",
    "article_pool": [1, 2, 3, 4],
    "event_constraints": {"must_link": [[1, 2], [1, 3]], "cannot_link": [[1, 4]]},
    "topic_constraints": {"must_link": [[1, 3]], "cannot_link": []},
    "review_log": [
        {"unit": "event", "ref_id": 1, "label": "merge", "excluded_articles": [4], "memo": "합쳐야 할 듯"},
        {"unit": "event", "ref_id": 2, "label": "ok", "excluded_articles": [], "memo": ""},
    ],
}

# 기사 1,2 → 이벤트 101 / 기사 3 → 이벤트 102 / 기사 4 → 이벤트 103
# 이벤트 101,102 → 토픽 5 / 이벤트 103 → 토픽 6
SNAPSHOT = {
    "snapshot_date": "2026-07-20T10:00:00",
    "events": [
        {"id": 101, "articles": [{"id": 1}, {"id": 2}]},
        {"id": 102, "articles": [{"id": 3}]},
        {"id": 103, "articles": [{"id": 4}]},
    ],
    "topics": [
        {"id": 5, "events": [{"id": 101, "article_ids": [1, 2]},
                             {"id": 102, "article_ids": [3]}]},
        {"id": 6, "events": [{"id": 103, "article_ids": [4]}]},
    ],
}


class TestRunAllChecks(unittest.TestCase):
    def setUp(self):
        self.r = run_all_checks(GOLD, SNAPSHOT)

    def test_event_must_link(self):
        # [1,2] 같은 이벤트 → 충족 / [1,3] 다른 이벤트 → 위반
        self.assertEqual(self.r["C-EM"]["satisfied"], 1)
        self.assertEqual(self.r["C-EM"]["violated"], 1)
        self.assertEqual(self.r["C-EM"]["rate"], 0.5)

    def test_event_cannot_link(self):
        # [1,4] 다른 이벤트 → 충족
        self.assertEqual(self.r["C-EC"]["satisfied"], 1)
        self.assertEqual(self.r["C-EC"]["rate"], 1.0)

    def test_topic_must_link_uses_topic_map_not_event_map(self):
        # [1,3]은 이벤트는 다르지만 토픽은 같음(5) → 토픽 must-link는 충족
        self.assertEqual(self.r["C-TM"]["satisfied"], 1)
        self.assertEqual(self.r["C-TM"]["rate"], 1.0)

    def test_empty_constraint_group_gives_none_rate(self):
        self.assertIsNone(self.r["C-TC"]["rate"])
        self.assertEqual(self.r["C-TC"]["total"], 0)

    def test_meta_is_populated(self):
        meta = self.r["meta"]
        self.assertEqual(meta["schema_version"], "constraints-v1")
        self.assertEqual(meta["gold_reviewer"], "정예은_오재민")
        self.assertEqual(meta["events_in_snapshot"], 3)
        self.assertEqual(meta["topics_in_snapshot"], 2)
        self.assertEqual(meta["articles_in_events"], 4)

    def test_missing_snapshot_sections_are_safe(self):
        r = run_all_checks(GOLD, {})
        # 모든 기사가 결과에 없으므로 전부 "불가"
        self.assertEqual(r["C-EM"]["unknown"], 2)
        self.assertIsNone(r["C-EM"]["rate"])


class TestCollectReviewMemos(unittest.TestCase):
    def test_only_entries_with_memo_are_kept(self):
        memos = collect_review_memos(GOLD)
        self.assertEqual(len(memos), 1)
        self.assertEqual(memos[0]["ref_id"], 1)
        self.assertEqual(memos[0]["excluded_articles"], [4])

    def test_missing_review_log_is_safe(self):
        self.assertEqual(collect_review_memos({}), [])


class TestExtractBaseline(unittest.TestCase):
    def test_roundtrip_through_compare(self):
        results = run_all_checks(GOLD, SNAPSHOT)
        baseline = extract_baseline(results)
        self.assertEqual(set(baseline["rates"]), {"C-EM", "C-EC", "C-TM", "C-TC"})
        # 같은 결과를 자기 자신의 기준선과 비교하면 항상 통과해야 한다.
        ok, _ = compare_with_baseline(results, baseline)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
