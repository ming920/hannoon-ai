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
from db.topics import create_topic, find_duplicate_topic, update_topic
from db.events import Event
from topic_classifier import naming_rules
from topic_classifier.pipeline import (
    _reason_rejects_assignment,
    _load_decision_score,
    _resolve_action,
    _select_candidate,
    _assign_hierarchical,
    _assign_subtopic_by_embedding,
    _dedup_guard,
    _sanitize_subtopic_title,
    run,
)
import topic_classifier.settings as topic_settings
from topic_classifier.prompts import (
    build_parent_topic_assignment_prompt,
    build_subtopic_assignment_prompt,
)


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


class FakeQueryOneConn(FakePipelineConn):
    """query_one 응답을 순서대로 스크립트할 수 있는 가짜 연결 (embedding 모드 테스트용)."""

    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.query_one_calls: list[tuple] = []

    def query_one(self, sql, params=None):
        self.query_one_calls.append((sql, params))
        return self._responses.pop(0) if self._responses else None


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

    def test_create_missing_new_title_backfilled_with_fallback(self):
        """LLM 이 create 에서 new_title 을 누락해도 KeyError 없이 fallback_title 로 보정되어야 한다."""
        candidates = [_make_candidate(5)]
        client = self._mock_client({"action": "create", "score": 0.2, "reason": "별도 사안"})
        action, decision = _resolve_action(
            client, candidates, lambda: "prompt", fallback_title="폴백제목"
        )
        self.assertEqual(action, "create")
        self.assertEqual(decision["new_title"], "폴백제목")

    def test_create_null_new_title_backfilled_with_fallback(self):
        """LLM 이 new_title 을 null 로 반환하면 'None' 제목 대신 fallback_title 을 써야 한다."""
        candidates = [_make_candidate(6)]
        client = self._mock_client({
            "action": "create", "new_title": None, "score": 0.2, "reason": "별도 사안",
        })
        _, decision = _resolve_action(
            client, candidates, lambda: "prompt", fallback_title="폴백제목"
        )
        self.assertEqual(decision["new_title"], "폴백제목")

    def test_custom_score_threshold_demotes_midband_assign(self):
        """score_threshold 를 0.85로 올리면 0.80 assign 이 create 로 강등되어야 한다."""
        candidates = [_make_candidate(7)]
        client = self._mock_client({
            "action": "assign", "topic_id": 7, "score": 0.80, "reason": "같은 갈래",
        })
        action, _ = _resolve_action(
            client, candidates, lambda: "prompt", fallback_title="폴백",
            score_threshold=0.85,
        )
        self.assertEqual(action, "create")

    def test_custom_score_threshold_keeps_confident_assign(self):
        """문턱 0.85에서도 0.90 assign 은 그대로 통과해야 한다."""
        candidates = [_make_candidate(8)]
        client = self._mock_client({
            "action": "assign", "topic_id": 8, "score": 0.90, "reason": "같은 갈래",
        })
        action, _ = _resolve_action(
            client, candidates, lambda: "prompt", fallback_title="폴백",
            score_threshold=0.85,
        )
        self.assertEqual(action, "assign")


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

    def test_new_parent_fallback_title_uses_cause(self):
        """후보 0개로 부모를 새로 만들 때 제목은 이벤트 제목이 아니라 cause 명사구여야 한다."""
        ev = _make_event()
        client = MagicMock()
        conn = FakePipelineConn()

        patches = self._common_patches()
        with patch(f"{_PATCH_BASE}.topic_causes.search_candidates", return_value=[]), \
             patch(f"{_PATCH_BASE}.topics.create_topic", side_effect=[100, 200]) as mock_create, \
             patch(f"{_PATCH_BASE}.topics.update_topic"):
            for p in patches:
                p.start()
            try:
                _assign_hierarchical(conn, client, ev, "전세 보증금 미반환", "결과텍스트", 5, 5)
            finally:
                for p in patches:
                    p.stop()

        # positional 호출: (conn, category, title, summary, parent_id)
        parent_args = mock_create.call_args_list[0][0]
        sub_args = mock_create.call_args_list[1][0]
        self.assertEqual(parent_args[2], "전세 보증금 미반환")
        # 서브토픽 폴백 제목은 기존대로 이벤트 제목을 유지한다
        self.assertEqual(sub_args[2], ev.title)

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
        with patch(f"{_PATCH_BASE}.SUBTOPIC_MODE", "llm"), \
             patch(f"{_PATCH_BASE}.topic_causes.search_candidates", side_effect=_search), \
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
        # 기존 부모 아래 서브 폴백 제목은 이벤트 제목이 아니라 cause 명사구여야 한다
        # (S-1 이벤트복사형 방지 — 2-3a)
        self.assertEqual(create_args[2], "원인")
        self.assertIn("create", label)

    def test_sub_assign_uses_subtopic_threshold(self):
        """서브 경로는 SUBTOPIC_ASSIGN_SCORE_THRESHOLD 로 강등을 판단해야 한다.

        부모 assign 0.90(기본 문턱 통과), 서브 assign 0.80은 서브 문턱 0.85 미달로
        create 강등 — 부모/서브 임계값 분리(critic C-3/C-11) 회귀 테스트.
        """
        ev = _make_event()
        parent_cand = _make_candidate(10, title="부모제목", summary="부모요약")
        sub_cand = _make_candidate(20, title="서브제목", summary="서브요약")
        conn = FakePipelineConn()

        # call 순서: 1) 부모 배정, 2) 서브 배정(강등), 3) 부모 롤업 (서브 롤업 없음)
        client = MagicMock()
        client.request_json.side_effect = [
            {"action": "assign", "topic_id": 10, "score": 0.90, "reason": "동일 흐름"},
            {"action": "assign", "topic_id": 20, "score": 0.80, "reason": "같은 갈래"},
            {"title": "부모갱신", "summary": "부모요약갱신"},
        ]

        def _search(conn_, emb, cat, dist, k, *, roots_only=False, parent_topic_id=None):
            if roots_only:
                return [parent_cand]
            if parent_topic_id == 10:
                return [sub_cand]
            return []

        patches = self._common_patches()
        with patch(f"{_PATCH_BASE}.SUBTOPIC_MODE", "llm"), \
             patch(f"{_PATCH_BASE}.SUBTOPIC_ASSIGN_SCORE_THRESHOLD", 0.85), \
             patch(f"{_PATCH_BASE}.topic_causes.search_candidates", side_effect=_search), \
             patch(f"{_PATCH_BASE}.topics.create_topic", return_value=300) as mock_create, \
             patch(f"{_PATCH_BASE}.topics.update_topic") as mock_update:
            for p in patches:
                p.start()
            try:
                label = _assign_hierarchical(conn, client, ev, "원인", "결과", 5, 5)
            finally:
                for p in patches:
                    p.stop()

        mock_update.assert_called_once()  # 부모만 update
        mock_create.assert_called_once()  # 서브는 강등되어 create
        self.assertIn("sub create", label)

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
        with patch(f"{_PATCH_BASE}.SUBTOPIC_MODE", "llm"), \
             patch(f"{_PATCH_BASE}.topic_causes.search_candidates", side_effect=_search), \
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
        """이벤트 처리 중 예외가 나면 해당 이벤트를 건너뛰고 처리된 건수를 반환해야 한다."""
        ev = _make_event()
        client = self._mock_client({"cause": "원인", "result": "결과"})
        conn = FakePipelineConn()

        with patch(f"{_PATCH_BASE}.events.fetch_unassigned", return_value=[ev]), \
             patch(f"{_PATCH_BASE}._get_client", return_value=client), \
             patch(f"{_PATCH_BASE}._assign_flat", side_effect=RuntimeError("DB 오류")):
            result = run(conn, min_net=1, batch_size=10, top_k=5, llm_model="m")

        self.assertEqual(result, 0)

    def test_run_continues_after_event_failure(self):
        """앞 이벤트가 실패해도 break 하지 않고 다음 이벤트를 계속 처리해야 한다(웨징 회귀)."""
        ev1 = _make_event(event_id=1)
        ev2 = _make_event(event_id=2)
        client = self._mock_client({"cause": "원인", "result": "결과"})
        conn = FakePipelineConn()

        with patch(f"{_PATCH_BASE}.events.fetch_unassigned", return_value=[ev1, ev2]), \
             patch(f"{_PATCH_BASE}._get_client", return_value=client), \
             patch(f"{_PATCH_BASE}._assign_flat",
                   side_effect=[RuntimeError("DB 오류"), "create"]) as mock_flat:
            result = run(conn, min_net=1, batch_size=10, top_k=5, llm_model="m")

        self.assertEqual(result, 1)
        self.assertEqual(mock_flat.call_count, 2)


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

    def test_prompt_defines_subtopic_as_multi_event_branch(self):
        """서브토픽을 개별 사건이 아닌 여러 관련 사건을 담는 중간 갈래로 정의해야 한다(싱글턴 회귀 방지)."""
        prompt = self._build()
        self.assertIn("여러 관련 사건", prompt)
        self.assertIn("구체적 사건·절차 단계가 서로 달라도 assign", prompt)

    def test_prompt_bans_guardrail_markers_in_assign_reason(self):
        """assign 사유에 가드레일 부정 마커 표현을 금지하는 지시가 있어야 한다."""
        prompt = self._build()
        self.assertIn("같은 표현을 쓰지 마세요", prompt)

    def test_prompt_bans_time_and_attribute_titles(self):
        """new_title 규칙에 시간구분형(S-2)·속성형(S-3) 금지 지시가 있어야 한다(2-3a)."""
        prompt = self._build()
        self.assertIn("단순 시간 구분으로 짓지 마세요", prompt)
        self.assertIn("찬반·논조 속성으로 짓지 마세요", prompt)


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


