<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# event_classifier

## Purpose
Stage 2 of the pipeline (**requires Postgres + pgvector**). Takes `done` articles, extracts a
one-sentence `main_event`, query-embeds it, pgvector-searches candidate events (recent ~2 days),
and asks the assignment model to *assign-or-create*. On success sets
`article_ai_results.status='event_assigned'`.

## Key Files
| File | Description |
|------|-------------|
| `pipeline.py` | `process_event_classification()` / `main()` — orchestrates extract → embed → search → assign/create → summary rollup. Houses the assignment guardrails |
| `prompts.py` | LLM prompt builders: `build_extract_main_event_prompt`, `build_event_assignment_prompt`, `build_event_summary_prompt`, plus verify/title helpers |
| `settings.py` | `EVENT_*` env vars: batch size (5), candidate limit/top_k (12), distance threshold (0.45), assign-score threshold (0.75), event model |

## For AI Agents

### Working In This Directory
- **Assignment guardrail (load-bearing):** after the LLM returns an `assign` decision, override
  it to `create` if the score is below `EVENT_ASSIGN_SCORE_THRESHOLD` (0.75) **or** the free-text
  `reason` contains negative markers (`무관`, `관련성이 없`, `새로운 사건`, …) detected by
  `_reason_rejects_assignment`. The LLM-selected `event_id` is also validated against the actual
  candidate set before any write. Preserve this pattern in any change.
- **Zero candidates → skip the LLM entirely and create a new event** (cost strategy).
- Multi-write units (assign + link article + chain update) belong in one `conn.transaction()`;
  never nest transactions.

### Testing Requirements
- No dedicated unit test module; verify with `python -m compileall` and, where possible, a real
  Postgres run via `classify_events.py --database-url ...`. Behavior overlaps the topic tests
  (shared guardrail logic) in `tests/test_topic_subtopics.py`.

### Common Patterns
- Embedding text is built from labeled fields (title/summary/main_event) via
  `_build_event_embedding_text`; trimmed to first sentences with `_get_first_sentences`.

## Dependencies

### Internal
- `db.events` (search/create/link/chain), `embedding`, `openai_client.client`, `summary_utils`.

### External
- `psycopg`-backed Postgres with pgvector `vector(4096)` columns.

<!-- MANUAL: -->
