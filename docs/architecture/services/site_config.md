# Site Config

**File:** `src/cofounder_agent/services/site_config.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_site_config.py`
**Last reviewed:** 2026-06-30

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

- `SiteConfig(*, initial_config=None, pool=None)` — constructor.
  `initial_config` seeds the in-memory dict (use this in tests).
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
  (`site_url`, `company_name`, etc.).
- `cfg.get(key, default="") -> str` — sync. Priority: cache > env
  var (uppercase) > default.
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
  `get()` since the last drain, then clears the set. The read-telemetry
  sink, called by `FlushSettingsReadTelemetryJob` (see "Read telemetry
  & orphan detection" below).

## Configuration

`SiteConfig` reads from `app_settings`; it doesn't read its own
settings. The set of keys depends entirely on what's in the table —
~1,090 keys (~68 secret) as of 2026-06. See
[`docs/reference/app-settings.md`](../../reference/app-settings.md)
for the current inventory, or run
`poindexter settings list` to query the table directly.

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
  in-memory set for telemetry; the separate
  `FlushSettingsReadTelemetryJob` performs the `last_read_at` UPDATE.
  See "Read telemetry & orphan detection" below.)
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

`get()` records every key it is asked for into a per-instance in-memory
set — an O(1) `set.add` on the hot path. It writes nothing itself, and
`load()`/`reload()` deliberately do NOT mark keys read, so a 60s cache
refresh never makes every key look consumed.

Two scheduled jobs close the loop (both reach the lifespan-bound
`SiteConfig` via `config["_site_config"]`, seeded into every job by the
plugin scheduler):

- **`FlushSettingsReadTelemetryJob`** (`services/jobs/flush_settings_read_telemetry.py`,
  every minute) drains the set via `drain_read_keys()` and batch-stamps
  `app_settings.last_read_at = NOW()` for those keys. The UPDATE only
  touches rows whose `last_read_at` is NULL or older than
  `settings_read_telemetry_min_restamp_seconds` (default 3600), so a
  hot key is written ~once/hour rather than 60×. Gated by
  `settings_read_telemetry_enabled` (default true).
- **`ProbeZeroReaderSettingsJob`** (`services/jobs/probe_zero_reader_settings.py`,
  every 6h) is the inverse query: non-secret, non-deprecated keys whose
  `last_read_at` is still NULL more than `settings_zero_reader_grace_days`
  (default 30) days after `created_at` are emitted as one advisory
  `settings_zero_reader_keys` finding (severity `warn`, stable
  `dedup_key`) routed to Discord ops via
  `findings.settings_zero_reader_keys.delivery`. The grace window self-
  suppresses on fresh installs and gives newly-seeded keys time to be
  read. The live list also renders on the **Integrations & Admin**
  Grafana board ("Settings Lifecycle — Orphan Candidates").

**Advisory, not authoritative.** `last_read_at` is only stamped on the
`SiteConfig.get` path. A key read EXCLUSIVELY via a non-SiteConfig path
— direct SQL (e.g. `findings_alert_router` reading `findings.*`
policies) or `SettingsService.get` — also surfaces as an orphan
candidate. Verify each key before retiring it. (If the signal proves
noisy, the next step is to instrument `SettingsService.get` the same
way.)

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