# ── 10-1. topic_classifier.prompts.build_parent_topic_assignment_prompt ──────

class ParentPromptTests(unittest.TestCase):
    """광의 부모 토픽 프롬프트의 핵심 요소를 검증 (리뷰 R-테스트갭 반영)."""

    def _build(self, candidates=None):
        return build_parent_topic_assignment_prompt(
            title="한미 반도체 공급망 MOU 체결",
            summary="한국과 미국이 반도체 공급망 협력 MOU를 체결했다.",
            cause="반도체 공급망 협력 필요성",
            result="한미 반도체 공급망 MOU가 체결됐다.",
            candidates=candidates or [],
        )

    def test_contains_event_and_issue_unit_definition(self):
        """이벤트 정보와 '하나의 사회적 이슈' 판단 기준이 프롬프트에 있어야 한다.

        토픽을 "넓은 주제·도메인"으로 정의하던 이전 문구는 버킷 과병합(covered P 저하)의
        원인이었다 — docs/entity_definitions.md 기준 이슈 단위 정의로 교체됨(2-4).
        """
        prompt = self._build()
        self.assertIn("한미 반도체 공급망 MOU 체결", prompt)
        self.assertIn("하나의 사회적 이슈", prompt)
        self.assertIn("구체적 사건이 서로 달라도 assign", prompt)

    def test_contains_naming_test_rule(self):
        """네이밍 테스트("<토픽명> — <이벤트명>")가 프롬프트에 명시되어야 한다."""
        prompt = self._build()
        self.assertIn("네이밍 테스트", prompt)
        self.assertIn("<토픽명> — <이벤트명>", prompt)

    def test_bans_generic_category_and_single_event_titles(self):
        """new_title 생성 규칙에 일반 카테고리명·단일 사건명 금지 지시가 있어야 한다(T-2/T-1)."""
        prompt = self._build()
        self.assertIn("일반 뉴스 카테고리명 단독으로 짓지 마세요", prompt)
        self.assertIn("단일 사건명으로 짓지 마세요", prompt)

    def test_contains_action_formats_and_candidate_fallback(self):
        """assign/create 형식 지시와 후보 없음 문구가 있어야 한다."""
        prompt = self._build()
        self.assertIn('"action": "assign"', prompt)
        self.assertIn('"action": "create"', prompt)
        self.assertIn("검색된 후보 없음", prompt)

    def test_bans_guardrail_markers_in_assign_reason(self):
        """가드레일 부정 마커 표현 금지 지시가 있어야 한다 (강등 오탐 방지)."""
        prompt = self._build()
        self.assertIn("같은 표현을 쓰지 마세요", prompt)


