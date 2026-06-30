<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# tests

## Purpose
stdlib `unittest` suites (no pytest config despite `.pytest_cache` in gitignore). All external
dependencies (LLMClient, embeddings, DB) are mocked, so tests are deterministic and need no
Postgres or API keys. **CI does not run these** — run them yourself.

## Key Files
| File | Description |
|------|-------------|
| `test_summary_quality.py` | Enforces summary/title normalization at the repository layer: `normalize_summary` (≤4 sentences, ≤700 chars), `normalize_topic_title` (≤`TOPIC_TITLE_MAX_CHARS`), `parse_json_object`, and that article/event/topic saves normalize automatically |
| `test_topic_subtopics.py` | Comprehensive 2-level topic hierarchy tests: `create_topic` parent handling, `search_candidates` `roots_only`/`parent_topic_id` filters, rejection-reason detection, score parsing, guardrail action resolution, candidate validation, full `_assign_hierarchical` flow, flat vs hierarchical `run()` dispatch, subtopic prompts |

## For AI Agents

### Working In This Directory
- Run a whole module: `python -m unittest tests.test_summary_quality`
- Run one test: `python -m unittest tests.test_summary_quality.SummaryQualityTests.test_normalize_summary_limits_sentences_and_chars`
- Mock external services (`LLMClient`, `embedding.*`, DB connections) — never make real network
  or Postgres calls. Follow the existing mocking patterns in `test_topic_subtopics.py`.
- These tests are the regression guard for the normalization invariant and the subtopic feature;
  when you change `summary_utils`, `db/`, or either classifier's guardrails, update/run them.

### Testing Requirements
- Tests use SQLite-backed or fully-mocked connections; no `DATABASE_URL` required.

### Common Patterns
- Deterministic assertions only — no time/randomness; canned LLM responses drive each branch.

## Dependencies

### Internal
- `summary_utils`, `db.topics`, `db.topic_causes`, `topic_classifier.*`, `openai_client.client`.

### External
- stdlib `unittest`, `unittest.mock`.

<!-- MANUAL: -->
