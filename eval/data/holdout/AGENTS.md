<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-10 | Updated: 2026-07-10 -->

# holdout

## Purpose
Holdout-split eval fixtures generated from `../../taxonomy/holdout_taxonomy.json` — an unseen
domain with no overlap to the dev set. Used to confirm the subtopic embedding mode generalizes
(the 2026-07-03/04 validation that adopted `TOPIC_SUBTOPIC_SIM_THRESHOLD=0.65` over the τ=0.55 that
over-merged here). Same schema as the parent `data/` fixtures.

## Key Files
| File | Description |
|------|-------------|
| `dummy_articles.json` | List (101) of article objects (`guid`, `title`, `summary`, `content`, `publisher`, `bias_type`, `published_at`, `category`); guids in the `dummy-E1xx-*` range |
| `gold_labels.json` | Dict `article_guid → {gold_topic, gold_subtopic, gold_event}` for the holdout set (~5 topics / 11 subtopics / 19 events) |

## For AI Agents

### Working In This Directory
- Generated artifacts — regenerate from `../../taxonomy/holdout_taxonomy.json` via
  `../../generate_dummy.py --out-dir eval/data/holdout` rather than hand-editing.
- Point the harness at these by passing `--articles`/`--gold` to `ingest_dummy.py`/`evaluate.py`;
  keep this set **out of** any parameter-tuning loop so it stays a true holdout.

## Dependencies

### Internal
- Output of `../../generate_dummy.py`; input to `../../ingest_dummy.py` and `../../evaluate.py`.

<!-- MANUAL: -->
