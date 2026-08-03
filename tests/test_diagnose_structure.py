"""eval/diagnose_structure.py 유닛테스트.

진단은 전부 순수 함수라 DB 없이 검증한다(스냅샷/정답은 dict 리터럴로 구성).

특히 `move_tradeoff`는 "배정 로직을 고쳐도 소용없다"는 판정을 내리는 함수라, 이득이
나는 구조와 손실이 나는 구조를 각각 고정해 둔다. 이 판정이 뒤집히면 앞으로의 개선 방향
전체가 뒤집히기 때문이다.

실행: python -m unittest tests.test_diagnose_structure
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from diagnose_structure import (  # noqa: E402
    build_gold_clusters,
    crosstab,
    diagnose,
    find_contradictions,
    ideal_assignment,
    move_tradeoff,
    score,
    usable_pairs,
)


# ══════════════════════════════════════════════════════════════════════════
# 정답 사건 구성 (must 의 전이성)
# ══════════════════════════════════════════════════════════════════════════


class TestBuildGoldClusters(unittest.TestCase):
    def test_must_link_is_transitive(self):
        """a~b, b~c 가 must 면 a 와 c 도 같은 사건이다."""
        clusters = build_gold_clusters([(1, 2), (2, 3)])
        self.assertEqual(clusters[1], clusters[3])

    def test_separate_chains_stay_separate(self):
        clusters = build_gold_clusters([(1, 2), (10, 11)])
        self.assertNotEqual(clusters[1], clusters[10])

    def test_representative_is_min_article_id(self):
        self.assertEqual(build_gold_clusters([(7, 3), (3, 5)]), {3: 3, 5: 3, 7: 3})

    def test_no_pairs_gives_empty_map(self):
        self.assertEqual(build_gold_clusters([]), {})


class TestFindContradictions(unittest.TestCase):
    def test_cannot_inside_a_must_chain_is_a_contradiction(self):
        """a~b~c 가 must 인데 a-c 가 cannot 이면 어떤 군집화로도 만족 못 한다."""
        gold = build_gold_clusters([(1, 2), (2, 3)])
        self.assertEqual(find_contradictions(gold, [(1, 3)]), [(1, 3)])

    def test_cannot_across_chains_is_fine(self):
        gold = build_gold_clusters([(1, 2), (10, 11)])
        self.assertEqual(find_contradictions(gold, [(1, 10)]), [])

    def test_article_without_gold_is_ignored(self):
        gold = build_gold_clusters([(1, 2)])
        self.assertEqual(find_contradictions(gold, [(1, 99)]), [])


# ══════════════════════════════════════════════════════════════════════════
# 이동 트레이드오프 — 재배정으로 고칠 수 있는지 판정하는 핵심 로직
# ══════════════════════════════════════════════════════════════════════════


class TestMoveTradeoff(unittest.TestCase):
    def test_isolated_article_moving_to_its_group_is_a_net_gain(self):
        """기사 1만 홀로 떨어져 있고 이웃 2·3이 한 군집에 있으면 옮기는 게 이득이다."""
        cluster_map = {1: 500, 2: 501, 3: 501}
        result = move_tradeoff(cluster_map, [(1, 2), (1, 3), (2, 3)])
        self.assertEqual(result["violated_pairs"], 2)
        self.assertEqual(result["pairs_gained"], 4)  # 위반 2쌍 각각에서 이웃 2명을 얻는다
        self.assertEqual(result["pairs_lost"], 0)  # 원래 군집에 must 이웃이 없다
        self.assertGreater(result["net"], 0)

    def test_moving_out_of_a_group_you_already_satisfy_is_a_net_loss(self):
        """기사 1이 이미 2·3과 같이 있는데 4 때문에 옮기면 2·3과 갈라진다.

        실제 데이터에서 관측된 구조다 — 위반 하나를 고치려다 충족 중인 여러 쌍을 깬다.
        """
        cluster_map = {1: 500, 2: 500, 3: 500, 4: 501}
        result = move_tradeoff(cluster_map, [(1, 2), (1, 3), (1, 4)])
        self.assertEqual(result["violated_pairs"], 1)
        self.assertEqual(result["pairs_gained"], 1)  # 고치려던 4와의 쌍 하나만 얻는다
        self.assertEqual(result["pairs_lost"], 2)  # 대신 2와 3을 잃는다
        self.assertEqual(result["net"], -1)

    def test_no_violation_means_nothing_to_move(self):
        result = move_tradeoff({1: 500, 2: 500}, [(1, 2)])
        self.assertEqual(result, {"violated_pairs": 0, "pairs_gained": 0,
                                  "pairs_lost": 0, "net": 0})


# ══════════════════════════════════════════════════════════════════════════
# 가로지름 / 상한 / 점수
# ══════════════════════════════════════════════════════════════════════════


class TestCrosstab(unittest.TestCase):
    def test_counts_over_merge_and_over_split(self):
        # 사건 A(기사 1,2)는 군집 500·501로 흩어졌고, 군집 501은 사건 A와 B를 함께 담았다
        cluster_map = {1: 500, 2: 501, 3: 501}
        gold = {1: 1, 2: 1, 3: 3}
        result = crosstab(cluster_map, gold)
        self.assertEqual(result["over_merged"], 1)
        self.assertEqual(result["over_split"], 1)
        self.assertEqual(result["gold_events"], 2)

    def test_perfect_match_has_no_crossing(self):
        result = crosstab({1: 500, 2: 500, 3: 501}, {1: 1, 2: 1, 3: 3})
        self.assertEqual(result["over_merged"], 0)
        self.assertEqual(result["over_split"], 0)


class TestIdealAssignment(unittest.TestCase):
    def test_gold_articles_are_regrouped_and_others_keep_current(self):
        assignment = ideal_assignment({1: 500, 2: 501, 9: 502}, {1: 1, 2: 1})
        self.assertEqual(assignment[1], assignment[2])  # 정답대로 합쳐진다
        self.assertEqual(assignment[9], 502)  # 정답이 없으면 현재 배정 유지

    def test_gold_keys_never_collide_with_cluster_ids(self):
        """정답 사건 id 가 마침 현재 이벤트 id 와 같아도 섞이면 안 된다."""
        assignment = ideal_assignment({1: 500, 2: 500}, {1: 500})
        self.assertNotEqual(assignment[1], assignment[2])


class TestScore(unittest.TestCase):
    def test_counts_must_and_cannot_separately(self):
        result = score({1: 500, 2: 500, 3: 501}, [(1, 2), (1, 3)], [(1, 3), (1, 2)])
        self.assertEqual(result["must_satisfied"], 1)
        self.assertEqual(result["cannot_satisfied"], 1)
        self.assertAlmostEqual(result["must_rate"], 0.5)


class TestUsablePairs(unittest.TestCase):
    def test_pair_with_missing_article_is_dropped(self):
        """충족률 분모와 같은 기준 — 결과에 없는 기사가 낀 쌍은 뺀다."""
        self.assertEqual(usable_pairs([[1, 2], [1, 99]], {1: 500, 2: 500}), [(1, 2)])

    def test_malformed_pairs_are_skipped(self):
        self.assertEqual(usable_pairs([[1], [], None], {1: 500}), [])


# ══════════════════════════════════════════════════════════════════════════
# 통합
# ══════════════════════════════════════════════════════════════════════════


class TestDiagnose(unittest.TestCase):
    def _gold(self):
        return {
            "schema_version": "constraints-v1",
            "event_constraints": {"must_link": [[1, 2], [1, 3]], "cannot_link": [[1, 4]]},
        }

    def test_reports_unreachable_reassignment(self):
        """기사 1이 2·3과 이미 붙어 있고 4만 떨어져 있으면 재배정으로는 못 고친다."""
        snapshot = {"events": [{"id": 500, "articles": [{"id": 1}, {"id": 2}, {"id": 3}]},
                               {"id": 501, "articles": [{"id": 4}]}]}
        result = diagnose(self._gold(), snapshot)
        self.assertEqual(result["current"]["must_satisfied"], 2)
        self.assertEqual(result["current"]["cannot_satisfied"], 1)
        self.assertEqual(result["contradictions"], [])
        self.assertEqual(result["move_tradeoff"]["violated_pairs"], 0)

    def test_ideal_reaches_full_must_satisfaction(self):
        snapshot = {"events": [{"id": 500, "articles": [{"id": 1}]},
                               {"id": 501, "articles": [{"id": 2}, {"id": 3}]},
                               {"id": 502, "articles": [{"id": 4}]}]}
        result = diagnose(self._gold(), snapshot)
        self.assertEqual(result["current"]["must_satisfied"], 0)
        self.assertEqual(result["ideal"]["must_satisfied"], 2)
        self.assertGreater(result["move_tradeoff"]["net"], 0)  # 이 경우엔 옮기면 이득

    def test_topic_unit_reads_topic_constraints(self):
        gold = {"schema_version": "constraints-v1",
                "topic_constraints": {"must_link": [[1, 2]], "cannot_link": []}}
        snapshot = {"topics": [{"id": 7, "events": [{"id": 100, "article_ids": [1, 2]}]}]}
        result = diagnose(gold, snapshot, unit="topic")
        self.assertEqual(result["meta"]["unit"], "topic")
        self.assertEqual(result["current"]["must_satisfied"], 1)


if __name__ == "__main__":
    unittest.main()
