"""토픽 배정 진단 로그(build_topic_decision_log / _resolve_action diag) 유닛테스트.

이벤트 쪽(build_decision_log)과 같은 목적이되, 토픽 경로에만 있는 결정 지점 둘을 더
구분해야 한다.

  dedup_merged  _dedup_guard가 create를 assign으로 **강등**했다. 가드레일(overridden)과
                방향이 반대라, must-link에는 도움이지만 cannot-link를 깰 수 있다.
  decided_by    SUBTOPIC_MODE=embedding이면 서브토픽은 LLM 없이 임베딩 최근접으로 정해진다.
                이때 후보 목록이 비는 것이 정상이며 거리·프롬프트 레버가 적용되지 않는다.

_resolve_action의 diag는 **선택적 out-파라미터**다. 기존 호출부·테스트 14곳이 2-튜플
언패킹에 의존하므로 반환 타입을 바꿀 수 없어서다. 하위호환(diag 미전달)도 함께 고정한다.

실행: python -m unittest tests.test_topic_decision_log
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from db.topic_causes import TopicCandidate  # noqa: E402
from topic_classifier.pipeline import (  # noqa: E402
    _resolve_action,
    build_topic_decision_log,
)
from topic_classifier.prompts import MAX_CANDIDATES  # noqa: E402


def _cand(topic_id: int, distance: float = 0.3, title: str | None = None) -> TopicCandidate:
    return TopicCandidate(
        topic_id=topic_id,
        category="정치",
        title=title if title is not None else f"토픽 {topic_id}",
        summary="요약",
        distance=distance,
        cause_texts=[],
    )


def _log(candidates, **kwargs) -> dict:
    base = dict(
        event_id=42,
        level="flat",
        cause="정부의 의대 정원 증원 발표",
        candidates=candidates,
        llm_decision={"action": "create", "topic_id": None, "score": 0.0, "reason": ""},
        overridden=False,
        dedup_merged=False,
        decided_by="llm",
        final_action="create",
        final_topic_id=None,
    )
    base.update(kwargs)
    return build_topic_decision_log(**base)


# ══════════════════════════════════════════════════════════════════════════
# 로그 조립
# ══════════════════════════════════════════════════════════════════════════


class TestCandidateSerialization(unittest.TestCase):
    def test_empty_candidates(self):
        log = _log([])
        self.assertEqual(log["candidate_count"], 0)
        self.assertEqual(log["candidates"], [])

    def test_topic_id_and_distance(self):
        log = _log([_cand(7, distance=0.123456)])
        self.assertEqual(log["candidates"][0]["topic_id"], 7)
        self.assertEqual(log["candidates"][0]["distance"], 0.1235)

    def test_zero_distance_is_kept(self):
        log = _log([_cand(7, distance=0.0)])
        self.assertEqual(log["candidates"][0]["distance"], 0.0)

    def test_long_title_truncated(self):
        log = _log([_cand(1, title="가" * 200)])
        self.assertEqual(len(log["candidates"][0]["title"]), 60)

    def test_empty_title_is_empty_string(self):
        log = _log([_cand(1, title="")])
        self.assertEqual(log["candidates"][0]["title"], "")


class TestTruncationBoundary(unittest.TestCase):
    """TOPIC_CANDIDATE_LIMIT > MAX_CANDIDATES(8) 설정에서 조용히 잘리는 구간을 고정한다."""

    def test_under_limit_all_shown(self):
        log = _log([_cand(i) for i in range(MAX_CANDIDATES - 1)])
        self.assertTrue(all(c["shown_to_llm"] for c in log["candidates"]))

    def test_exactly_at_limit_all_shown(self):
        log = _log([_cand(i) for i in range(MAX_CANDIDATES)])
        self.assertTrue(all(c["shown_to_llm"] for c in log["candidates"]))

    def test_over_limit_tail_hidden(self):
        log = _log([_cand(i) for i in range(12)])
        shown = [c for c in log["candidates"] if c["shown_to_llm"]]
        self.assertEqual(len(shown), MAX_CANDIDATES)
        self.assertEqual(len(log["candidates"]), 12)  # 잘린 것도 로그에는 남는다

    def test_candidate_count_reflects_retrieved_not_shown(self):
        log = _log([_cand(i) for i in range(12)])
        self.assertEqual(log["candidate_count"], 12)


class TestTopicSpecificFields(unittest.TestCase):
    def test_level_values(self):
        for level in ("flat", "parent", "subtopic"):
            self.assertEqual(_log([], level=level)["level"], level)

    def test_dedup_merged_is_recorded(self):
        log = _log([_cand(1)], dedup_merged=True, final_action="assign", final_topic_id=1)
        self.assertTrue(log["dedup_merged"])
        self.assertEqual(log["final_action"], "assign")

    def test_embedding_mode_is_recorded_with_no_candidates(self):
        # 임베딩 모드는 cause 후보 검색을 건너뛰므로 후보가 0인 것이 정상이다.
        log = _log([], decided_by="embedding", final_action="assign", final_topic_id=9)
        self.assertEqual(log["decided_by"], "embedding")
        self.assertEqual(log["candidate_count"], 0)
        self.assertEqual(log["final_topic_id"], 9)

    def test_core_fields_preserved(self):
        log = _log([])
        self.assertEqual(log["event_id"], 42)
        self.assertEqual(log["cause"], "정부의 의대 정원 증원 발표")

    def test_json_serializable_with_korean(self):
        import json

        dumped = json.dumps(_log([_cand(1)]), ensure_ascii=False)
        self.assertIn("정부의 의대 정원 증원 발표", dumped)


# ══════════════════════════════════════════════════════════════════════════
# _resolve_action 의 diag out-파라미터
# ══════════════════════════════════════════════════════════════════════════


def _mock_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.request_json.return_value = response
    return client


class TestResolveActionDiag(unittest.TestCase):
    def test_accepted_assign_records_llm_decision(self):
        diag: dict = {}
        client = _mock_client(
            {"action": "assign", "topic_id": 1, "score": 0.9, "reason": "동일 흐름"}
        )
        action, _ = _resolve_action(
            client, [_cand(1)], lambda: "p", fallback_title="폴백", diag=diag
        )
        self.assertEqual(action, "assign")
        self.assertEqual(diag["llm_decision"]["action"], "assign")
        self.assertEqual(diag["llm_decision"]["topic_id"], 1)
        self.assertEqual(diag["llm_decision"]["score"], 0.9)
        self.assertFalse(diag["overridden"])
        self.assertEqual(diag["decided_by"], "llm")

    def test_score_demotion_is_marked_overridden_and_keeps_original(self):
        # 가드레일이 decision을 통째로 교체해도 LLM 원본 점수·의도가 남아야 한다.
        diag: dict = {}
        client = _mock_client(
            {"action": "assign", "topic_id": 2, "score": 0.3, "reason": "관련"}
        )
        action, decision = _resolve_action(
            client, [_cand(2)], lambda: "p", fallback_title="폴백", diag=diag
        )
        self.assertEqual(action, "create")
        self.assertTrue(diag["overridden"])
        self.assertEqual(diag["llm_decision"]["action"], "assign")
        self.assertEqual(diag["llm_decision"]["topic_id"], 2)
        self.assertEqual(diag["llm_decision"]["score"], 0.3)

    def test_negative_reason_demotion_is_marked_overridden(self):
        diag: dict = {}
        client = _mock_client(
            {"action": "assign", "topic_id": 3, "score": 0.95, "reason": "사실상 무관한 사건"}
        )
        action, _ = _resolve_action(
            client, [_cand(3)], lambda: "p", fallback_title="폴백", diag=diag
        )
        self.assertEqual(action, "create")
        self.assertTrue(diag["overridden"])
        self.assertEqual(diag["llm_decision"]["score"], 0.95)

    def test_no_candidates_marks_decided_by_no_candidates(self):
        diag: dict = {}
        client = _mock_client({"action": "assign"})
        action, _ = _resolve_action(client, [], lambda: "p", fallback_title="폴백", diag=diag)
        client.request_json.assert_not_called()
        self.assertEqual(action, "create")
        self.assertEqual(diag["decided_by"], "no_candidates")
        self.assertFalse(diag["overridden"])

    def test_plain_create_is_not_overridden(self):
        diag: dict = {}
        client = _mock_client({"action": "create", "new_title": "새 토픽", "score": 0.0})
        action, _ = _resolve_action(
            client, [_cand(1)], lambda: "p", fallback_title="폴백", diag=diag
        )
        self.assertEqual(action, "create")
        self.assertFalse(diag["overridden"])
        self.assertEqual(diag["llm_decision"]["action"], "create")

    def test_backwards_compatible_without_diag(self):
        # 기존 호출부 14곳은 diag를 넘기지 않는다. 그 계약이 깨지면 안 된다.
        client = _mock_client(
            {"action": "assign", "topic_id": 1, "score": 0.9, "reason": "동일"}
        )
        result = _resolve_action(client, [_cand(1)], lambda: "p", fallback_title="폴백")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "assign")


if __name__ == "__main__":
    unittest.main()
