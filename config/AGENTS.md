<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-07-01 | Updated: 2026-07-01 -->

# config

## Purpose
Static configuration data loaded at runtime.

## Key Files
| File | Description |
|------|-------------|
| `feeds.json` | RSS source list: a single `"feeds"` array of ~69 feed URLs across 20+ Korean news outlets (politics/economy/society/international) |

## For AI Agents

### Working In This Directory
- `feeds.json` is read via `collector.utils.load_feed_urls()` (path default
  `config/feeds.json` from `collector.settings`); CLI `--feeds` args are merged and deduplicated.
- Keep the top-level shape `{"feeds": [ ...urls... ]}` — the loader expects the `feeds` key.
- Publisher/category/bias inference for each feed is derived from the URL by
  `collector.utils` pattern tables, not stored here.

### Common Patterns
- Plain data file; no code. Add a feed by appending its URL to the array.

## Dependencies

### Internal
- Consumed by `collector.utils.load_feed_urls` / `collector.cli`.

<!-- MANUAL: -->
