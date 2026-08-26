<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-10 | Updated: 2026-07-10 -->

# taxonomy

## Purpose
Hand-authored **ground-truth taxonomy trees** that define the correct `topic → subtopic → event`
structure the eval harness scores against. `generate_dummy.py` reads a taxonomy here and emits the
matching `../data/*` article + gold-label fixtures. This is the human-collaboration point of the
harness — the metrics are only as meaningful as these trees.

## Key Files
| File | Description |
|------|-------------|
| `gold_taxonomy.json` | Primary dev/design set. `{_readme, topics}`; ~5 topics → 14 subtopics → 29 events. Each event carries `base_facts`, `num_articles`, `article_variants_hint`, and `gold_case*` metadata used by `generate_dummy.py` (dry-run and LLM). Drives `../data/dummy_articles.json` (~180 articles) + `../data/gold_labels.json` |
| `holdout_taxonomy.json` | Unseen-domain **holdout** set (authored 2026-07-04) with no overlap to `gold_taxonomy.json`, used to check the subtopic embedding mode for overfitting. ~5 topics → 11 subtopics → 19 events; drives `../data/holdout/*` (101 articles) |

## For AI Agents

### Working In This Directory
- Plain JSON data, no code. The `_readme` key documents the schema and design intent (Korean) —
  read it before editing; it is ignored by the loader, which only consumes `topics`.
- `event.id` / subtopic / topic ids become the `gold_event` / `gold_subtopic` / `gold_topic`
  labels; keep them stable, or regenerate `../data/` after any change.
- Adding events/topics grows the fixture; follow the scale-up ladder in `../README.md`
  (small → mid → large) rather than ballooning one set.

## Dependencies

### Internal
- Consumed by `../generate_dummy.py`; the ids it defines are the gold labels compared in
  `../evaluate.py`.

<!-- MANUAL: -->