# ── 11. pipeline._assign_subtopic_by_embedding — 임베딩 최근접 서브 배정 ──────

class AssignSubtopicByEmbeddingTests(unittest.TestCase):
    """embedding 서브토픽 모드의 assign-or-create 결정을 검증 (LLM 무관, 결정론적)."""

    def test_new_parent_creates_without_queries(self):
        """parent_id가 None(새 부모)이면 DB 조회 없이 즉시 create 해야 한다."""
        conn = FakeQueryOneConn([])
        ev = _make_event()
        action, decision, chosen = _assign_subtopic_by_embedding(conn, ev, None)
        self.assertEqual(action, "create")
        self.assertEqual(decision["new_title"], ev.title)
        self.assertIsNone(chosen)
        self.assertEqual(conn.query_one_calls, [])

    def test_missing_embedding_creates(self):
        """이벤트 임베딩이 없으면 create 해야 한다."""
        conn = FakeQueryOneConn([{"embedding": None}])
        action, _, chosen = _assign_subtopic_by_embedding(conn, _make_event(), 10)
        self.assertEqual(action, "create")
        self.assertIsNone(chosen)

    def test_similarity_below_threshold_creates(self):
        """최근접 유사도가 문턱 미만이면 create 해야 한다."""
        conn = FakeQueryOneConn([
            {"embedding": "[0.1,0.2]"},
            {"leaf_id": 20, "sim": 0.30},
        ])
        with patch(f"{_PATCH_BASE}.SUBTOPIC_SIM_THRESHOLD", 0.55):
            action, decision, chosen = _assign_subtopic_by_embedding(conn, _make_event(), 10)
        self.assertEqual(action, "create")
        self.assertIn("0.300", decision["reason"])
        self.assertIsNone(chosen)

    def test_similarity_above_threshold_assigns_nearest(self):
        """문턱 이상이면 최근접 서브토픽에 assign하고 TopicCandidate를 반환해야 한다."""
        conn = FakeQueryOneConn([
            {"embedding": "[0.1,0.2]"},
            {"leaf_id": 20, "sim": 0.83},
            {"id": 20, "category": "사회", "title": "서브제목", "summary": "서브요약"},
        ])
        with patch(f"{_PATCH_BASE}.SUBTOPIC_SIM_THRESHOLD", 0.55):
            action, decision, chosen = _assign_subtopic_by_embedding(conn, _make_event(), 10)
        self.assertEqual(action, "assign")
        self.assertEqual(decision["topic_id"], 20)
        self.assertAlmostEqual(decision["score"], 0.83)
        self.assertEqual(chosen.topic_id, 20)
        self.assertEqual(chosen.title, "서브제목")

    def test_no_subtopics_under_parent_creates(self):
        """부모 아래 서브토픽이 하나도 없으면 create 해야 한다."""
        conn = FakeQueryOneConn([{"embedding": "[0.1,0.2]"}, None])
        action, _, chosen = _assign_subtopic_by_embedding(conn, _make_event(), 10)
        self.assertEqual(action, "create")
        self.assertIsNone(chosen)

    def test_similarity_exactly_at_threshold_assigns(self):
        """sim == 임계값(경계)이면 assign이어야 한다 (>= 비교, 리뷰 R-테스트갭 반영)."""
        conn = FakeQueryOneConn([
            {"embedding": "[0.1,0.2]"},
            {"leaf_id": 20, "sim": 0.55},
            {"id": 20, "category": "사회", "title": "서브제목", "summary": "서브요약"},
        ])
        with patch(f"{_PATCH_BASE}.SUBTOPIC_SIM_THRESHOLD", 0.55):
            action, _, chosen = _assign_subtopic_by_embedding(conn, _make_event(), 10)
        self.assertEqual(action, "assign")
        self.assertEqual(chosen.topic_id, 20)

    def test_nan_similarity_falls_back_to_create(self):
        """sim이 NaN(영벡터 등)이면 assign으로 새지 않고 create 해야 한다."""
        conn = FakeQueryOneConn([
            {"embedding": "[0.0,0.0]"},
            {"leaf_id": 20, "sim": float("nan")},
        ])
        with patch(f"{_PATCH_BASE}.SUBTOPIC_SIM_THRESHOLD", 0.55):
            action, _, chosen = _assign_subtopic_by_embedding(conn, _make_event(), 10)
        self.assertEqual(action, "create")
        self.assertIsNone(chosen)


