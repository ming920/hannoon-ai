"""eval/review_worklist.py 유닛테스트.

이 도구가 만드는 것은 **정답 데이터**다. 여기서 쌍을 잘못 만들면 그 뒤의 모든 측정이
조용히 틀린 기준으로 채점된다 — 분류기 버그보다 고치기 어렵다. 그래서 쌍 생성 규칙과
모순 검출을 특히 촘촘히 본다.

실행: python -m unittest tests.test_review_worklist
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from review_worklist import (  # noqa: E402
    SCHEMA_VERSION,
    VERDICT_CORRECT,
    VERDICT_EXCLUDE,
    VERDICT_UNSURE,
    WEAK_DISTANCE,
    anchor_is_suspect,
    assign_distances,
    build_worklist,
    format_worklist_markdown,
    known_conflicts,
    merge_into_constraints,
    pairs_from_item,
    score_event,
    select_events,
)


def _log(*entries) -> str:
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)


def _event(eid: int, article_ids: list, title: str = "제목") -> dict:
    return {
        "id": eid, "title": title,
        "articles": [
            {"id": a, "title": f"기사 {a}", "publisher": "매체"} for a in article_ids
        ],
    }


class AssignDistanceTests(unittest.TestCase):
    """로그에서 '어느 거리로 붙었는가'를 복원한다."""

    def test_reads_distance_of_the_chosen_candidate(self):
        text = _log({
            "article_id": 1, "final_action": "assign",
            "llm_decision": {"event_id": 20},
            "candidates": [{"event_id": 10, "distance": 0.11},
                           {"event_id": 20, "distance": 0.44}],
        })
        # 가장 가까운 후보가 아니라 **실제로 선택된** 후보의 거리여야 한다.
        self.assertEqual(assign_distances(text), {1: 0.44})

    def test_created_articles_are_not_merges(self):
        text = _log({
            "article_id": 1, "final_action": "create",
            "llm_decision": {"event_id": None},
            "candidates": [{"event_id": 10, "distance": 0.9}],
        })
        self.assertEqual(assign_distances(text), {})

    def test_ignores_non_json_and_broken_lines(self):
        text = "[event] 처리 대상 기사: 5건\n{broken\n" + _log({
            "article_id": 7, "final_action": "assign",
            "llm_decision": {"event_id": 3},
            "candidates": [{"event_id": 3, "distance": 0.2}],
        })
        self.assertEqual(assign_distances(text), {7: 0.2})

    def test_missing_candidate_is_skipped(self):
        text = _log({
            "article_id": 1, "final_action": "assign",
            "llm_decision": {"event_id": 99},
            "candidates": [{"event_id": 10, "distance": 0.11}],
        })
        self.assertEqual(assign_distances(text), {})


class ScoreEventTests(unittest.TestCase):
    def test_counts_weak_merges(self):
        s = score_event(_event(1, [10, 11, 12]),
                        {10: 0.2, 11: WEAK_DISTANCE, 12: 0.45})
        self.assertEqual(s["weak_count"], 2)   # 경계값은 약한 병합에 포함
        self.assertEqual(s["max_distance"], 0.45)

    def test_single_article_event_is_not_eligible(self):
        # 병합이 없으므로 과병합 후보가 될 수 없다.
        self.assertFalse(score_event(_event(1, [10]), {})["eligible"])

    def test_anchor_article_without_distance_is_tolerated(self):
        s = score_event(_event(1, [10, 11]), {11: 0.5})
        self.assertEqual(s["weak_count"], 1)
        self.assertEqual(s["size"], 2)


class AnchorSuspectTests(unittest.TestCase):
    """다수가 멀면 다수가 아니라 기준 기사가 이질적일 수 있다.

    실제 E1005 가 그랬다 — 제목은 '용수 확보'인데 기사 24건 중 21건이 '광주 군공항 부지
    확정'이었다. 이 구분을 표시하지 않으면 대기열이 "다수를 빼라"고 유도하고, 그렇게 만든
    정답은 조용히 틀린 기준이 된다.
    """

    def test_majority_far_flags_the_anchor(self):
        self.assertTrue(anchor_is_suspect(weak_count=21, known_count=23))

    def test_minority_far_does_not_flag(self):
        self.assertFalse(anchor_is_suspect(weak_count=2, known_count=23))

    def test_tiny_event_never_flags(self):
        # 2건 중 2건이 멀다고 "다수"라고 말할 수 없다.
        self.assertFalse(anchor_is_suspect(weak_count=2, known_count=2))

    def test_boundary_needs_strict_majority(self):
        self.assertFalse(anchor_is_suspect(weak_count=6, known_count=10))
        self.assertTrue(anchor_is_suspect(weak_count=7, known_count=10))

    def test_markdown_shows_the_caution(self):
        snapshot = {"snapshot_date": "x", "events": [_event(1, [1, 2, 3, 4, 5])]}
        wl = build_worklist(snapshot, {2: 0.9, 3: 0.9, 4: 0.9, 5: 0.1},
                            targeted=5, control=0, seed=1)
        self.assertTrue(wl["items"][0]["anchor_suspect"])
        self.assertIn("기준 기사", format_worklist_markdown(wl))


class SelectEventsTests(unittest.TestCase):
    def setUp(self):
        self.events = [_event(i, [i * 10, i * 10 + 1]) for i in range(1, 9)]
        # 이벤트 1,2 만 약한 병합을 가진다
        self.dist = {10: 0.9, 11: 0.1, 20: 0.5, 21: 0.1}
        for i in range(3, 9):
            self.dist[i * 10] = 0.1
            self.dist[i * 10 + 1] = 0.1

    def test_only_events_with_weak_merges_are_targeted(self):
        picked = select_events(self.events, self.dist, targeted=5, control=0, seed=1)
        self.assertEqual([p["event_id"] for p in picked], [1, 2])

    def test_control_does_not_overlap_targeted(self):
        picked = select_events(self.events, self.dist, targeted=5, control=3, seed=1)
        targeted = {p["event_id"] for p in picked if p["selection"] == "targeted"}
        control = {p["event_id"] for p in picked if p["selection"] == "control"}
        self.assertEqual(targeted & control, set())
        self.assertEqual(len(control), 3)

    def test_same_seed_gives_same_worklist(self):
        a = select_events(self.events, self.dist, targeted=2, control=3, seed=7)
        b = select_events(self.events, self.dist, targeted=2, control=3, seed=7)
        self.assertEqual([x["event_id"] for x in a], [x["event_id"] for x in b])

    def test_control_capped_by_available_events(self):
        picked = select_events(self.events, self.dist, targeted=5, control=99, seed=1)
        self.assertEqual(len(picked), len(self.events))

    def test_targeted_limit_respected(self):
        dist = {a["id"]: 0.9 for e in self.events for a in e["articles"]}
        picked = select_events(self.events, dist, targeted=3, control=0, seed=1)
        self.assertEqual(len(picked), 3)


class PairGenerationTests(unittest.TestCase):
    """여기서 만든 쌍이 곧 정답이 된다 — 규칙이 어긋나면 측정 전체가 틀어진다."""

    def _item(self, verdict, not_belonging=()):
        return {
            "verdict": verdict,
            "not_belonging": list(not_belonging),
            "articles": [{"id": 1}, {"id": 2}, {"id": 3}],
        }

    def test_correct_makes_all_pairs_must_link(self):
        must, cannot = pairs_from_item(self._item(VERDICT_CORRECT))
        self.assertEqual(must, [[1, 2], [1, 3], [2, 3]])
        self.assertEqual(cannot, [])

    def test_exclude_separates_only_from_the_kept_set(self):
        must, cannot = pairs_from_item(self._item(VERDICT_EXCLUDE, [3]))
        self.assertEqual(cannot, [[1, 3], [2, 3]])
        self.assertEqual(must, [[1, 2]])

    def test_two_excluded_articles_are_not_linked_to_each_other(self):
        # 둘 다 이 사건이 아니라는 것뿐, 서로 같은 사건인지는 알 수 없다.
        must, cannot = pairs_from_item(self._item(VERDICT_EXCLUDE, [2, 3]))
        self.assertEqual(cannot, [[1, 2], [1, 3]])
        self.assertEqual(must, [])
        self.assertNotIn([2, 3], cannot)

    def test_unsure_makes_nothing(self):
        self.assertEqual(pairs_from_item(self._item(VERDICT_UNSURE)), ([], []))

    def test_unfilled_verdict_makes_nothing(self):
        self.assertEqual(pairs_from_item(self._item(None)), ([], []))

    def test_unknown_article_id_is_ignored(self):
        must, cannot = pairs_from_item(self._item(VERDICT_EXCLUDE, [999]))
        self.assertEqual(cannot, [])
        self.assertEqual(must, [[1, 2], [1, 3], [2, 3]])

    def test_excluding_everything_yields_nothing(self):
        must, cannot = pairs_from_item(self._item(VERDICT_EXCLUDE, [1, 2, 3]))
        self.assertEqual((must, cannot), ([], []))


class MergeTests(unittest.TestCase):
    BASE = {
        "schema_version": "constraints-v1",
        "event_constraints": {"must_link": [[1, 2]], "cannot_link": [[5, 6]]},
    }

    def _worklist(self, *items) -> dict:
        return {"schema_version": SCHEMA_VERSION, "snapshot_date": "2026-07-27",
                "items": list(items)}

    def test_adds_cannot_link_pairs(self):
        wl = self._worklist({
            "verdict": VERDICT_EXCLUDE, "not_belonging": [30],
            "articles": [{"id": 10}, {"id": 20}, {"id": 30}],
        })
        merged = merge_into_constraints(self.BASE, wl)
        cannot = merged["event_constraints"]["cannot_link"]
        self.assertIn([10, 30], cannot)
        self.assertIn([20, 30], cannot)
        self.assertIn([5, 6], cannot)  # 기존 것 보존

    def test_original_is_not_mutated(self):
        wl = self._worklist({
            "verdict": VERDICT_CORRECT, "articles": [{"id": 10}, {"id": 20}]})
        merge_into_constraints(self.BASE, wl)
        self.assertEqual(self.BASE["event_constraints"]["must_link"], [[1, 2]])

    def test_duplicate_pairs_are_not_double_counted(self):
        wl = self._worklist({
            "verdict": VERDICT_CORRECT, "articles": [{"id": 1}, {"id": 2}]})
        merged = merge_into_constraints(self.BASE, wl)
        self.assertEqual(merged["event_constraints"]["must_link"], [[1, 2]])
        self.assertEqual(merged["_review_rounds"][-1]["added_must_link"], 0)

    def test_contradiction_is_rejected(self):
        # 기존 정답이 "떨어져야 한다"고 한 쌍을 이번 검수가 "같다"고 하면 채점이 무의미해진다.
        wl = self._worklist({
            "verdict": VERDICT_CORRECT, "articles": [{"id": 5}, {"id": 6}]})
        with self.assertRaises(ValueError) as ctx:
            merge_into_constraints(self.BASE, wl)
        self.assertIn("모순", str(ctx.exception))

    def test_conflict_without_event_id_is_readable(self):
        # event_id 가 없는 옛 대기열이라도 "ENone" 같은 출력이 나오면 안 된다.
        wl = self._worklist({
            "verdict": VERDICT_CORRECT, "articles": [{"id": 5}, {"id": 6}]})
        with self.assertRaises(ValueError) as ctx:
            merge_into_constraints(self.BASE, wl)
        self.assertNotIn("ENone", str(ctx.exception))

    def test_round_summary_is_recorded(self):
        wl = self._worklist(
            {"verdict": VERDICT_CORRECT, "articles": [{"id": 10}, {"id": 20}]},
            {"verdict": VERDICT_UNSURE, "articles": [{"id": 30}, {"id": 40}]},
            {"verdict": None, "articles": [{"id": 50}, {"id": 60}]},
        )
        info = merge_into_constraints(self.BASE, wl)["_review_rounds"][-1]
        self.assertEqual(info["verdicts"][VERDICT_CORRECT], 1)
        self.assertEqual(info["verdicts"][VERDICT_UNSURE], 1)
        self.assertEqual(info["verdicts"]["(미기입)"], 1)

    def test_rounds_accumulate(self):
        wl1 = self._worklist({
            "verdict": VERDICT_CORRECT, "articles": [{"id": 10}, {"id": 20}]})
        once = merge_into_constraints(self.BASE, wl1)
        twice = merge_into_constraints(once, wl1)
        self.assertEqual(len(twice["_review_rounds"]), 2)


class KnownConflictTests(unittest.TestCase):
    """이전 검수와 충돌할 항목을 **검수 시점에** 알려야 한다.

    merge 단계에서야 거부당하면 검수자는 어느 판단을 되돌려야 하는지 알기 어렵다.
    실제로 모의 검수에서 45쌍이 한꺼번에 터졌다.
    """

    PRIOR = {"event_constraints": {"cannot_link": [[4, 100], [4, 200], [7, 8]]}}

    def test_finds_prior_separations_inside_the_event(self):
        self.assertEqual(
            known_conflicts([4, 100, 300], {(4, 100), (4, 200), (7, 8)}),
            [[4, 100]],
        )

    def test_pair_split_across_events_is_not_a_conflict(self):
        self.assertEqual(known_conflicts([4, 300], {(4, 100)}), [])

    def test_build_marks_conflicted_items(self):
        snapshot = {"snapshot_date": "x", "events": [_event(1, [4, 100, 300])]}
        wl = build_worklist(snapshot, {100: 0.9, 300: 0.9}, targeted=5, control=0,
                            seed=1, prior_constraints=self.PRIOR)
        self.assertEqual(wl["items"][0]["known_conflicts"], [[4, 100]])

    def test_markdown_warns_about_conflicts(self):
        snapshot = {"snapshot_date": "x", "events": [_event(1, [4, 100, 300])]}
        wl = build_worklist(snapshot, {100: 0.9, 300: 0.9}, targeted=5, control=0,
                            seed=1, prior_constraints=self.PRIOR)
        text = format_worklist_markdown(wl)
        self.assertIn("이전 검수", text)
        self.assertIn("4↔100", text)

    def test_no_prior_constraints_means_no_conflicts(self):
        snapshot = {"snapshot_date": "x", "events": [_event(1, [4, 100])]}
        wl = build_worklist(snapshot, {100: 0.9}, targeted=5, control=0, seed=1)
        self.assertEqual(wl["items"][0]["known_conflicts"], [])

    def test_merge_error_names_the_event(self):
        base = {"event_constraints": {"must_link": [], "cannot_link": [[4, 100]]}}
        wl = {"schema_version": SCHEMA_VERSION, "items": [{
            "event_id": 77, "verdict": VERDICT_CORRECT,
            "articles": [{"id": 4}, {"id": 100}],
        }]}
        with self.assertRaises(ValueError) as ctx:
            merge_into_constraints(base, wl)
        self.assertIn("E77", str(ctx.exception))


class WorklistShapeTests(unittest.TestCase):
    def setUp(self):
        snapshot = {
            "snapshot_date": "2026-07-27T00:00:00",
            "events": [_event(1, [10, 11], "흡입 이벤트"), _event(2, [20, 21], "정상")],
        }
        self.wl = build_worklist(snapshot, {10: 0.9, 11: 0.1, 20: 0.1, 21: 0.1},
                                 targeted=5, control=1, seed=3)

    def test_verdict_fields_start_empty(self):
        for item in self.wl["items"]:
            self.assertIsNone(item["verdict"])
            self.assertEqual(item["not_belonging"], [])

    def test_articles_carry_what_a_reviewer_needs(self):
        art = self.wl["items"][0]["articles"][0]
        for key in ("id", "title", "publisher", "distance"):
            self.assertIn(key, art)

    def test_markdown_renders_both_sections(self):
        text = format_worklist_markdown(self.wl)
        self.assertIn("의심 항목", text)
        self.assertIn("무작위 대조군", text)
        self.assertIn("E1", text)

    def test_markdown_marks_anchor_article(self):
        # 거리가 없는 기사는 이 이벤트를 만든 기사다 — 빈칸으로 두면 오해를 산다.
        self.assertIn("생성", format_worklist_markdown(self.wl))


if __name__ == "__main__":
    unittest.main()
