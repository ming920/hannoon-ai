from __future__ import annotations

import argparse
import json
import os
import re
import sys

from collector.storage import ensure_db
from db import events
from embedding import embed_passage, embed_query, to_vector_literal
from event_classifier.prompts import (
    build_event_assignment_prompt,
    build_event_summary_prompt,
    build_extract_main_event_prompt,
)
from event_classifier.settings import (
    ASSIGN_SCORE_THRESHOLD,
    BATCH_SIZE,
    DISTANCE_THRESHOLD,
    LLM_MODEL,
    TOP_K,
)
from openai_client.client import LLMClient, parse_json_object
from summary_utils import normalize_summary


_client: LLMClient | None = None


def _get_client(model: str = LLM_MODEL) -> LLMClient:
    """같은 실행 안에서는 모델별 LLM 클라이언트를 재사용한다."""
    global _client
    if _client is None or _client.model != model:
        _client = LLMClient(model=model)
    return _client


def _parse_json(response: str) -> dict:
    """LLM 응답을 JSON 객체로 정규화한다."""
    return parse_json_object(response.strip())


def _reason_rejects_assignment(reason: str | None) -> bool:
    """Detect contradictory assign decisions whose reason says the event is unrelated."""
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
        "동일한 사건으로 볼 수 없",
        "새로운 사건",
        "새로운 사안",
    )
    return any(marker in normalized for marker in negative_markers)


def _load_decision_score(decision: dict) -> float:
    try:
        return float(decision.get("score", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _get_first_sentences(text: str, limit: int = 4) -> str:
    """이벤트 추출과 임베딩에 사용할 기사 앞부분을 잘라낸다."""
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?。！？])\s+", " ".join(text.split()))
    selected = [sentence.strip() for sentence in sentences if sentence.strip()][:limit]
    return " ".join(selected)


def _join_labeled_text(*items: tuple[str, str | None]) -> str:
    lines = []
    for label, value in items:
        text = " ".join(str(value or "").split())
        if text:
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def _build_event_source_text(
    title: str,
    ai_summary: str,
    article_excerpt: str,
) -> str:
    return _join_labeled_text(
        ("title", title),
        ("ai_summary", ai_summary),
        ("article_excerpt", article_excerpt),
    )


def _build_event_embedding_text(
    title: str,
    ai_summary: str,
    main_event: str,
    article_excerpt: str,
) -> str:
    return _join_labeled_text(
        ("title", title),
        ("ai_summary", ai_summary),
        ("main_event", main_event),
        ("article_excerpt", "" if ai_summary else article_excerpt),
    )


FETCH_EVENT_SUMMARY_ARTICLES_SQL = """
SELECT a.id AS article_id, a.title AS title, r.summary AS summary
FROM event_articles ea
JOIN articles a ON a.id = ea.article_id
JOIN article_ai_results r ON r.article_id = a.id
WHERE ea.event_id = ?
  AND r.summary IS NOT NULL
  AND btrim(r.summary) <> ''
ORDER BY a.id ASC
"""


# articles 테이블의 벡터 및 대표 사건 데이터를 보완하는 쿼리
UPDATE_ARTICLE_ANALYSIS_SQL = """
UPDATE articles
SET embedding = ?::vector,
    core_content = ?,
    updated_at = now()
WHERE id = ?
"""

# 처리가 끝난 기사의 AI 분석 상태를 event_assigned로 변경하는 쿼리
UPDATE_AI_RESULT_STATUS_SQL = """
UPDATE article_ai_results
SET status = ?::public.article_ai_status,
    updated_at = now()
WHERE article_id = ?
"""


def _load_event_summary_articles(conn, event_id: int) -> list[dict]:
    return [
        {
            "article_id": row["article_id"],
            "title": row["title"],
            "summary": normalize_summary(row["summary"]),
        }
        for row in conn.query(FETCH_EVENT_SUMMARY_ARTICLES_SQL, (event_id,))
        if normalize_summary(row["summary"])
    ]


