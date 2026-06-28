from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

from collector.storage import ensure_db
from event_classifier.prompts import build_event_summary_prompt
from openai_client.client import LLMClient
from summary_utils import TOPIC_TITLE_MAX_CHARS, normalize_summary, normalize_topic_title
from topic_classifier.prompts import build_topic_rollup_prompt


BAD_ARTICLE_SUMMARIES_SQL = """
SELECT r.article_id, r.summary
FROM article_ai_results r
WHERE r.summary IS NOT NULL
  AND (
      length(btrim(r.summary)) > 700
      OR length(btrim(r.summary)) < 80
  )
ORDER BY r.updated_at DESC NULLS LAST, r.article_id DESC
LIMIT ?
"""

UPDATE_ARTICLE_SUMMARY_SQL = """
UPDATE article_ai_results
SET summary = ?,
    updated_at = now()
WHERE article_id = ?
"""

EVENT_SUMMARY_TARGETS_SQL = """
WITH mapped_counts AS (
    SELECT e.id AS event_id,
           count(DISTINCT ea.article_id) AS mapped_count
    FROM events e
    LEFT JOIN event_articles ea ON ea.event_id = e.id
    GROUP BY e.id
)
SELECT e.id, e.title, e.summary, mapped_counts.mapped_count
FROM events e
JOIN mapped_counts ON mapped_counts.event_id = e.id
WHERE e.summary IS NOT NULL
  AND (
      length(btrim(e.summary)) > 700
      OR length(btrim(e.summary)) < 80
      OR (
          mapped_counts.mapped_count >= 2
          AND EXISTS (
              SELECT 1
              FROM event_articles ea
              JOIN article_ai_results r ON r.article_id = ea.article_id
              WHERE ea.event_id = e.id
                AND btrim(r.summary) = btrim(e.summary)
          )
      )
  )
ORDER BY e.updated_at DESC NULLS LAST, e.id DESC
LIMIT ?
"""

UPDATE_EVENT_SUMMARY_SQL = """
UPDATE events
SET summary = ?,
    updated_at = now()
WHERE id = ?
"""

EVENT_ARTICLE_SUMMARIES_SQL = """
SELECT a.id AS article_id, a.title AS title, r.summary AS summary
FROM event_articles ea
JOIN articles a ON a.id = ea.article_id
JOIN article_ai_results r ON r.article_id = a.id
WHERE ea.event_id = ?
  AND r.summary IS NOT NULL
  AND btrim(r.summary) <> ''
ORDER BY a.id ASC
"""

TOPIC_SUMMARY_TARGETS_SQL = f"""
SELECT t.id, t.title, t.summary
FROM topics t
WHERE t.summary IS NOT NULL
  AND (
      length(btrim(t.title)) > {TOPIC_TITLE_MAX_CHARS}
      OR
      length(btrim(t.summary)) > 700
      OR length(btrim(t.summary)) < 80
      OR EXISTS (
          SELECT 1
          FROM events e
          WHERE e.topic_id = t.id
            AND btrim(e.summary) = btrim(t.summary)
      )
  )
ORDER BY t.updated_at DESC NULLS LAST, t.id DESC
LIMIT ?
"""

UPDATE_TOPIC_SQL = """
UPDATE topics
SET title = ?,
    summary = ?
WHERE id = ?
"""

TOPIC_EVENT_SUMMARIES_SQL = """
SELECT id, title, summary
FROM events
WHERE topic_id = ?
  AND summary IS NOT NULL
  AND btrim(summary) <> ''
ORDER BY created_at ASC, id ASC
"""

TIMEOUT_FAILED_JOBS_COUNT_SQL = """
SELECT count(*) AS count
FROM article_jobs
WHERE status = 'failed'
  AND last_error ILIKE 'Request timed out.%'
"""

RESET_TIMEOUT_FAILED_JOBS_SQL = """
UPDATE article_jobs
SET status = 'pending'::public.article_job_status,
    attempts = 0,
    last_error = NULL,
    last_attempt_at = NULL,
    updated_at = now()
WHERE status = 'failed'
  AND last_error ILIKE 'Request timed out.%'
"""


