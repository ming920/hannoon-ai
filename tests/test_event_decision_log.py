"""이벤트 배정 진단 로그(build_decision_log) 단위테스트.

must-link 위반의 원인은 셋인데 처방이 서로 다르다.

  1. 후보 목록에 아예 없음            → 거리 임계값이 걸러냈다
  2. 후보에 있으나 shown_to_llm=False → TOP_K가 MAX_EVENT_CANDIDATES보다 커서 절삭됐다
  3. LLM에 보였는데 create를 골랐다    → 배정 프롬프트 또는 점수 임계값 문제

셋을 구분하려면 로그가 "검색된 후보 전체"와 "그중 LLM이 실제로 본 것"을 따로
남겨야 한다. 특히 2번은 EVENT_CANDIDATE_LIMIT(기본 12) > MAX_EVENT_CANDIDATES(8)
설정에서 조용히 발생하므로 경계를 테스트로 고정한다.

실행: python -m unittest tests.test_event_decision_log
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from event_classifier.pipeline import build_decision_log  # noqa: E402
from event_classifier.prompts import MAX_EVENT_CANDIDATES  # noqa: E402


def _candidates(n: int) -> list[dict]:
    """거리 오름차순 후보 n개. pgvector 검색 결과 형태를 흉내낸다."""
    return [
        {"id": 100 + i, "distance": 0.1 + i * 0.01, "title": f"후보이벤트-{i:03d}"}
        for i in range(n)
    ]


def _log(candidates: list[dict], **kwargs) -> dict:
    base = dict(
        article_id=2611,
        main_event="전공의 집단 사직서 제출",
        result="created event 1002",
        candidates=candidates,
        llm_decision={"action": "create", "event_id": None, "score": 0.0, "reason": "새로운 사건"},
        overridden=False,
        final_action="create",
    )
    base.update(kwargs)
    return build_decision_log(**base)


class TestNoCandidates(unittest.TestCase):
    """원인 1 — 거리 임계값이 후보를 전부 걸러낸 경우."""

    def test_empty_candidates(self):
        log = _log([])
        self.assertEqual(log["candidate_count"], 0)
        self.assertEqual(log["candidates"], [])
        self.assertEqual(log["final_action"], "create")


class TestTruncationBoundary(unittest.TestCase):
    """원인 2 — 검색은 됐지만 프롬프트에서 절삭된 경우."""

    def test_under_limit_all_shown(self):
        log = _log(_candidates(MAX_EVENT_CANDIDATES - 1))
        self.assertTrue(all(c["shown_to_llm"] for c in log["candidates"]))

    def test_exactly_at_limit_all_shown(self):
        log = _log(_candidates(MAX_EVENT_CANDIDATES))
        self.assertEqual(len(log["candidates"]), MAX_EVENT_CANDIDATES)
        self.assertTrue(all(c["shown_to_llm"] for c in log["candidates"]))

    def test_over_limit_tail_is_hidden(self):
        # EVENT_CANDIDATE_LIMIT 기본값 12 > MAX_EVENT_CANDIDATES 8 인 실제 설정
        log = _log(_candidates(12))
        shown = [c for c in log["candidates"] if c["shown_to_llm"]]
        hidden = [c for c in log["candidates"] if not c["shown_to_llm"]]
        self.assertEqual(len(shown), MAX_EVENT_CANDIDATES)
        self.assertEqual(len(hidden), 12 - MAX_EVENT_CANDIDATES)

    def test_all_retrieved_candidates_are_logged_not_just_shown_ones(self):
        # 절삭된 후보도 로그에는 남아야 원인 2를 진단할 수 있다.
        log = _log(_candidates(12))
        self.assertEqual(log["candidate_count"], 12)
        self.assertEqual(len(log["candidates"]), 12)

    def test_hidden_candidates_keep_distance_order(self):
        log = _log(_candidates(12))
        distances = [c["distance"] for c in log["candidates"]]
        self.assertEqual(distances, sorted(distances))


class TestCandidateFields(unittest.TestCase):
    def test_event_id_is_normalized_to_int(self):
        log = _log([{"id": "101", "distance": 0.3, "title": "제목"}])
        self.assertEqual(log["candidates"][0]["event_id"], 101)
        self.assertIsInstance(log["candidates"][0]["event_id"], int)

    def test_distance_is_rounded_to_four_places(self):
        log = _log([{"id": 1, "distance": 0.123456789, "title": "제목"}])
        self.assertEqual(log["candidates"][0]["distance"], 0.1235)

    def test_missing_distance_becomes_zero(self):
        log = _log([{"id": 1, "title": "제목"}])
        self.assertEqual(log["candidates"][0]["distance"], 0.0)

    def test_long_title_is_truncated(self):
        log = _log([{"id": 1, "distance": 0.1, "title": "가" * 200}])
        self.assertEqual(len(log["candidates"][0]["title"]), 60)

    def test_missing_title_becomes_empty_string(self):
        log = _log([{"id": 1, "distance": 0.1}])
        self.assertEqual(log["candidates"][0]["title"], "")


class TestDecisionFields(unittest.TestCase):
    """원인 3 — LLM이 후보를 보고도 배정하지 않은 경우를 구분할 수 있어야 한다."""

    def test_llm_assign_overridden_to_create_is_visible(self):
        # 가드레일이 decision을 덮어써도 LLM 원본 판단이 로그에 남아야 한다.
        log = _log(
            _candidates(3),
            llm_decision={
                "action": "assign",
                "event_id": 101,
                "score": 0.72,
                "reason": "같은 사건으로 보임",
            },
            overridden=True,
            final_action="create",
            result="created event 1002",
        )
        self.assertEqual(log["llm_decision"]["action"], "assign")
        self.assertEqual(log["llm_decision"]["event_id"], 101)
        self.assertEqual(log["llm_decision"]["score"], 0.72)
        self.assertTrue(log["overridden"])
        self.assertEqual(log["final_action"], "create")

    def test_plain_assign_is_not_marked_overridden(self):
        log = _log(
            _candidates(3),
            llm_decision={
                "action": "assign",
                "event_id": 101,
                "score": 0.95,
                "reason": "동일 사건",
            },
            overridden=False,
            final_action="assign",
            result="assigned to event 101",
        )
        self.assertFalse(log["overridden"])
        self.assertEqual(log["final_action"], "assign")

    def test_core_fields_are_preserved(self):
        log = _log([])
        self.assertEqual(log["article_id"], 2611)
        self.assertEqual(log["main_event"], "전공의 집단 사직서 제출")
        self.assertEqual(log["result"], "created event 1002")


class TestSerializable(unittest.TestCase):
    def test_log_is_json_serializable(self):
        import json

        log = _log(_candidates(12))
        # ensure_ascii=False 로 한국어가 그대로 남아야 사람이 읽을 수 있다.
        dumped = json.dumps(log, ensure_ascii=False)
        self.assertIn("전공의 집단 사직서 제출", dumped)
        self.assertEqual(json.loads(dumped)["candidate_count"], 12)


# ══════════════════════════════════════════════════════════════════════════
# 후보 부족 시 거리 넓히기 (2단계 검색)
# ══════════════════════════════════════════════════════════════════════════


class TestShouldWidenSearch(unittest.TestCase):
    """거리 임계값을 통째로 완화하면 과병합이 늘지만, 후보가 안 잡힌 경우만 넓히면
    정상 케이스는 그대로다. 그 '부족할 때만'이 지켜지는지 고정한다."""

    def _widen(self, count, *, fallback, base=0.40, minimum=2) -> bool:
        from unittest.mock import patch

        from event_classifier import pipeline

        with patch.object(pipeline, "FALLBACK_DISTANCE_THRESHOLD", fallback), \
             patch.object(pipeline, "DISTANCE_THRESHOLD", base), \
             patch.object(pipeline, "FALLBACK_MIN_CANDIDATES", minimum):
            return pipeline.should_widen_search(count)

    def test_disabled_by_default_value_zero(self):
        """기본값 0 은 비활성 — 후보가 하나도 없어도 재검색하지 않는다."""
        self.assertFalse(self._widen(0, fallback=0.0))

    def test_fallback_not_greater_than_base_is_disabled(self):
        """넓히는 값이 기본 임계값 이하면 넓히는 의미가 없다(오히려 좁아진다)."""
        self.assertFalse(self._widen(0, fallback=0.40))
        self.assertFalse(self._widen(0, fallback=0.30))

    def test_widens_when_candidates_are_scarce(self):
        self.assertTrue(self._widen(0, fallback=0.50))
        self.assertTrue(self._widen(1, fallback=0.50))

    def test_does_not_widen_when_enough_candidates(self):
        """정상적으로 후보가 잡히면 건드리지 않는다 — 과병합을 늘리지 않는 핵심."""
        self.assertFalse(self._widen(2, fallback=0.50))
        self.assertFalse(self._widen(12, fallback=0.50))

    def test_minimum_is_exclusive_boundary(self):
        self.assertTrue(self._widen(2, fallback=0.50, minimum=3))
        self.assertFalse(self._widen(3, fallback=0.50, minimum=3))


class TestWidenedFlagInLog(unittest.TestCase):
    def test_defaults_to_false(self):
        self.assertFalse(_log([])["widened_search"])

    def test_records_when_widened(self):
        log = build_decision_log(
            article_id=1, main_event="사건", result="assigned",
            candidates=[], llm_decision={}, overridden=False,
            final_action="create", widened=True,
        )
        self.assertTrue(log["widened_search"])


if __name__ == "__main__":
    unittest.main()
