<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# hannoon-ai

## Purpose
A Korean-news RSS pipeline that collects articles, crawls full text, then uses Upstage Solar
LLMs + embeddings to summarize articles, group them into **events**, and group events into
**topics** (with an optional 2-level subtopic hierarchy). The defining cost strategy: a cheap
model (`solar-mini`) does high-volume per-article work (cleanup, summary); the expensive model
(`solar-pro3`) is reserved for event/topic *assignment* decisions that corrupt the long-lived
DB structure if wrong. When pgvector candidate search returns **zero** candidates, the
assignment LLM call is skipped entirely and a new event/topic is created directly.

## Key Files
| File | Description |
|------|-------------|
| `main.py` | Collector entry point; prepends `src/` to `sys.path`, delegates to `collector.cli.main()` |
| `classify_events.py` | Event-classifier entry point; delegates to `event_classifier.pipeline.main()` (needs Postgres + pgvector) |
| `classify_topics.py` | Topic-classifier entry point; delegates to `topic_classifier.cli.main()` (needs Postgres + pgvector) |
| `requirements.txt` | Pinned runtime deps (feedparser, requests, beautifulsoup4, psycopg, openai, python-dotenv) |
| `pyproject.toml` | Package metadata; `packages.find` rooted at `src/` (repo is *not* pip-installed for runtime) |
| `README.md` | Korean operator-facing walkthrough |
| `TODO.md` | Ordered work plan (abuse removal → event audit → subtopic dev → audits) |
| `.env.example` | Full env-var template (loaded via python-dotenv, `override=True`) |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `src/` | All application source (3 independent stages) (see `src/AGENTS.md`) |
| `tests/` | stdlib `unittest` suites (see `tests/AGENTS.md`) |
| `scripts/` | One-off maintenance scripts (see `scripts/AGENTS.md`) |
| `docs/` | Design & diagnosis docs (see `docs/AGENTS.md`) |
| `config/` | `feeds.json` RSS source list (see `config/AGENTS.md`) |
| `migrations/` | Postgres schema migrations applied manually/via Supabase (see `migrations/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- Respond in **Korean**; comments and operator-facing strings are Korean — match that when editing.
- The repo is **not** pip-installed. Entry points prepend `src/` to `sys.path`, so internal
  imports are top-level (`from collector...`, `from db...`), **not** `src.`-prefixed.
- All config is env vars; never hard-code keys/models. See `.env.example`.

### Testing Requirements
- CI runs **only** `python -m compileall -q main.py src` and `python main.py --help`. It does
  **not** run unit tests — run them yourself: `python -m unittest tests.test_summary_quality`
  and `python -m unittest tests.test_topic_subtopics`.
- CI is defined in `.github/workflows/deploy.yml` (push/PR to `develop`); non-PR pushes to
  `develop` rsync-deploy to EC2.

### Common Patterns
- The three stages (collector / event classifier / topic classifier) are **independent CLIs**
  communicating only through the database — never call one stage's internals from another.
- Cost strategy is load-bearing: keep cheap per-article work on `solar-mini`, gate expensive
  `solar-pro3` calls behind non-empty pgvector candidate sets.
- **Production schema lives in the sibling `../hannoon-supabase` project, not here.** Any table/
  column this code references must already exist as a Supabase migration. The local
  `migrations/` SQL (e.g. `0001_topics_parent_topic_id.sql`) must be **ported into a Supabase
  migration** before it affects production. See `../AGENTS.md` for the full schema-sync contract
  and the currently-open `topics.parent_topic_id` gap.

## Dependencies

### External
- `feedparser`, `requests`, `beautifulsoup4` — RSS ingest + full-text crawl
- `psycopg[binary]` — Postgres/pgvector production backend (optional import)
- `openai` — OpenAI-compatible client pointed at Upstage Solar
- `python-dotenv`, `python-dateutil`

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
