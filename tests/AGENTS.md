<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-10 -->

# tests

## Purpose
stdlib `unittest` suites (no pytest config despite `.pytest_cache` in gitignore). External
dependencies (LLMClient, embeddings, DB) are either mocked or replaced with fake connections, and
the collector/eval helpers are exercised as pure functions, so every test is deterministic and
needs no Postgres or API keys. **CI does not run these** — run them yourself. 9 modules total; most
prepend `../src` (and `test_evaluate_helpers` / `test_rubric_checks` also `../eval`) to `sys.path`,
mirroring the entry points.

## Key Files
| File | Description |
|------|-------------|
| `test_apply_local_seed.py` | `scripts/apply_local_seed.py` guards (no DB): **`--replace` is refused on any non-local host even with `--allow-remote`** (wiping a remote DB is not this script's job — `eval/reset_test_db.py` owns that), host-only guards take no DB state so they run *before* connecting, article-vs-event seed detection (quoted and unquoted `INSERT INTO public.articles`), and `CLEARED_TABLES`/`CLEAR_STATEMENTS` consistency so the `--help` text can't drift from what is actually deleted |
| `test_local_embedding_proxy.py` | `scripts/local_embedding_proxy.py` pure helpers: zero-padding to `vector(4096)` **preserves cosine distance** (identical to 12 places; relative ordering survives) — the premise the whole proxy rests on; oversized vectors are refused rather than truncated; a base64 `embedding` string raises instead of silently passing through unpadded (the actual bug hit in 2026-08, since the OpenAI SDK defaults to `encoding_format=base64`) |
| `test_summary_quality.py` | Enforces summary/title normalization at the repository layer: `normalize_summary` (≤4 sentences, ≤700 chars), `normalize_topic_title` (≤`TOPIC_TITLE_MAX_CHARS`=30), `parse_json_object`, and that article/event/topic saves normalize automatically |
| `test_topic_subtopics.py` | Comprehensive 2-level topic hierarchy tests (largest module): `create_topic` parent handling, `search_candidates` `roots_only`/`parent_topic_id` filters, rejection-reason detection, score parsing, guardrail action resolution, candidate validation, full `_assign_hierarchical` flow, flat vs hierarchical `run()` dispatch, subtopic prompts |
| `test_content_footer.py` | `collector.content_cleaner.strip_boilerplate_footer` regression: publisher copyright/제보 footers (연합/뉴시스/세계/서울/국민일보/연합뉴스TV) are trimmed while incidental in-body marker mentions (e.g. "…페이스북을 통해", bare ⓒ, topical "무단 전재") are preserved |
| `test_extraction_selectors.py` | `collector.utils.extract_article_text` publisher-specific selectors: registered domains (연합 `.story-news.article`, 서울 `#articleContent`, 동아 `.news_view`, 한겨레 `.article-text`) use dedicated selectors; unregistered domains fall back to the generic heuristic; `page_url` is optional (backward-compatible) |
| `test_image_filter.py` | `collector.utils._is_unwanted_image_url`: rejects logo/icon/reporter-photo/placeholder URLs (incl. digit-suffixed `logo01`, `default` substrings) while keeping legit news photos whose slug incidentally contains white/google/ico/writer |
| `test_rollup_prompts.py` | Rollup-prompt truncation regression: `build_topic_rollup_prompt` / `build_event_summary_prompt` keep the **newest** N items (`[-N:]`, not `[:N]`) so active topics/events keep updating; empty lists show fallback text |
| `test_postgres_schema_validation.py` | `collector.storage` startup validators via `FakeConn` (no DB): `_required_columns_for` collector-vs-classifier scoping, `_validate_postgres_schema` RuntimeError on missing tables/columns (`parent_topic_id`, `topic_causes`, `embedding_text`), `_validate_vector_dimensions` pgvector 4096-dim mismatch detection |
| `test_evaluate_helpers.py` | `eval/evaluate.py` data-wrangling helpers on synthetic dicts (no DB): `_resolve_event_gold_labels` majority-vote gold aggregation + inconsistency warnings, `build_event/subtopic/topic_level_maps`, `compute_hierarchy_consistency`, and `metrics.evaluate_level` covered-only diagnostics that exclude phantom singletons (`__missing_N__`) |
| `test_rubric_checks.py` | `eval/rubric_checks.py`: pure title classifiers (time-based/attribute/category subtopic-topic title regexes, 5+ Korean positive/negative cases each, incl. compound-noun false-positive guards like "부정선거"), `title_similarity`/`find_similar_title_pairs`/`compute_topic_depth`, and a 9-check-ID smoke test against a hand-built SQLite in-memory fixture (`events`/`topics`/`event_articles` created manually — `ensure_sqlite_db` only auto-creates collector tables) |

## For AI Agents

### Working In This Directory
- Run the whole suite: `python -m unittest discover -s tests`
- Run a whole module: `python -m unittest tests.test_summary_quality`
- Run one test: `python -m unittest tests.test_summary_quality.SummaryQualityTests.test_normalize_summary_limits_sentences_and_chars`
- Mock external services (`LLMClient`, `embedding.*`, DB connections) or use the fake-connection
  pattern (`test_postgres_schema_validation.py`) — never make real network or Postgres calls.
- These modules are the regression guard for the normalization invariant, the subtopic feature,
  collector extraction/cleanup heuristics, the Postgres schema validator, prompt truncation, and
  the eval metric-wrangling/rubric-check helpers; when you change `summary_utils`, `db/`, either
  classifier's guardrails/prompts, `collector.{utils,content_cleaner,storage}`, or
  `eval/{evaluate,metrics,rubric_checks}`, update/run the matching module. `rubric_checks.py`'s
  title-pattern regexes should stay in sync with `../docs/entity_definitions.md`.

### Testing Requirements
- Tests use SQLite-backed, fully-mocked, or fake connections; no `DATABASE_URL` or API keys required.

### Common Patterns
- Deterministic assertions only — no time/randomness; canned LLM responses and synthetic dicts
  drive each branch. `test_content_footer` uses a fixed-length body because footer markers are only
  scanned in the last ~40% of the text.

## Dependencies

### Internal
- `summary_utils`, `db.topics`, `db.topic_causes`, `topic_classifier.{pipeline,prompts}`,
  `event_classifier.prompts`, `openai_client.client`, `collector.{utils,content_cleaner,storage}`,
  and (via `../eval` on the path) `evaluate` / `metrics` / `rubric_checks`.

### External
- stdlib `unittest`, `unittest.mock`.

<!-- MANUAL: -->
