"""
서브토픽(계층 토픽 분류) 기능 결정론적 단위/통합 테스트.

커버 대상:
- db.topics.create_topic — parent_topic_id 파라미터 지원
- db.topic_causes.search_candidates — roots_only / parent_topic_id 필터
- topic_classifier.pipeline._reason_rejects_assignment — 부정 마커 감지
- topic_classifier.pipeline._load_decision_score — 점수 안전 파싱
- topic_classifier.pipeline._resolve_action — 후보 없을 때 LLM 생략 및 가드레일
- topic_classifier.pipeline._select_candidate — 후보 검증
- topic_classifier.pipeline._assign_hierarchical — 2단계 계층 배정 전체 경로
- topic_classifier.pipeline.run — subtopics_enabled 분기 및 처리 건수
- topic_classifier.prompts.build_subtopic_assignment_prompt — 프롬프트 내용 검증

외부 의존성(LLMClient, embed_*, topic_causes.search_candidates) 모두 unittest.mock으로 격리.
DB 레이어는 pgvector SQL 때문에 SQLite로 실행 불가하므로 FakeConn/FakePipelineConn을 사용.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, call, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from db.topic_causes import TopicCandidate, search_candidates
from db.topics import create_topic, update_topic
from db.events import Event
from topic_classifier.pipeline import (
    _reason_rejects_assignment,
    _load_decision_score,
    _resolve_action,
    _select_candidate,
    _assign_hierarchical,
    run,
)
from topic_classifier.prompts import build_subtopic_assignment_prompt


# ── 헬퍼: DB 가짜 객체 ────────────────────────────────────────────────────────

class FakeConn:
    """topics/topic_causes 단위 테스트용 최소 가짜 연결 (query_one/execute 추적)."""

    def __init__(self):
        self.query_one_calls: list[tuple] = []
        self.execute_calls: list[tuple] = []
        self._query_one_counter = 0
        self._id_seq = iter(range(1, 100))

    def query_one(self, sql, params=None):
        self.query_one_calls.append((sql, params))
        return {"id": next(self._id_seq)}

    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))

    def query(self, sql, params=None):
        return []

    @contextmanager
    def transaction(self):
        yield self


class FakePipelineConn:
    """pipeline 함수 통합 테스트용 가짜 연결 (query는 빈 목록 반환)."""

    def __init__(self):
        self.execute_calls: list[tuple] = []

    def query(self, sql, params=None):
        # _load_topic_summary_events / _load_parent_summary_events 호출 대응
        return []

    def query_one(self, sql, params=None):
        return None

    def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))

    @contextmanager
    def transaction(self):
        yield self


def _make_candidate(topic_id: int, title: str = "토픽제목", summary: str = "토픽요약",
                    distance: float = 0.2, causes: list[str] | None = None) -> TopicCandidate:
    """테스트용 TopicCandidate 헬퍼."""
    return TopicCandidate(
        topic_id=topic_id,
        category="사회",
        title=title,
        summary=summary,
        distance=distance,
        cause_texts=causes or ["원인1"],
    )


def _make_event(event_id: int = 5, title: str = "이벤트제목",
                summary: str = "이벤트요약") -> Event:
    """테스트용 Event 헬퍼."""
    return Event(
        id=event_id,
        category="사회",
        title=title,
        summary=summary,
        embedding_text="이벤트 본문",
    )


# ── 1. db.topics.create_topic — parent_topic_id 지원 ─────────────────────────

class TopicCreateWithParentTests(unittest.TestCase):
    """create_topic 이 parent_topic_id 를 SQL 파라미터에 올바르게 전달하는지 검증."""

    def test_root_topic_passes_none_as_fourth_param(self):
        """parent_topic_id를 생략하면 SQL 4번째 인자가 None(최상위 토픽)이어야 한다."""
        conn = FakeConn()
        create_topic(conn, "사회", "테스트 토픽 제목", "테스트 요약 텍스트")
        _, params = conn.query_one_calls[0]
        # params: (category, title, summary, parent_topic_id)
        self.assertIsNone(params[3])

    def test_subtopic_passes_parent_id_as_fourth_param(self):
        """parent_topic_id=42 를 넘기면 SQL 4번째 인자로 42가 전달되어야 한다."""
        conn = FakeConn()
        create_topic(conn, "사회", "서브토픽 제목 테스트", "서브토픽 요약", parent_topic_id=42)
        _, params = conn.query_one_calls[0]
        self.assertEqual(params[3], 42)

    def test_create_topic_returns_id_from_db(self):
        """create_topic 은 DB 가 반환한 id 를 그대로 돌려줘야 한다."""
        conn = FakeConn()
        topic_id = create_topic(conn, "사회", "새 토픽 제목 이름", "새 토픽 요약")
        self.assertEqual(topic_id, 1)

    def test_empty_title_raises_value_error(self):
        """title 과 summary 가 모두 빈 문자열이면 ValueError 가 발생해야 한다."""
        conn = FakeConn()
        with self.assertRaises(ValueError):
            create_topic(conn, "사회", "", "")

    def test_parent_subtopic_chain(self):
        """부모(id=1)를 만든 뒤 그 아래 서브토픽(parent_id=1)을 만들 때 파라미터가 정확해야 한다."""
        conn = FakeConn()
        parent_id = create_topic(conn, "사회", "부모 토픽 제목 이름", "부모 토픽 요약")
        sub_id = create_topic(conn, "사회", "서브토픽 제목 이름", "서브토픽 요약", parent_topic_id=parent_id)

        _, parent_params = conn.query_one_calls[0]
        _, sub_params = conn.query_one_calls[1]

        self.assertIsNone(parent_params[3], "부모 토픽은 parent_topic_id=None 이어야 한다")
        self.assertEqual(sub_params[3], parent_id, "서브토픽은 parent_topic_id=부모ID 이어야 한다")
        self.assertNotEqual(parent_id, sub_id)


# ── 2. db.topic_causes.search_candidates — 계층 필터 ─────────────────────────

class TopicCandidateSearchTests(unittest.TestCase):
    """search_candidates 가 roots_only/parent_topic_id 옵션에 따라 올바른 SQL 조건과
    파라미터를 사용하는지, 그리고 결과를 TopicCandidate 로 올바르게 집계하는지 검증."""

    def _make_mock_conn(self, rows=None):
        conn = MagicMock()
        conn.query.return_value = rows or []
        return conn

    def test_invalid_top_k_raises(self):
        """top_k=0 이면 ValueError 가 발생해야 한다."""
        conn = self._make_mock_conn()
        with self.assertRaises(ValueError, msg="top_k must be greater than 0"):
            search_candidates(conn, "[0.1]", "사회", 0.5, top_k=0)

    def test_roots_only_and_parent_id_mutually_exclusive(self):
        """roots_only=True 와 parent_topic_id 를 동시에 지정하면 ValueError 가 발생해야 한다."""
        conn = self._make_mock_conn()
        with self.assertRaises(ValueError):
            search_candidates(conn, "[0.1]", "사회", 0.5, 5, roots_only=True, parent_topic_id=1)

    def test_roots_only_injects_is_null_predicate(self):
        """roots_only=True 시 실행되는 SQL 에 'IS NULL' 조건이 포함되어야 한다."""
        conn = self._make_mock_conn()
        search_candidates(conn, "[0.1]", "사회", 0.5, 5, roots_only=True)
        executed_sql = conn.query.call_args[0][0]
        self.assertIn("IS NULL", executed_sql)

    def test_parent_topic_id_appends_param_and_predicate(self):
        """parent_topic_id=7 지정 시 SQL 에 '= ?' 조건이 있고 파라미터에 7 이 포함되어야 한다."""
        conn = self._make_mock_conn()
        search_candidates(conn, "[0.1]", "사회", 0.5, 5, parent_topic_id=7)
        executed_sql, params = conn.query.call_args[0]
        self.assertIn("= ?", executed_sql)
        self.assertIn(7, params)

    def test_flat_mode_no_extra_predicate(self):
        """아무 옵션도 없는 평면 모드에서는 IS NULL / = ? 조건이 없어야 한다."""
        conn = self._make_mock_conn()
        search_candidates(conn, "[0.1]", "사회", 0.5, 5)
        executed_sql = conn.query.call_args[0][0]
        # 부모 스코프 절이 삽입되지 않은 빈 parent_predicate
        self.assertNotIn("IS NULL", executed_sql)
        # parent_topic_id = ? 형태의 자기참조 조건 없음
        # (topic_rank <= ? 처럼 원래 있는 <= 조건과 구별하기 위해 IS NULL 부재만 확인)

    def test_groups_multiple_rows_by_topic_id(self):
        """같은 topic_id 로 온 두 행은 단일 TopicCandidate 로 집계되어야 한다."""
        rows = [
            {
                "topic_id": 1, "topic_category": "사회", "topic_title": "토픽",
                "topic_summary": "요약", "cause_text": "원인A", "distance": 0.1,
            },
            {
                "topic_id": 1, "topic_category": "사회", "topic_title": "토픽",
                "topic_summary": "요약", "cause_text": "원인B", "distance": 0.2,
            },
        ]
        conn = self._make_mock_conn(rows)
        candidates = search_candidates(conn, "[0.1]", "사회", 0.5, 5)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].topic_id, 1)
        self.assertIn("원인A", candidates[0].cause_texts)
        self.assertIn("원인B", candidates[0].cause_texts)
        # distance 는 최솟값으로 갱신되어야 한다
        self.assertAlmostEqual(candidates[0].distance, 0.1)


# ── 3. pipeline._reason_rejects_assignment — 부정 마커 감지 ───────────────────

class ReasonRejectsAssignmentTests(unittest.TestCase):
    """_reason_rejects_assignment 가 부정 마커를 올바르게 인식하는지 검증."""

    def test_rejects_무관(self):
        self.assertTrue(_reason_rejects_assignment("이 이벤트는 토픽과 무관하다"))

    def test_rejects_관련성이_없어(self):
        self.assertTrue(_reason_rejects_assignment("현재 원인과 관련성이 없어 새로 만든다"))

    def test_rejects_새로운_사건(self):
        self.assertTrue(_reason_rejects_assignment("새로운 사건으로 판단됨"))

    def test_rejects_일치하지_않(self):
        self.assertTrue(_reason_rejects_assignment("피해자와 지역이 일치하지 않는다"))

    def test_accepts_positive_reason(self):
        """긍정적 배정 근거는 거부되면 안 된다."""
        self.assertFalse(_reason_rejects_assignment("동일 사건의 연속 전개로 판단"))

    def test_accepts_none(self):
        """reason 이 None 이어도 오류 없이 False 를 반환해야 한다."""
        self.assertFalse(_reason_rejects_assignment(None))

    def test_accepts_empty_string(self):
        self.assertFalse(_reason_rejects_assignment(""))

    def test_case_insensitive_and_whitespace_normalized(self):
        """공백이 많아도 마커를 정상 감지해야 한다."""
        self.assertTrue(_reason_rejects_assignment("  새로운   사건  "))


# ── 4. pipeline._load_decision_score — 점수 안전 파싱 ─────────────────────────

class LoadDecisionScoreTests(unittest.TestCase):
    """_load_decision_score 가 다양한 입력에서 float 를 안전하게 반환하는지 검증."""

    def test_normal_float(self):
        self.assertAlmostEqual(_load_decision_score({"score": 0.85}), 0.85)

    def test_missing_key_returns_zero(self):
        self.assertAlmostEqual(_load_decision_score({}), 0.0)

    def test_non_numeric_returns_zero(self):
        self.assertAlmostEqual(_load_decision_score({"score": "높음"}), 0.0)

    def test_integer_score_cast_to_float(self):
        self.assertAlmostEqual(_load_decision_score({"score": 1}), 1.0)


# ── 5. pipeline._resolve_action — 후보 없음 생략 및 가드레일 ───────────────────

class ResolveActionTests(unittest.TestCase):
    """_resolve_action 의 핵심 분기를 검증: 후보 없음→LLM 생략, 점수/사유 가드레일."""

    def _mock_client(self, response: dict) -> MagicMock:
        client = MagicMock()
        client.request_json.return_value = response
        return client

    def test_no_candidates_skips_llm_and_returns_create(self):
        """후보가 없을 때는 LLM 을 호출하지 않고 즉시 create 를 반환해야 한다."""
        client = self._mock_client({"action": "assign"})
        action, decision = _resolve_action(
            client, [], lambda: "prompt", fallback_title="폴백제목"
        )
        client.request_json.assert_not_called()
        self.assertEqual(action, "create")
        self.assertEqual(decision["new_title"], "폴백제목")

    def test_assign_above_threshold_accepted(self):
        """점수 0.9(≥0.75 임계값)이고 긍정 사유면 assign 을 그대로 반환해야 한다."""
        candidates = [_make_candidate(1)]
        client = self._mock_client({
            "action": "assign", "topic_id": 1, "score": 0.9, "reason": "동일 사건 흐름",
        })
        action, decision = _resolve_action(
            client, candidates, lambda: "prompt", fallback_title="폴백"
        )
        self.assertEqual(action, "assign")
        self.assertEqual(decision["topic_id"], 1)

    def test_assign_score_below_threshold_demoted_to_create(self):
        """점수 0.3(＜0.75)이면 assign 이 create 로 강등되어야 한다."""
        candidates = [_make_candidate(2)]
        client = self._mock_client({
            "action": "assign", "topic_id": 2, "score": 0.3, "reason": "관련",
        })
        action, _ = _resolve_action(
            client, candidates, lambda: "prompt", fallback_title="폴백"
        )
        self.assertEqual(action, "create")

    def test_assign_negative_reason_demoted_to_create(self):
        """점수가 높아도 reason 에 부정 마커가 있으면 create 로 강등되어야 한다."""
        candidates = [_make_candidate(3)]
        client = self._mock_client({
            "action": "assign", "topic_id": 3, "score": 0.95, "reason": "사실상 무관한 사건",
        })
        action, _ = _resolve_action(
            client, candidates, lambda: "prompt", fallback_title="폴백"
        )
        self.assertEqual(action, "create")

    def test_invalid_action_raises_value_error(self):
        """LLM 이 'assign'/'create' 이외의 action 을 반환하면 ValueError 가 발생해야 한다."""
        candidates = [_make_candidate(4)]
        client = self._mock_client({"action": "unknown"})
        with self.assertRaises(ValueError):
            _resolve_action(client, candidates, lambda: "prompt", fallback_title="폴백")

    def test_fallback_reason_used_when_no_candidates(self):
        """후보가 없을 때 결정 dict 의 reason 이 fallback_reason 으로 설정되어야 한다."""
        client = MagicMock()
        _, decision = _resolve_action(
            client, [], lambda: "p", fallback_title="T", fallback_reason="검색 후보 없음"
        )
        self.assertEqual(decision["reason"], "검색 후보 없음")


# ── 6. pipeline._select_candidate — 후보 집합 검증 ────────────────────────────

class SelectCandidateTests(unittest.TestCase):
    """_select_candidate 가 topic_id 를 실제 후보 집합과 교차 검증하는지 확인."""

    def test_valid_topic_id_returns_candidate(self):
        candidates = [_make_candidate(10), _make_candidate(20)]
        decision = {"action": "assign", "topic_id": 20, "score": 0.9}
        chosen = _select_candidate(decision, candidates)
        self.assertEqual(chosen.topic_id, 20)

    def test_unknown_topic_id_raises(self):
        """LLM 이 후보에 없는 topic_id 를 반환하면 ValueError 가 발생해야 한다."""
        candidates = [_make_candidate(10)]
        decision = {"action": "assign", "topic_id": 99, "score": 0.9}
        with self.assertRaises(ValueError, msg="LLM selected unknown topic_id"):
            _select_candidate(decision, candidates)


# ── 7. pipeline._assign_hierarchical — 2단계 계층 배정 ────────────────────────

_PATCH_BASE = "topic_classifier.pipeline"


class AssignHierarchicalTests(unittest.TestCase):
    """_assign_hierarchical 의 주요 3가지 경로를 검증.

    LLMClient, embed_*, to_vector_literal, topic_causes, topics, events 를 모두 모킹해
    외부 의존성을 완전히 격리한다.
    """

    def _common_patches(self):
        """모든 외부 의존성을 한번에 모킹하는 컨텍스트 매니저 스택을 반환한다."""
        return [
            patch(f"{_PATCH_BASE}.embed_query", return_value=[0.1]),
            patch(f"{_PATCH_BASE}.embed_passage", return_value=[0.1]),
            patch(f"{_PATCH_BASE}.to_vector_literal", return_value="[0.1]"),
            patch(f"{_PATCH_BASE}.topic_causes.add_cause"),
            patch(f"{_PATCH_BASE}.events.assign_topic"),
            patch(f"{_PATCH_BASE}.events.find_prev_event", return_value=None),
            patch(f"{_PATCH_BASE}.events.find_next_event_id", return_value=None),
            patch(f"{_PATCH_BASE}.events.link_into_chain"),
        ]

    def test_new_parent_new_sub_creates_both(self):
        """후보가 없으면 부모·서브토픽 모두 신규 생성하고 LLM 을 호출하지 않아야 한다."""
        ev = _make_event()
        client = MagicMock()
        conn = FakePipelineConn()

        patches = self._common_patches()
        with patch(f"{_PATCH_BASE}.topic_causes.search_candidates", return_value=[]), \
             patch(f"{_PATCH_BASE}.topics.create_topic", side_effect=[100, 200]), \
             patch(f"{_PATCH_BASE}.topics.update_topic"):
            for p in patches:
                p.start()
            try:
                label = _assign_hierarchical(conn, client, ev, "원인텍스트", "결과텍스트", 5, 5)
            finally:
                for p in patches:
                    p.stop()

        # LLM 호출 없음
        client.request_json.assert_not_called()
        # 반환 레이블에 create / leaf 200 이 포함되어야 한다
        self.assertIn("create", label)
        self.assertIn("200", label)

    def test_existing_parent_new_sub_assigns_parent_creates_sub(self):
        """기존 부모에 배정되고 서브토픽 후보가 없으면 서브는 신규 생성되어야 한다."""
        ev = _make_event()
        parent_cand = _make_candidate(10, title="부모토픽제목", summary="부모요약")
        conn = FakePipelineConn()

        # call 순서: 1) 부모 배정 LLM, 2) 부모 롤업 LLM
        client = MagicMock()
        client.request_json.side_effect = [
            {"action": "assign", "topic_id": 10, "score": 0.9, "reason": "동일 사건"},
            {"title": "갱신제목", "summary": "갱신요약"},
        ]

        def _search(conn_, emb, cat, dist, k, *, roots_only=False, parent_topic_id=None):
            if roots_only:
                return [parent_cand]
            return []  # 서브토픽 후보 없음

        patches = self._common_patches()
        with patch(f"{_PATCH_BASE}.topic_causes.search_candidates", side_effect=_search), \
             patch(f"{_PATCH_BASE}.topics.create_topic", return_value=201) as mock_create, \
             patch(f"{_PATCH_BASE}.topics.update_topic") as mock_update:
            for p in patches:
                p.start()
            try:
                label = _assign_hierarchical(conn, client, ev, "원인", "결과", 5, 5)
            finally:
                for p in patches:
                    p.stop()

        # 부모는 update, 서브는 create
        mock_update.assert_called_once()
        mock_create.assert_called_once()
        # create 할 때 parent_topic_id=10 으로 호출되어야 한다
        _, create_kwargs = mock_create.call_args
        # positional 호출: (conn, category, title, summary, parent_id)
        create_args = mock_create.call_args[0]
        self.assertEqual(create_args[4], 10)
        self.assertIn("create", label)

    def test_existing_parent_existing_sub_assigns_both(self):
        """부모·서브 모두 기존 토픽에 배정될 때 두 롤업 LLM 이 호출되어야 한다."""
        ev = _make_event()
        parent_cand = _make_candidate(10, title="부모제목", summary="부모요약")
        sub_cand = _make_candidate(20, title="서브제목", summary="서브요약")
        conn = FakePipelineConn()

        # call 순서: 1) 부모 배정, 2) 서브 배정, 3) 부모 롤업, 4) 서브 롤업
        client = MagicMock()
        client.request_json.side_effect = [
            {"action": "assign", "topic_id": 10, "score": 0.92, "reason": "동일 흐름"},
            {"action": "assign", "topic_id": 20, "score": 0.88, "reason": "서브 동일"},
            {"title": "부모갱신", "summary": "부모요약갱신"},
            {"title": "서브갱신", "summary": "서브요약갱신"},
        ]

        def _search(conn_, emb, cat, dist, k, *, roots_only=False, parent_topic_id=None):
            if roots_only:
                return [parent_cand]
            if parent_topic_id == 10:
                return [sub_cand]
            return []

        patches = self._common_patches()
        with patch(f"{_PATCH_BASE}.topic_causes.search_candidates", side_effect=_search), \
             patch(f"{_PATCH_BASE}.topics.create_topic") as mock_create, \
             patch(f"{_PATCH_BASE}.topics.update_topic") as mock_update:
            for p in patches:
                p.start()
            try:
                label = _assign_hierarchical(conn, client, ev, "원인", "결과", 5, 5)
            finally:
                for p in patches:
                    p.stop()

        # create 없이 두 번 update
        mock_create.assert_not_called()
        self.assertEqual(mock_update.call_count, 2)
        # LLM 은 총 4번 호출 (배정 2 + 롤업 2)
        self.assertEqual(client.request_json.call_count, 4)
        # 반환 레이블에 assign 정보 포함
        self.assertIn("assign", label)
        self.assertIn("20", label)


# ── 8. pipeline.run — subtopics_enabled 분기 및 처리 건수 ─────────────────────

class RunPipelineTests(unittest.TestCase):
    """run() 이 subtopics_enabled 에 따라 올바른 헬퍼를 호출하고 처리 건수를 반환하는지 검증."""

    def _mock_client(self, cause_result: dict) -> MagicMock:
        client = MagicMock()
        client.request_json.return_value = cause_result
        return client

    def test_returns_zero_when_no_unassigned_events(self):
        """미배정 이벤트가 없을 때 0 을 반환하고 이후 로직을 실행하지 않아야 한다."""
        conn = FakePipelineConn()
        with patch(f"{_PATCH_BASE}.events.fetch_unassigned", return_value=[]), \
             patch(f"{_PATCH_BASE}._get_client", return_value=MagicMock()):
            result = run(conn, min_net=1, batch_size=10, top_k=5, llm_model="test-model")
        self.assertEqual(result, 0)

    def test_flat_mode_dispatches_to_assign_flat(self):
        """subtopics_enabled=False 일 때 _assign_flat 을 호출해야 한다."""
        ev = _make_event()
        client = self._mock_client({"cause": "원인텍스트", "result": "결과텍스트"})
        conn = FakePipelineConn()

        with patch(f"{_PATCH_BASE}.events.fetch_unassigned", return_value=[ev]), \
             patch(f"{_PATCH_BASE}._get_client", return_value=client), \
             patch(f"{_PATCH_BASE}._assign_flat", return_value="create") as mock_flat, \
             patch(f"{_PATCH_BASE}._assign_hierarchical") as mock_hier:
            result = run(conn, min_net=1, batch_size=10, top_k=5, llm_model="m",
                         subtopics_enabled=False)

        self.assertEqual(result, 1)
        mock_flat.assert_called_once()
        mock_hier.assert_not_called()

    def test_hierarchical_mode_dispatches_to_assign_hierarchical(self):
        """subtopics_enabled=True 일 때 _assign_hierarchical 을 호출해야 한다."""
        ev = _make_event()
        client = self._mock_client({"cause": "원인텍스트", "result": "결과텍스트"})
        conn = FakePipelineConn()

        with patch(f"{_PATCH_BASE}.events.fetch_unassigned", return_value=[ev]), \
             patch(f"{_PATCH_BASE}._get_client", return_value=client), \
             patch(f"{_PATCH_BASE}._assign_flat") as mock_flat, \
             patch(f"{_PATCH_BASE}._assign_hierarchical",
                   return_value="parent create / sub create (leaf 99)") as mock_hier:
            result = run(conn, min_net=1, batch_size=10, top_k=5, llm_model="m",
                         subtopics_enabled=True)

        self.assertEqual(result, 1)
        mock_hier.assert_called_once()
        mock_flat.assert_not_called()

    def test_subtopic_top_k_defaults_to_top_k_when_none(self):
        """subtopic_top_k=None 이면 top_k 값으로 대체하여 _assign_hierarchical 에 전달해야 한다."""
        ev = _make_event()
        client = self._mock_client({"cause": "원인", "result": "결과"})
        conn = FakePipelineConn()

        with patch(f"{_PATCH_BASE}.events.fetch_unassigned", return_value=[ev]), \
             patch(f"{_PATCH_BASE}._get_client", return_value=client), \
             patch(f"{_PATCH_BASE}._assign_hierarchical",
                   return_value="parent create / sub create (leaf 1)") as mock_hier:
            run(conn, min_net=1, batch_size=10, top_k=7, llm_model="m",
                subtopics_enabled=True, subtopic_top_k=None)

        # 6번째 positional arg 가 subtopic_top_k = top_k = 7 이어야 한다
        call_args = mock_hier.call_args[0]
        self.assertEqual(call_args[6], 7)

    def test_run_returns_zero_on_exception(self):
        """이벤트 처리 중 예외가 나면 break 하고 처리된 건수를 반환해야 한다."""
        ev = _make_event()
        client = self._mock_client({"cause": "원인", "result": "결과"})
        conn = FakePipelineConn()

        with patch(f"{_PATCH_BASE}.events.fetch_unassigned", return_value=[ev]), \
             patch(f"{_PATCH_BASE}._get_client", return_value=client), \
             patch(f"{_PATCH_BASE}._assign_flat", side_effect=RuntimeError("DB 오류")):
            result = run(conn, min_net=1, batch_size=10, top_k=5, llm_model="m")

        self.assertEqual(result, 0)


# ── 9. topic_classifier.prompts.build_subtopic_assignment_prompt ──────────────

class SubtopicPromptTests(unittest.TestCase):
    """build_subtopic_assignment_prompt 가 부모 토픽·이벤트·서브토픽 정보를
    프롬프트에 올바르게 담는지 검증."""

    def _build(self, candidates=None):
        return build_subtopic_assignment_prompt(
            parent_title="지방선거 이슈",
            parent_summary="2026년 지방선거 관련 보도.",
            title="당선인 발표",
            summary="선거 결과 당선인 확정 보도.",
            cause="지방선거 개표 완료",
            result="당선인 명단이 공식 발표됐다.",
            candidates=candidates or [],
        )

    def test_contains_parent_title_in_prompt(self):
        """프롬프트에 부모 토픽 제목이 포함되어야 한다."""
        prompt = self._build()
        self.assertIn("지방선거 이슈", prompt)

    def test_contains_parent_summary_in_prompt(self):
        """프롬프트에 부모 토픽 요약이 포함되어야 한다."""
        prompt = self._build()
        self.assertIn("2026년 지방선거", prompt)

    def test_contains_event_info_in_prompt(self):
        """프롬프트에 이벤트 제목, 원인, 결과가 포함되어야 한다."""
        prompt = self._build()
        self.assertIn("당선인 발표", prompt)
        self.assertIn("지방선거 개표 완료", prompt)
        self.assertIn("당선인 명단이 공식 발표됐다", prompt)

    def test_no_candidates_shows_fallback_text(self):
        """후보가 없을 때 프롬프트에 '검색된 후보 없음' 이 표시되어야 한다."""
        prompt = self._build(candidates=[])
        self.assertIn("검색된 후보 없음", prompt)

    def test_with_candidate_shows_topic_id_and_title(self):
        """후보가 있을 때 topic_id 와 제목이 프롬프트에 출력되어야 한다."""
        cand = _make_candidate(topic_id=55, title="부정선거 의혹", summary="투표함 조작 의혹")
        prompt = self._build(candidates=[cand])
        self.assertIn("55", prompt)
        self.assertIn("부정선거 의혹", prompt)

    def test_prompt_contains_action_format_instructions(self):
        """프롬프트에 assign / create 액션 형식 지시가 모두 포함되어야 한다."""
        prompt = self._build()
        self.assertIn('"action": "assign"', prompt)
        self.assertIn('"action": "create"', prompt)


# ── 10. settings — SUBTOPICS_ENABLED 환경변수 파싱 ────────────────────────────

class SubtopicsSettingsTests(unittest.TestCase):
    """SUBTOPICS_ENABLED 환경변수 파싱 로직을 importlib.reload 로 검증."""

    def _reload_settings(self, env_val: str):
        import importlib
        import topic_classifier.settings as s
        with patch.dict(os.environ, {"TOPIC_SUBTOPICS_ENABLED": env_val}):
            importlib.reload(s)
            return s.SUBTOPICS_ENABLED

    def test_true_string_enables(self):
        self.assertTrue(self._reload_settings("true"))

    def test_one_string_enables(self):
        self.assertTrue(self._reload_settings("1"))

    def test_yes_string_enables(self):
        self.assertTrue(self._reload_settings("yes"))

    def test_false_string_disables(self):
        self.assertFalse(self._reload_settings("false"))

    def test_empty_string_disables(self):
        self.assertFalse(self._reload_settings(""))

    def test_arbitrary_string_disables(self):
        self.assertFalse(self._reload_settings("enabled"))

    def tearDown(self):
        """테스트 후 settings 모듈을 기본값으로 복원한다."""
        import importlib
        import topic_classifier.settings as s
        os.environ.pop("TOPIC_SUBTOPICS_ENABLED", None)
        importlib.reload(s)


if __name__ == "__main__":
    unittest.main()