# ── 12. embedding 모드 통합 — _assign_hierarchical 분기 ──────────────────────

class EmbeddingModeHierarchicalTests(unittest.TestCase):
    """SUBTOPIC_MODE=embedding에서 서브 배정이 LLM·후보검색 없이 동작하는지 검증."""

    def _common_patches(self):
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

    def test_embedding_mode_assigns_without_sub_llm_or_candidate_search(self):
        """부모 assign 후 서브는 임베딩 쿼리로 assign — 서브 LLM 배정·후보검색이 없어야 한다."""
        ev = _make_event()
        parent_cand = _make_candidate(10, title="부모제목", summary="부모요약")
        conn = FakeQueryOneConn([
            {"embedding": "[0.1,0.2]"},
            {"leaf_id": 20, "sim": 0.90},
            {"id": 20, "category": "사회", "title": "서브제목", "summary": "서브요약"},
        ])

        # LLM 호출: 1) 부모 배정, 2) 부모 롤업, 3) 서브 롤업 (서브 배정 판단 없음)
        client = MagicMock()
        client.request_json.side_effect = [
            {"action": "assign", "topic_id": 10, "score": 0.9, "reason": "동일 흐름"},
            {"title": "부모갱신", "summary": "부모요약갱신"},
            {"title": "서브갱신", "summary": "서브요약갱신"},
        ]

        search_calls = []
        def _search(conn_, emb, cat, dist, k, *, roots_only=False, parent_topic_id=None):
            search_calls.append({"roots_only": roots_only, "parent_topic_id": parent_topic_id})
            return [parent_cand] if roots_only else []

        patches = self._common_patches()
        with patch(f"{_PATCH_BASE}.SUBTOPIC_MODE", "embedding"), \
             patch(f"{_PATCH_BASE}.SUBTOPIC_SIM_THRESHOLD", 0.55), \
             patch(f"{_PATCH_BASE}.topic_causes.search_candidates", side_effect=_search), \
             patch(f"{_PATCH_BASE}.topics.create_topic") as mock_create, \
             patch(f"{_PATCH_BASE}.topics.update_topic") as mock_update:
            for p in patches:
                p.start()
            try:
                label = _assign_hierarchical(conn, client, ev, "원인", "결과", 5, 5)
            finally:
                for p in patches:
                    p.stop()

        # 후보 검색은 부모(roots_only) 1회뿐 — 서브 스코프 검색 없음
        self.assertEqual(len(search_calls), 1)
        self.assertTrue(search_calls[0]["roots_only"])
        # LLM은 3회 (부모 배정 + 롤업 2) — 서브 배정 판단 호출 없음
        self.assertEqual(client.request_json.call_count, 3)
        mock_create.assert_not_called()
        self.assertEqual(mock_update.call_count, 2)
        self.assertIn("assign 20", label)

    def test_embedding_mode_low_similarity_creates_subtopic(self):
        """유사도 미달이면 서브를 create하고 부모 아래에 붙여야 한다."""
        ev = _make_event()
        parent_cand = _make_candidate(10, title="부모제목", summary="부모요약")
        conn = FakeQueryOneConn([
            {"embedding": "[0.1,0.2]"},
            {"leaf_id": 20, "sim": 0.20},
        ])

        client = MagicMock()
        client.request_json.side_effect = [
            {"action": "assign", "topic_id": 10, "score": 0.9, "reason": "동일 흐름"},
            {"title": "부모갱신", "summary": "부모요약갱신"},
        ]

        patches = self._common_patches()
        with patch(f"{_PATCH_BASE}.SUBTOPIC_MODE", "embedding"), \
             patch(f"{_PATCH_BASE}.SUBTOPIC_SIM_THRESHOLD", 0.55), \
             patch(f"{_PATCH_BASE}.topic_causes.search_candidates", return_value=[parent_cand]), \
             patch(f"{_PATCH_BASE}.topics.create_topic", return_value=300) as mock_create, \
             patch(f"{_PATCH_BASE}.topics.update_topic") as mock_update:
            for p in patches:
                p.start()
            try:
                label = _assign_hierarchical(conn, client, ev, "원인", "결과", 5, 5)
            finally:
                for p in patches:
                    p.stop()

        mock_update.assert_called_once()  # 부모만 update
        mock_create.assert_called_once()  # 서브 create
        create_args = mock_create.call_args[0]
        self.assertEqual(create_args[4], 10)  # parent_id 아래에 생성
        self.assertIn("sub create", label)