def main() -> int:
    load_dotenv(ROOT / ".env", override=True)

    parser = argparse.ArgumentParser(
        description="Backfill article/event/topic summaries. Dry-run by default."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres/Supabase DB URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument("--limit", type=int, default=100, help="Max rows per target table.")
    parser.add_argument("--apply", action="store_true", help="Write updates to the DB.")
    parser.add_argument(
        "--llm-rollups",
        action="store_true",
        help="Regenerate event/topic rollups with an LLM instead of deterministic clipping.",
    )
    parser.add_argument(
        "--llm-model",
        default=(
            os.environ.get("LLM_TOPIC_EVENT_MODEL")
            or os.environ.get("LLM_DEFAULT_MODEL")
            or "solar-mini"
        ),
        help="LLM model for --llm-rollups.",
    )
    parser.add_argument(
        "--reset-timeout-jobs",
        action="store_true",
        help="Reset timeout-failed article jobs to pending. Requires --apply to write.",
    )
    args = parser.parse_args()

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 1
    if args.limit <= 0:
        print("--limit must be greater than 0.", file=sys.stderr)
        return 1

    client = LLMClient(model=args.llm_model) if args.apply and args.llm_rollups else None

    with ensure_db(str(ROOT / "data" / "news.db"), database_url=database_url) as conn:
        result = {
            "mode": "apply" if args.apply else "dry-run",
            "article_ai_results": _backfill_articles(conn, args.apply, args.limit),
            "events": _backfill_events(conn, args.apply, args.limit, client),
            "topics": _backfill_topics(conn, args.apply, args.limit, client),
        }
        if args.reset_timeout_jobs:
            result["timeout_jobs"] = _reset_timeout_jobs(conn, args.apply)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _backfill_articles(conn, apply: bool, limit: int) -> dict:
    rows = conn.query(BAD_ARTICLE_SUMMARIES_SQL, (limit,))
    changed = 0
    needs_regeneration = 0
    for row in rows:
        normalized = normalize_summary(row["summary"])
        if not normalized or normalized == row["summary"]:
            needs_regeneration += 1
            continue
        changed += 1
        if apply:
            conn.execute(UPDATE_ARTICLE_SUMMARY_SQL, (normalized, row["article_id"]))
    return {
        "matched": len(rows),
        "would_update": changed,
        "needs_regeneration": needs_regeneration,
    }


def _backfill_events(conn, apply: bool, limit: int, client: LLMClient | None) -> dict:
    rows = conn.query(EVENT_SUMMARY_TARGETS_SQL, (limit,))
    changed = 0
    for row in rows:
        summary = _rollup_event_summary(conn, client, row) if client else normalize_summary(row["summary"])
        if not summary or summary == row["summary"]:
            continue
        changed += 1
        if apply:
            conn.execute(UPDATE_EVENT_SUMMARY_SQL, (summary, row["id"]))
    return {"matched": len(rows), "would_update": changed, "llm_rollups": client is not None}


def _rollup_event_summary(conn, client: LLMClient, row) -> str:
    article_summaries = [
        {
            "article_id": item["article_id"],
            "title": item["title"],
            "summary": normalize_summary(item["summary"]),
        }
        for item in conn.query(EVENT_ARTICLE_SUMMARIES_SQL, (row["id"],))
        if normalize_summary(item["summary"])
    ]
    if len(article_summaries) <= 1:
        return normalize_summary(article_summaries[0]["summary"]) if article_summaries else normalize_summary(row["summary"])
    data = client.request_json(
        build_event_summary_prompt(row["title"], article_summaries),
        required_keys={"summary"},
        temperature=0,
    )
    return normalize_summary(data.get("summary")) or normalize_summary(row["summary"])


def _backfill_topics(conn, apply: bool, limit: int, client: LLMClient | None) -> dict:
    rows = conn.query(TOPIC_SUMMARY_TARGETS_SQL, (limit,))
    changed = 0
    for row in rows:
        update = _rollup_topic(conn, client, row) if client else {
            "title": (
                normalize_topic_title(row["title"])
                or normalize_topic_title(row["summary"])
                or "제목 없음"
            ),
            "summary": normalize_summary(row["summary"]),
        }
        if not update["summary"] or (
            update["title"] == row["title"] and update["summary"] == row["summary"]
        ):
            continue
        changed += 1
        if apply:
            conn.execute(UPDATE_TOPIC_SQL, (update["title"], update["summary"], row["id"]))
    return {"matched": len(rows), "would_update": changed, "llm_rollups": client is not None}


def _rollup_topic(conn, client: LLMClient, row) -> dict:
    event_summaries = [
        {
            "event_id": item["id"],
            "title": item["title"],
            "summary": normalize_summary(item["summary"]),
        }
        for item in conn.query(TOPIC_EVENT_SUMMARIES_SQL, (row["id"],))
        if normalize_summary(item["summary"])
    ]
    if not event_summaries:
        return {
            "title": (
                normalize_topic_title(row["title"])
                or normalize_topic_title(row["summary"])
                or "제목 없음"
            ),
            "summary": normalize_summary(row["summary"]),
        }
    data = client.request_json(
        build_topic_rollup_prompt(row["title"], event_summaries),
        required_keys={"title", "summary"},
        temperature=0,
    )
    return {
        "title": (
            normalize_topic_title(data.get("title") or row["title"])
            or normalize_topic_title(row["summary"])
            or "제목 없음"
        ),
        "summary": normalize_summary(data.get("summary")) or normalize_summary(row["summary"]),
    }


def _reset_timeout_jobs(conn, apply: bool) -> dict:
    row = conn.query_one(TIMEOUT_FAILED_JOBS_COUNT_SQL)
    count = int(row["count"] if row else 0)
    if apply and count:
        conn.execute(RESET_TIMEOUT_FAILED_JOBS_SQL)
    return {"matched": count, "would_reset": count}


if __name__ == "__main__":
    raise SystemExit(main())
