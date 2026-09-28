# Site Config

**File:** `src/cofounder_agent/poindexter/services/site_config.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_site_config.py`
**Last reviewed:** 2026-09-28

## What it does

`SiteConfig` is the dependency-injectable seam over `app_settings`.
Every service that used to call `os.getenv()` now reads through a
`SiteConfig` instance: route handlers receive it via FastAPI
`Depends(get_site_config_dependency)`, services accept it in
`__init__`, pipeline stages pull it from `context["site_config"]`,
plugins/taps/sources from `config["_site_config"]`. The instance is
constructed once in `main.py` lifespan, populated from `app_settings`
at startup, and attached to `app.state.site_config`.

Non-secret settings are loaded into an in-memory dict at startup so
`get()` is sync. Secrets (rows with `is_secret=true`) are deliberately
NOT cached — `get_secret()` is async and hits the DB on every call so
they don't leak into debug dumps. Both flow through the same
`app_settings` table; only the cache treatment differs.

**Module-level singleton and `set_site_config` fan-out both retired.**
The module-level `site_config` singleton was deleted 2026-05-09 (glad-labs-stack#330).
The intermediate per-module `set_site_config()` / `WIRED_MODULES` fan-out pattern
was retired by the #788 capstone — `services/di_wiring.py::WIRED_MODULES` is now
an **empty tuple**. Do **not** add new `set_site_config` setters or rely on
`WIRED_MODULES`; that seam is dead.

The live composition root is **`AppContainer`** (`services/container.py`), built
once per entry point by `services.bootstrap.build_container`. It holds the
process-wide `SiteConfig` and exposes migrated services as `cached_property`
accessors. A scheduled `reload_site_config` job refreshes the DB-loaded values
every minute via `site_config.reload(pool)` — because `AppContainer` holds the
same instance by reference, fresh DB values propagate to every service the
container constructed.

Tests construct their own `SiteConfig(initial_config={...})` or use the
`default_container_active` fixture from `tests/unit/conftest.py`, which registers
a seeded `SiteConfig` on an `AppContainer` so container-accessor modules
(`prompt_manager`, `gpu_scheduler`, …) see the brand seed.

## Public API

- `SiteConfig(*, initial_config=None, pool=None, read_recorder=None)` —
  constructor. `initial_config` seeds the in-memory dict (use this in
  tests). `read_recorder` is where `get()`/`require()` record the keys
  they read; it defaults to the instance's own set. The CLI passes
  `settings_read_sink.record_read` (see "Read telemetry" below).
- `await cfg.load(pool) -> int` — fetch all non-secret rows from
  `app_settings` into the cache. Call once at startup. Returns count.
- `await cfg.reload(pool) -> int` — atomic replace of the cache.
  Safe to call on a running app; useful after settings updates.
- `await cfg.get_secret(key, default="") -> str` — async DB lookup
  for secret rows. Falls back to the uppercase env var, then default.
  Handles both `enc:v1:...` encrypted and legacy plaintext rows
  transparently via `plugins.secrets.get_secret`.
- `cfg.require(key) -> str` — sync. Raises `RuntimeError` if the
  key isn't in cache or env. Use for settings that MUST be set
  (`site_url`, `company_name`, etc.). Records the read, like `get()`.
- `cfg.get(key, default="") -> str` — sync. Priority: cache > env
  var (uppercase) > default. Records the read for telemetry.
- `cfg.peek(key, default="") -> str` — resolves exactly like `get()` but
  records nothing and skips the deprecation warning. For settings-admin
  surfaces that fetch a key someone named (the console chat's
  `get_setting` tool); code that uses a value calls `get()`.
- `cfg.get_int(key, default=0) -> int` — coerces, falls back on
  ValueError/TypeError.
- `cfg.get_float(key, default=0.0) -> float` — same.
- `cfg.get_bool(key, default=False) -> bool` — accepts
  `true`, `1`, `yes`, `on` (case-insensitive).
- `cfg.get_list(key, default="") -> list[str]` — comma-separated.
- `cfg.is_loaded -> bool` — property.
- `cfg.all() -> dict[str, str]` — copy of the cache (debug only;
  excludes secrets by construction).
- `cfg.drain_read_keys() -> list[str]` — returns the keys read via
  `get()`/`require()` since the last drain, then clears the set (always
  empty with a `read_recorder`). The read-telemetry buffer, drained by
  `services.settings_read_telemetry.flush_read_telemetry` (see "Read
  telemetry & orphan detection" below).

## Configuration

`SiteConfig` reads from `app_settings`; it doesn't read its own
settings. The set of keys depends entirely on what's in the table —
1,242 keys (~68 secret) on prod as of 2026-07. See
[`docs/reference/app-settings.md`](../../reference/app-settings.md)
for the current inventory, or run
`poindexter settings list` to query the table directly.

### Who seeds `app_settings` (fresh install)

Three sources seed the table, all with `INSERT ... ON CONFLICT (key) DO
NOTHING` — so **first writer wins**, and which one wins depends on the install
path:

| source                                    | keys | when                                                                                                               |
| ----------------------------------------- | ---- | ------------------------------------------------------------------------------------------------------------------ |
| `poindexter/brain/seed_app_settings.json` | 81   | brain daemon boot, if the table is empty or missing a `REQUIRED_KEYS` value (free-tier profile, `_meta.tier=free`) |
| `0000_baseline.seeds.sql`                 | ~692 | migration runner, every boot                                                                                       |
| `settings_defaults.py::DEFAULTS`          | ~734 | `seed_all_defaults`, every boot, after migrations                                                                  |

On `docker compose up` against an empty DB the brain seeds first (`worker`
declares `depends_on: brain-daemon: service_healthy`), giving the precedence
`brain > baseline > DEFAULTS`. Via `poindexter setup`, migrations plus
`seed_all_defaults` run before any container, so the brain seed no-ops and the
order is `baseline > DEFAULTS`. Either way, for a key the baseline also seeds,
the `DEFAULTS` value is only reachable if it matches the baseline.
`settings_defaults.py` and `0000_baseline.seeds.sql` must therefore agree on
every overlapping key; `poindexter/brain/seed_app_settings.json` may differ only via the
declared `TIER_POLICY` allowlist. All three are held consistent by
`scripts/ci/settings_seed_value_drift_lint.py` (in the `migrations-smoke`
check).

A sibling gate, `scripts/ci/settings_phantom_read_lint.py` (in `test-backend`),
catches the mirror-image bug: a literal `app_settings` key **read** in
production code that **none** of the three sources above defines — a fresh
install has no row for it, so the read silently falls back to whatever
default is baked into the call site, forever, and no gate can see the gap.
Motivating case: `gpu_scheduler.py` read `electricity_rate_kwh_usd`, which
nothing seeds, while the real, EIA-maintained `electricity_rate_kwh` sat
unread (glad-labs-stack#4065). It is the inverse of
`ProbeZeroReaderSettingsJob` (a key that exists but is never read) — this one
finds a key that is read but never exists. Secrets are exempt structurally
(read exclusively via `.get_secret()`); everything else unseeded on purpose
(a legacy-key fallback, an OSS-privacy redaction, a bootstrap credential)
needs a reasoned entry in that lint's own `ALLOWLIST`. A ratchet, like the
value-drift lint above: existing gaps are grandfathered, only a net-new one
fails CI. It sees literal keys only: a key assembled at the call site
(`research_quality_service.py`'s `_weight()` reads `f"research_{key}_weight"`)
is invisible to it by construction, so a dynamically keyed tunable has to be
seeded by hand, with a unit test that ties each seeded key to the read it backs
(see `TestScoringWeightSettings`).

The only env vars `SiteConfig` itself touches:

- `<KEY>` (uppercase) — fallback for any `get()`/`require()` lookup
  that isn't in the DB cache. This is for bootstrap-only settings;
  the codebase's general direction is DB-first (#198).
- `DATABASE_URL` / `LOCAL_DATABASE_URL` — chicken-and-egg, used to
  resolve the pool BEFORE `SiteConfig` exists. Resolved by
  `brain.bootstrap.resolve_database_url`.

## Dependencies

- **Reads from:** `app_settings` table (one query at startup, one per
  `get_secret()` call).
- **Writes to:** no DB writes — read-only by design; settings are
  mutated via `services.settings_service.SettingsService` or the
  `/api/settings` route. (`get()` does record each read key in an
  in-memory set for telemetry; `services/settings_read_telemetry.py`
  performs the `last_read_at` UPDATE, called by every process that reads
  settings: the worker once a minute, and each content-flow run, CLI
  command and auto-embed pass as it finishes. See "Read telemetry & orphan
  detection" below.)
- **External APIs:** none.

## Failure modes

- **DB pool not provided / pool is None on `load()`** — logs
  `[SITE_CONFIG] No DB pool — using env var fallbacks only`, returns
  `0`. Subsequent `get()` calls will only hit env vars + defaults.
  This is the pre-startup bootstrap state.
- **`load()` query fails** — caught, logged as warning, returns `0`.
  Cache stays empty. Recover by calling `reload(pool)` once the DB
  is back.
- **`require()` on unset key** — raises
  `RuntimeError("Required setting '<key>' is not configured. Set it
in app_settings table or as env var <KEY>.")`. This is the "fail
  loud" principle — no silent fallbacks for required settings.
- **`get_secret()` query fails** — caught, logged as warning,
  falls through to env var + default. The caller can't tell whether
  the key truly doesn't exist or whether the lookup failed; check
  logs.
- **Secret stored as plaintext (legacy)** — `plugins.secrets.get_secret`
  returns the value as-is; it's caller-transparent. Migration to
  encrypted-at-rest is tracked separately.

## Common ops

- **Set a value via CLI:** `poindexter settings set <key> <value>`.
- **List all settings:** `poindexter settings list` or
  `SELECT key, value, is_secret FROM app_settings ORDER BY key;`
- **Reload after a manual DB edit:** call
  `await app.state.site_config.reload(app.state.pool)` from a
  one-off script, or restart the app.
- **Add a new key with default:** insert into `app_settings` (or use
  `services.bootstrap_defaults.ensure_defaults`); read it via
  `cfg.get("<key>", "<default>")` in code so tests without the DB
  row still work.
- **Mark a key as secret:** `UPDATE app_settings SET is_secret = true
WHERE key = '<key>';` — then update callers to use `get_secret()`
  instead of `get()`. The cache will skip it on next `reload()`.
- **Test seam:** use the `default_container_active` fixture or
  `SiteConfig(initial_config={"site_url": "https://test"})` passed
  via constructor DI. The module-level singleton and `set_site_config`
  fan-out are retired; do not add new `set_site_config` calls.
- **Find the env var equivalent:** any `cfg.get("foo_bar")` falls
  back to `FOO_BAR`. Use sparingly — DB-first is the policy.

## Read telemetry & orphan detection (#756)

`get()` and `require()` record every key they are asked for into a
per-instance in-memory set — an O(1) `set.add` on the hot path. They write
nothing themselves, and `load()`/`reload()` deliberately do NOT mark keys
read, so a 60s cache refresh never makes every key look consumed.

### What counts as a read

The probe below asks one question: does the running system consult this
key? So a read counts when code asks for a key it names, and only then:

- **Recorded:** `SiteConfig.get`/`get_int`/`get_float`/`get_bool`/`get_list`
  and `require` (into the instance's set), plus `SettingsService.get`,
  `DatabaseService.get_setting_value` and the raw-SQL helpers that call
  `record_read` (into the process-wide `services.settings_read_sink`).
- **Not recorded: the settings-admin surfaces**, which fetch a key because
  a person or an agent named it: `GET/POST/PUT /api/settings/{key}` (the
  row fetch in `AdminDatabase.get_setting`, behind
  `poindexter settings get`, the console's settings editor and the MCP
  `set_setting` tool), the MCP `get_setting` tool (the same row fetch)
  and the console chat's
  `get_setting` tool (`SiteConfig.peek`). A lookup says nothing about
  whether the system uses a key.
- **Not recorded either:** bulk reads (`get_all_settings`,
  `SettingsService.get_all`/`get_by_category`, `SiteConfig.all()`) and
  `get_secret()`. Secrets are outside the probe, and each `get_secret()`
  call queries the database anyway.

Until 2026-09-28 the settings-admin surfaces did record. That mattered
more than it sounds, because **a stamp is permanent as far as the probe is
concerned**: it lists only keys whose `last_read_at` is NULL, and nothing
clears a stamp. So `poindexter settings set <key> …` followed by the usual
`poindexter settings get <key>` hid the key from the probe for good. On
prod, 11 of 742 stamped keys carried exactly that signature, stamped 6-68 s
after a value edit and never again. Loki still held three of those edits
(`ragas_enabled`, `compose_drift_on_demand_services`,
`persona.presenter.portrait_url`) and shows the host's `GET
/api/settings/<key>` just before each stamp. Migration
`20260928_141858_clear_last_read_at_stamps_left_by_settings_admin_lookups`
clears stamps with that signature: within 5 minutes after the last value
edit and unmoved for a day.

### Flushing: every process flushes its own reads

The read set and the sink are process memory, so a read is stamped only
if the process that made it flushes. The drain-and-stamp step is
`services/settings_read_telemetry.py::flush_read_telemetry(pool, site_config=None)`.
It drains the `SiteConfig` set via `drain_read_keys()`, unions it with
the process-wide sink, and batch-stamps `app_settings.last_read_at =
NOW()` for those keys. The UPDATE only touches rows whose `last_read_at`
is NULL or older than `settings_read_telemetry_min_restamp_seconds`
(default 3600), so a hot key is written about once an hour however many
processes read it. Gated by `settings_read_telemetry_enabled` (default
true), which governs every caller. Called with no `SiteConfig`, it drains
only the sink and reads those two controls from `app_settings` on the
connection that runs the UPDATE. A failed stamp is logged and reported,
never raised.

- **The worker.** `FlushSettingsReadTelemetryJob`
  (`services/jobs/flush_settings_read_telemetry.py`, every minute)
  flushes the lifespan-bound `SiteConfig`, which the plugin scheduler
  seeds into every job as `config["_site_config"]`.
- **Each Prefect content-flow run.** `content_generation_flow`
  (`services/flows/content_generation.py`) flushes the run's own
  `SiteConfig` from its `finally`, before the run's DB pool closes, on a
  crashed run as well as a clean one. The Prefect worker runs every flow
  run in a fresh subprocess (`prefect worker start --type process`) that
  builds its own `SiteConfig` and exits when the run ends; nothing else
  can reach that subprocess's buffers. Until 2026-09-28 the flow didn't
  flush, so no read made inside the pipeline was ever stamped.
  `content_flow_stale_inprogress_minutes`, read by ~700 flow runs a day,
  was NULL, and the zero-reader finding listed live QA weights
  (`qa_final_score_threshold`, `qa_critic_weight`) as unread.
- **Each `poindexter` CLI command.** `cli/_bootstrap.py::close_cli_pool`
  flushes the process-wide sink before it closes the command's pool, and
  `scripts/ci/cli_audit_sink_lint.py` already routes every CLI pool
  through it. Every `SiteConfig` a command builds comes from
  `cli_site_config(pool)`, which passes `settings_read_sink.record_read` as
  its `read_recorder`: commands build them inside `run_service` factories
  and `_make_site_config` helpers, and the instance is often gone before
  the pool closes, so its reads can't wait on it. `container_for_cli`
  flushes its container's `SiteConfig` in its `finally`.
- **Each auto-embed pass.** `services/taps/runner.py::run_all` builds its
  own `SiteConfig` and flushes it before returning. The sidecar is its own
  process, and before this all six `tap_*` keys read as never-read on
  prod while it read them every hour.
- **Every script and voice agent that loads a `SiteConfig`**
  (`scripts/regen_media_scripts.py`, the tools under `scripts/`, the parked
  voice agents) flushes it before its pool closes.

### Keeping the list complete

**`scripts/ci/settings_read_flush_lint.py`** (in `test-backend` and
`lint-main`) finds every construction of a DB-loaded `SiteConfig` —
`SiteConfig(pool=…)`, a `SiteConfig(…)` that is then `.load()`-ed or
`.reload()`-ed, and every call to `build_container`,
`build_and_wire_subprocess_with_container` or `build_and_wire_for_subprocess`
— and requires the function it sits in to flush, directly or through a
helper in the same module. A helper that only builds and returns the
`SiteConfig` is covered when all its callers in the module flush. Anything
else needs an `ALLOWLIST` entry with the reason its reads aren't stamped
there. The entries today:

- the worker's lifespan instance in `main.py`, flushed by
  `FlushSettingsReadTelemetryJob` in another module;
- `cli_site_config`, whose reads go to the sink `close_cli_pool` flushes
  (the lint also rejects `cli_site_config` outside `poindexter/cli/`);
- the MCP server's `_get_site_config`: a long-lived adapter that caches
  its `SiteConfig` for the life of the process, with no teardown to flush
  from.

Everything that can flush does, including every script under `scripts/`
that loads a `SiteConfig` and the parked voice agents, so the list stays
short and each entry is a structural exception.

A stale entry fails the lint, so an exemption can't outlive the code it
excused.

### The orphan probe

**`ProbeZeroReaderSettingsJob`** (`services/jobs/probe_zero_reader_settings.py`,
every 6h) is the inverse query: non-secret, non-deprecated keys whose
`last_read_at` is still NULL more than `settings_zero_reader_grace_days`
(default 30) days after `created_at` are emitted as one advisory
`settings_zero_reader_keys` finding (severity `warn`, stable
`dedup_key`) routed to Discord ops via
`findings.settings_zero_reader_keys.delivery`. The grace window
self-suppresses on fresh installs and gives newly-seeded keys time to be
read. The live list also renders on the **Integrations & Admin** Grafana
board ("Settings Lifecycle — Orphan Candidates").

**Advisory, not authoritative.** A key read EXCLUSIVELY via a path none
of the above covers still surfaces as an orphan candidate:

- **Raw SQL** that doesn't call `record_read` (e.g.
  `findings_alert_router` reading the `findings.*` policies with
  `LIKE 'findings.%'`).
- **A `site_config.all()` snapshot** filtered in code. `persona_service.get_persona`
  reads every `persona.<slug>.*` key this way, so they read as never
  read even though every narration resolves them.
- **A process that never flushes**: the **brain daemon**'s own asyncpg
  reads (it never builds a `SiteConfig`) and the MCP server (the only
  `SiteConfig` the lint's `ALLOWLIST` excuses without a flush elsewhere).

The other direction has a limit too: a stamp never expires. A key whose
last reader was deleted keeps the stamp its last read left, and the probe,
which lists only NULL keys, will not report it.

A NULL `last_read_at` is a candidate, never proof. Verify each key
from its readers in code before retiring it.

## See also

- `CLAUDE.md` "Configuration (#198 — no hardcoded values in code)" —
  full explanation of the DI seam, deprecated singleton, and how
  callers should plumb the instance.
- `docs/architecture/services/cost_guard.md` — example of a service
  that takes `site_config` in `__init__` and reads its limits via
  `_limit()` helper.
- `services.settings_service.SettingsService` — the write path
  (mutates settings; logs to `audit_log` for the change history).
- `feedback_no_env_vars` (operator design note)
  and `feedback_db_first_config.md` — why this exists.
