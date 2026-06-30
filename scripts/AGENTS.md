<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# scripts

## Purpose
One-off maintenance / backfill scripts run by operators against the production DB. Not part of
the automated pipeline.

## Key Files
| File | Description |
|------|-------------|
| `backfill_summaries.py` | Bulk-normalize existing article/event/topic summaries (and optionally regenerate them with an LLM rollup); can reset timeout-failed article jobs |

## For AI Agents

### Working In This Directory
- `backfill_summaries.py` is **dry-run by default** — it only writes with `--apply`. Preserve
  that safety default in any change.
- Flags: `--database-url` (Postgres/Supabase), `--limit` (batch, default 100), `--apply`,
  `--llm-rollups` (regenerate instead of deterministic clip), `--llm-model`,
  `--reset-timeout-jobs`.
- Reuse `summary_utils` normalization and the `storage.py` connection API rather than writing
  raw SQL/normalization here.

### Testing Requirements
- No dedicated tests; validate manually against a non-production DB with `--apply` omitted first.

### Common Patterns
- Run as a standalone module: `python scripts/backfill_summaries.py [options]`.

## Dependencies

### Internal
- `collector.storage` (connection), `summary_utils` (normalization), `openai_client.client`
  (LLM rollups).

### External
- `psycopg`-backed Postgres.

<!-- MANUAL: -->
