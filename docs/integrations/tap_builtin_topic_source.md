# Handler: `tap.builtin_topic_source`

Adapter that brings the existing in-repo topic_source scrapers (hackernews, devto, web_search, knowledge, codebase) into the declarative `external_taps` model.

## What it does

Delegates to `services.topic_sources.runner.run_all()` (the same runner the scheduler already uses) and filters the summary to the source name specified in `row.tap_type`. The actual topic persistence (dedup, similarity scoring, `content_task` row creation) happens inside the existing plugin — this handler is purely a shape adapter.

## Row configuration

```
name:            operator-chosen slug, usually == tap_type
handler_name:    builtin_topic_source
tap_type:        name of the in-repo topic_source plugin
                 (hackernews | devto | web_search | knowledge | codebase)
target_table:    content_tasks  (advisory — the plugin already knows)
schedule:        how often to fire (e.g. "every 1 hour")
config:          forwarded to the plugin via the topic_sources runner's
                 PluginConfig.load path. Free-form per-source config.
enabled:         false until operator flips on
```

## Seeded rows (all disabled)

Migration 0090 seeds one row per current topic_source plugin with sensible schedules:

| Name         | Schedule       |
| ------------ | -------------- |
| `hackernews` | every 1 hour   |
| `devto`      | every 2 hours  |
| `web_search` | every 6 hours  |
| `knowledge`  | every 12 hours |
| `codebase`   | every 1 day    |

## Operator runbook

### Enabling a seeded topic source

```
poindexter taps enable hackernews
poindexter taps run hackernews      # optional — fires immediately
```

Wait for the next scheduler tick and verify `last_run_at` populates:

```
poindexter taps list
```

### Adding a new built-in source

If you write a new `services/topic_sources/*.py` plugin and register it under `plugins.topic_source`, seed it as a tap row:

```sql
INSERT INTO external_taps
  (name, handler_name, tap_type, target_table, schedule, enabled, metadata)
VALUES (
  'reddit',
  'builtin_topic_source',
  'reddit',
  'content_tasks',
  'every 2 hours',
  FALSE,
  jsonb_build_object('description', 'Reddit submissions from operator-defined subreddits')
);
```

### Disabling

```
poindexter taps disable hackernews
```

Immediately effective — no restart.

### Why this adapter exists

The existing topic_source plugin architecture is already declarative-ish (via `plugin.topic_source.<name>` app_settings rows). This adapter just brings the enablement into one table (`external_taps`) so:

1. Grafana can show tap state alongside webhook / retention state.
2. Operator CLI is uniform (`poindexter taps …` mirrors `webhooks …` and `retention …`).
3. Future Singer-protocol taps live in the same table — one list of "what is this system ingesting from?".

## Caveats

- **Full-runner invocation:** each handler call invokes `run_all()` and filters to the tap's name. When multiple built-in taps are enabled, each fires its own `run_all()`. The existing runner is idempotent via content_hash dedup, so this is safe but slightly wasteful. Fixing this would require the topic_sources runner to accept a single-source filter — tracked as a follow-up.
- **No per-source config isolation in v1:** the old `plugin.topic_source.<name>.config` app_settings rows still drive per-source behavior. Moving that config into `external_taps.config` is a follow-up migration.

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Sibling handler: `tap.singer_subprocess` (stub — full external Singer tap support)
- Existing runner: `services/topic_sources/runner.py`
- GH-103 (external taps issue)
