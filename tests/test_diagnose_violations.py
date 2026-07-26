"""eval/diagnose_violations.py 유닛테스트.

위반 원인 A/B/C/D 판정이 핵심이다. 오진하면 엉뚱한 곳을 고치게 되므로 네 유형의
경계를 전부 고정한다. 특히:

  - A와 B의 경계: 후보 목록에 "없음" vs "있지만 LLM에 안 보임"
  - C와 D의 경계: LLM이 스스로 거절 vs 가드레일이 LLM의 assign을 뒤집음
  - 처리 순서: 나중에 처리된 기사가 "붙을 기회를 가진 쪽"이므로 그쪽 로그를 봐야 한다

실행: python -m unittest tests.test_diagnose_violations
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from diagnose_violations import (  # noqa: E402
    CAUSE_A,
    CAUSE_B,
    CAUSE_C,
    CAUSE_D,
    CAUSE_NO_LOG,
    T_A_NOT_RETRIEVED,
    T_B_TRUNCATED,
    T_C_LLM_REJECTED,
    T_D_GUARDRAIL,
    T_EVENT_PROPAGATED,
    T_NO_LOG,
    T_SUBTOPIC_SPLIT,
    diagnose_cannot_link,
    diagnose_must_link,
    diagnose_topic_must_link,
    find_candidate,
    find_guardrail_saves,
    parse_decision_logs,
    parse_topic_logs,
    run_diagnosis,
    satisfied_pairs,
    simulate_score_threshold,
    topic_scope_of,
)


def _log(article_id, *, candidates=(), llm=None, overridden=False, final="create"):
    return {
        "article_id": article_id,
        "main_event": f"사건-{article_id}",
        "result": f"{final} event",
        "candidate_count": len(candidates),
        "candidates": list(candidates),
        "llm_decision": llm or {"action": "create", "event_id": None, "score": 0.0, "reason": ""},
        "overridden": overridden,
        "final_action": final,
    }


def _cand(event_id, distance=0.3, shown=True):
    return {"event_id": event_id, "distance": distance, "shown_to_llm": shown, "title": "제목"}


# ══════════════════════════════════════════════════════════════════════════
# 로그 파싱
# ══════════════════════════════════════════════════════════════════════════


class TestParseDecisionLogs(unittest.TestCase):
    def test_skips_human_readable_lines(self):
        text = (
            "[event] 처리 대상 기사: 5건\n"
            '{"article_id": 1, "candidates": []}\n'
            "[event] article 2 failed: boom\n"
            '{"article_id": 3, "candidates": []}\n'
        )
        logs, order = parse_decision_logs(text)
        self.assertEqual(set(logs), {1, 3})
        self.assertEqual(order, {1: 0, 3: 1})

    def test_skips_malformed_json(self):
        logs, _ = parse_decision_logs('{"article_id": 1}\n{broken\n')
        self.assertEqual(set(logs), {1})

    def test_skips_objects_without_article_id(self):
        logs, _ = parse_decision_logs('{"foo": 1}\n{"article_id": 2}\n')
        self.assertEqual(set(logs), {2})

    def test_order_reflects_processing_sequence(self):
        logs, order = parse_decision_logs(
            '{"article_id": 50}\n{"article_id": 10}\n{"article_id": 30}\n'
        )
        self.assertEqual(order, {50: 0, 10: 1, 30: 2})

    def test_duplicate_article_keeps_first(self):
        logs, order = parse_decision_logs(
            '{"article_id": 7, "result": "first"}\n{"article_id": 7, "result": "second"}\n'
        )
        self.assertEqual(logs[7]["result"], "first")
        self.assertEqual(order, {7: 0})

    def test_empty_input(self):
        logs, order = parse_decision_logs("")
        self.assertEqual((logs, order), ({}, {}))


class TestFindCandidate(unittest.TestCase):
    def test_found(self):
        log = _log(1, candidates=[_cand(100), _cand(101)])
        self.assertEqual(find_candidate(log, 101)["event_id"], 101)

    def test_not_found_returns_none(self):
        self.assertIsNone(find_candidate(_log(1, candidates=[_cand(100)]), 999))

    def test_missing_candidates_key_is_safe(self):
        self.assertIsNone(find_candidate({"article_id": 1}, 100))


# ══════════════════════════════════════════════════════════════════════════
# must-link 위반 원인 판정
# ══════════════════════════════════════════════════════════════════════════


class TestDiagnoseMustLink(unittest.TestCase):
    # 기사 1은 이벤트 100, 기사 2는 이벤트 200 → must-link 위반
    CLUSTERS = {1: 100, 2: 200}
    ORDER = {1: 0, 2: 1}  # 기사 1이 먼저 처리됨 → 기사 2가 붙을 기회를 가졌다

    def _diagnose(self, log2):
        logs = {1: _log(1), 2: log2}
        return diagnose_must_link([1, 2], self.CLUSTERS, logs, self.ORDER)

    def test_a_when_target_event_not_in_candidates(self):
        d = self._diagnose(_log(2, candidates=[_cand(999)]))
        self.assertEqual(d["cause"], CAUSE_A)
        self.assertEqual(d["target_event"], 100)
        self.assertEqual(d["landed_event"], 200)

    def test_a_when_no_candidates_at_all(self):
        d = self._diagnose(_log(2, candidates=[]))
        self.assertEqual(d["cause"], CAUSE_A)
        self.assertEqual(d["candidate_count"], 0)

    def test_b_when_target_retrieved_but_truncated(self):
        d = self._diagnose(_log(2, candidates=[_cand(999), _cand(100, shown=False)]))
        self.assertEqual(d["cause"], CAUSE_B)
        self.assertEqual(d["rank"], 1)

    def test_c_when_llm_saw_it_and_chose_create(self):
        d = self._diagnose(
            _log(2, candidates=[_cand(100)],
                 llm={"action": "create", "event_id": None, "score": 0.0, "reason": "다른 사건"})
        )
        self.assertEqual(d["cause"], CAUSE_C)
        self.assertEqual(d["llm_action"], "create")

    def test_c_when_llm_picked_a_different_candidate(self):
        d = self._diagnose(
            _log(2, candidates=[_cand(100), _cand(555)],
                 llm={"action": "assign", "event_id": 555, "score": 0.9, "reason": "저쪽"},
                 final="assign")
        )
        self.assertEqual(d["cause"], CAUSE_C)
        self.assertEqual(d["llm_chose_event"], 555)

    def test_d_when_guardrail_overrode_assign_to_target(self):
        d = self._diagnose(
            _log(2, candidates=[_cand(100)],
                 llm={"action": "assign", "event_id": 100, "score": 0.72, "reason": "비슷함"},
                 overridden=True)
        )
        self.assertEqual(d["cause"], CAUSE_D)
        self.assertEqual(d["llm_score"], 0.72)

    def test_d_requires_override_to_target_not_other_event(self):
        # 가드레일이 뒤집긴 했지만 LLM이 노린 건 다른 이벤트였다 → D가 아니라 C
        d = self._diagnose(
            _log(2, candidates=[_cand(100), _cand(555)],
                 llm={"action": "assign", "event_id": 555, "score": 0.72, "reason": "저쪽"},
                 overridden=True)
        )
        self.assertEqual(d["cause"], CAUSE_C)

    def test_no_log_when_article_missing(self):
        d = diagnose_must_link([1, 99], self.CLUSTERS, {1: _log(1)}, {1: 0})
        self.assertEqual(d["cause"], CAUSE_NO_LOG)
        self.assertEqual(d["missing_from_log"], [99])

    def test_later_article_is_the_one_inspected(self):
        # 기사 2를 먼저 처리했다면, 붙을 기회를 가진 쪽은 기사 1이다.
        logs = {1: _log(1, candidates=[_cand(200, shown=False)]), 2: _log(2, candidates=[])}
        d = diagnose_must_link([1, 2], self.CLUSTERS, logs, {2: 0, 1: 1})
        self.assertEqual(d["later_article"], 1)
        self.assertEqual(d["earlier_article"], 2)
        self.assertEqual(d["target_event"], 200)
        self.assertEqual(d["cause"], CAUSE_B)

    def test_distance_is_carried_through(self):
        d = self._diagnose(_log(2, candidates=[_cand(100, distance=0.4321)]))
        self.assertEqual(d["distance"], 0.4321)


class TestDiagnoseCannotLink(unittest.TestCase):
    CLUSTERS = {1: 100, 2: 100}  # 같은 이벤트 → cannot-link 위반

    def test_records_score_and_distance(self):
        logs = {
            1: _log(1),
            2: _log(2, candidates=[_cand(100, distance=0.12)],
                    llm={"action": "assign", "event_id": 100, "score": 0.93, "reason": "동일"},
                    final="assign"),
        }
        d = diagnose_cannot_link([1, 2], self.CLUSTERS, logs, {1: 0, 2: 1})
        self.assertEqual(d["merged_into_event"], 100)
        self.assertEqual(d["llm_score"], 0.93)
        self.assertEqual(d["distance"], 0.12)

    def test_missing_log_is_flagged(self):
        d = diagnose_cannot_link([1, 2], self.CLUSTERS, {1: _log(1)}, {1: 0})
        self.assertEqual(d["cause"], CAUSE_NO_LOG)


# ══════════════════════════════════════════════════════════════════════════
# 충족 쌍 추출 / 가드레일 기여 / 임계값 시뮬레이션
# ══════════════════════════════════════════════════════════════════════════


class TestSatisfiedPairs(unittest.TestCase):
    CLUSTERS = {1: 100, 2: 100, 3: 200}

    def test_must_link_satisfied(self):
        self.assertEqual(satisfied_pairs([[1, 2], [1, 3]], self.CLUSTERS, expect_same=True), [[1, 2]])

    def test_cannot_link_satisfied(self):
        self.assertEqual(satisfied_pairs([[1, 2], [1, 3]], self.CLUSTERS, expect_same=False), [[1, 3]])

    def test_unknown_article_excluded(self):
        self.assertEqual(satisfied_pairs([[1, 999]], self.CLUSTERS, expect_same=False), [])

    def test_malformed_pair_skipped(self):
        self.assertEqual(satisfied_pairs([[1], []], self.CLUSTERS, expect_same=True), [])


class TestFindGuardrailSaves(unittest.TestCase):
    CLUSTERS = {1: 100, 3: 200}

    def test_counts_only_overridden_toward_the_partner_event(self):
        logs = {
            1: _log(1),
            3: _log(3, candidates=[_cand(100)],
                    llm={"action": "assign", "event_id": 100, "score": 0.75, "reason": "비슷"},
                    overridden=True),
        }
        saves = find_guardrail_saves([[1, 3]], self.CLUSTERS, logs, {1: 0, 3: 1})
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0]["llm_score"], 0.75)

    def test_ignores_when_not_overridden(self):
        logs = {1: _log(1), 3: _log(3, candidates=[_cand(100)])}
        self.assertEqual(find_guardrail_saves([[1, 3]], self.CLUSTERS, logs, {1: 0, 3: 1}), [])


class TestSimulateScoreThreshold(unittest.TestCase):
    GUARDRAIL = [{"llm_score": 0.78}, {"llm_score": 0.72}, {"llm_score": 0.55}]
    SAVES = [{"llm_score": 0.76}, {"llm_score": 0.60}]

    def test_lowering_threshold_rescues_more(self):
        rows = simulate_score_threshold(self.GUARDRAIL, self.SAVES, 0.80, [0.80, 0.75, 0.70, 0.50])
        by_t = {r["threshold"]: r for r in rows}
        self.assertEqual(by_t[0.80]["must_link_rescued"], 0)
        self.assertEqual(by_t[0.75]["must_link_rescued"], 1)   # 0.78
        self.assertEqual(by_t[0.70]["must_link_rescued"], 2)   # 0.78, 0.72
        self.assertEqual(by_t[0.50]["must_link_rescued"], 3)

    def test_lowering_threshold_also_breaks_cannot_link(self):
        rows = simulate_score_threshold(self.GUARDRAIL, self.SAVES, 0.80, [0.80, 0.75, 0.50])
        by_t = {r["threshold"]: r for r in rows}
        self.assertEqual(by_t[0.80]["cannot_link_broken"], 0)
        self.assertEqual(by_t[0.75]["cannot_link_broken"], 1)  # 0.76
        self.assertEqual(by_t[0.50]["cannot_link_broken"], 2)

    def test_net_is_rescued_minus_broken(self):
        rows = simulate_score_threshold(self.GUARDRAIL, self.SAVES, 0.80, [0.75])
        self.assertEqual(rows[0]["net"], 0)  # +1 구제, -1 파손

    def test_current_threshold_is_marked(self):
        rows = simulate_score_threshold(self.GUARDRAIL, self.SAVES, 0.80, [0.80, 0.75])
        self.assertTrue(rows[0]["is_current"])
        self.assertFalse(rows[1]["is_current"])

    def test_missing_score_is_ignored_not_crashing(self):
        rows = simulate_score_threshold([{"llm_score": None}, {}], [], 0.80, [0.50])
        self.assertEqual(rows[0]["must_link_rescued"], 0)


# ══════════════════════════════════════════════════════════════════════════
# 통합
# ══════════════════════════════════════════════════════════════════════════


GOLD = {
    "schema_version": "constraints-v1",
    "reviewer": "정예은_오재민",
    "event_constraints": {
        "must_link": [[1, 2], [1, 3], [1, 4], [1, 5]],
        "cannot_link": [[1, 6]],
    },
    "topic_constraints": {"must_link": [], "cannot_link": []},
}

# 기사 1 → 이벤트 100 / 2,3,4,5 는 제각기 다른 이벤트 → must-link 4건 전부 위반
SNAPSHOT = {
    "events": [
        {"id": 100, "articles": [{"id": 1}, {"id": 6}]},   # 6이 붙어 cannot-link 위반
        {"id": 201, "articles": [{"id": 2}]},
        {"id": 202, "articles": [{"id": 3}]},
        {"id": 203, "articles": [{"id": 4}]},
        {"id": 204, "articles": [{"id": 5}]},
    ],
    "topics": [],
}

LOG_TEXT = "\n".join(
    [
        "[event] 처리 대상 기사: 6건",
        # 기사 1 — 최초, 이벤트 100 생성
        '{"article_id": 1, "candidate_count": 0, "candidates": [], '
        '"llm_decision": {"action": "create", "event_id": null, "score": 0.0, "reason": ""}, '
        '"overridden": false, "final_action": "create", "result": "created event 100"}',
        # 기사 2 — 후보에 100이 없음 → A
        '{"article_id": 2, "candidate_count": 1, '
        '"candidates": [{"event_id": 999, "distance": 0.2, "shown_to_llm": true, "title": "x"}], '
        '"llm_decision": {"action": "create", "event_id": null, "score": 0.0, "reason": ""}, '
        '"overridden": false, "final_action": "create", "result": "created event 201"}',
        # 기사 3 — 100이 있으나 절삭 → B
        '{"article_id": 3, "candidate_count": 2, '
        '"candidates": [{"event_id": 999, "distance": 0.2, "shown_to_llm": true, "title": "x"}, '
        '{"event_id": 100, "distance": 0.5, "shown_to_llm": false, "title": "y"}], '
        '"llm_decision": {"action": "create", "event_id": null, "score": 0.0, "reason": ""}, '
        '"overridden": false, "final_action": "create", "result": "created event 202"}',
        # 기사 4 — 100을 보고도 create → C
        '{"article_id": 4, "candidate_count": 1, '
        '"candidates": [{"event_id": 100, "distance": 0.3, "shown_to_llm": true, "title": "y"}], '
        '"llm_decision": {"action": "create", "event_id": null, "score": 0.0, "reason": "다른 사건"}, '
        '"overridden": false, "final_action": "create", "result": "created event 203"}',
        # 기사 5 — LLM은 100에 assign, 가드레일이 뒤집음 → D
        '{"article_id": 5, "candidate_count": 1, '
        '"candidates": [{"event_id": 100, "distance": 0.25, "shown_to_llm": true, "title": "y"}], '
        '"llm_decision": {"action": "assign", "event_id": 100, "score": 0.72, "reason": "비슷"}, '
        '"overridden": true, "final_action": "create", "result": "created event 204"}',
        # 기사 6 — 100에 배정됨 → cannot-link 위반
        '{"article_id": 6, "candidate_count": 1, '
        '"candidates": [{"event_id": 100, "distance": 0.1, "shown_to_llm": true, "title": "y"}], '
        '"llm_decision": {"action": "assign", "event_id": 100, "score": 0.95, "reason": "동일"}, '
        '"overridden": false, "final_action": "assign", "result": "assigned to event 100"}',
    ]
)


class TestRunDiagnosis(unittest.TestCase):
    def setUp(self):
        logs, order = parse_decision_logs(LOG_TEXT)
        self.r = run_diagnosis(GOLD, SNAPSHOT, logs, order)

    def test_all_four_causes_are_detected_once_each(self):
        self.assertEqual(
            self.r["must_link_causes"],
            {CAUSE_A: 1, CAUSE_B: 1, CAUSE_C: 1, CAUSE_D: 1, CAUSE_NO_LOG: 0},
        )

    def test_meta_counts(self):
        meta = self.r["meta"]
        self.assertEqual(meta["must_link_total"], 4)
        self.assertEqual(meta["must_link_violated"], 4)
        self.assertEqual(meta["cannot_link_total"], 1)
        self.assertEqual(meta["cannot_link_violated"], 1)
        self.assertEqual(meta["articles_in_log"], 6)

    def test_truncation_rank_is_reported(self):
        self.assertEqual(self.r["truncation_ranks"], [1])

    def test_cannot_link_violation_carries_score(self):
        self.assertEqual(len(self.r["cannot_link_diagnoses"]), 1)
        self.assertEqual(self.r["cannot_link_diagnoses"][0]["llm_score"], 0.95)

    def test_no_guardrail_saves_when_all_cannot_link_violated(self):
        # cannot-link 1쌍이 이미 깨졌으므로 "가드레일이 지켜준 쌍"은 없다.
        self.assertEqual(self.r["guardrail_saves"], [])

    def test_diagnoses_cover_every_violation(self):
        self.assertEqual(len(self.r["must_link_diagnoses"]), 4)


# ══════════════════════════════════════════════════════════════════════════
# 토픽 제약 진단
# ══════════════════════════════════════════════════════════════════════════


def _tlog(event_id, level, *, candidates=(), llm=None, overridden=False,
          decided_by="llm", final_action="create", final_topic_id=None):
    return {
        "event_id": event_id,
        "level": level,
        "cause": f"원인-{event_id}",
        "candidate_count": len(candidates),
        "candidates": list(candidates),
        "llm_decision": llm or {"action": "create", "topic_id": None, "score": 0.0, "reason": ""},
        "overridden": overridden,
        "dedup_merged": False,
        "decided_by": decided_by,
        "final_action": final_action,
        "final_topic_id": final_topic_id,
    }


def _tcand(topic_id, distance=0.3, shown=True):
    return {"topic_id": topic_id, "distance": distance, "shown_to_llm": shown, "title": "t"}


class TestParseTopicLogs(unittest.TestCase):
    def test_keys_by_event_and_level(self):
        text = "\n".join([
            "[topic] 배치 시작: 2건",
            json.dumps({"event_id": 5, "level": "parent"}),
            json.dumps({"event_id": 5, "level": "subtopic"}),
        ])
        logs, order = parse_topic_logs(text)
        self.assertEqual(set(logs), {(5, "parent"), (5, "subtopic")})
        self.assertEqual(order, {5: 0})

    def test_order_counts_events_not_lines(self):
        text = "\n".join([
            json.dumps({"event_id": 9, "level": "parent"}),
            json.dumps({"event_id": 9, "level": "subtopic"}),
            json.dumps({"event_id": 3, "level": "flat"}),
        ])
        _, order = parse_topic_logs(text)
        self.assertEqual(order, {9: 0, 3: 1})

    def test_skips_event_classifier_logs(self):
        # 이벤트 로그는 level이 없으므로 걸러져야 한다.
        logs, _ = parse_topic_logs(json.dumps({"article_id": 1, "main_event": "x"}))
        self.assertEqual(logs, {})

    def test_skips_malformed(self):
        logs, _ = parse_topic_logs('{broken\n{"event_id": 1, "level": "flat"}')
        self.assertEqual(set(logs), {(1, "flat")})


class TestTopicScopeOf(unittest.TestCase):
    def test_flat_mode_parent_equals_leaf(self):
        logs = {(1, "flat"): _tlog(1, "flat", final_topic_id=70)}
        scope = topic_scope_of(logs, 1)
        self.assertEqual(scope["parent"], 70)
        self.assertEqual(scope["leaf"], 70)
        self.assertEqual(scope["mode"], "flat")

    def test_hierarchical_mode(self):
        logs = {
            (1, "parent"): _tlog(1, "parent", final_topic_id=70),
            (1, "subtopic"): _tlog(1, "subtopic", final_topic_id=71),
        }
        scope = topic_scope_of(logs, 1)
        self.assertEqual((scope["parent"], scope["leaf"]), (70, 71))
        self.assertEqual(scope["mode"], "hierarchical")

    def test_missing_event_returns_empty(self):
        self.assertEqual(topic_scope_of({}, 1), {})


class TestDiagnoseTopicMustLink(unittest.TestCase):
    # 기사 1 → 이벤트 100, 기사 2 → 이벤트 200 (다른 이벤트라야 토픽 위반이 성립)
    EV = {1: 100, 2: 200}
    ORDER = {100: 0, 200: 1}  # 이벤트 100이 먼저 처리됨

    def test_event_propagation_takes_priority(self):
        # 이벤트 must-link도 위반이면 토픽 레버로는 못 고친다.
        d = diagnose_topic_must_link([1, 2], self.EV, {}, self.ORDER, {frozenset((1, 2))})
        self.assertEqual(d["cause"], T_EVENT_PROPAGATED)

    def test_same_event_is_snapshot_inconsistency(self):
        d = diagnose_topic_must_link([1, 2], {1: 100, 2: 100}, {}, self.ORDER, set())
        self.assertEqual(d["cause"], T_NO_LOG)
        self.assertIn("스냅샷", d["note"])

    def test_missing_article_from_snapshot(self):
        d = diagnose_topic_must_link([1, 9], self.EV, {}, self.ORDER, set())
        self.assertEqual(d["cause"], T_NO_LOG)

    def test_subtopic_split_when_parent_matches(self):
        logs = {
            (100, "parent"): _tlog(100, "parent", final_topic_id=70),
            (100, "subtopic"): _tlog(100, "subtopic", final_topic_id=71),
            (200, "parent"): _tlog(200, "parent", final_topic_id=70),
            (200, "subtopic"): _tlog(200, "subtopic", final_topic_id=72,
                                     decided_by="embedding"),
        }
        d = diagnose_topic_must_link([1, 2], self.EV, logs, self.ORDER, set())
        self.assertEqual(d["cause"], T_SUBTOPIC_SPLIT)
        self.assertEqual(d["parent_topic"], 70)
        self.assertEqual(d["leaves"], [71, 72])
        self.assertEqual(d["decided_by"], "embedding")

    def _parent_case(self, later_parent_log):
        logs = {
            (100, "flat"): _tlog(100, "flat", final_topic_id=70),
            (200, "flat"): later_parent_log,
        }
        return diagnose_topic_must_link([1, 2], self.EV, logs, self.ORDER, set())

    def test_a_when_target_topic_not_retrieved(self):
        d = self._parent_case(
            _tlog(200, "flat", candidates=[_tcand(999)], final_topic_id=80)
        )
        self.assertEqual(d["cause"], T_A_NOT_RETRIEVED)
        self.assertEqual(d["target_topic"], 70)
        self.assertEqual(d["landed_topic"], 80)

    def test_b_when_truncated(self):
        d = self._parent_case(
            _tlog(200, "flat",
                  candidates=[_tcand(999), _tcand(70, shown=False)], final_topic_id=80)
        )
        self.assertEqual(d["cause"], T_B_TRUNCATED)
        self.assertEqual(d["rank"], 1)

    def test_c_when_llm_rejected(self):
        d = self._parent_case(
            _tlog(200, "flat", candidates=[_tcand(70)],
                  llm={"action": "create", "topic_id": None, "score": 0.0, "reason": "다른 이슈"},
                  final_topic_id=80)
        )
        self.assertEqual(d["cause"], T_C_LLM_REJECTED)

    def test_d_when_guardrail_overrode(self):
        d = self._parent_case(
            _tlog(200, "flat", candidates=[_tcand(70)],
                  llm={"action": "assign", "topic_id": 70, "score": 0.6, "reason": "비슷"},
                  overridden=True, final_topic_id=80)
        )
        self.assertEqual(d["cause"], T_D_GUARDRAIL)
        self.assertEqual(d["llm_score"], 0.6)

    def test_no_log_when_event_absent_from_topic_log(self):
        d = diagnose_topic_must_link([1, 2], self.EV, {}, {100: 0}, set())
        self.assertEqual(d["cause"], T_NO_LOG)
        self.assertEqual(d["missing_events_from_log"], [200])


class TestRunDiagnosisWithTopics(unittest.TestCase):
    GOLD = {
        "schema_version": "constraints-v1",
        "reviewer": "정예은_오재민",
        "event_constraints": {"must_link": [[1, 2]], "cannot_link": []},
        # 기사 1·3은 같은 토픽이어야 하는데 갈렸다 (이벤트 제약과 무관한 순수 토픽 문제)
        "topic_constraints": {"must_link": [[1, 3]], "cannot_link": []},
    }
    SNAPSHOT = {
        "events": [
            {"id": 100, "articles": [{"id": 1}]},
            {"id": 200, "articles": [{"id": 2}]},
            {"id": 300, "articles": [{"id": 3}]},
        ],
        "topics": [
            {"id": 70, "events": [{"id": 100, "article_ids": [1]},
                                  {"id": 200, "article_ids": [2]}]},
            {"id": 80, "events": [{"id": 300, "article_ids": [3]}]},
        ],
    }

    def setUp(self):
        topic_text = "\n".join([
            json.dumps(_tlog(100, "flat", final_topic_id=70)),
            json.dumps(_tlog(200, "flat", final_topic_id=70)),
            json.dumps(_tlog(300, "flat", candidates=[_tcand(999)], final_topic_id=80)),
        ])
        tlogs, torder = parse_topic_logs(topic_text)
        self.r = run_diagnosis(self.GOLD, self.SNAPSHOT, {}, {}, tlogs, torder)

    def test_topic_meta_counts(self):
        meta = self.r["meta"]
        self.assertEqual(meta["topic_must_link_total"], 1)
        self.assertEqual(meta["topic_must_link_violated"], 1)
        self.assertEqual(meta["topic_cannot_link_total"], 0)
        self.assertTrue(meta["topic_logs_present"])
        self.assertEqual(meta["events_in_topic_log"], 3)

    def test_topic_cause_is_diagnosed(self):
        self.assertEqual(self.r["topic_must_link_causes"][T_A_NOT_RETRIEVED], 1)

    def test_without_topic_logs_diagnosis_is_skipped(self):
        r = run_diagnosis(self.GOLD, self.SNAPSHOT, {}, {})
        self.assertFalse(r["meta"]["topic_logs_present"])
        self.assertEqual(r["topic_must_link_diagnoses"], [])
        # 위반 집계 자체는 로그 없이도 나온다.
        self.assertEqual(r["meta"]["topic_must_link_violated"], 1)


if __name__ == "__main__":
    unittest.main()
