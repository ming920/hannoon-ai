"""evaluate.py 데이터 랭글링 헬퍼 함수 유닛테스트.

DB 연결 없이 순수 자료구조(dict)를 합성 입력으로 검증한다.
실행: python -m unittest tests.test_evaluate_helpers
"""
from __future__ import annotations

import os
import sys
import unittest

# eval/ 디렉터리를 경로에 추가한다(evaluate.py 임포트용).
# evaluate.py 자체가 모듈 로드 시 src/를 경로에 추가하므로
# collector.storage 임포트도 자동으로 처리된다.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from evaluate import (  # noqa: E402
    _resolve_event_gold_labels,
    build_event_level_maps,
    build_subtopic_level_maps,
    build_topic_level_maps,
    compute_hierarchy_consistency,
)


class TestResolveEventGoldLabels(unittest.TestCase):
    """_resolve_event_gold_labels: gold 집계 헬퍼."""

    def _make_gold(self, mapping: dict[str, str], field: str) -> dict[str, dict[str, str]]:
        """guid → {field: label} 형식의 최소 gold_labels를 반환한다."""
        return {guid: {field: lbl} for guid, lbl in mapping.items()}

    def test_consistent_labels_no_warning(self):
        """같은 이벤트의 기사들이 모두 같은 라벨이면 경고 없음."""
        gold_labels = self._make_gold(
            {"g1": "E1", "g2": "E1", "g3": "E2"}, "gold_event"
        )
        event_pred = {"g1": 10, "g2": 10, "g3": 20}
        result, warnings = _resolve_event_gold_labels(gold_labels, event_pred, "gold_event")
        self.assertEqual(result, {10: "E1", 20: "E2"})
        self.assertEqual(warnings, [])

    def test_inconsistent_labels_emits_warning(self):
        """같은 이벤트 내 라벨 불일치 시 경고를 수집한다."""
        gold_labels = {
            "g1": {"gold_subtopic": "S1"},
            "g2": {"gold_subtopic": "S2"},  # 같은 이벤트(ev 5)인데 다른 라벨
        }
        event_pred = {"g1": 5, "g2": 5}
        result, warnings = _resolve_event_gold_labels(gold_labels, event_pred, "gold_subtopic")
        self.assertEqual(len(warnings), 1)
        self.assertIn("5", warnings[0])  # 이벤트 ID 언급
        # 대표값은 둘 중 하나 (다수결/first-wins)
        self.assertIn(result[5], {"S1", "S2"})

    def test_majority_wins(self):
        """다수결로 대표값을 결정한다."""
        gold_labels = {
            "g1": {"gold_topic": "T1"},
            "g2": {"gold_topic": "T1"},
            "g3": {"gold_topic": "T2"},
        }
        event_pred = {"g1": 1, "g2": 1, "g3": 1}
        result, warnings = _resolve_event_gold_labels(gold_labels, event_pred, "gold_topic")
        # T1이 2번, T2가 1번 → 대표값은 T1
        self.assertEqual(result[1], "T1")
        self.assertEqual(len(warnings), 1)

    def test_guid_not_in_event_pred_skipped(self):
        """event_pred에 없는 guid는 결과에 포함되지 않는다."""
        gold_labels = {"g1": {"gold_event": "E1"}, "g99": {"gold_event": "E9"}}
        event_pred = {"g1": 10}  # g99 없음
        result, warnings = _resolve_event_gold_labels(gold_labels, event_pred, "gold_event")
        self.assertEqual(result, {10: "E1"})
        self.assertEqual(warnings, [])


class TestBuildEventLevelMaps(unittest.TestCase):
    """build_event_level_maps: 기사 단위 이벤트 pred/gold."""

    def setUp(self):
        self.gold_labels = {
            "g1": {"gold_event": "E1", "gold_subtopic": "S1", "gold_topic": "T1"},
            "g2": {"gold_event": "E1", "gold_subtopic": "S1", "gold_topic": "T1"},
            "g3": {"gold_event": "E2", "gold_subtopic": "S2", "gold_topic": "T2"},
        }

    def test_full_coverage(self):
        """event_pred에 모든 guid가 있으면 pred == gold 크기."""
        event_pred = {"g1": 100, "g2": 100, "g3": 200}
        pred, gold = build_event_level_maps(self.gold_labels, event_pred)
        self.assertEqual(len(pred), 3)
        self.assertEqual(len(gold), 3)
        self.assertEqual(pred["g1"], 100)
        self.assertEqual(gold["g1"], "E1")

    def test_partial_coverage(self):
        """event_pred에 없는 guid는 gold에는 있고 pred에는 없다."""
        event_pred = {"g1": 100}  # g2, g3 미배정
        pred, gold = build_event_level_maps(self.gold_labels, event_pred)
        self.assertEqual(len(gold), 3)
        self.assertEqual(len(pred), 1)
        self.assertIn("g1", pred)
        self.assertNotIn("g2", pred)
        self.assertNotIn("g3", pred)

    def test_empty_event_pred(self):
        """event_pred가 비어 있으면 pred는 비어 있고 gold는 채워져 있다."""
        pred, gold = build_event_level_maps(self.gold_labels, {})
        self.assertEqual(pred, {})
        self.assertEqual(len(gold), 3)