def _append_article_summary(
    items: list[dict],
    *,
    article_id: int,
    title: str,
    summary: str,
) -> list[dict]:
    seen_ids = {int(item["article_id"]) for item in items if item.get("article_id") is not None}
    normalized = normalize_summary(summary)
    if article_id not in seen_ids and normalized:
        items.append({"article_id": article_id, "title": title, "summary": normalized})
    return items


def _generate_event_summary(
    client: LLMClient,
    *,
    event_title: str,
    article_summaries: list[dict],
    fallback_summary: str,
) -> str:
    fallback = normalize_summary(fallback_summary)
    if len(article_summaries) <= 1:
        return normalize_summary(article_summaries[0]["summary"]) if article_summaries else fallback
    try:
        data = client.request_json(
            build_event_summary_prompt(event_title, article_summaries),
            required_keys={"summary"},
            temperature=0,
        )
        summary = normalize_summary(data.get("summary"))
    except Exception as exc:
        print(f"[event] summary rollup failed: {exc}", file=sys.stderr)
        summary = ""
    return summary or fallback


def process_event_classification(
    database_url: str | None,
    single_article_id: int | None = None,
    *,
    llm_model: str = LLM_MODEL,
) -> int:
    """분석 완료 기사들을 이벤트에 배정하거나 새 이벤트로 생성한다."""
    client = _get_client(llm_model)
    processed_count = 0

    with ensure_db("data/news.db", database_url=database_url) as conn:
        if single_article_id is not None:
            row = conn.query_one(
                """
                SELECT a.id, a.title, a.content, a.published_at, a.category,
                       a.article_image_url, a.bias_type, r.summary AS ai_summary
                FROM articles a
                JOIN article_ai_results r ON a.id = r.article_id
                WHERE a.id = ?
                """,
                (single_article_id,),
            )
            done_articles = [row] if row else []
        else:
            done_articles = conn.query(
                """
                SELECT a.id, a.title, a.content, a.published_at, a.category,
                       a.article_image_url, a.bias_type, r.summary AS ai_summary
                FROM articles a
                JOIN article_ai_results r ON a.id = r.article_id
                WHERE r.status = 'done'
                  AND a.content IS NOT NULL
                  AND a.content != ''
                ORDER BY a.published_at ASC
                LIMIT ?
                """,
                (BATCH_SIZE,),
            )

        print(f"[event] 처리 대상 기사: {len(done_articles)}건")

        for art in done_articles:
            art_id = art["id"]
            title = str(art["title"] or "").strip()
            full_content = art["content"]
            category = art["category"]
            img_url = art["article_image_url"]
            ai_summary = str(art["ai_summary"] or "").strip()

            trimmed_content = _get_first_sentences(full_content)
            event_source_text = _build_event_source_text(
                title,
                ai_summary,
                trimmed_content,
            )
            if not event_source_text:
                continue

            try:
                # 1차로 기사 앞부분에서 이벤트를 한 문장으로 압축한다.
                res_extract = _parse_json(
                    client.request(
                        build_extract_main_event_prompt(event_source_text),
                        response_format={"type": "json_object"},
                        temperature=0,
                    )
                )
                main_event = str(res_extract.get("main_event") or "").strip()
                if not main_event:
                    raise ValueError("LLM did not return main_event.")

                event_embedding_text = _build_event_embedding_text(
                    title,
                    ai_summary,
                    main_event,
                    trimmed_content,
                )

                # 기존 이벤트 후보는 LLM이 아니라 pgvector 거리 검색으로 먼저 좁힌다.
                article_query_embedding = to_vector_literal(embed_query(event_embedding_text))
                candidates = events.search_candidate_events(
                    conn,
                    article_query_embedding,
                    str(art["published_at"]),
                    DISTANCE_THRESHOLD,
                    TOP_K,
                )
                article_embedding = to_vector_literal(embed_passage(event_embedding_text))
                if candidates:
                    decision = _parse_json(
                        client.request(
                            build_event_assignment_prompt(
                                title,
                                ai_summary,
                                main_event,
                                category,
                                candidates,
                            ),
                            response_format={"type": "json_object"},
                            temperature=0,
                        )
                    )
                else:
                    decision = {
                        "action": "create",
                        "event_title": main_event,
                        "score": 0.0,
                        "reason": "검색 후보 없음",
                    }
                action = decision.get("action")
                if action not in {"assign", "create"}:
                    raise ValueError(f"Invalid event action from LLM: {action!r}")
                if action == "assign" and (
                    _load_decision_score(decision) < ASSIGN_SCORE_THRESHOLD
                    or _reason_rejects_assignment(decision.get("reason"))
                ):
                    decision = {
                        "action": "create",
                        "event_title": main_event,
                        "score": _load_decision_score(decision),
                        "reason": (
                            "기존 후보와 같은 구체적 이벤트라는 확신이 낮아 "
                            "새 이벤트로 생성"
                        ),
                    }
                    action = "create"

                event_id = None
                event_summary = None
                if action == "assign":
                    event_id = int(decision["event_id"])
                    candidate_ids = {int(candidate["id"]) for candidate in candidates}
                    if event_id not in candidate_ids:
                        raise ValueError(f"LLM selected unknown event_id={event_id}.")
                    selected_event = next(
                        candidate for candidate in candidates if int(candidate["id"]) == event_id
                    )
                    event_summary = _generate_event_summary(
                        client,
                        event_title=str(selected_event.get("title") or title),
                        article_summaries=_append_article_summary(
                            _load_event_summary_articles(conn, event_id),
                            article_id=art_id,
                            title=title,
                            summary=ai_summary,
                        ),
                        fallback_summary=(
                            str(selected_event.get("summary") or "")
                            or ai_summary
                            or main_event
                        ),
                    )

                with conn.transaction():
                    # 기사 자체에도 같은 임베딩과 핵심 사건 문장을 저장해 후속 분석에서 재사용한다.
                    conn.execute(UPDATE_ARTICLE_ANALYSIS_SQL, (article_embedding, main_event, art_id))

                    if action == "assign":
                        assert event_id is not None
                        assert event_summary is not None
                        reason = str(decision.get("reason") or "llm_event_assignment")
                        events.link_article_to_event(
                            conn,
                            event_id,
                            art_id,
                            reason,
                        )
                        events.update_event_summary(conn, event_id, event_summary)
                        msg = f"assigned to event {event_id}"
                    else:
                        generated_title = str(
                            decision.get("event_title") or main_event
                        ).strip()
                        new_id = events.create_new_event(
                            conn,
                            category=category,
                            title=generated_title,
                            summary=normalize_summary(ai_summary or main_event),
                            core_content=main_event,
                            embedding_text=event_embedding_text,
                            embedding_literal=article_embedding,
                            event_image_url=img_url,
                        )
                        events.link_article_to_event(
                            conn,
                            new_id,
                            art_id,
                            str(decision.get("reason") or "new_event"),
                        )
                        msg = f"created event {new_id}"

                    conn.execute(UPDATE_AI_RESULT_STATUS_SQL, ("event_assigned", art_id))

                print(
                    json.dumps(
                        {
                            "article_id": art_id,
                            "main_event": main_event,
                            "result": msg,
                        },
                        ensure_ascii=False,
                    )
                )
                processed_count += 1

            except Exception as exc:
                print(f"[event] article {art_id} failed: {exc}", file=sys.stderr)
                continue

    return processed_count


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM으로 분석 완료 기사를 이벤트에 배정")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres/Supabase DB URL. 기본값은 DATABASE_URL 환경 변수",
    )
    parser.add_argument("--article-id", type=int, default=None, help="특정 기사 id 하나만 처리")
    parser.add_argument("--llm-model", default=LLM_MODEL, help="이벤트 배정에 사용할 LLM 모델")
    args = parser.parse_args()

    if not args.database_url:
        print(
            "[event] Postgres connection is required. Set --database-url or DATABASE_URL.",
            file=sys.stderr,
        )
        return 1

    process_event_classification(
        database_url=args.database_url,
        single_article_id=args.article_id,
        llm_model=args.llm_model,
    )
    return 0
