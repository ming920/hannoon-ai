<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# src

## Purpose
All application source. Three independent pipeline stages (collector, event classifier, topic
classifier) plus shared cross-cutting utilities. Stages communicate only through the database
via the unified `storage.py` connection API.

## Key Files
| File | Description |
|------|-------------|
| `embedding.py` | Upstage embeddings wrapper. `embed_query()` (search) vs `embed_passage()` (storage); 4096-dim; `to_vector_literal()` formats vectors for SQL |
| `summary_utils.py` | `normalize_summary()` (≤4 sentences, ≤700 chars) and `normalize_topic_title()` (≤30 chars); all summary/title DB writes go through these |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `collector/` | Stage 1: RSS ingest, crawl, content cleanup, article summary (see `collector/AGENTS.md`) |
| `event_classifier/` | Stage 2: group `done` articles into events (see `event_classifier/AGENTS.md`) |
| `topic_classifier/` | Stage 3: group events into topics / subtopics (see `topic_classifier/AGENTS.md`) |
| `db/` | Shared entity helpers for events/topics/topic_causes (see `db/AGENTS.md`) |
| `openai_client/` | OpenAI-compatible LLM client wrapper (see `openai_client/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- Internal imports are top-level (`from db.events import ...`, `from collector.storage import ...`)
  because entry points add `src/` to `sys.path`. Do **not** prefix with `src.`.
- Vectors are **4096-dim**; pgvector columns must be `vector(4096)`. Use `embed_query` for
  search-time text and `embed_passage` for stored text — they use different Upstage models.
- Every summary/title written to the DB must pass through `summary_utils`; the test suite
  enforces this at the repository layer.

### Testing Requirements
- Run `python -m unittest tests.test_summary_quality` and `tests.test_topic_subtopics`.

### Common Patterns
- Env-driven config with generic var names (`LLM_API_KEY`, `LLM_BASE_URL`) plus `UPSTAGE_*` /
  `OPENAI_*` fallbacks so the provider can be swapped.

## Dependencies

### Internal
- `summary_utils` is imported by `db/`, `collector/`, and both classifiers.
- `embedding` and `openai_client` are imported by both classifiers and parts of the collector.

### External
- `openai` (Upstage base URL), `python-dotenv`

<!-- MANUAL: -->