class TestBuildSubtopicLevelMaps(unittest.TestCase):
    """build_subtopic_level_maps: 이벤트 단위 서브토픽 pred/gold."""

    def setUp(self):
        self.gold_labels = {
            "g1": {"gold_event": "E1", "gold_subtopic": "S1", "gold_topic": "T1"},
            "g2": {"gold_event": "E1", "gold_subtopic": "S1", "gold_topic": "T1"},
            "g3": {"gold_event": "E2", "gold_subtopic": "S2", "gold_topic": "T2"},
        }
        self.event_pred = {"g1": 10, "g2": 10, "g3": 20}
        self.event_topic = {
            10: {"leaf_topic_id": 101, "root_topic_id": 1001},
            20: {"leaf_topic_id": 201, "root_topic_id": 2001},
        }

    def test_basic(self):
        pred, gold, warnings = build_subtopic_level_maps(
            self.gold_labels, self.event_pred, self.event_topic
        )
        self.assertEqual(gold, {10: "S1", 20: "S2"})
        self.assertEqual(pred, {10: 101, 20: 201})
        self.assertEqual(warnings, [])

    def test_event_not_in_event_topic_excluded_from_pred(self):
        """event_topic에 없는 이벤트는 gold에는 있지만 pred에는 없다."""
        pred, gold, warnings = build_subtopic_level_maps(
            self.gold_labels, self.event_pred, {}  # 빈 event_topic
        )
        self.assertEqual(len(gold), 2)
        self.assertEqual(pred, {})

    def test_inconsistent_gold_subtopic_warning(self):
        """같은 이벤트에 다른 gold_subtopic이 있으면 경고가 발생한다."""
        gold_labels = {
            "g1": {"gold_event": "E1", "gold_subtopic": "S1", "gold_topic": "T1"},
            "g2": {"gold_event": "E1", "gold_subtopic": "S9", "gold_topic": "T1"},  # 불일치
        }
        _pred, _gold, warnings = build_subtopic_level_maps(
            gold_labels, {"g1": 10, "g2": 10}, self.event_topic
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("gold_subtopic", warnings[0])


class TestBuildTopicLevelMaps(unittest.TestCase):
    """build_topic_level_maps: 이벤트 단위 토픽 pred/gold."""

    def setUp(self):
        self.gold_labels = {
            "g1": {"gold_event": "E1", "gold_subtopic": "S1", "gold_topic": "T1"},
            "g2": {"gold_event": "E1", "gold_subtopic": "S1", "gold_topic": "T1"},
            "g3": {"gold_event": "E2", "gold_subtopic": "S2", "gold_topic": "T2"},
        }
        self.event_pred = {"g1": 10, "g2": 10, "g3": 20}
        self.event_topic = {
            10: {"leaf_topic_id": 101, "root_topic_id": 1001},
            20: {"leaf_topic_id": 201, "root_topic_id": 2001},
        }

    def test_basic(self):
        pred, gold, warnings = build_topic_level_maps(
            self.gold_labels, self.event_pred, self.event_topic
        )
        self.assertEqual(gold, {10: "T1", 20: "T2"})
        self.assertEqual(pred, {10: 1001, 20: 2001})
        self.assertEqual(warnings, [])

    def test_returns_three_tuple(self):
        """반환값이 (pred, gold, warnings) 3-튜플이어야 한다."""
        result = build_topic_level_maps(
            self.gold_labels, self.event_pred, self.event_topic
        )
        self.assertEqual(len(result), 3)

    def test_inconsistent_gold_topic_warning(self):
        gold_labels = {
            "g1": {"gold_event": "E1", "gold_subtopic": "S1", "gold_topic": "T1"},
            "g2": {"gold_event": "E1", "gold_subtopic": "S1", "gold_topic": "T9"},  # 불일치
        }
        _pred, _gold, warnings = build_topic_level_maps(
            gold_labels, {"g1": 10, "g2": 10}, self.event_topic
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("gold_topic", warnings[0])


class TestComputeHierarchyConsistency(unittest.TestCase):
    """compute_hierarchy_consistency: 계층 정합성 계산."""

    def test_perfect_consistency(self):
        """같은 gold_topic의 이벤트들이 모두 같은 root를 공유하면 1.0."""
        event_gold_topic = {1: "T1", 2: "T1", 3: "T2"}
        event_topic = {
            1: {"leaf_topic_id": 10, "root_topic_id": 100},
            2: {"leaf_topic_id": 11, "root_topic_id": 100},  # 같은 root
            3: {"leaf_topic_id": 20, "root_topic_id": 200},
        }
        self.assertAlmostEqual(
            compute_hierarchy_consistency(event_gold_topic, event_topic), 1.0
        )

    def test_partial_inconsistency(self):
        """T1 이벤트들이 다른 root를 쓰면 해당 이벤트들이 비정합으로 카운트된다."""
        event_gold_topic = {1: "T1", 2: "T1", 3: "T2"}
        event_topic = {
            1: {"leaf_topic_id": 10, "root_topic_id": 100},
            2: {"leaf_topic_id": 11, "root_topic_id": 999},  # 다른 root → T1 전체 비정합
            3: {"leaf_topic_id": 20, "root_topic_id": 200},
        }
        # T1: roots={100,999} 불일치 → ev1, ev2 비정합
        # T2: roots={200} 일치   → ev3 정합
        # consistent=1, total=3 → 1/3
        result = compute_hierarchy_consistency(event_gold_topic, event_topic)
        self.assertAlmostEqual(result, 1 / 3)

    def test_empty_event_topic(self):
        """event_topic이 비어 있으면 0.0을 반환한다."""
        event_gold_topic = {1: "T1", 2: "T2"}
        self.assertEqual(compute_hierarchy_consistency(event_gold_topic, {}), 0.0)

    def test_empty_event_gold_topic(self):
        """event_gold_topic이 비어 있으면 0.0을 반환한다."""
        self.assertEqual(compute_hierarchy_consistency({}, {1: {"root_topic_id": 1}}), 0.0)

    def test_event_not_in_event_topic_excluded(self):
        """event_topic에 없는 이벤트는 total에 포함되지 않는다."""
        event_gold_topic = {1: "T1", 99: "T1"}  # ev99는 event_topic에 없음
        event_topic = {1: {"leaf_topic_id": 10, "root_topic_id": 100}}
        # ev1만 카운트, ev99는 제외 → consistent=1, total=1
        self.assertAlmostEqual(
            compute_hierarchy_consistency(event_gold_topic, event_topic), 1.0
        )


class TestCoveredOnlyMetrics(unittest.TestCase):
    """metrics.evaluate_level의 covered_only 지표.

    미배정 아이템이 유령 싱글턴(__missing_N__)으로 주입되어 singleton_rate·F1을
    왜곡하는 것을 분리 계산하는지 검증한다 (2026-07-03 critic C-1 회귀 방지).
    """

    def _evaluate(self, pred, gold):
        from metrics import evaluate_level
        return evaluate_level(pred, gold)

    def test_full_coverage_covered_equals_augmented(self):
        """전 아이템이 배정되면 covered-only와 기본 F1이 같아야 한다."""
        gold = {"a": "X", "b": "X", "c": "Y"}
        pred = {"a": 1, "b": 1, "c": 2}
        res = self._evaluate(pred, gold)
        self.assertAlmostEqual(res["covered_only"]["bcubed_f1"], res["bcubed_f1"])

    def test_missing_items_do_not_pollute_covered_metrics(self):
        """미배정 아이템은 covered-only 지표에서 제외되어야 한다.

        미배정 c가 다항목 gold 클러스터 X={a,b,c}의 일원이어야 augmented에
        recall 패널티가 생긴다(gold 싱글턴이면 유령 싱글턴이 완벽 매치됨).
        """
        gold = {"a": "X", "b": "X", "c": "X", "d": "Y"}
        pred = {"a": 1, "b": 1}  # c, d 미배정
        res = self._evaluate(pred, gold)
        # 배정분(a,b)은 완벽 군집 → covered-only F1 = 1.0
        self.assertAlmostEqual(res["covered_only"]["bcubed_f1"], 1.0)
        # augmented F1은 미배정 패널티로 1.0 미만
        self.assertLess(res["bcubed_f1"], 1.0)

    def test_covered_diagnostics_exclude_phantom_singletons(self):
        """covered-only 진단의 num_pred/singleton_rate에 유령 싱글턴이 없어야 한다."""
        gold = {"a": "X", "b": "X", "c": "Y", "d": "Z"}
        pred = {"a": 1, "b": 1}
        res = self._evaluate(pred, gold)
        self.assertEqual(res["covered_only"]["diagnostics"]["num_pred_clusters"], 1)
        self.assertEqual(res["covered_only"]["diagnostics"]["singleton_rate"], 0.0)
        # augmented 쪽은 유령 싱글턴 2개가 포함된다
        self.assertEqual(res["diagnostics"]["num_pred_clusters"], 3)

    def test_empty_pred_returns_zero_covered_metrics(self):
        """배정이 하나도 없으면 covered-only는 0으로 안전하게 반환한다."""
        gold = {"a": "X"}
        res = self._evaluate({}, gold)
        self.assertEqual(res["covered_only"]["bcubed_f1"], 0.0)


if __name__ == "__main__":
    unittest.main()
