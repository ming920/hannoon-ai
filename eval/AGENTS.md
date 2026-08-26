<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-10 | Updated: 2026-07-11 -->

# eval

## Purpose
Offline **clustering-quality eval harness** for the 3-stage pipeline. It generates dummy Korean
articles from a hand-designed gold taxonomy, runs the real `classify_events` / `classify_topics`
stages against a local pgvector DB, and scores the resulting **article → event → subtopic → topic**
clustering against gold labels with pure-Python metrics (ARI, NMI, V-measure, B-cubed F1,
singleton rate, hierarchy consistency). It is the measurement loop used to tune classifier
thresholds and prompts. It is **not** part of the production pipeline and is invoked manually.

The 2026-07-03 holdout validation done with this harness is what selected the adopted defaults
`TOPIC_SUBTOPIC_MODE=embedding` + `TOPIC_SUBTOPIC_SIM_THRESHOLD=0.65` (deterministic, zero
subtopic-assignment LLM calls). See `README.md` for the full result tables and run recipes.

## Key Files
| File | Description |
|------|-------------|
| `README.md` | Korean operator guide: prerequisites (Supabase CLI or bare pgvector), full run flow, oracle/random self-tests, `metrics.csv` column reference, tunable env vars, scale-up procedure |
| `run_iteration.py` | One-cycle orchestrator: reset → ingest → **event drain loop** → in-process `article_count` resync → **topic drain loop** → evaluate. Drains by re-invoking each classifier until the DB backlog is 0 (because `.env` `load_dotenv(override=True)` defeats parent-injected batch sizes); `--run-id`, `--config-tag`, `--database-url` |
| `evaluate.py` | Reads classifier predictions from Postgres + `gold_labels.json`, builds per-level pred/gold maps, computes metrics, writes a `results/<run-id>.md` report and appends a `results/metrics.csv` row (`CSV_COLUMNS`). Supports **DB-free** `--use-gold-as-pred` (oracle, expect 1.0) and `--random-pred --seed` (random baseline, expect low) self-tests |
| `metrics.py` | Pure stdlib (`math`, `collections`) clustering metrics — `bcubed`, `adjusted_rand_index`, `normalized_mutual_information`, `homogeneity`/`completeness`/`v_measure`, `cluster_diagnostics`, and `evaluate_level` (which injects unassigned gold items as phantom singletons `__missing_N__` to penalize missed predictions, and reports a `covered_only` view that excludes them). No sklearn/numpy |
| `generate_dummy.py` | Turns a gold taxonomy into `dummy_articles.json` + `gold_labels.json`. `--dry-run` builds deterministic placeholder articles from each event's `base_facts` (no API key); without it, calls Upstage LLM per article variant. `--limit-events`, `--articles-per-event`, `--taxonomy`, `--out-dir` |
| `ingest_dummy.py` | Injects dummy articles into `articles` + pre-creates `article_ai_results` with `status='done'` so the event classifier picks them up. Idempotent by `guid` (dedup) and via `save_article_analysis_result` ON CONFLICT |
| `reset_test_db.py` | Wipes classifier output (`event_articles`, `events`, `topic_causes`, `topics`) and resets `article_ai_results.status='done'` + `articles.embedding/core_content=NULL`. **3 safety guards**: `EVAL_ALLOW_DESTRUCTIVE_RESET=1`, localhost-only host (`--allow-remote` to override), and `--yes` |
| `reset_classifier_only.py` | Lighter reset for the **real-corpus** constraint loop: wipes only classifier output (`event_articles`, `events`, `topic_causes`, `topics`) and rewinds `article_ai_results.status` to `'done'` — **rows and summaries survive**. `reset_test_db.py` deletes `article_ai_results` outright, which is right for the synthetic loop but would force regenerating 2,578 LLM summaries on every constraint iteration. Shares the same 3 safety guards (imports `_check_safety_guards`, passing its own narrower banner text so the warning matches what it actually deletes). Verifies the result and exits 1 if anything survived that shouldn't |
| `rubric_checks.py` | Computes machine-checkable rubric violations against `../docs/entity_definitions.md` from a DB snapshot: 9 check IDs (R-E1/E2 event fragmentation signals, R-S1..S5 subtopic/hierarchy violations, R-T1/T2 topic duplication/category-title violations) plus a subtopic-row-count observation. Pure title classifiers/similarity functions live at module top (importable, stdlib-only — `difflib`, `re`); DB aggregation is separate per check. Console Korean summary by default, `--json` for machine-readable output. Reused for the Phase 0-2 production rebaseline and Phase 5 re-measurement |
| `constraint_checks.py` | Scores **human-reviewed pair constraints** (`constraints-v1`, in `data/constraints/`) against a re-classification result snapshot. 4 check IDs (C-EM/C-EC event must/cannot-link, C-TM/C-TC topic must/cannot-link). Satisfaction rate = `satisfied / (satisfied + violated)`; pairs whose articles are absent from the snapshot count as "불가" and are excluded from the denominator. **Pass criterion is no drop vs. a stored baseline, not zero violations** — `--baseline` compares and exits 1 on regression, `--write-baseline` records one, `--tolerance` allows a drop margin. Pure judgement functions at module top (stdlib only, no DB); JSON-file input, so it runs without Postgres. Console Korean summary by default, `--json` for machine-readable output |
| `diagnose_violations.py` | Joins constraint violations with `classify_events.py`'s per-article decision log (JSONL on stdout) to explain **why** each must-link violation happened. 4 causes with distinct fixes: **A** target event never retrieved (distance threshold), **B** retrieved but truncated out of the prompt (`EVENT_CANDIDATE_LIMIT` > `MAX_EVENT_CANDIDATES`), **C** LLM saw it and refused (prompt problem), **D** guardrail overrode the LLM's assign (score threshold). Inspects the *later-processed* article of each pair, since that is the one that had a chance to join the existing event. Also simulates both knobs: minimum `MAX_EVENT_CANDIDATES` that would rescue all B, and a score-threshold sweep showing must-link rescued vs. cannot-link broken at each step (first-order only — changing a threshold cascades into later candidate sets). With `--topic-log` it also diagnoses topic constraints, where the first split is **whether the event layer already failed**: two articles in the same event always share a topic, so a topic must-link violation means they landed in different events — if that separation also violates an event must-link constraint, no topic lever can fix it (`T_EVENT_PROPAGATED`). Remaining causes are `T_SUBTOPIC_SPLIT` (same parent, different leaf — reported with `decided_by` since embedding mode has no LLM lever) plus parent-level A/B/C/D |
| `extract_snapshot.py` | Reads the classifier output into the JSON snapshot the scoring tools consume — the step that used to require pasting SQL into the Supabase dashboard, which broke the iteration loop every cycle. Read-only. Unlike the SQL in `data/constraints/README.md` it assembles the JSON in Python from plain SELECTs, so it obeys the `?`-placeholder rule and runs on SQLite (testable). `--event-id-min` / `--topic-id-min` act as cursors for incremental extraction |
| `topic_harness.py` | **Iteration harness for topic classification.** One run does: reset the topic layer only (events preserved, so topic levers are isolated and no event re-classification is paid for) → drain `classify_topics.py` capturing stdout as the diagnostic log → extract snapshot → constraint rates → cause diagnosis → rubric cross-check → append a row to `results/topic_runs.csv` and write `results/topic-<run-id>.md`, auto-comparing against the previous run. `--set KEY=VALUE` patches `.env` for the run and restores it after (process env injection is defeated by `load_dotenv(override=True)`). `--skip-classify` rescores the current DB state. **The rate alone is not trustworthy**: topic `cannot_link` is 0 pairs in the gold, so collapsing every event into one topic scores 100% — `detect_warnings` flags a rate rise paired with a topic-count collapse, and the report always carries topic count and R-T1 next to the rate |
| `event_harness.py` | Same skeleton for the event layer. Reset reuses `reset_classifier_only` so article summaries survive. `count_pending_articles` must mirror the classifier's own `done_articles` query exactly — a mismatch makes the drain never finish or stop early, so it is pinned by test. Event failure is bidirectional (merge-everything scores must-link 100%, split-everything scores cannot-link 100%), so it warns on three patterns: single-article events >70%, rate rise paired with a ≥20% event-count drop, and must-link up while cannot-link down. Re-running events invalidates the topic layer, so the reset clears topics too |
| `harness_common.py` | Shared plumbing for both harnesses: CSV accumulation, previous-run comparison (parameterized by each harness's column sets), string→number coercion for CSV reads, `.env` patch/restore, and the drain loop. `drain` is the only shared loop that spends money, so its stuck detection — stop when the backlog stops shrinking — is directly tested |
| `sweep.py` | Runs one harness across several configs in sequence and prints a comparison table. `--config "tag:KEY=VAL,KEY=VAL"` per configuration; `--dry-run` prints the plan without spending. Rejects duplicate tags **before** running, since colliding run-ids would silently overwrite earlier results after the API spend |
| `annotation_guide.md` | Korean human annotation guide for Phase 1 gold relabeling (dev + holdout): decision tree for event identity ("same action/decision" test), merge/split rules with boundary rulings cited from the misclassification casebook (corrections, wire syndication, live updates, "사태/다툼" clusters), subtopic S-1..S-4 positive/negative examples, topic T-1/T-2 + naming-test-as-signal-not-gate, the ≥90% consistency spot-check procedure, and the issue-time-window holdout split rule |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `taxonomy/` | Hand-authored gold + holdout taxonomy trees (ground truth) (see `taxonomy/AGENTS.md`) |
| `data/` | Generated `dummy_articles.json` / `gold_labels.json` fixtures, incl. a `holdout/` split; also the hand-made `constraints/` gold and `seed/` article dumps (see `data/AGENTS.md`) |

Note: `results/` is created at runtime by `evaluate.py` and is **git-ignored** (not present in the
repo). `metrics.csv` and `<run-id>.md` there are regenerable artifacts, not source.

## For AI Agents

### Working In This Directory
- Scripts prepend `../src` to `sys.path` (like the top-level entry points) and reuse
  `collector.storage.ensure_db` for the connection, so internal imports are top-level
  (`from collector...`), **not** `src.`-prefixed. Keep that.
- **`reset_test_db.py` is destructive.** Preserve all 3 guards; never weaken the localhost check
  or the `EVAL_ALLOW_DESTRUCTIVE_RESET` gate. `run_iteration.py` injects that env var itself.
- The drain loops in `run_iteration.py` exist because classifiers process one batch per process and
  `.env` overrides parent env — don't "simplify" them into a single classifier call.
- Before trusting a run, execute the **oracle** (`--use-gold-as-pred`, all levels must be 1.0) and
  **random** (`--random-pred`, must be well below 1.0) self-tests; they validate `metrics.py`
  itself and need no DB.
- Keep `metrics.py` dependency-free (stdlib only) — it is also unit-tested from
  `tests/test_evaluate_helpers.py`.

### Testing Requirements
- `evaluate.py` data-wrangling helpers and `metrics.evaluate_level` covered-only behavior are
  covered by `tests/test_evaluate_helpers.py` (DB-free, synthetic dicts):
  `python -m unittest tests.test_evaluate_helpers`. Run it after changing either file.
- A full `run_iteration.py` cycle needs a **local pgvector DB** (Supabase CLI recommended; bare
  `pgvector/pgvector` works but lacks the `article_count` trigger, which step 4 resyncs) and an
  `UPSTAGE_API_KEY` unless you stay on `generate_dummy.py --dry-run`.

### Common Patterns
- `gold_labels.json` maps `article_guid → {gold_topic, gold_subtopic, gold_event}`; event/subtopic
  metrics aggregate article gold to the event level by majority vote (`_resolve_event_gold_labels`,
  warns on intra-event label disagreement).
- Tune via env vars read by `src/*/settings.py` (`EVENT_DISTANCE_THRESHOLD`,
  `TOPIC_SUBTOPICS_ENABLED`, …); `run_iteration.py` injects `TOPIC_SUBTOPICS_ENABLED=true`.

## Dependencies

### Internal
- `collector.storage` (connection + schema validators), `classify_events.py` / `classify_topics.py`
  (invoked as subprocesses), `src/*/settings.py` (env-var defaults), `embedding` (indirectly via
  the classifiers).

### External
- Postgres + pgvector (local), Upstage API (embeddings + LLM for non-dry-run generation),
  `psycopg`, `python-dotenv`. `metrics.py` itself is stdlib-only.

<!-- MANUAL: -->
