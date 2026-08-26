<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# db

## Purpose
Shared entity-level DB helpers for the event and topic classifiers. These operate on a
`storage.py` connection (SQLite or Postgres) and own the SQL for events, topics, and the
`topic_causes` embedding-search table. `__init__.py` is empty (namespace package).

## Key Files
| File | Description |
|------|-------------|
| `events.py` | `Event` dataclass + helpers: `fetch_unassigned`, `assign_topic`, `find_prev_event`/`find_next_event_id`/`link_into_chain` (doubly-linked chain), `search_candidate_events` (pgvector, recent 2 days), `create_new_event`, `update_event_summary`, `link_article_to_event` |
| `topics.py` | `Topic` dataclass (with `parent_topic_id`) + `create_topic` (root if no parent), `update_topic` (writes go through `summary_utils`), `find_duplicate_topic` (title-similarity dedup guard within a category+parent scope, used by `topic_classifier.pipeline._dedup_guard` before any create) |
| `topic_causes.py` | `TopicCandidate` dataclass + `search_candidates` (cosine distance, category filter, `roots_only`/`parent_topic_id` hierarchy scoping) and `add_cause` |

## For AI Agents

### Working In This Directory
- **Write all SQL with `?` placeholders** — the Postgres wrapper rewrites them. Don't hand-write
  `%s`.
- All title/summary writes must pass through `summary_utils` (`events.py` and `topics.py`
  already do). Keep that invariant for any new write helper.
- `search_candidates` hierarchy options are **mutually exclusive**: `roots_only=True` for
  parent-stage search, `parent_topic_id=<id>` for subtopic-stage search, both omitted for flat
  mode. Don't pass both.
- These helpers assume **pgvector** is present; they are production-path code (no SQLite
  auto-schema for vector columns).

### Testing Requirements
- `create_topic` parent handling and `search_candidates` filters are covered by
  `tests/test_topic_subtopics.py` — run it after changes here.

### Common Patterns
- Candidate searches return at most `top_k` rows; `topic_causes` caps causes per topic at
  `MAX_CAUSES_PER_TOPIC`.
- The event chain orders by `(created_at, id)` and handles inserting events older than the
  current topic head.

## Dependencies

### Internal
- `summary_utils` (normalization). Consumed by `event_classifier` and `topic_classifier`.

### External
- pgvector `vector(4096)` columns via the `storage.py` Postgres connection.

<!-- MANUAL: -->
