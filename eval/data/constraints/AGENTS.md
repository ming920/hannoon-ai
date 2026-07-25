<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-25 | Updated: 2026-07-25 -->

# constraints

## Purpose
Human-reviewed **pair constraints** (`constraints-v1`) — the only hand-made ground truth in this
repo. Everything else under `../` is synthetic output of `../../generate_dummy.py`. Consumed by
`../../constraint_checks.py`, which scores a re-classification snapshot against these pairs and
gates on **no drop vs. a stored baseline** (not zero violations).

Migrated here on 2026-07-25 from a top-level `테스트/` directory that no code referenced.

## Key Files
| File | Description |
|------|-------------|
| `review_2026-07-20.json` | Review of the 2026-07-07 snapshot by 정예은·오재민, exported 2026-07-20. `article_pool` 2,561 ids; event `must_link` 1,244 / `cannot_link` 48; topic `must_link` 1,309 / `cannot_link` 0; `review_log` 100 entries (all `unit="event"`; labels ok 49 / split 34 / vague 11 / merge 6; 16 carry a memo) |
| `README.md` | Korean operator guide: format, usage, the extraction SQL that produces the comparison snapshot, and the output-table reference |
| `baseline.json` | (not yet created) Written by `constraint_checks.py --write-baseline`; holds the four satisfaction rates that later runs are compared against |

## For AI Agents

### Working In This Directory
- **Hand-made and irreproducible. Never overwrite, "regenerate", or reformat `review_*.json`.**
  There is no generator for it; a lost file means redoing human review. This is the exact opposite
  of the `../dummy_articles.json` / `../gold_labels.json` rule.
- `review_log` memos are keyed by the *review-time event id* (`ref_id`), not by article pair.
  `constraints-v1` carries no article→source-event map, so memos **cannot** be auto-attached to a
  violated pair. `constraint_checks.py` prints them as a separate reference section instead — do not
  "fix" this by guessing a mapping.
- `_work` is the review UI's internal state (`events` keyed by event-id string → `{excluded,
  mergeWith, label}`). It is not used for judgement; treat it as opaque.
- Constrained articles must exist in the compared snapshot or the pair counts as 불가 and drops out
  of the rate denominator. Seed the corpus from `../seed/` — and use the **full** dump, since the
  313-row subset covers only 290 of the 764 constrained articles.

## Dependencies

### Internal
- Input to `../../constraint_checks.py`; corpus counterpart is `../seed/`.

<!-- MANUAL: -->
