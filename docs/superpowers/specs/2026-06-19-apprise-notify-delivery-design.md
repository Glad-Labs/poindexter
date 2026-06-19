# Apprise notification delivery — design

**Date:** 2026-06-19
**Status:** Approved (design) — brainstorming session 2026-06-19
**Tracking:** OSS surface → file the implementation issue on `Glad-Labs/poindexter`; code PR opens against `Glad-Labs/glad-labs-stack` per the two-remote model.
**Surfaced by:** maintenance-burden audit (this session) — "what do I have hand-rolled that an OSS product could replace?"

## Problem / motivation

Operator notifications fan out through two **hand-written, per-channel HTTP
handlers**:

- `services/integrations/handlers/outbound_discord.py` — POSTs `{"content": …}`
  to a Discord webhook URL.
- `services/integrations/handlers/outbound_telegram.py` — POSTs to the Telegram
  Bot API `sendMessage` and parses the `{"ok": …}` envelope.

Each new notification channel (Slack, ntfy, Pushover, email, SMS, …) today
means **writing and maintaining another handler module** with its own HTTP
shape, auth format, and error parsing. [Apprise](https://github.com/caronc/apprise)
already speaks ~100 notification services behind one `add(url)` / `notify()`
API, and it is **already shipped in our image** (`apprise 1.11.0` is in
`poetry.lock`, pulled in transitively by Prefect).

This design replaces the two per-channel delivery handlers with **one generic,
data-driven Apprise handler**, so future channels become a **row insert**, not
a code change.

## The honest ROI ceiling

This is **tidy + optionality**, not firefighting. State it plainly so the
change is approved with eyes open:

- The two existing handlers are stable and rarely change.
- The dependency is already shipped (zero new supply-chain surface, zero image
  growth).
- Behavior is **identical today** — same two channels, same messages.
- The only real payoff: **future channel optionality** + one delivery path
  instead of N, aligning the notification surface with the existing
  declarative-data-plane pattern ("add a platform = insert a row").

## Hard constraint — Telegram streaming stays bespoke

`outbound_telegram.py` also exposes `send_telegram_message` /
`edit_telegram_message` / `_parse_bot_result`. These are used by
`services/pipeline_streaming.py` to **edit a single Telegram message in place**
as a pipeline run progresses (it needs the returned `message_id` and an
`editMessageText` call). Apprise is fire-and-forget — no `message_id`, no edit.

**Therefore:** the Telegram Bot API helpers are retained. Only the
**notify (fire-and-forget) path** moves to Apprise. The `outbound_telegram.py`
module shrinks to its helper functions; its `@register_handler("outbound",
"telegram_post")` wrapper is removed.

## Scope (YAGNI)

**In scope:** the two operator-notify rows — `telegram_ops` and `discord_ops`.

**Explicitly out of scope:**

- `vercel_isr` (an ISR revalidation call, not a notification).
- `alertmanager` (inbound webhook receiver).
- `publishing_mastodon` / `publishing_youtube` (a different surface with its own
  adapter semantics).
- Wiring any _new_ channel now — that is the whole point: it becomes trivial
  later.

## Architecture

### Config model — `apprise_url` template with placeholder substitution

The row carries the recipe; the handler stays ~15 lines and never grows. The
non-secret Apprise URL lives in the row's `config` JSONB; the secret stays in
`app_settings` and is substituted only at dispatch time, so the token never
lands in a non-secret column.

Placeholders supported in `config.apprise_url`:

- `{secret}` — the value resolved via `resolve_secret(row, site_config)` (the
  existing `secret_key_ref` mechanism).
- `{<config-key>}` — any other key present in the row's `config` (e.g.
  `{chat_id}`).

Row shapes after re-point:

| Row            | `handler_name`   | `secret_key_ref`          | `config.apprise_url`          | other config          |
| -------------- | ---------------- | ------------------------- | ----------------------------- | --------------------- |
| `telegram_ops` | `apprise_notify` | `telegram_bot_token`      | `tgram://{secret}/{chat_id}/` | `chat_id` (unchanged) |
| `discord_ops`  | `apprise_notify` | `discord_ops_webhook_url` | `{secret}`                    | —                     |

Notes:

