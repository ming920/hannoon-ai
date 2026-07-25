<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-10 | Updated: 2026-07-10 -->

# data

## Purpose
Generated eval fixtures: the dummy articles fed into the pipeline and the gold labels scored
against. Produced by `../generate_dummy.py` from `../taxonomy/gold_taxonomy.json`, consumed by
`../ingest_dummy.py` (articles) and `../evaluate.py` (gold labels).

## Key Files
| File | Description |
|------|-------------|
| `dummy_articles.json` | List (~180) of article objects: `guid`, `title`, `summary`, `content`, `publisher`, `bias_type`, `published_at`, `category`. `guid` is the join key (e.g. `dummy-E001-0`) |
| `gold_labels.json` | Dict `article_guid → {gold_topic, gold_subtopic, gold_event}` — the ground truth for the dev set |
| `.gitkeep` | Keeps the directory tracked |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `holdout/` | Unseen-domain holdout split fixtures (same schema) (see `holdout/AGENTS.md`) |
| `constraints/` | **Hand-made, not generated.** Human-reviewed pair constraints (`constraints-v1`) consumed by `../constraint_checks.py`. Cannot be regenerated — never overwrite (see `constraints/README.md`) |
| `seed/` | **Hand-made, not generated.** Raw `articles` INSERT dumps that reproduce the corpus the constraint gold assumes. The 313-row subset covers only 38% of the constrained articles — use the full dump for verification (see `seed/README.md`) |

## For AI Agents

### Working In This Directory
- **Generated artifacts, not hand-edited.** Regenerate via `../generate_dummy.py` after changing a
  taxonomy rather than editing article/label JSON by hand; the two files must stay in sync on `guid`.
  **This rule covers the files at this level and `holdout/` only.** `constraints/` and `seed/` are the
  opposite: hand-made, irreproducible inputs. Never overwrite or "regenerate" them.
- `gold_labels.json` is also the input to the DB-free oracle/random self-tests
  (`../evaluate.py --use-gold-as-pred` / `--random-pred`).

## Dependencies

### Internal
- Output of `../generate_dummy.py`; input to `../ingest_dummy.py` and `../evaluate.py`.

<!-- MANUAL: -->
