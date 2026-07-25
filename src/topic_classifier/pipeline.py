from __future__ import annotations

import os
import sys

from db import events, topics, topic_causes
from db.topic_causes import TopicCandidate
from embedding import embed_passage, embed_query, to_vector_literal
from openai_client.client import LLMClient
from topic_classifier import naming_rules
from topic_classifier.prompts import (
    build_parent_topic_assignment_prompt,
    build_subtopic_assignment_prompt,
    build_topic_assignment_prompt,
    build_topic_cause_result_prompt,
    build_topic_rollup_prompt,
)
from topic_classifier.settings import (
    ASSIGN_SCORE_THRESHOLD,
    DISTANCE_THRESHOLD,
    LLM_MODEL,
    SUBTOPIC_ASSIGN_SCORE_THRESHOLD,
    SUBTOPIC_MODE,
    SUBTOPIC_SIM_THRESHOLD,
    TOPIC_DUP_SIM_THRESHOLD,
)
from summary_utils import normalize_summary, normalize_topic_title


_client: LLMClient | None = None


FETCH_TOPIC_SUMMARY_EVENTS_SQL = """
SELECT id, title, summary
FROM events
WHERE topic_id = ?
  AND summary IS NOT NULL
  AND btrim(summary) <> ''
ORDER BY created_at ASC, id ASC
"""

# 부모 토픽 단위 요약 롤업용: 해당 부모 아래 모든 서브토픽에 달린 이벤트와,
# (계층 도입 이전에) 부모 토픽에 직접 달려 있던 이벤트를 함께 모은다.
FETCH_PARENT_TOPIC_SUMMARY_EVENTS_SQL = """
SELECT e.id AS id, e.title AS title, e.summary AS summary
FROM events e
JOIN topics t ON t.id = e.topic_id
WHERE (t.parent_topic_id = ? OR t.id = ?)
  AND e.summary IS NOT NULL
  AND btrim(e.summary) <> ''
ORDER BY e.created_at ASC, e.id ASC
"""

# embedding 서브토픽 모드용: 새 이벤트의 저장 임베딩(이벤트 분류 단계에서 기록됨)을 읽는다.
FETCH_EVENT_EMBEDDING_SQL = "SELECT embedding::text AS embedding FROM events WHERE id = ?"

# embedding 서브토픽 모드용: 부모 아래 각 서브토픽의 멤버 이벤트들과의
# 최대 코사인 유사도가 가장 높은 서브토픽 하나를 반환한다 (greedy-max).
FIND_NEAREST_SUBTOPIC_SQL = """
SELECT e.topic_id AS leaf_id, MAX(1 - (e.embedding <=> ?::vector)) AS sim
FROM events e
JOIN topics t ON t.id = e.topic_id
WHERE t.parent_topic_id = ?
  AND e.embedding IS NOT NULL
GROUP BY e.topic_id
ORDER BY sim DESC
LIMIT 1
"""

FETCH_TOPIC_BY_ID_SQL = "SELECT id, category, title, summary FROM topics WHERE id = ?"


def _get_client(model: str = LLM_MODEL) -> LLMClient:
    """같은 실행 안에서는 모델별 LLM 클라이언트를 재사용한다."""
    global _client
    if _client is None or _client.model != model:
        _client = LLMClient(model=model)
    return _client


def _call_json(client: LLMClient, prompt: str, required_keys: set[str]) -> dict:
    """LLM에 JSON 응답을 요청하고 필수 키를 검증한다."""
    data = client.request_json(prompt, required_keys=required_keys, temperature=0)
    return data


def _reason_rejects_assignment(reason: str | None) -> bool:
    """Detect contradictory assign decisions whose reason says the topic is unrelated."""
    normalized = " ".join(str(reason or "").split()).lower()
    if not normalized:
        return False
    negative_markers = (
        "무관",
        "관련성이 없어",
        "관련성이 없다",
        "연관성이 없어",
        "연관성이 없다",
        "직접적인 연관성이 없어",
        "직접적인 연관성이 없다",
        "일치하지 않",
        "후보가 없음",
        "새로운 사건",
        "새로운 사안",
    )
    return any(marker in normalized for marker in negative_markers)