- **Telegram** resolves to `tgram://{bot_token}/{chat_id}/` — the documented
  Apprise format. `chat_id` is read from the existing `config.chat_id`; it is
  **not** copied into a new literal string, keeping the baseline seed
  leak-neutral (see Open items).
- **Discord** — Apprise accepts the **native** webhook URL
  (`https://discord.com/api/webhooks/{id}/{token}`) directly, so the stored
  `discord_ops_webhook_url` secret is already a valid Apprise target;
  `apprise_url` is just `{secret}`.

### The generic handler

`services/integrations/handlers/outbound_apprise.py`:

```
@register_handler("outbound", "apprise_notify")
async def apprise_notify(payload, *, site_config, row, pool):
    1. body = coerce(payload)          # str | {"content"|"text": str} -> str
    2. template = row["config"]["apprise_url"]   # fail loud if missing
    3. secret = await resolve_secret(row, site_config)  # may be None
    4. url = substitute(template, {"secret": secret, **row["config"]})
    5. aobj = apprise.Apprise(); ok = aobj.add(url)   # fail loud if add() False
    6. delivered = await aobj.async_notify(body=body, title="")
       #   (fallback: await asyncio.to_thread(aobj.notify, body=body, title=""))
    7. if not delivered: raise RuntimeError(...)   # dispatcher records + re-raises
    8. return {"delivered": True}
```

- **Payload compatibility:** accepts the existing `str` and
  `{"content": …}` / `{"text": …}` dict shapes so `notify_operator` and any
  other caller is unchanged.
- **Title:** empty (`""`) — matches today's plain-content posts.
- **Async:** Apprise is `requests`-based/synchronous internally; use
  `async_notify` (available in Apprise 1.x), or thread-offload via
  `asyncio.to_thread` if that entrypoint is unavailable at the pinned version.
  Never block the event loop.

### Data flow (caller side unchanged)

```
notify_operator(msg, critical)
  -> outbound_dispatcher.deliver("telegram_ops" | "discord_ops", payload, ...)
  -> _load_row()  (direction=outbound, enabled, parse config JSONB)
  -> registry.dispatch("outbound", "apprise_notify", payload, ...)
  -> apprise_notify(): resolve secret -> build url -> async_notify
  -> _record_success / _record_failure on the webhook_endpoints row
```

`notify_operator`'s signature, the critical→`telegram_ops` /
routine→`discord_ops` routing, and the "all paths failed" legacy-Discord
last-ditch fallback in `operator_notify.py` are all unchanged.

## Components / changes

1. **Declare the dependency** — add `apprise = ">=1.11,<2.0"` to
   `[tool.poetry.dependencies]` in `src/cofounder_agent/pyproject.toml`.
   Already resolved in `poetry.lock` via Prefect; this makes the direct use
   explicit and owns the version floor.
2. **New handler** — `services/integrations/handlers/outbound_apprise.py` as
   above.
3. **Register it** — add `outbound_apprise` to the import block + `_ = (...)`
   reference tuple in `services/integrations/handlers/__init__.py::load_all`,
   and update the "outbound.\* surface" reference comment.
4. **Re-point rows:**
   - **Fresh installs** — edit the `telegram_ops` + `discord_ops`
     `INSERT INTO webhook_endpoints (...)` rows in
     `services/migrations/0000_baseline.seeds.sql`
     (`handler_name='apprise_notify'`, add `apprise_url` to `config`). Per
     `feedback_seed_data_in_baseline_not_new_migrations`.
   - **Existing install (prod)** — one timestamped migration
     (`YYYYMMDD_HHMMSS_repoint_ops_webhooks_to_apprise.py`):
     `UPDATE webhook_endpoints SET handler_name='apprise_notify',
config = config || '{"apprise_url": …}'::jsonb
WHERE name IN ('telegram_ops','discord_ops')`.
     Idempotent / re-runnable.
5. **Delete `outbound_discord.py`** and remove its entry
   (`"services.integrations.handlers.outbound_discord"`) from
   `WIRED_HTTP_CLIENT_MODULES` in `services/http_client.py` (line ~130), plus
   its import in `handlers/__init__.py`. (The shared-httpx wiring is unneeded —
   Apprise manages its own HTTP.)
