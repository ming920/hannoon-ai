<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# scripts

## Purpose
Operator and local-dev helpers. Two kinds live here, and they must not be confused:
- **Production maintenance** (`backfill_summaries.py`) — one-off backfills run against the real DB.
- **Local dev support** (`local_embedding_proxy.py`, `apply_local_seed.py`) — used only while
  bringing up a local Supabase per `../docs/local-setup.md`; both refuse or are meaningless
  against production. Neither is part of the automated pipeline.

## Key Files
| File | Description |
|------|-------------|
| `local_embedding_proxy.py` | **Local dev only.** OpenAI-compatible embedding proxy in front of Ollama that zero-pads vectors to the schema's `vector(4096)` (bge-m3 returns 1024). Zero-padding preserves cosine distance, so pgvector candidate search is unaffected. Forces `encoding_format=float` upstream — the OpenAI SDK defaults to base64, which would slip through unpadded. Padding/encoding logic is pure (`pad_embeddings`, `force_float_encoding`) and tested in `tests/test_local_embedding_proxy.py` |
| `apply_local_seed.py` | **Local dev only.** Loads `eval/data/seed/*.sql` into a local Postgres without psql (psycopg instead). Guards, split so host checks run *before* connecting: non-localhost is refused without `--allow-remote`, and **`--replace` (which deletes 11 tables) is refused on any remote host even with `--allow-remote`**. Article seeds refuse a non-empty `articles` table; event seeds refuse an empty one (they reference articles by FK). Tested in `tests/test_apply_local_seed.py` |
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
