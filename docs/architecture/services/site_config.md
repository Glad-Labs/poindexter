# Site Config

**File:** `src/cofounder_agent/services/site_config.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_site_config.py`
**Last reviewed:** 2026-04-30

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

The module-level `site_config` singleton at the bottom of the file is
kept for transitional callers but is being phased out (Phase H, GH#95).
Tests should construct their own `SiteConfig(initial_config={...})`
or use the `test_site_config` fixture in `tests/unit/conftest.py`.

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

## Configuration

`SiteConfig` reads from `app_settings`; it doesn't read its own
settings. The set of keys depends entirely on what's in the table —
roughly 310 active keys as of April 2026. See
`scripts/list_app_settings.py` (or query the table directly) for the
current inventory.

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
- **Writes to:** nothing. Read-only by design — settings are mutated
  via `services.settings_service.SettingsService` or the
  `/api/settings` route.
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

- **Set a value via CLI:** `poindexter set <key> <value>`.
- **List all settings:** `poindexter list-settings` or
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
- **Test seam:** use the `test_site_config` fixture or
  `SiteConfig(initial_config={"site_url": "https://test"})`. Do NOT
  mutate the module-level singleton in tests — that pollutes other
  tests that load defaults.
- **Find the env var equivalent:** any `cfg.get("foo_bar")` falls
  back to `FOO_BAR`. Use sparingly — DB-first is the policy.

## See also

- `CLAUDE.md` "Configuration (#198 — no hardcoded values in code)" —
  full explanation of the DI seam, deprecated singleton, and how
  callers should plumb the instance.
- `docs/architecture/services/cost_guard.md` — example of a service
  that takes `site_config` in `__init__` and reads its limits via
  `_limit()` helper.
- `services.settings_service.SettingsService` — the write path
  (mutate settings + emit `pipeline_events`).
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_env_vars.md`
  and `feedback_db_first_config.md` — why this exists.