# ── 13. settings — SUBTOPIC_ASSIGN_SCORE_THRESHOLD 파싱 ──────────────────────

class SubtopicThresholdSettingsTests(unittest.TestCase):
    """서브토픽 전용 assign 임계값의 기본값·env 오버라이드를 검증."""

    def _reload_settings(self):
        import importlib
        importlib.reload(topic_settings)
        return topic_settings

    def test_defaults_to_assign_threshold(self):
        """env 미지정 시 ASSIGN_SCORE_THRESHOLD 와 동일해야 한다(기존 동작 유지)."""
        os.environ.pop("TOPIC_SUBTOPIC_ASSIGN_SCORE_THRESHOLD", None)
        s = self._reload_settings()
        self.assertAlmostEqual(
            s.SUBTOPIC_ASSIGN_SCORE_THRESHOLD, s.ASSIGN_SCORE_THRESHOLD
        )

    def test_env_override_applies(self):
        """TOPIC_SUBTOPIC_ASSIGN_SCORE_THRESHOLD=0.85 지정 시 그 값을 써야 한다."""
        with patch.dict(os.environ, {"TOPIC_SUBTOPIC_ASSIGN_SCORE_THRESHOLD": "0.85"}):
            s = self._reload_settings()
            self.assertAlmostEqual(s.SUBTOPIC_ASSIGN_SCORE_THRESHOLD, 0.85)

    def test_subtopic_mode_defaults_to_embedding(self):
        """SUBTOPIC_MODE 미지정 시 embedding이어야 한다 (홀드아웃 검증 후 기본 채택).

        원본·홀드아웃 두 세트 모두에서 llm 대비 우위 확인 (holdout-emb065-1:
        covered P 0.926/F1 0.812 vs llm F1 0.557). TOPIC_SUBTOPICS_ENABLED
        기본 false라 평면 모드 프로덕션에는 영향 없음.
        """
        os.environ.pop("TOPIC_SUBTOPIC_MODE", None)
        s = self._reload_settings()
        self.assertEqual(s.SUBTOPIC_MODE, "embedding")

    def test_subtopic_mode_env_override(self):
        """TOPIC_SUBTOPIC_MODE=llm 지정 시 기존 LLM 방식으로 되돌릴 수 있어야 한다."""
        with patch.dict(os.environ, {"TOPIC_SUBTOPIC_MODE": "LLM"}):
            s = self._reload_settings()
            self.assertEqual(s.SUBTOPIC_MODE, "llm")

    def test_sim_threshold_default_and_override(self):
        """SUBTOPIC_SIM_THRESHOLD 기본 0.55(실데이터 실측), env 오버라이드 가능.

        0.65 → 0.50 → 0.55 로 옮겨왔다. 0.65 는 실데이터에서 서브토픽 38개 중 37개를
        이벤트 1개짜리로 만들었고(R-S1 97.4%), 0.50 은 반대로 '트럼프'만 공통인 이벤트
        4개를 한 토픽에 묶었다. 토픽 cannot 정답 144쌍으로 재보고 0.55 를 채택했다.
        근거는 settings.py 주석에 있다.
        """
        os.environ.pop("TOPIC_SUBTOPIC_SIM_THRESHOLD", None)
        s = self._reload_settings()
        self.assertAlmostEqual(s.SUBTOPIC_SIM_THRESHOLD, 0.55)
        with patch.dict(os.environ, {"TOPIC_SUBTOPIC_SIM_THRESHOLD": "0.65"}):
            s = self._reload_settings()
            self.assertAlmostEqual(s.SUBTOPIC_SIM_THRESHOLD, 0.65)

    def tearDown(self):
        os.environ.pop("TOPIC_SUBTOPIC_ASSIGN_SCORE_THRESHOLD", None)
        os.environ.pop("TOPIC_SUBTOPIC_MODE", None)
        os.environ.pop("TOPIC_SUBTOPIC_SIM_THRESHOLD", None)
        self._reload_settings()


