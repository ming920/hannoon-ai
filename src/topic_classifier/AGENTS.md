<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-10 -->

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
| `pipeline.py` | `run()` orchestrator; `_assign_flat()` (single level) vs `_assign_hierarchical()` (parent→subtopic); `_assign_subtopic_by_embedding()` (default embedding-mode subtopic assign: event-embedding cosine nearest-neighbor, no LLM; create fallback title prefers `cause` over `ev.title` to avoid S-1 event-copy titles); `_resolve_action()` guardrails; `_dedup_guard()` (create→assign demotion via `db.topics.find_duplicate_topic`, R-T1 prevention); `_sanitize_subtopic_title()` (S-1~S-4 naming-rule rename via `cause`, safety net for `naming_rules.py`); `_link_chain()` prev/next; topic rollup |
| `prompts.py` | Builders: `build_topic_cause_result_prompt`, `build_topic_assignment_prompt`, `build_parent_topic_assignment_prompt` (defines parent topics as one social issue with the "<title> — <event>" naming test, not a broad domain bucket — see `docs/entity_definitions.md`), `build_subtopic_assignment_prompt` (LLM subtopic mode only; bans time-segment/attribute-style titles), `build_topic_update_prompt`, `build_topic_rollup_prompt` |
| `naming_rules.py` | Pure S-1~S-4 subtopic title checks ported from `eval/rubric_checks.py` (src can't import eval): `is_time_segment_title` (S-2), `is_attribute_title` (S-3), `is_event_copy_title` (S-1, ≥0.90 similarity to the triggering event's title), `is_parent_scope_title` (S-4, ≥0.85 similarity to parent title), `violates_subtopic_naming` (aggregate) |
| `settings.py` | `TOPIC_*` env vars: min net count (5), batch size (10), top_k (12), subtopics enabled (false), subtopic top_k, distance threshold (**0.90**), assign-score threshold (0.75); subtopic knobs: `TOPIC_SUBTOPIC_MODE` (**`embedding`** default, or `llm`), `TOPIC_SUBTOPIC_SIM_THRESHOLD` (0.65), `TOPIC_SUBTOPIC_ASSIGN_SCORE_THRESHOLD`; `TOPIC_DUP_SIM_THRESHOLD` (0.85, create-dedup guard) |

## For AI Agents

### Working In This Directory
- **Same assignment guardrail as the event classifier:** force `create` if score <
  `TOPIC_ASSIGN_SCORE_THRESHOLD` (0.75) **or** the `reason` contains negative markers. Validate
  the LLM-selected `topic_id` against the candidate set before writing. **Zero candidates → skip
  the LLM and create.**
- **Hierarchy, stage 1 (parent/root):** LLM assign-or-create over `db.topic_causes.search_candidates`
  with `roots_only=True` (parent_topic_id IS NULL) and the guardrail above.
- **Hierarchy, stage 2 (subtopic) default is `TOPIC_SUBTOPIC_MODE=embedding`** — `_assign_subtopic_by_embedding`
  picks the nearest existing subtopic under the parent by **event-embedding cosine similarity** (no
  LLM call, deterministic) and assigns only if `sim >= TOPIC_SUBTOPIC_SIM_THRESHOLD` (0.65), else
  creates a new subtopic. A brand-new parent ⇒ no subtopic candidates ⇒ immediate subtopic create.
  Setting `TOPIC_SUBTOPIC_MODE=llm` switches stage 2 back to cause-candidate search
  (`parent_topic_id=<parent_id>`) + LLM assign-or-create gated by `TOPIC_SUBTOPIC_ASSIGN_SCORE_THRESHOLD`.
  This embedding default was adopted from the 2026-07-03/04 `eval/` holdout validation.
- Both parent and subtopic accumulate causes via `topic_causes.add_cause()`.
- Category filtering prevents cross-category topic merges — keep it.
- **Dedup guard (2-4):** right after any `create` decision (flat topic, parent, or subtopic),
  `_dedup_guard()` re-checks the same scope (root topics in-category, or subtopics under the
  resolved parent) for a title match via `db.topics.find_duplicate_topic` (difflib similarity ≥
  `TOPIC_DUP_SIM_THRESHOLD`, default 0.85) and demotes to `assign` if found — bypasses
  `_select_candidate` since the match may be outside the embedding-search candidate set.
- **Subtopic naming guard (2-3a):** `_sanitize_subtopic_title()` runs on any subtopic `create`
  and renames via `cause` if the title trips `naming_rules.violates_subtopic_naming` (S-1~S-4);
  leaves the title untouched if the `cause`-based replacement would also violate (avoids the
  degenerate case where a freshly-created parent and its first subtopic would collide on the same
  `cause` string).
- The prev/next event chain is a doubly-linked list ordered by `(created_at, id)`; wrap chain
  edits in a single `conn.transaction()` and never nest.

### Testing Requirements
- Covered by `tests/test_topic_subtopics.py` (~1100 lines, all external deps mocked):
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
