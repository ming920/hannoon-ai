<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# topic_classifier

## Purpose
Stage 3 of the pipeline (**requires Postgres + pgvector**). Takes events with enough net
articles, extracts `cause`/`result`, cause-embeds, searches candidate topics via `topic_causes`,
assigns-or-creates, and maintains a prev/next event chain per topic. Supports an optional
**2-level hierarchy**: parent (root) topics → subtopics, where `events.topic_id` always points to
the leaf (subtopic).

## Key Files
| File | Description |
|------|-------------|
| `cli.py` | `main()` / `build_parser()` — flags: `--database-url`, `--min-net-article-count`, `--batch-size`, `--top-k`, `--llm-model`, `--subtopics/--no-subtopics`, `--subtopic-top-k` |
| `pipeline.py` | `run()` orchestrator; `_assign_flat()` (single level) vs `_assign_hierarchical()` (parent→subtopic); `_resolve_action()` guardrails; `_link_chain()` prev/next; topic rollup |
| `prompts.py` | Builders: `build_topic_cause_result_prompt`, `build_topic_assignment_prompt`, `build_subtopic_assignment_prompt`, `build_topic_rollup_prompt` |
| `settings.py` | `TOPIC_*` env vars: min net count (5), batch size (10), top_k (12), subtopics enabled (false), subtopic top_k, distance threshold (0.50), assign-score threshold (0.75), topic model |

## For AI Agents

### Working In This Directory
- **Same assignment guardrail as the event classifier:** force `create` if score <
  `TOPIC_ASSIGN_SCORE_THRESHOLD` (0.75) **or** the `reason` contains negative markers. Validate
  the LLM-selected `topic_id` against the candidate set before writing. **Zero candidates → skip
  the LLM and create.**
- **Hierarchy is mutually-exclusive candidate scoping** in `db.topic_causes.search_candidates`:
  stage 1 uses `roots_only=True` (parent_topic_id IS NULL); stage 2 uses
  `parent_topic_id=<parent_id>`. A new parent ⇒ no subtopic candidates ⇒ immediate subtopic
  create. Both parent and subtopic accumulate causes via `topic_causes.add_cause()`.
- Category filtering prevents cross-category topic merges — keep it.
- The prev/next event chain is a doubly-linked list ordered by `(created_at, id)`; wrap chain
  edits in a single `conn.transaction()` and never nest.

### Testing Requirements
- Covered by `tests/test_topic_subtopics.py` (685 lines, all external deps mocked):
  `python -m unittest tests.test_topic_subtopics`. This is the primary regression guard for the
  subtopic feature — run it after any change here.

### Common Patterns
- `cause` is a noun phrase tuned for embedding search; `result` is a complete sentence.
- Flat vs hierarchical mode is dispatched in `run()` by `subtopics_enabled`.

## Dependencies

### Internal
- `db.events`, `db.topics`, `db.topic_causes`, `embedding`, `openai_client.client`,
  `summary_utils`, and `collector.{settings,storage}` (via `cli.py`).

### External
- `psycopg`-backed Postgres with pgvector; needs the `topics.parent_topic_id` migration applied.

<!-- MANUAL: -->