# ── 14. db.topics.find_duplicate_topic — 중복 토픽 검색 ───────────────────────

class FakeQueryConn:
    """.query() 응답을 스크립트할 수 있는 가짜 연결 (find_duplicate_topic 테스트용)."""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.query_calls: list[tuple] = []

    def query(self, sql, params=None):
        self.query_calls.append((sql, params))
        return self._rows


class FindDuplicateTopicTests(unittest.TestCase):
    """find_duplicate_topic 이 스코프 내 최고 유사도 토픽을 올바르게 고르는지 검증."""

    def test_empty_title_returns_none(self):
        conn = FakeQueryConn([{"id": 1, "title": "아무 제목"}])
        self.assertIsNone(find_duplicate_topic(conn, "사회", "", None, 0.85))

    def test_no_rows_returns_none(self):
        conn = FakeQueryConn([])
        self.assertIsNone(find_duplicate_topic(conn, "사회", "새 토픽 제목", None, 0.85))

    def test_exact_match_returns_dup(self):
        conn = FakeQueryConn([{"id": 7, "title": "전세 사기 피해 대책"}])
        dup = find_duplicate_topic(conn, "사회", "전세 사기 피해 대책", None, 0.85)
        self.assertIsNotNone(dup)
        self.assertEqual(dup["id"], 7)
        self.assertAlmostEqual(dup["similarity"], 1.0)

    def test_below_threshold_returns_none(self):
        conn = FakeQueryConn([{"id": 7, "title": "완전히 무관한 이슈 제목"}])
        self.assertIsNone(find_duplicate_topic(conn, "사회", "전세 사기 피해 대책", None, 0.85))

    def test_picks_highest_similarity_among_multiple_matches(self):
        conn = FakeQueryConn([
            {"id": 1, "title": "전세 사기 피해 대책"},
            {"id": 2, "title": "전세 사기 피해 대책 마련"},
        ])
        dup = find_duplicate_topic(conn, "사회", "전세 사기 피해 대책", None, 0.85)
        self.assertEqual(dup["id"], 1)

    def test_root_scope_queries_parent_is_null(self):
        """parent_topic_id=None 이면 최상위 스코프 SQL을 써야 한다."""
        conn = FakeQueryConn([])
        find_duplicate_topic(conn, "사회", "제목", None, 0.85)
        sql, params = conn.query_calls[0]
        self.assertIn("IS NULL", sql)
        self.assertEqual(params, ("사회",))

    def test_sub_scope_queries_parent_equals(self):
        """parent_topic_id=10 이면 해당 부모 스코프 SQL과 파라미터를 써야 한다."""
        conn = FakeQueryConn([])
        find_duplicate_topic(conn, "사회", "제목", 10, 0.85)
        sql, params = conn.query_calls[0]
        self.assertNotIn("IS NULL", sql)
        self.assertEqual(params, ("사회", 10))


