<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# collector

## Purpose
Stage 1 of the pipeline. Ingests RSS feeds, crawls full article text, runs LLM cleanup only
when ad/boilerplate is detected, then summarizes each article. Results land in
`article_ai_results` with `status='done'`, ready for the event classifier. Also home to the
**shared DB abstraction** (`storage.py`) used by every stage.

## Key Files
| File | Description |
|------|-------------|
| `cli.py` | CLI orchestrator. Subcommands `fetch` / `crawl` / `process` / `run` (default = all three) |
| `rss.py` | `fetch_feed()` — feedparser ingest with ETag/modified caching, GUID/link dedup, single-transaction insert |
| `crawler.py` | `crawl_articles()` / `fetch_url_article_data()` — HTTP/HTTPS/`file://` crawl, domain rate-limit, optional LLM cleanup, status→`ready` |
| `content_cleaner.py` | `should_llm_cleanup()` heuristic + `ArticleTextCleaner` / `clean_article_text()` — strip ads/boilerplate, preserve facts |
| `article_ai_pipeline.py` | `process_pending_articles()` — drives `ArticleLLMAnalyzer` over `ready` jobs, persists results |
| `article_llm.py` | `ArticleLLMAnalyzer.analyze()` → `ArticleAnalysis(summary, keywords)`; structured 4-sentence summary |
| `storage.py` | `ensure_db()` → `SqliteConnection` \| `PostgresConnection`; unified `query/query_one/execute/transaction` API; schema DDL; job/result helpers |
| `utils.py` | HTML parsing (site-specific + DOM-scoring fallback), feed→category/publisher inference, `load_feed_urls()` |
| `settings.py` | Defaults + env vars (DB path, batch sizes, crawl length, domain delay, LLM model names, user agent) |

## For AI Agents

### Working In This Directory
- **`storage.py` is the most important cross-cutting file. New DB code must use its API** so it
  runs on both SQLite (local/test) and Postgres (production):
  - **Write SQL with `?` placeholders everywhere.** The Postgres wrapper rewrites `?` → `%s`
    and appends `RETURNING id` for `INSERT INTO articles`.
  - **Connections are autocommit.** Single writes commit immediately. Wrap multi-write atomic
    units in `with conn.transaction():`. **Never nest** `transaction()` blocks.
  - SQLite **auto-creates** all tables/columns on connect; Postgres does **not** (schema is
    managed by Supabase migrations). Local SQLite is only for fetch/crawl/article-analysis.
- LLM cleanup is gated by `should_llm_cleanup()` — don't make every crawl call the model.
- `crawler.py` supports `file://` URLs and an `offline` flag for deterministic crawl tests.

### Testing Requirements
- These modules are exercised indirectly by `tests/test_summary_quality.py` (summary
  normalization on save). Run via SQLite — no Postgres needed for this stage.

### Common Patterns
- Status machine on `articles`: `needs_crawl` → `ready` (or `crawl_failed`) → AI job `done`.
- Each public stage is invoked through `cli.py`; keep new flags consistent via `add_common_args`.

## Dependencies

### Internal
- `openai_client.client` (LLM), `summary_utils` (normalize), `utils` (HTML/feed parsing).

### External
- `feedparser`, `requests`, `beautifulsoup4`, `sqlite3`, `psycopg` (optional), `python-dateutil` (optional).

<!-- MANUAL: -->