def _load_decision_score(decision: dict) -> float:
    try:
        return float(decision.get("score", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _load_topic_summary_events(conn, topic_id: int) -> list[dict]:
    return [
        {
            "event_id": row["id"],
            "title": row["title"],
            "summary": normalize_summary(row["summary"]),
        }
        for row in conn.query(FETCH_TOPIC_SUMMARY_EVENTS_SQL, (topic_id,))
        if normalize_summary(row["summary"])
    ]


def _load_parent_summary_events(conn, parent_topic_id: int) -> list[dict]:
    """부모 토픽 롤업용으로 그 아래 서브토픽 이벤트들을 모은다."""
    return [
        {
            "event_id": row["id"],
            "title": row["title"],
            "summary": normalize_summary(row["summary"]),
        }
        for row in conn.query(
            FETCH_PARENT_TOPIC_SUMMARY_EVENTS_SQL,
            (parent_topic_id, parent_topic_id),
        )
        if normalize_summary(row["summary"])
    ]


def _resolve_action(
    client,
    candidates,
    build_prompt,
    *,
    fallback_title,
    fallback_reason="검색 후보 없음",
    score_threshold=ASSIGN_SCORE_THRESHOLD,
):
    """assign-or-create 결정을 공통 처리한다.

    후보가 있으면 LLM 배정 판단을, 후보가 0개면 LLM 호출을 생략하고 즉시 create로
    결정한다(비용 전략 핵심). 이어서 점수/사유 가드레일을 적용해 모순된 assign을
    create로 강등한 뒤 (action, decision)을 반환한다. 평면·계층 분류가 모두 재사용한다.
    score_threshold로 경로별(부모/서브) assign 점수 문턱을 달리 줄 수 있다.
    """
    if candidates:
        decision = _call_json(client, build_prompt(), required_keys={"action"})
    else:
        decision = {
            "action": "create",
            "new_title": fallback_title,
            "score": 0.0,
            "reason": fallback_reason,
        }

    action = decision["action"]
    if action not in {"assign", "create"}:
        raise ValueError(f"Invalid topic action from LLM: {action!r}")
    if action == "assign" and (
        _load_decision_score(decision) < score_threshold
        or _reason_rejects_assignment(decision.get("reason"))
    ):
        decision = {
            "action": "create",
            "new_title": fallback_title,
            "score": _load_decision_score(decision),
            "reason": (
                "기존 후보와 직접 상관관계가 없다는 배정 사유가 감지되어 "
                "새 토픽으로 생성"
            ),
        }
        action = "create"
    if action == "create":
        # LLM이 new_title을 누락하면 KeyError, null이면 "None" 제목 토픽이 생기므로
        # 모든 create 경로에서 fallback_title로 보정한다.
        decision["new_title"] = str(decision.get("new_title") or fallback_title).strip()
    return action, decision


def _assign_subtopic_by_embedding(conn, ev, parent_id, cause: str = ""):
    """이벤트 임베딩 코사인 최근접(greedy-max)으로 서브토픽 assign-or-create를 결정한다.

    LLM 호출 없이 pgvector 검색만 사용하므로 결정론적이다. 부모가 이번에 새로
    생성되어 parent_id가 None이면 서브토픽 후보가 없으므로 즉시 create.
    create 시 제목은 cause(원인 명사구)를 우선 사용한다 — ev.title을 그대로 쓰면
    서브토픽이 이벤트의 1:1 별칭이 되어 명명 규칙 S-1(이벤트복사형)을 위반한다.
    cause가 없으면(호출부에서 신규 부모라 빈 문자열을 넘긴 경우 등) ev.title로 대체한다.
    반환: (action, decision, chosen) — assign이면 chosen은 TopicCandidate.
    """
    def _create(reason):
        decision = {
            "action": "create",
            "new_title": cause or ev.title,
            "score": 0.0,
            "reason": reason,
        }
        return "create", decision, None

    if parent_id is None:
        return _create("새 부모 토픽 — 서브토픽 후보 없음")
    row = conn.query_one(FETCH_EVENT_EMBEDDING_SQL, (ev.id,))
    embedding = row["embedding"] if row else None
    if not embedding:
        return _create("이벤트 임베딩 없음 — 새 서브토픽 생성")
    nearest = conn.query_one(FIND_NEAREST_SUBTOPIC_SQL, (embedding, parent_id))
    if nearest is None:
        return _create("부모 스코프 내 서브토픽 없음")
    sim = float(nearest["sim"])
    # NaN(영벡터 임베딩 등) 은 비교가 항상 False가 되므로 not >= 형태로 create에 흡수한다.
    if not (sim >= SUBTOPIC_SIM_THRESHOLD):
        return _create(
            f"최근접 서브토픽 유사도 {sim:.3f} < {SUBTOPIC_SIM_THRESHOLD} — 새 갈래로 생성"
        )
    leaf = conn.query_one(FETCH_TOPIC_BY_ID_SQL, (nearest["leaf_id"],))
    chosen = TopicCandidate(
        topic_id=leaf["id"],
        category=leaf["category"],
        title=leaf["title"],
        summary=leaf["summary"],
        distance=1.0 - sim,
        cause_texts=[],
    )
    decision = {
        "action": "assign",
        "topic_id": leaf["id"],
        "score": sim,
        "reason": f"이벤트 임베딩 코사인 유사도 {sim:.3f}",
    }
    return "assign", decision, chosen


def _select_candidate(decision, candidates):
    """LLM이 고른 topic_id가 실제 후보 집합에 있는지 검증하고 해당 후보를 반환한다."""
    topic_id = int(decision["topic_id"])
    candidate_ids = {candidate.topic_id for candidate in candidates}
    if topic_id not in candidate_ids:
        raise ValueError(f"LLM selected unknown topic_id={topic_id}.")
    return next(candidate for candidate in candidates if candidate.topic_id == topic_id)


def _dedup_guard(conn, action, decision, *, category, parent_topic_id, threshold):
    """create 결정 시 동일 스코프 내 유사 제목 기존 토픽이 있으면 assign으로 강등한다.

    R-T1(중복·고유사 토픽 쌍)의 예방 가드다. cause 임베딩 후보 검색이 거리 임계값을
    살짝 벗어나 후보를 놓쳤거나 LLM이 잘못 create를 골랐을 때의 안전망 역할을 한다.
    candidates 목록에 없는 토픽으로도 병합할 수 있어야 하므로 _select_candidate를
    거치지 않고 TopicCandidate를 직접 구성해 반환한다.
    반환: (action, decision, chosen) — 강등되면 chosen에 TopicCandidate, 아니면 chosen=None.
    """
    if action != "create":
        return action, decision, None
    title = normalize_topic_title(str(decision.get("new_title") or ""))
    dup = topics.find_duplicate_topic(conn, category, title, parent_topic_id, threshold)
    if dup is None:
        return action, decision, None
    row = conn.query_one(FETCH_TOPIC_BY_ID_SQL, (dup["id"],))
    if row is None:
        return action, decision, None
    chosen = TopicCandidate(
        topic_id=row["id"],
        category=row["category"],
        title=row["title"],
        summary=row["summary"],
        distance=1.0 - dup["similarity"],
        cause_texts=[],
    )
    new_decision = {
        "action": "assign",
        "topic_id": row["id"],
        "score": dup["similarity"],
        "reason": f"제목 유사도 {dup['similarity']:.3f} — 중복 생성 방지로 기존 토픽에 병합",
    }
    return "assign", new_decision, chosen


def _sanitize_subtopic_title(decision: dict, *, event_title: str, parent_title: str, cause: str) -> None:
    """서브토픽 create 제목이 명명 4규칙(S-1~S-4)을 위반하면 cause 기반 명사구로
    재명명을 시도한다. decision을 in-place로 갱신한다.

    cause로도 규칙을 위반하면(드문 경우) 원래 제목을 그대로 둔다 — 완전 차단보다는
    완화가 목적이며, 잘못된 강제 치환으로 더 나쁜 제목을 만들지 않기 위함이다.
    """
    title = str(decision.get("new_title") or "").strip()
    violation = naming_rules.violates_subtopic_naming(
        title, event_title=event_title, parent_title=parent_title
    )
    if violation is None:
        return
    candidate = normalize_topic_title(cause) if cause else ""
    if candidate and naming_rules.violates_subtopic_naming(
        candidate, event_title=event_title, parent_title=parent_title
    ) is None:
        decision["new_title"] = candidate
        decision["reason"] = (
            f"{decision.get('reason', '')} (명명 규칙 위반 {violation} — cause 기반으로 재명명)"
        ).strip()


def _link_chain(conn, leaf_topic_id: int, event_id: int) -> None:
    """leaf 토픽(평면 토픽 또는 서브토픽) 내부 이벤트 prev/next 체인을 연결한다."""
    prev = events.find_prev_event(conn, leaf_topic_id, event_id)
    if prev is not None:
        prev_id, next_id = prev["id"], prev["next_event_id"]
    else:
        prev_id, next_id = None, events.find_next_event_id(conn, leaf_topic_id, event_id)
    events.link_into_chain(conn, event_id, prev_id, next_id)


def _append_event_summary(
    items: list[dict],
    *,
    event_id: int,
    title: str,
    summary: str,
) -> list[dict]:
    seen_ids = {int(item["event_id"]) for item in items if item.get("event_id") is not None}
    normalized = normalize_summary(summary)
    if event_id not in seen_ids and normalized:
        items.append({"event_id": event_id, "title": title, "summary": normalized})
    return items


def _generate_topic_update(
    client: LLMClient,
    *,
    topic_title: str,
    event_summaries: list[dict],
    fallback_summary: str,
) -> dict:
    fallback_summary = normalize_summary(fallback_summary)
    try:
        data = client.request_json(
            build_topic_rollup_prompt(topic_title, event_summaries),
            required_keys={"title", "summary"},
            temperature=0,
        )
        title = normalize_topic_title(data.get("title") or topic_title)
        summary = normalize_summary(data.get("summary"))
    except Exception as exc:
        print(f"[topic] summary rollup failed: {exc}", file=sys.stderr)
        title = normalize_topic_title(topic_title)
        summary = ""
    return {
        "title": title or normalize_topic_title(fallback_summary) or "제목 없음",
        "summary": summary or fallback_summary,
    }


def _assign_flat(conn, client, ev, cause: str, result: str, top_k: int) -> str:
    """평면(단일 레벨) 토픽 배정. 기존 동작과 동일하며 헬퍼로만 정리했다."""
    # 토픽 후보 검색은 cause 임베딩으로 먼저 좁히고, 최종 판단만 LLM에 맡긴다.
    cause_query_embedding = to_vector_literal(embed_query(cause))
    candidates = topic_causes.search_candidates(
        conn,
        cause_query_embedding,
        ev.category,
        DISTANCE_THRESHOLD,
        top_k,
    )
    action, decision = _resolve_action(
        client,
        candidates,
        lambda: build_topic_assignment_prompt(ev.title, ev.summary, cause, result, candidates),
        fallback_title=ev.title,
    )
    action, decision, dedup_chosen = _dedup_guard(
        conn, action, decision,
        category=ev.category, parent_topic_id=None, threshold=TOPIC_DUP_SIM_THRESHOLD,
    )

    result_embedding = to_vector_literal(embed_passage(result))
    cause_embedding = (
        to_vector_literal(embed_passage(cause)) if action == "create" else None
    )

    topic_update = None
    chosen = dedup_chosen
    if action == "assign":
        if chosen is None:
            chosen = _select_candidate(decision, candidates)
        topic_update = _generate_topic_update(
            client,
            topic_title=chosen.title,
            event_summaries=_append_event_summary(
                _load_topic_summary_events(conn, chosen.topic_id),
                event_id=ev.id,
                title=ev.title,
                summary=ev.summary,
            ),
            fallback_summary=f"{chosen.summary} {ev.summary}",
        )

    with conn.transaction():
        # 5-1. 토픽 확정
        if action == "create":
            topic_id = topics.create_topic(
                conn,
                ev.category,
                str(decision["new_title"]).strip(),
                normalize_summary(ev.summary),
            )
        else:
            topic_id = chosen.topic_id
            topics.update_topic(
                conn,
                topic_id,
                str(topic_update["title"]).strip(),
                str(topic_update["summary"]).strip(),
            )

        # 5-2. 이벤트 ↔ 토픽 매핑 (배정 근거 reason 함께 기록)
        events.assign_topic(conn, ev.id, topic_id, decision.get("reason"))
        topic_causes.add_cause(conn, topic_id, result, result_embedding)
        if action == "create":
            # 새 토픽은 원인과 결과를 모두 저장해 다음 이벤트 검색 품질을 높인다.
            topic_causes.add_cause(conn, topic_id, cause, cause_embedding)

        _link_chain(conn, topic_id, ev.id)

    return "create" if action == "create" else f"assign {topic_id}"


def _assign_hierarchical(
    conn, client, ev, cause: str, result: str, top_k: int, subtopic_top_k: int
) -> str:
    """계층(부모 토픽 → 서브토픽) 배정.

    1) 부모는 최상위 토픽(roots_only)으로만 후보를 좁혀 assign-or-create.
    2) 부모가 기존 토픽이면 그 부모 스코프 안에서 서브토픽 assign-or-create.
       부모가 새로 생성되면 그 아래 서브토픽이 없으므로 후보 0개 → 즉시 서브토픽 create.
    events.topic_id는 leaf(서브토픽)만 참조하고, 체인도 leaf 단위로 연결한다.
    """
    cause_query_embedding = to_vector_literal(embed_query(cause))

    # 1) 부모(최상위) 토픽 결정
    parent_candidates = topic_causes.search_candidates(
        conn,
        cause_query_embedding,
        ev.category,
        DISTANCE_THRESHOLD,
        top_k,
        roots_only=True,
    )
    # PARENT_PROMPT_MODE 환경변수로 A/B 테스트용 프롬프트를 선택한다.
    # "strict" → 기존 엄격 프롬프트(build_topic_assignment_prompt),
    # 그 외(기본값 "broad") → 광의 테마 프롬프트(build_parent_topic_assignment_prompt).
    # 이 변수는 .env 에 넣지 않으므로, 프로세스 환경변수 주입이 그대로 유효하다.
    parent_prompt_mode = os.getenv("PARENT_PROMPT_MODE", "broad")
    parent_action, parent_decision = _resolve_action(
        client,
        parent_candidates,
        lambda: (
            build_topic_assignment_prompt(ev.title, ev.summary, cause, result, parent_candidates)
            if parent_prompt_mode == "strict"
            else build_parent_topic_assignment_prompt(ev.title, ev.summary, cause, result, parent_candidates)
        ),
        # 부모 폴백 제목은 이벤트 제목(30자 절단 문장)이 아니라 cause 명사구를 쓴다.
        # 부모는 여러 사건을 담는 이슈 단위이므로 명사구가 제목으로 더 적합하다.
        fallback_title=cause or ev.title,
    )
    parent_action, parent_decision, parent_dedup_chosen = _dedup_guard(
        conn, parent_action, parent_decision,
        category=ev.category, parent_topic_id=None, threshold=TOPIC_DUP_SIM_THRESHOLD,
    )

    # 2) 서브토픽 후보 검색 — 부모가 기존 토픽일 때만 부모 스코프로 좁혀 검색한다.
    #    (embedding 모드는 cause 후보 검색 대신 이벤트 임베딩 최근접을 쓰므로 생략)
    parent_chosen = None
    sub_candidates: list = []
    if parent_action == "assign":
        parent_chosen = parent_dedup_chosen or _select_candidate(parent_decision, parent_candidates)
        parent_title_ctx = parent_chosen.title
        parent_summary_ctx = parent_chosen.summary
        if SUBTOPIC_MODE != "embedding":
            sub_candidates = topic_causes.search_candidates(
                conn,
                cause_query_embedding,
                ev.category,
                DISTANCE_THRESHOLD,
                subtopic_top_k,
                parent_topic_id=parent_chosen.topic_id,
            )
    else:
        # 새 부모: 서브토픽 후보가 없으므로 LLM 호출을 생략하고 즉시 서브토픽 create.
        parent_title_ctx = str(parent_decision["new_title"]).strip()
        parent_summary_ctx = normalize_summary(ev.summary)

    # 서브토픽 폴백 제목은 부모가 기존 토픽일 때만 cause 명사구를 우선한다. 부모까지
    # 이번에 함께 새로 만들어지는 경우(parent_chosen is None) cause를 쓰면 부모와 서브가
    # 같은 문자열이 되어 S-4(부모 동일범위)를 유발하므로, 그 경우는 기존대로 ev.title을 쓴다.
    sub_fallback_title = (cause or ev.title) if parent_chosen is not None else ev.title

    sub_chosen = None
    if SUBTOPIC_MODE == "embedding":
        # 서브토픽은 이벤트 임베딩 최근접(greedy-max)으로 결정 — LLM 배정 판단 없음.
        sub_action, sub_decision, sub_chosen = _assign_subtopic_by_embedding(
            conn, ev, parent_chosen.topic_id if parent_chosen else None,
            cause if parent_chosen is not None else "",
        )
    else:
        sub_action, sub_decision = _resolve_action(
            client,
            sub_candidates,
            lambda: build_subtopic_assignment_prompt(
                parent_title_ctx,
                parent_summary_ctx,
                ev.title,
                ev.summary,
                cause,
                result,
                sub_candidates,
            ),
            fallback_title=sub_fallback_title,
            # 서브토픽은 과병합 방지를 위해 부모보다 높은 문턱을 쓸 수 있다.
            score_threshold=SUBTOPIC_ASSIGN_SCORE_THRESHOLD,
        )
        if sub_action == "assign":
            sub_chosen = _select_candidate(sub_decision, sub_candidates)

    if sub_action == "create":
        # 명명 4규칙(S-1~S-4) 위반 시 cause 기반으로 재명명 시도(코드 가드).
        _sanitize_subtopic_title(
            sub_decision, event_title=ev.title, parent_title=parent_title_ctx, cause=cause,
        )
    if parent_chosen is not None:
        # 서브토픽 중복 방지: 부모가 기존 토픽일 때만 검사 가능(신규 부모는 서브가 아직 없음).
        sub_action, sub_decision, dedup_sub_chosen = _dedup_guard(
            conn, sub_action, sub_decision,
            category=ev.category, parent_topic_id=parent_chosen.topic_id,
            threshold=TOPIC_DUP_SIM_THRESHOLD,
        )
        if dedup_sub_chosen is not None:
            sub_chosen = dedup_sub_chosen

    # 임베딩: result는 항상 저장, cause는 부모·서브 중 하나라도 새로 생성할 때만 필요.
    result_embedding = to_vector_literal(embed_passage(result))
    need_cause = parent_action == "create" or sub_action == "create"
    cause_embedding = to_vector_literal(embed_passage(cause)) if need_cause else None

    # 롤업 LLM 호출은 트랜잭션 밖에서 수행한다(네트워크 호출을 트랜잭션에 가두지 않음).
    parent_update = None
    if parent_action == "assign":
        parent_update = _generate_topic_update(
            client,
            topic_title=parent_chosen.title,
            event_summaries=_append_event_summary(
                _load_parent_summary_events(conn, parent_chosen.topic_id),
                event_id=ev.id,
                title=ev.title,
                summary=ev.summary,
            ),
            fallback_summary=f"{parent_chosen.summary} {ev.summary}",
        )

    sub_update = None
    if sub_action == "assign":
        sub_update = _generate_topic_update(
            client,
            topic_title=sub_chosen.title,
            event_summaries=_append_event_summary(
                _load_topic_summary_events(conn, sub_chosen.topic_id),
                event_id=ev.id,
                title=ev.title,
                summary=ev.summary,
            ),
            fallback_summary=f"{sub_chosen.summary} {ev.summary}",
        )

    with conn.transaction():
        # 부모 토픽 확정 (parent_topic_id IS NULL인 최상위 토픽)
        if parent_action == "create":
            parent_id = topics.create_topic(
                conn,
                ev.category,
                parent_title_ctx,
                parent_summary_ctx,
                None,
            )
            topic_causes.add_cause(conn, parent_id, result, result_embedding)
            topic_causes.add_cause(conn, parent_id, cause, cause_embedding)
        else:
            parent_id = parent_chosen.topic_id
            topics.update_topic(
                conn,
                parent_id,
                str(parent_update["title"]).strip(),
                str(parent_update["summary"]).strip(),
            )
            topic_causes.add_cause(conn, parent_id, result, result_embedding)

        # 서브토픽(leaf) 확정 — 위에서 정해진 parent_id 아래에 둔다.
        if sub_action == "create":
            sub_id = topics.create_topic(
                conn,
                ev.category,
                str(sub_decision["new_title"]).strip(),
                normalize_summary(ev.summary),
                parent_id,
            )
            topic_causes.add_cause(conn, sub_id, result, result_embedding)
            topic_causes.add_cause(conn, sub_id, cause, cause_embedding)
        else:
            sub_id = sub_chosen.topic_id
            topics.update_topic(
                conn,
                sub_id,
                str(sub_update["title"]).strip(),
                str(sub_update["summary"]).strip(),
            )
            topic_causes.add_cause(conn, sub_id, result, result_embedding)

        # events.topic_id는 leaf(서브토픽)만 참조하고, 체인도 leaf 단위로 연결한다.
        events.assign_topic(conn, ev.id, sub_id, sub_decision.get("reason"))
        _link_chain(conn, sub_id, ev.id)

    parent_label = "create" if parent_action == "create" else f"assign {parent_id}"
    sub_label = "create" if sub_action == "create" else f"assign {sub_id}"
    return f"parent {parent_label} / sub {sub_label} (leaf {sub_id})"


def run(
    conn,
    min_net: int,
    batch_size: int,
    top_k: int,
    llm_model: str,
    *,
    subtopics_enabled: bool = False,
    subtopic_top_k: int | None = None,
) -> int:
    """미분류 이벤트를 기존 토픽에 배정하거나 새 토픽으로 생성한다.

    subtopics_enabled=True면 부모 토픽 → 서브토픽 2단계 계층으로 배정한다.
    subtopic_top_k가 None이면 top_k와 동일하게 동작한다.
    """
    client = _get_client(llm_model)
    if subtopic_top_k is None:
        subtopic_top_k = top_k

    batch = events.fetch_unassigned(conn, min_net, batch_size)
    if not batch:
        print("[topic] 미배정 이벤트 없음")
        return 0

    mode = "계층(서브토픽)" if subtopics_enabled else "평면"
    print(f"[topic] 배치 시작: {len(batch)}건 (모드: {mode})")
    processed = 0
    for ev in batch:
        try:
            print(f"[topic] event {ev.id} 처리 중: {ev.title}")

            cr = _call_json(
                client,
                build_topic_cause_result_prompt(ev.embedding_text),
                required_keys={"cause", "result"},
            )
            cause = str(cr["cause"]).strip()
            result = str(cr["result"]).strip()
            if not cause or not result:
                raise ValueError("LLM returned empty cause/result.")

            if subtopics_enabled:
                label = _assign_hierarchical(
                    conn, client, ev, cause, result, top_k, subtopic_top_k
                )
            else:
                label = _assign_flat(conn, client, ev, cause, result, top_k)

            processed += 1
            print(f"[topic] event {ev.id} -> {label}")

        except Exception as exc:
            # FETCH_UNASSIGNED가 created_at ASC라 break하면 결정적으로 실패하는
            # 이벤트 하나가 이후 배치 전체를 영구 정지시킨다. 이벤트 분류기처럼
            # 해당 이벤트만 건너뛰고 나머지를 계속 처리한다.
            print(f"[topic] event {ev.id} failed: {exc}", file=sys.stderr)
            continue

    return processed
