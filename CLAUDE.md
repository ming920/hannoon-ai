# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

Respond in Korean. (Per `.github/copilot-instructions.md` — applies regardless of input language.)

## What this is

A Korean-news RSS pipeline that collects articles, crawls full text, then uses
Upstage Solar LLMs + embeddings to summarize articles, group them into **events**,
and group events into **topics**. Read `README.md` (Korean) for the full operator-facing
walkthrough and `docs/topic_classification_overview.md` for the topic-grouping design.

The defining cost strategy: cheap model (`solar-mini`) for high-volume per-article work
(cleanup, summary), expensive model (`solar-pro3`) only for event/topic *assignment*
decisions that corrupt the long-lived DB structure if wrong. Crucially, when pgvector
candidate search returns **zero** candidates, the assignment LLM call is skipped entirely
and a new event/topic is created directly.

## Commands

Install: `python -m pip install -r requirements.txt` then `copy .env.example .env`.

Run the collector pipeline (entry point `main.py`, delegates to `src/collector/cli.py`):
```powershell
python main.py run      # fetch + crawl + process (default if no subcommand)
python main.py fetch    # RSS items only
python main.py crawl    # crawl full text for articles that need it
python main.py process  # LLM analysis of "ready" articles
```

Event and topic classification (separate entry points, **require Postgres + pgvector**):
```powershell
python classify_events.py --database-url "postgresql://..."
python classify_topics.py --database-url "postgresql://..."
```
Both fall back to the `DATABASE_URL` env var if `--database-url` is omitted.

Tests (stdlib `unittest`, no pytest config despite `.pytest_cache` in gitignore):
```powershell
python -m unittest tests.test_summary_quality              # whole module
python -m unittest tests.test_summary_quality.SummaryQualityTests.test_normalize_summary_limits_sentences_and_chars  # single test
```

CI (`.github/workflows/deploy.yml`, on push/PR to `develop`) runs exactly:
`python -m compileall -q main.py src` and `python main.py --help`. It does **not** run the
unit tests — run them yourself. On non-PR pushes to `develop` it rsync-deploys to EC2.

## Architecture

Three independent stages, run as separate CLIs, communicating only through the database:

1. **Collector** (`src/collector/`): `rss.py` (feedparser ingest) → `crawler.py` +
   `content_cleaner.py` (full-text crawl, LLM cleanup only when ad/boilerplate traces are
   detected) → `article_ai_pipeline.py` driving `article_llm.py` (summary). Results
   land in `article_ai_results` with `status='done'`.
2. **Event classifier** (`src/event_classifier/`): `pipeline.py` takes `done` articles,
   extracts a one-sentence `main_event`, query-embeds it, pgvector-searches candidate events,
   and asks `solar-pro3` to assign-or-create. Sets `article_ai_results.status='event_assigned'`.
3. **Topic classifier** (`src/topic_classifier/`): `pipeline.py` takes events with enough
   net articles, extracts `cause`/`result`, cause-embeds, searches candidate topics via
   `topic_causes`, assigns-or-creates, and maintains a prev/next event chain per topic.

### The DB abstraction (most important cross-cutting detail)

`src/collector/storage.py` provides `ensure_db(db_path, database_url=None)` returning either
a `SqliteConnection` (local dev/test) or `PostgresConnection` (production Supabase). Both expose
the **same** API: `query()`, `query_one()`, `execute()`, and `transaction()`. New code must use
this API so it runs on both backends. Key rules:

- **SQLite uses `?` placeholders everywhere.** The Postgres wrapper rewrites `?` → `%s` and
  appends `RETURNING id` for `INSERT INTO articles`. Always write SQL with `?`.
- **Connections are autocommit.** Single writes commit immediately. Wrap multi-write atomic
  units in `with conn.transaction():`. **Never nest** `transaction()` blocks (SQLite can't nest
  BEGIN; psycopg makes them savepoints that don't commit on exit).
- SQLite path **auto-creates** all tables/columns on connect; the Postgres path does **not** —
  production schema (including pgvector columns) is managed by Supabase migrations applied
  separately. Local SQLite is only for fetch/crawl/article-analysis; event/topic classification
  needs real Postgres + pgvector.

### LLM and embeddings

- `src/openai_client/client.py` — `LLMClient`, an OpenAI-compatible Chat Completions wrapper
  (default base URL = Upstage). `request_json()` enforces `response_format=json_object` and
  validates required keys; `parse_json_object()` tolerantly extracts the outer `{...}` when a
  model wraps JSON in prose. Env var names are deliberately generic (`LLM_API_KEY`,
  `LLM_BASE_URL`) with `UPSTAGE_*`/`OPENAI_*` fallbacks so the provider can be swapped.
- `src/embedding.py` — Upstage embeddings. `embed_query()` (search) vs `embed_passage()`
  (storage) use different models. Vectors are **4096-dim**; pgvector columns must be
  `vector(4096)`. `to_vector_literal()` formats vectors for SQL.
- `src/summary_utils.py` — `normalize_summary()` (≤700 chars, sentence-limited) and
  `normalize_topic_title()` (≤`TOPIC_TITLE_MAX_CHARS`). All summary/title writes to the DB go
  through these; the test suite enforces it at the repository layer.

### Assignment-decision guardrails

Both classifier pipelines apply the same defensive pattern after the LLM returns an `assign`
decision: if the score is below `ASSIGN_SCORE_THRESHOLD` **or** the free-text `reason` contains
negative markers (`무관`, `관련성이 없`, `새로운 사건`, …) detected by `_reason_rejects_assignment`,
the decision is overridden to `create`. This prevents the LLM from contradicting itself and
polluting event/topic structure. The LLM-selected `event_id`/`topic_id` is also validated
against the actual candidate set before any write.

## Configuration

All config is env vars (loaded via `python-dotenv`, `override=True`); see `.env.example` for
the full list. Notable ones beyond models/keys: `EVENT_*` and `TOPIC_*` tune batch size,
candidate limit (`top_k`), distance threshold, and assign-score threshold for each classifier
(defaults live in the respective `settings.py`).

## Conventions

- The repo is not pip-installed; entry points (`main.py`, `classify_*.py`) prepend `src/` to
  `sys.path`, so internal imports are top-level (`from collector...`, `from db...`), not `src.`-prefixed.
- Comments and operator-facing strings are in Korean; match that when editing existing files.
