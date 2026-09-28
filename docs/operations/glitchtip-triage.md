# GlitchTip noise control

GlitchTip (http://localhost:8080, org `glad-labs`, project `poindexter`) is the
runtime-error sink for worker / brain / voice. Left alone it accumulates
hundreds of "issues" that are mostly not bugs, which is worse than having no
error tracker: a 364-issue list trains you to ignore the list.

Noise is suppressed at **two** layers. Reach for the earlier one first — it is
strictly better to never capture a non-error than to capture and then close it.

| Layer            | Where                                                                                                | Use it for                                              |
| ---------------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| **Capture-side** | `services/sentry_integration.py::_before_send`                                                       | Events that should never have been errors at all        |
| **Triage-side**  | `poindexter/brain/glitchtip_triage_probe.py` + `app_settings.glitchtip_triage_auto_resolve_patterns` | Real errors that are known/expected, and stale one-offs |

## What the SDK records: integrations

Before either layer runs, the SDK's integrations decide what gets captured and
what context an event carries. Each process passes its integrations explicitly
with `auto_enabling_integrations=False`, so the list in code is the whole list:

| Process (`server_name`)                                                             | Initialised by                                                    | Integrations                                                                      |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `poindexter-worker`, and every Prefect content flow run (`poindexter-prefect-flow`) | `services/sentry_integration.py` (`SentryIntegration.initialize`) | FastAPI, Starlette, asyncio, logging, threading, plus `sentry_extra_integrations` |
| `poindexter-brain`                                                                  | `poindexter/brain/brain_daemon.py` (`_init_sentry`)               | logging, asyncio                                                                  |
| `poindexter-mcp-http`                                                               | `mcp-server/http_server.py`                                       | SDK auto-enabling, unchanged (see below)                                          |

All of them keep the SDK defaults: excepthook, atexit (which flushes queued
events when a short-lived flow run exits), dedupe, argv, modules, and stdlib
(breadcrumbs for `http.client` requests and subprocesses). The worker and each
flow run log the enabled set at init: `Integrations: argv,asyncio,atexit,...`.

### Why auto-enabling is off

Left on, sentry-sdk 2.x enables an integration for every installed library it
recognises, whatever `integrations=` says. In the worker image that was 21
integrations. The LangChain one imports `langchain_classic` → transformers →
torch, and the LangGraph one imports the LangChain one. Measured in the
prefect-worker image on 2026-09-28, after the content flow had imported its own
modules:

| `sentry_sdk.init` | Time      | Peak RSS                     |
| ----------------- | --------- | ---------------------------- |
| auto-enabling on  | 6.0–6.3 s | 172 → 701 MB, torch imported |
| auto-enabling off | 0.01 s    | 172 → 174 MB                 |

Every content flow run initialises Sentry, and a run starts about every two
minutes even on an empty queue. Live, each `prefect.engine` subprocess mapped
libtorch and peaked at about 855 MB. `disabled_integrations` cannot fix this:
the SDK imports an integration to put it on that list.

The rest of the auto set did harm as well. Over the 322 GlitchTip events of
the 14 days to 2026-09-28:

- **asyncpg** records one breadcrumb per query, and an event keeps only the
  last 100. Queries were 66% of the worker's breadcrumbs and 80% of the
  brain's; log lines, the part that tells the story, were 16% and 8.5%.
- **httpx** records every request URL as a breadcrumb, path and query string
  unredacted. URLs that carry a credential in their path, like a chat
  webhook, were stored with it. Turning httpx off did not close that leak:
  the default stdlib integration does the same for `http.client`, which is
  how the brain's `urllib` pages go out. See
  [Credentials never leave the process](#credentials-never-leave-the-process).

The MCP HTTP server keeps auto-enabling on purpose. Its venv has no LangChain
or torch, it initialises once per long-lived process (0.78 s, most of it
importing FastAPI and `mcp`, which it loads anyway), and the auto-enabled
FastAPI, Starlette and `mcp` integrations are what capture its request errors.

### Opting an integration back in: `sentry_extra_integrations`

A CSV of sentry-sdk integration identifiers (default empty) that
`SentryIntegration.initialize` adds on top of its core list, for the worker
and the content flow runs. The brain always uses its fixed list. Each
identifier is a module under `sentry_sdk.integrations`. A name that is
malformed, unknown, core, or whose library is not installed is logged as an
error and skipped. If an extra refuses at setup, init retries with the core
list alone. An optional extra never costs a process its error tracking.

It is read at init: restart the worker to apply it; flow runs pick it up on
their next run. Import cost of each candidate in the prefect-worker image,
measured after the content flow's own modules had loaded:

| Identifier                                     | Adds                                            | Import cost                                       |
| ---------------------------------------------- | ----------------------------------------------- | ------------------------------------------------- |
| `asyncpg`                                      | a breadcrumb and span per SQL query             | negligible (crowds out log breadcrumbs, as above) |
| `httpx`                                        | a breadcrumb and span per HTTP request          | negligible (full URLs, scrubbed as below)         |
| `aiohttp`, `redis`, `boto3`, `huggingface_hub` | per-call breadcrumbs and spans                  | ≤ 0.11 s, ≤ 6 MB                                  |
| `sqlalchemy`                                   | query breadcrumbs (our code uses no SQLAlchemy) | 0.22 s, +15 MB                                    |
| `openai`                                       | spans for OpenAI-client LLM calls               | 0.52 s, +28 MB                                    |
| `langchain`, `langgraph`                       | spans for chains and graph runs                 | 4.2–4.8 s, +424–432 MB, imports torch             |

## Credentials never leave the process

The SDK records every outbound HTTP request as a breadcrumb with its path and
query string (`parse_url(..., sanitize=False)`, in the stdlib integration as in
the httpx one), and by default it ships each stack frame's local variables
with an exception. Chat webhooks and bot APIs carry their credential in the
URL. Measured 2026-09-28 over the 8,499 events GlitchTip held (29 June to 28
September):

| Where it was stored                                                             | Events | Last 14 days |
| ------------------------------------------------------------------------------- | ------ | ------------ |
| `httplib` breadcrumbs (`data.url`): the Discord webhook and Telegram bot tokens | 4,447  | 76           |
| stack-frame locals, listed below                                                | 1,394  | 43           |
| subprocess breadcrumbs (`message`): a DSN password, a `POSTGRES_PASSWORD=`      | 3      | 1            |
| `http.query` and log breadcrumbs: presigned-S3 signatures                       | 2      | 0            |

The breadcrumbs held the Discord token 8,981 times and the Telegram token 250.
The locals held the same two tokens, the Postgres DSN password
(`gpu_scheduler`, asyncpg's `dsn`), the R2 secret access key (`upload_to_r2`'s
`secret_key`, 555 events), the Lemon Squeezy API key, a GitHub token and a
relay secret (`pro_delivery`'s config), the newsletter relay secret and other
bearer tokens in `headers` dicts, and a Cloudflare API token.

Every `sentry_sdk.init` now gets two defences from
[`poindexter/brain/sentry_scrub.py`](../../src/cofounder_agent/poindexter/brain/sentry_scrub.py):
the worker and each Prefect flow run through `SentryIntegration.initialize`,
the brain through `_init_sentry`, the MCP HTTP server through
`http_server._init_sentry`.

1. **Stack-frame locals stay home.** `sentry_include_local_variables`
   (default `false`) sets `include_local_variables`. No pattern list can know
   every secret shape a local variable might hold (the R2 secret key is 64
   bare hex characters), so the complete fix for that surface is not to send
   it. Turn it on only while chasing a bug that needs locals, and back off
   after. While it is on, a local whose name says it holds a credential
   (`secret_key`, `api_token`, `relay_secret`, `dsn`, `webhook_url`, …) is
   filtered whole, and the patterns below run over the rest.
2. **Credential-shaped text becomes `[Filtered]`** (the SDK's own marker) in
   each breadcrumb as it is recorded (`before_breadcrumb`), and in every
   string of an event or transaction just before it is sent (`before_send`,
   `before_send_transaction`). The second pass runs on the serialized event,
   so it also covers exception values, log messages, `extra`, `contexts`,
   `request`, tags, span names and the grouping fingerprint the worker builds
   from exception text.

The built-in patterns, each matched on the shape of a credential rather than a
variable name:

| Pattern                 | Catches                                                                                         |
| ----------------------- | ----------------------------------------------------------------------------------------------- |
| `telegram_bot_path`     | `/bot<id>:<token>` in Bot API URLs and raw request bytes                                        |
| `discord_webhook_path`  | `/api/webhooks/<id>/<token>`, with or without `/v10`                                            |
| `query_secret`          | a secret-ish query value: `?key=`, `&token=`, `&access_token=`, `X-Amz-Signature=` and the like |
| `url_userinfo_password` | `scheme://user:password@host`                                                                   |
| `env_secret_assignment` | `POSTGRES_PASSWORD=…`, `API_TOKEN=…` on a command line                                          |
| `authorization_header`  | `Bearer …` / `Basic …` values of 16+ characters                                                 |
| `jwt`                   | any `eyJ….eyJ….…` token                                                                         |
| `github_token`          | `ghp_` / `gho_` / `ghu_` / `ghs_` / `ghr_` / `github_pat_` tokens                               |
| `sk_api_key`            | `sk-…` keys (OpenAI-compatible, `sk-ant-`, `sk-lf-`)                                            |
| `repr_secret_kwarg`     | a secret-named keyword in a repr or call: `relay_secret='…'`, `ls_api_key="…"`                  |
| `repr_secret_item`      | a secret-named dict item or JSON member: `'secret_key': '…'`, `"api_token": "…"`                |

Replayed over all 8,506 events GlitchTip held on 2026-09-28, locals included
as they were stored, the scrubber left no credential shape and no secret-named
local unfiltered.

**`sentry_secret_scrub_patterns`** (default `[]`) is a JSON array of extra
`[regex, replacement]` pairs, _added_ to the built-ins and never instead of
them, so no edited row can switch the scrubber off. An invalid value logs an
error once and adds nothing. The worker reads it live (the 1-minute
`reload_site_config`); the brain and the MCP HTTP server read it and
`sentry_include_local_variables` at init, so restart them to apply.

Both hooks **fail closed**. The SDK keeps the _original_ breadcrumb when
`before_breadcrumb` raises, and silently drops the event when `before_send`
does, so each hook catches its own failure, logs a warning, and drops what it
could not scrub rather than let it out.

A new `sentry_sdk.init` anywhere in the tree must pass
`**sentry_scrub.init_options(...)` (or the same four options).
`tests/unit/brain/test_sentry_scrub_init_sites.py` fails otherwise.

Events stored before the fix keep what they captured until GlitchTip's 90-day
retention (`GLITCHTIP_MAX_EVENT_LIFE_DAYS`) drops them. This query counts
events still holding a chat-webhook or bot token, without printing any:

```bash
docker exec poindexter-glitchtip-db psql -U glitchtip -d glitchtip -Atc "
  SELECT count(*), max(timestamp) FROM issue_events_issueevent
  WHERE data::text ~ 'discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_.-]{20,}'
     OR data::text ~ 'bot[0-9]{5,}:[A-Za-z0-9_-]{20,}'"
```

After a deploy, `max(timestamp)` should stop advancing.

## Layer 1 — capture-side (`_before_send`)

Runs in-process before an event is sent, after the credential scrub above (so
a fingerprint is built from text that has already had its secrets removed).
Two knobs, both `app_settings`-driven so they change without a redeploy (read
through the cached `SiteConfig`, so the 1-minute `reload_site_config` job
propagates edits):

- **`sentry_drop_exception_types`** — CSV of exception class names to drop
  entirely. Matched by name across the MRO, so it needs no import of the
  raising package and covers subclasses.
- **`sentry_fingerprint_scrub_patterns`** — JSON array of
  `[regex, replacement]` pairs applied to the event's **grouping fingerprint
  only**. The message an operator reads is never modified.

### When to drop an exception type

Only when the exception is _expected control flow_ — something the code raises
to steer itself, which the SDK misreads as an unhandled failure. Current
entries:

- `GraphInterrupt` — LangGraph `interrupt()` suspending a graph for operator
  approval. A pause, not a failure.
- `GpuBusyError` — `gpu.lock(..., max_wait_s=...)` refusing a hopeless wait so
  fail-soft callers skip cleanly. Already recorded as an `info`-severity
  `gpu_admission_rejected` finding, so capturing it as an _error_ double-reports
  one designed outcome at two contradictory severities.

A real failure that merely happens often does **not** belong here — that is
Layer 2's job, where closing an issue still leaves a record and a recurrence
re-opens it.

### Why fingerprints need scrubbing

GlitchTip derives an issue's identity from the exception value (or log
message). Any volatile token in that text mints a brand-new issue **per
event**, which looks like a widespread outage and buries everything else.

Only set a scrub pattern for text that is genuinely meaningless for grouping.
The default three, each from a real incident:

| Pattern                 | Real signature                                                   | Damage                        |
| ----------------------- | ---------------------------------------------------------------- | ----------------------------- |
| `/tmp/tmp[A-Za-z0-9_]+` | `S3UploadFailedError: Failed to upload /tmp/tmpnjvtpvv5.json …`  | 164 issues from ONE R2 outage |
| UUID                    | `Request validation middleware error for /api/chat/watch/<uuid>` | 11 issues                     |
| `\d+\.\d+s\b`           | `pg_advisory_lock wait exceeded 44.999968992000504s`             | 31 issues                     |

The fingerprint is only overridden when scrubbing actually changed the text, so
events with no volatile tokens keep the SDK's default grouping. Bare integers
are deliberately **not** scrubbed — they are usually meaningful (HTTP status,
errno), and full-precision floats were the actual fragmenter.

A malformed setting logs loud and falls back to the built-in defaults rather
than silently disabling scrubbing.

## Layer 2 — the triage probe ruleset

`glitchtip_triage_auto_resolve_patterns` is a JSONB array evaluated **in
declaration order**; `_match_rule` returns the **first** match. Each entry:

```json
{
  "title_pattern": "<regex>",
  "action": "resolve" | "ignore",
  "reason": "<why this is known noise>",
  "max_count": 1000,
  "min_age_days": 7,
  "level_in": ["error"]
}
```

- **`resolve`** closes the issue. Preferred for backlog hygiene — the list
  stays short, and a recurrence re-opens it as a GlitchTip regression, so
  nothing is lost.
- **`ignore`** suppresses alerting but leaves the issue open forever. Use only
  for mechanical echoes that need no closure (e.g. `^\[operator_notifier\]`,
  which is the notifier logging its own outbound page). `ignore` rules are
  exempt from the ceiling described below.

### Trap: the silent `max_count` ceiling

**A `resolve` rule with no explicit `max_count` is silently bounded to
`glitchtip_triage_default_resolve_max_count` (50).** That is the #304
runaway-outage backstop: if an issue is exploding, stop auto-closing it and let
a human look. But `count` is **cumulative and never resets**, so a chronic
known-noise issue eventually crosses any ceiling, the rule stops matching
_permanently_, and the noise it exists to suppress starts paging again.

This trap has now re-armed three times (2026-07-12, 2026-07-14, 2026-08-08). It
is pinned by
`tests/unit/services/migrations/test_glitchtip_triage_patterns_seed.py`, which
fails if **any** seeded `resolve` rule omits `max_count`.

Size ceilings from the issue's **observed accumulation rate**, not a round
number — roughly a year of headroom at current rate. That still trips within
days on a genuine 10× spike:

```
count / (lastSeen - firstSeen in days) = events/day
max_count ≈ count + 365 × events/day
```

Watch for burst issues (many events in an hour, then dormant): their rate is
meaningless to extrapolate — cap those at a multiple of the current count.

### The catch-all GC rule

The last entry is a `resolve` rule with `title_pattern: "."`, gated on
`min_age_days: 7` **and** `max_count: 5` — it closes stale one-off transients
that no specific rule covers. Before it existed, 272 of 364 open issues were
exactly that, and `min_age_days` had never been used once despite being built
for it.

**It must stay last.** A catch-all anywhere else shadows every rule declared
after it. This ordering is enforced by a contract test, not just a comment.

Note `min_age_days` gates on **`firstSeen`**, not `lastSeen` — it means "this
issue has existed for N days", not "has been quiet for N days". Combined with a
low `max_count`, that is the intended "old and rare" filter.

## Diagnostics

The probe's own helpers are the fastest way to reason about live state, run
inside the brain container so they reuse its DSN and secret decryption:

```bash
docker exec poindexter-brain-daemon python3 -c "
import asyncio, asyncpg, httpx
from poindexter.brain import bootstrap, glitchtip_triage_probe as p
async def m():
    pool=await asyncpg.create_pool(bootstrap.resolve_database_url())
    tok=await p._read_secret(pool,p.TOKEN_SETTING_KEY)
    base=await p._read_setting(pool,p.BASE_URL_SETTING_KEY,p.BASE_URL_DEFAULT)
    org=await p._read_setting(pool,p.ORG_SLUG_SETTING_KEY,p.ORG_SLUG_DEFAULT)
    rules=await p._read_rules(pool, await p._read_default_resolve_max_count(pool))
    async with httpx.AsyncClient(headers={'Authorization':f'Bearer {tok}'}) as c:
        issues=await p._fetch_open_issues(c,base,org)
    print(len(issues),'open;',sum(1 for i in issues if p._match_rule(i,rules)),'matched')
asyncio.run(m())"
```

To find rules that have stopped firing, test each unmatched issue's title
against every rule's regex **ignoring the gates** — a regex that matches while
the rule didn't fire means a gate (usually `max_count`) blocked it.

Other traps worth knowing:

- **`is:unresolved` can desync from an issue's own `status`.** The list
  endpoint is backed by a search index that lags the primary record, so a new
  rule may take several probe cycles to visibly close something. Verify against
  the single-issue endpoint (`GET /api/0/issues/<id>/`, no org prefix) rather
  than trusting one list-endpoint miss.
- **Distinct issues can share a truncated alert title.** `notify_operator`
  truncates to 80 chars, so several different Ollama `APIConnectionError`
  signatures look identical in a Discord scan. Resolve the permalink/issue ID
  before concluding anything.
- **Repeat pages for one already-alerted issue** usually mean the brain
  restarted — per-issue dedupe lives in an in-process set. Documented behavior,
  not a bug.
- **Host reboots generate a burst of name-resolution errors.** Containers point
  at Docker's `127.0.0.11`, which forwards to the host's `systemd-resolved`; when
  that stops during shutdown, in-flight lookups fail. `TerminationSignal: 15` in
  the same window is the matching SIGTERM. Before adding yet another DNS
  suppression rule, check `last reboot` and
  `journalctl -u systemd-resolved -u tailscaled` for a teardown that explains
  the timestamps.

## Related

- [`poindexter/brain/glitchtip_triage_probe.py`](../../src/cofounder_agent/poindexter/brain/glitchtip_triage_probe.py) — the probe
- [`services/sentry_integration.py`](../../src/cofounder_agent/poindexter/services/sentry_integration.py) — capture-side filter
- [`poindexter/brain/sentry_scrub.py`](../../src/cofounder_agent/poindexter/brain/sentry_scrub.py) — the credential scrubber every `sentry_sdk.init` wires in
- [Findings dashboard](http://localhost:3000/d/findings) — the _other_ signal
  path; a condition worth an operator's attention should be a
  [finding](../architecture/anti-hallucination.md), not just a captured
  exception.