# ── 15. topic_classifier.naming_rules — 서브토픽 명명 4규칙 ───────────────────

class NamingRulesTests(unittest.TestCase):
    """S-1~S-4 판정 함수가 entity_definitions.md 예시를 올바르게 분류하는지 검증."""

    def test_time_segment_titles_detected(self):
        self.assertTrue(naming_rules.is_time_segment_title("G7 첫째 날"))
        self.assertTrue(naming_rules.is_time_segment_title("협상 3일차"))
        self.assertTrue(naming_rules.is_time_segment_title("2주차 진행 상황"))

    def test_non_time_segment_titles_pass(self):
        self.assertFalse(naming_rules.is_time_segment_title("부정선거 의혹 수사"))

    def test_attribute_titles_detected(self):
        self.assertTrue(naming_rules.is_attribute_title("찬성 여론"))
        self.assertTrue(naming_rules.is_attribute_title("반대"))

    def test_compound_noun_not_falsely_flagged_as_attribute(self):
        """'부정선거'처럼 속성 키워드가 복합명사를 이루면 오탐하면 안 된다."""
        self.assertFalse(naming_rules.is_attribute_title("부정선거 의혹 수사"))

    def test_event_copy_title_detected(self):
        self.assertTrue(naming_rules.is_event_copy_title("전세사기 피해자 집단소송 제기", "전세사기 피해자 집단소송 제기"))

    def test_distinct_title_not_event_copy(self):
        self.assertFalse(naming_rules.is_event_copy_title("전세 보증금 미반환", "전세사기 피해자 집단소송 제기"))

    def test_parent_scope_title_detected(self):
        self.assertTrue(naming_rules.is_parent_scope_title("의료개혁", "의료개혁"))

    def test_violates_subtopic_naming_returns_none_for_valid_title(self):
        self.assertIsNone(
            naming_rules.violates_subtopic_naming(
                "전공의 집단사직", event_title="정부 의대 증원 발표", parent_title="의료개혁"
            )
        )

    def test_violates_subtopic_naming_reports_time_segment_first(self):
        self.assertEqual(
            naming_rules.violates_subtopic_naming(
                "협상 3일차", event_title="어떤 이벤트", parent_title="어떤 부모"
            ),
            "S-2 시간구분형",
        )

    def test_violates_subtopic_naming_reports_empty(self):
        self.assertEqual(
            naming_rules.violates_subtopic_naming("", event_title="e", parent_title="p"),
            "empty",
        )