6. **Shrink `outbound_telegram.py`** — remove the `telegram_post`
   `@register_handler` function; **keep** `send_telegram_message`,
   `edit_telegram_message`, `_parse_bot_result` for `pipeline_streaming.py`.
7. **Tests** (`feedback_docs_and_tests_default`):
   - New `tests/unit/services/integrations/handlers/test_outbound_apprise.py`:
     URL templating (`{secret}` + `{chat_id}`), payload coercion, empty-title,
     `add()`-failure raises, `notify()`-False raises, missing `apprise_url`
     fails loud. Apprise `add`/`async_notify` mocked — no network.
   - Update `tests/unit/services/test_outbound_handlers.py`: drop
     `discord_post` / `telegram_post` handler cases; retain coverage of the
     surviving Telegram helpers (or move them to a `test_outbound_telegram_helpers.py`).
   - `pipeline_streaming` tests untouched (helpers unchanged).
   - Re-point assertion (migration smoke / `integration_db`): after migrate,
     both rows have `handler_name='apprise_notify'` and a valid `apprise_url`.
8. **Docs** — replace `docs/integrations/outbound_discord_post.md` +
   `docs/integrations/outbound_telegram_post.md` with
   `docs/integrations/outbound_apprise.md` (generic handler + `apprise_url`
   config + "new channel = insert a row"); update references in
   `docs/integrations/index.mdx`, `docs.json`, the declarative-data-plane RFC,
   and `docs/reference/services.md` if regenerated.

## Error handling

- Apprise `add(url)` returning `False` (malformed URL) or `async_notify`
  returning falsey (delivery failure) → handler raises `RuntimeError`. The
  `outbound_dispatcher` already catches handler exceptions, writes
  `last_error` + bumps `total_failure` on the row, and re-raises — identical to
  the current contract. `notify_operator` swallows (best-effort) and, for
  `critical=True`, still falls through to its legacy-Discord last-ditch path.
- Missing `config.apprise_url`, or a `{secret}` placeholder with no resolvable
  secret → raise with a remediation message naming the row + the missing key.
  No silent default (`feedback_no_silent_defaults`).

## Testing & verification gates

- All new/updated unit tests green; full backend unit suite green
  (`feedback_ci_is_the_review_gate`).
- Migration smoke (`scripts/ci/migrations_smoke.py`) passes on a fresh DB and
  re-points the two rows.
- **Behavior-parity check (manual, on Matt's box before merge):** fire one
  routine (`discord_ops`) and one critical (`telegram_ops`) `notify_operator`
  call; confirm both arrive and read the same as today. Watch the Discord
  output for embed-vs-plain drift (see Risks).
- `webhook_endpoints` success/failure counters still increment (same rows) —
  no Grafana panel change needed; confirm the existing Integrations panels
  still populate.

## Risks & mitigations

| Risk                                                                                                                       | Mitigation                                                                                                                                            |
| -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Discord renders as an embed** instead of plain `content` (Apprise default may differ from today's bare `{"content": …}`) | Append `?format=text` (and `footer=No`) to the discord `apprise_url`; verify in the manual parity check before merge.                                 |
| **Async blocking** — Apprise is synchronous internally                                                                     | Use `async_notify`, else `asyncio.to_thread`. Asserted by a test that the handler is awaitable and doesn't call the sync `notify` on the loop thread. |
| **Telegram helper regression** during the module shrink                                                                    | Keep the helpers byte-for-byte; `pipeline_streaming` tests must stay green.                                                                           |

## Out of scope / future

- Wiring a new channel (ntfy / Pushover / Slack / email). Once this lands it is
  a single `webhook_endpoints` row insert with an `apprise_url` — no code.
- Migrating the other outbound surfaces (`vercel_isr`) onto Apprise — they are
  not notifications.

## Open items

- **Pre-existing observation (not caused by this change):** the `telegram_ops`
  baseline row in `0000_baseline.seeds.sql` carries a literal `chat_id` in
  `config.chat_id`. This change is **leak-neutral** — it keeps the value in
  `config` and references it via the `{chat_id}` placeholder rather than copying
  it into a new string. Whether a literal operator `chat_id` should ship in the
  public-mirror baseline at all (cf. the #597/#598 public-mirror leak sweep and
  `feedback_no_operator_info_to_public_repo`) is a **separate** question to
  verify outside this PR.
