<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# docs

## Purpose
Design and diagnosis documentation (Korean). Read these before changing classifier behavior.

## Key Files
| File | Description |
|------|-------------|
| `topic_classification_overview.md` | Design of the topic-grouping stage: cause/result extraction, pgvector `topic_causes` search, assign-or-create, and the optional 2-level hierarchy (`--subtopics`, `topics.parent_topic_id`) |
| `classification_diagnosis.md` | E2E diagnostic report (2026-06-29) on real data: found 76.8% single-article events (fragmentation), core issue is **over-create** not over-assign; recommends tuning distance threshold (0.45→0.50), widening the 2-day search window, cross-category topic matching, and duplicate-topic detection |

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