# ── 16. pipeline._dedup_guard — create 직전 중복 방지 강등 ────────────────────

class DedupGuardTests(unittest.TestCase):
    """_dedup_guard 가 create 를 assign 으로 강등하는 흐름을 검증."""

    def test_non_create_action_passthrough(self):
        conn = FakeQueryConn([])
        decision = {"action": "assign", "topic_id": 5}
        action, out_decision, chosen = _dedup_guard(
            conn, "assign", decision, category="사회", parent_topic_id=None, threshold=0.85
        )
        self.assertEqual(action, "assign")
        self.assertIs(out_decision, decision)
        self.assertIsNone(chosen)

    def test_no_duplicate_found_keeps_create(self):
        conn = FakeQueryConn([])
        decision = {"action": "create", "new_title": "새 이슈 제목"}
        action, _, chosen = _dedup_guard(
            conn, "create", decision, category="사회", parent_topic_id=None, threshold=0.85
        )
        self.assertEqual(action, "create")
        self.assertIsNone(chosen)

    def test_duplicate_found_demotes_to_assign(self):
        """유사 제목 기존 토픽이 있으면 create가 assign으로 강등되고 TopicCandidate가 채워져야 한다."""
        conn = FakePipelineConn()
        conn.query = lambda sql, params=None: [{"id": 9, "title": "전세 사기 피해 대책"}]
        conn.query_one = lambda sql, params=None: {
            "id": 9, "category": "사회", "title": "전세 사기 피해 대책", "summary": "요약",
        }
        decision = {"action": "create", "new_title": "전세 사기 피해 대책"}
        action, out_decision, chosen = _dedup_guard(
            conn, "create", decision, category="사회", parent_topic_id=None, threshold=0.85
        )
        self.assertEqual(action, "assign")
        self.assertEqual(out_decision["topic_id"], 9)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.topic_id, 9)
        self.assertEqual(chosen.title, "전세 사기 피해 대책")


# ── 17. pipeline._sanitize_subtopic_title — 명명 규칙 위반 시 재명명 ───────────

class SanitizeSubtopicTitleTests(unittest.TestCase):
    """_sanitize_subtopic_title 이 위반 시 cause로 재명명하고, 통과 시 그대로 두는지 검증."""

    def test_valid_title_left_unchanged(self):
        decision = {"action": "create", "new_title": "전공의 집단사직"}
        _sanitize_subtopic_title(
            decision, event_title="정부 의대 증원 발표", parent_title="의료개혁", cause="전공의 처우 불만"
        )
        self.assertEqual(decision["new_title"], "전공의 집단사직")

    def test_time_segment_title_renamed_with_cause(self):
        decision = {"action": "create", "new_title": "협상 3일차"}
        _sanitize_subtopic_title(
            decision, event_title="이벤트제목", parent_title="부모제목", cause="노사 임금 협상 결렬"
        )
        self.assertEqual(decision["new_title"], "노사 임금 협상 결렬")

    def test_event_copy_title_renamed_with_cause(self):
        decision = {"action": "create", "new_title": "이벤트제목"}
        _sanitize_subtopic_title(
            decision, event_title="이벤트제목", parent_title="부모제목", cause="전세 보증금 미반환"
        )
        self.assertEqual(decision["new_title"], "전세 보증금 미반환")

    def test_violation_kept_when_cause_also_violates(self):
        """cause로도 규칙을 위반하면(예: cause가 비어 있음) 원래 제목을 그대로 둔다."""
        decision = {"action": "create", "new_title": "협상 3일차"}
        _sanitize_subtopic_title(
            decision, event_title="이벤트제목", parent_title="부모제목", cause=""
        )
        self.assertEqual(decision["new_title"], "협상 3일차")


if __name__ == "__main__":
    unittest.main()
