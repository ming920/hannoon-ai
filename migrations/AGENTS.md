<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# migrations

## Purpose
Hand-authored Postgres schema migrations, applied manually / via Supabase (the Postgres path in
`storage.py` does **not** auto-create schema). SQLite local dev auto-creates its own schema and
does not use these files.

## Key Files
| File | Description |
|------|-------------|
| `0001_topics_parent_topic_id.sql` | Adds self-referential `parent_topic_id BIGINT` to `topics` (FK ON DELETE SET NULL) + index, enabling the 2-level subtopic hierarchy; existing topics become roots (`parent_topic_id IS NULL`) |

## For AI Agents

### Working In This Directory
- Migrations are **applied separately from code deploys** — production schema (including pgvector
  `vector(4096)` columns) lives in Supabase, not in the app. Adding a column here does not change
  the running DB until an operator applies it.
- Number new files sequentially (`0002_...`) and keep them idempotent/forward-only where
  practical.
- When a migration adds a column the classifiers depend on (like `parent_topic_id`), note the
  dependency in the relevant `src/.../AGENTS.md` and gate the feature behind its env flag.

### Common Patterns
- Raw SQL DDL; one logical change per file.

## Dependencies

### Internal
- `db.topics` / `db.topic_causes` and `topic_classifier` rely on `parent_topic_id` for subtopics.

### External
- Postgres + pgvector (Supabase-managed).

<!-- MANUAL: -->
