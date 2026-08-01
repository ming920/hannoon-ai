<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-10 -->

# docs

## Purpose
Design and diagnosis documentation (Korean). Read these before changing classifier behavior.

## Key Files
| File | Description |
|------|-------------|
| `local-setup.md` | Onboarding: how a teammate brings up a **local Supabase** and runs event/topic classification. Schema is owned by the sibling `hannoon-supabase` repo (not this one); covers `supabase db reset`, seeding 2,578 articles via `../scripts/apply_local_seed.py`, and two `.env` paths (Upstage API vs local Ollama + `../scripts/local_embedding_proxy.py` for the 1024→4096 dimension gap) |
| `topic_classification_overview.md` | Design of the topic-grouping stage: cause/result extraction, pgvector `topic_causes` search, assign-or-create, and the optional 2-level hierarchy (`--subtopics`, `topics.parent_topic_id`) |
| `classification_diagnosis.md` | E2E diagnostic report (2026-06-29) on real data: found 76.8% single-article events (fragmentation), core issue is **over-create** not over-assign; recommends tuning distance threshold (0.45→0.50), widening the 2-day search window, cross-category topic matching, and duplicate-topic detection |
| `entity_definitions.md` | 2026-07-10 user-confirmed source-of-truth definitions for topic/subtopic/event (purpose, definition, creation criteria, exclusions, examples), each mapped to a machine-checkable rubric ID (R-Exx/R-Sxx/R-Txx) implemented in `../eval/rubric_checks.py`; criteria that can't be machine-checked are marked as manual judgment for Phase 1 gold relabeling |

## For AI Agents

### Working In This Directory
- These docs are operator-facing context, not code — keep them in sync when classifier behavior,
  thresholds, or the hierarchy design changes.
- The diagnosis doc is a point-in-time finding; cite its date when referencing its numbers.

### Common Patterns
- Written in Korean; match that when editing.

## Dependencies

### Internal
- Describes `src/event_classifier/` and `src/topic_classifier/` behavior.

<!-- MANUAL: -->
