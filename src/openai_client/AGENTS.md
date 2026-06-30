<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# openai_client

## Purpose
OpenAI-compatible Chat Completions wrapper used by every LLM call in the project. Defaults to
the Upstage base URL, with deliberately generic env var names so the provider can be swapped.
`__init__.py` is empty.

## Key Files
| File | Description |
|------|-------------|
| `client.py` | `LLMClient(model, api_key, base_url, timeout)` with `request()` and `request_json()`; module-level `parse_json_object()` |

## For AI Agents

### Working In This Directory
- `request_json()` enforces `response_format=json_object` and validates `required_keys`; prefer
  it over `request()` for any structured output.
- `parse_json_object()` tolerantly extracts the outer `{...}` when a model wraps JSON in prose —
  use it rather than a bare `json.loads`.
- Env vars: `LLM_API_KEY` (→ `UPSTAGE_API_KEY` / `OPENAI_API_KEY` fallback), `LLM_BASE_URL`
  (default `https://api.upstage.ai/v1`), `LLM_TIMEOUT_SECONDS` (60), `LLM_MAX_RETRIES` (0). Never
  hard-code keys or URLs.

### Testing Requirements
- `parse_json_object` behavior is asserted in `tests/test_summary_quality.py`. Mock `LLMClient`
  in tests rather than making network calls.

### Common Patterns
- A single shared client class for cleanup, summary, event, and topic calls — the model name is
  passed per-call/per-stage from each stage's `settings.py`.

## Dependencies

### External
- `openai`, `python-dotenv`.

<!-- MANUAL: -->
