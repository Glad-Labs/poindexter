# Handler: `tap.singer_subprocess`

Run a Singer-protocol tap binary as a subprocess, parse its stdout (SCHEMA / RECORD / STATE messages), and route each record through a registered record-handler that writes to a target table. State persists back to the row's `state` column on a clean exit so the next run resumes from the last bookmark.

The Singer protocol (https://github.com/singer-io/getting-started) is the de-facto standard for stdout-based ETL — 600+ taps already exist for Stripe, Google Search Console, GA4, HubSpot, MySQL, Postgres, Salesforce, etc. With this handler, Poindexter consumes any of them without per-source code on our side.

## Row configuration

```
name:             operator-chosen slug, e.g. "gsc_main", "stripe_charges"
handler_name:     singer_subprocess
tap_type:         informational, e.g. "tap-google-search-console"
target_table:     where the record_handler writes (e.g. "external_metrics")
record_handler:   registered handler under "tap" surface that consumes RECORDs
                  (e.g. "external_metrics_writer")
state:            JSONB; tap-managed, written back on success
config:           {
                    "command": "tap-google-search-console",        # or "python -m tap_csv"
                    "tap_config": { ... tap-specific JSON ... },
                    "streams": ["page_metrics"],                    # optional whitelist
                    "max_records": 50000,                           # safety cap, default 50k
                    "timeout_seconds": 600,                         # SIGTERM after this; SIGKILL +5s
                    "metrics_mapping": { ... }                      # consumed by record_handler
                  }
enabled:          false until operator flips on
```

## What happens at run time

1. Read `config.command` (shlex-split — no shell expansion, no injection vectors).
2. Read `config.tap_config` — written to a temp `config.json`.
3. Read `row.state` — written to a temp `state.json` (incremental bookmark resumption).
4. Spawn `<command> --config config.json --state state.json`.
5. Parse stdout line-by-line:
   - **SCHEMA** — register the schema for the named stream (RECORDs without a preceding SCHEMA fail).
   - **RECORD** — dispatch to the registered `record_handler` with `{stream, record, schema, time_extracted}`.
   - **STATE** — buffer the latest STATE.value; persist to the row only on a clean run.
6. Wait for the subprocess. On exit 0 → commit state. On non-zero → record `last_error` with stderr tail (last 200 lines, capped 2 KB), do not advance state.

## Safety + limits

- **No shell expansion.** `shlex.split` cannot interpret `;`, `&&`, backticks, or `$(...)`. The `command` field is operator-supplied but cannot escape into shell execution.
- **Timeout.** `config.timeout_seconds` (default 600s). On expiry, SIGTERM, then SIGKILL after 5s. The next run starts fresh from the unchanged state.
- **Max records cap.** `config.max_records` (default 50k) — prevents a runaway tap from filling the target table.
- **Stderr cap.** Last 200 lines kept, drained concurrently so a chatty tap can't fill the OS pipe and block.
- **State atomicity.** STATE only commits on exit 0. A failed mid-stream run leaves the bookmark unchanged, so the next run re-fetches the same window.

## Operator runbook

### First-time setup for a Singer tap (e.g. GSC)

1. Install the tap into Poindexter's Python environment:

   ```
   pip install singer-tap-google-search-console
   ```

   Or use any Singer-spec tap regardless of language — only `command` matters.

2. Build a `tap_config.json` per the tap's documentation. For GSC that's an OAuth token + a list of properties to query.

3. Insert a row:

   ```sql
   INSERT INTO external_taps
     (name, handler_name, tap_type, target_table, record_handler,
      schedule, config, enabled, metadata)
   VALUES (
     'gsc_main',
     'singer_subprocess',
     'tap-google-search-console',
     'external_metrics',
     'external_metrics_writer',
     'every 6 hours',
     jsonb_build_object(
       'command', 'tap-google-search-console',
       'tap_config', '{
         "client_id": "...",
         "client_secret": "...",
         "refresh_token": "...",
         "site_urls": ["https://www.gladlabs.io"],
         "start_date": "2026-04-01"
       }'::jsonb,
       'streams', jsonb_build_array('performance_report_date'),
       'metrics_mapping', '{
         "performance_report_date": {
           "source": "google_search_console",
           "date_field": "date",
           "post_field": "slug",
           "metric_fields": ["impressions", "clicks", "ctr", "position"],
           "dimension_fields": ["country", "device", "query"]
         }
       }'::jsonb,
       'max_records', 50000,
       'timeout_seconds', 1800
     ),
     FALSE,
     jsonb_build_object('description', 'Google Search Console performance metrics')
   );
   ```

4. Test on demand:

   ```
   poindexter taps run gsc_main
   ```

5. Flip on:
   ```
   poindexter taps enable gsc_main
   ```

### Common failure modes

| Symptom                                         | Likely cause                                       | Fix                                                                      |
| ----------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------ |
| `tap exited 1` with auth-related stderr         | OAuth token expired                                | refresh `tap_config.refresh_token`, re-run                               |
| `RECORD for stream X arrived before its SCHEMA` | tap is non-compliant; emits records before schemas | report upstream; use a different tap                                     |
| `tap timed out after 600s`                      | tap is slow or stuck                               | bump `timeout_seconds`; consider narrower date range in tap_config       |
| Records inserted to wrong table                 | `record_handler` mismatch                          | verify the row's `record_handler` matches what your taps' streams expect |

### Multi-stream taps

Some taps emit many streams (page_metrics, query_metrics, device_metrics). Use:

- `config.streams` — whitelist; records on streams not listed are dropped.
- `config.metrics_mapping` — one mapping per stream the operator wants persisted; unmapped streams are silently skipped by `external_metrics_writer`.

### Adding a brand-new record_handler

Register a handler under the `tap` surface:

```python
from services.integrations.registry import register_handler

@register_handler("tap", "stripe_charge_writer")
async def stripe_charge_writer(payload, *, site_config, row, pool):
    record = payload["record"]
    # INSERT into your target table...
```

Then point an `external_taps.record_handler` row at `stripe_charge_writer`. The `tap.singer_subprocess` dispatcher resolves the handler by name and routes RECORD messages to it.

## Companion handler

`tap.external_metrics_writer` is the default record consumer for analytics-shaped taps. See `docs/integrations/tap_external_metrics_writer.md` for its mapping config.

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- GH-103 (Singer tap support — closes when this handler is in production use)
- GH-27 (feedback-loop tables — `external_metrics` is the target for analytics taps)
- Singer spec: https://github.com/singer-io/getting-started/blob/master/docs/SPEC.md
