# Handler: `tap.singer_subprocess` (stub — full impl pending)

**Status:** v1 ships as a stub that raises `NotImplementedError`. Enabling a row with `handler_name='singer_subprocess'` will fail loudly at dispatch time — the row itself is valid, the handler just isn't built yet. No operator should hit this in normal use because the seeded rows all target `builtin_topic_source`.

## Intended behavior (follow-up work)

When implemented, this handler will:

1. Read `row.config.command` — the path to (or PEP 723 spec for) a Singer-spec tap binary.
2. Read `row.config.tap_config` JSONB — written to a temp `config.json`.
3. Read `row.state` JSONB — written to a temp `state.json` for incremental sync.
4. Spawn the tap binary: `<command> --config config.json --state state.json`.
5. Parse the tap's stdout as Singer messages (one JSON object per line):
   - `SCHEMA` — validate against the operator's declared schema in `row.config.schema`.
   - `RECORD` — dispatch to `row.record_handler` (a registered handler under any surface) which knows how to INSERT into `row.target_table`. This is how a Singer tap can feed into the same handlers as inbound webhooks (e.g. Lemon Squeezy via tap OR webhook, both using `revenue_event_writer`).
   - `STATE` — remember the latest state; persist back to `row.state` on successful completion so the next run resumes from here.
6. Wait for the subprocess to exit. On exit code 0, commit the new state. On non-zero, record `last_error` with stderr tail and leave state unchanged.
7. Return `{"records": N}` for the runner's total count.

### Required row configuration (future)

```
name:             operator-chosen slug, e.g. "stripe_charges"
handler_name:     singer_subprocess
tap_type:         Singer package ID, e.g. "singer-io/tap-stripe"
target_table:     e.g. "revenue_events"
record_handler:   e.g. "revenue_event_writer"  (registered in any surface)
schedule:         "every 1 hour"
config:           {
                    "command": "tap-stripe",
                    "tap_config": {
                      "client_secret_ref": "stripe_api_key",
                      "account_id": "acct_..."
                    },
                    "credentials_ref": "stripe_api_key"
                  }
state:            {}    (populated by the runner after each successful run)
enabled:          false until operator flips on
```

### Open design questions for the follow-up

- Subprocess timeout handling — taps that hang indefinitely vs. long-but-progressing-slowly syncs.
- Back-pressure when `target_table` is slow — buffer on disk? Drop records with a warning?
- `record_handler` contract — does it receive one record at a time, or batched? Batched inserts are much faster but complicate ordering for STATE.
- State persistence atomicity — write state only after the last record for the batch is confirmed persisted, to prevent lost records on mid-stream failure.

Tracked as future work under GH-103. When an operator wants to wire a concrete Singer tap, that's when we finalize the design against the real use case instead of an imagined one.

## Related

- RFC: `docs/architecture/declarative-data-plane-rfc-2026-04-24.md`
- Sibling (working) handler: `tap.builtin_topic_source`
- GH-103 (external Singer tap support issue)
- Singer spec: https://github.com/singer-io/getting-started
