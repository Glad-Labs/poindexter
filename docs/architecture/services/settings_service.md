# Settings Service

**File:** `src/cofounder_agent/services/settings_service.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_settings_service.py`
**Last reviewed:** 2026-04-30

## What it does

`SettingsService` is the **write path** for the `app_settings` table:
upsert, delete, and category-scoped reads. It also has a TTL-cached
read path (`get`, `get_all`) that the `/api/settings` route handler
and the OpenClaw setting-management UI use directly.

Every other service in the codebase reads settings through `SiteConfig`
(`docs/architecture/services/site_config.md`), which has its own
in-memory cache populated at startup. `SettingsService` is the layer
that mutates the underlying table; `SiteConfig` is the layer that
exposes it to consumers. When a setting is written here, downstream
readers won't see it until either `SiteConfig.reload()` is called or
the app restarts.

It's instantiated once in `main.py` lifespan
(`SettingsService(db_pool)`) and registered in `services.container`
under the key `"settings"`. It's distinct from `SiteConfig` —
`SettingsService` is async-everywhere, takes a raw `asyncpg` pool, and
hits the DB on every write.

## Public API

- `SettingsService(pool)` — constructor. Stores the asyncpg pool and
  initializes an empty cache with a 60-second TTL.
- `await svc.get(key, default=None) -> str | None` — TTL-cached read.
  Falls back to the uppercase env var when the DB row is empty,
  matching `SiteConfig.get()` semantics for migration compatibility.
- `await svc.get_by_category(category) -> dict[str, str]` —
  `{key: value}` for every cached row whose `category` column matches.
- `await svc.set(key, value, category=None, description=None, is_secret=None)` —
  upsert. Optional fields are only updated on conflict if explicitly
  passed (None means "leave the existing column alone"). Invalidates
  the local cache so the next `get_*` call re-fetches.
- `await svc.delete(key)` — DELETE by key. Logs the result string and
  invalidates the cache.
- `await svc.get_all(include_secrets=False) -> list[dict]` — full
  dump of `[{key, value, category, description, is_secret,
updated_at}]`, sorted by key. Secret values are replaced with
  `"********"` unless `include_secrets=True`.
- `await svc.refresh_cache()` — force a re-read from DB. Called
  automatically by `_ensure_cache()` when the cache is empty or
  stale.

## Key behaviors / invariants

- **Cache is per-instance, not process-global.** `SettingsService`
  has its own dict; `SiteConfig` has its own dict. Writing through
  this service does NOT update `SiteConfig`'s cache. Callers that
  depend on the new value must either go through `SettingsService` for
  the read too, or call `app.state.site_config.reload(pool)`
  afterward.
- **TTL is 60 seconds, hardcoded.** Set in `__init__` as
  `self._cache_ttl = 60`. Not currently DB-configurable.
- **Cache invalidation is a 0-timestamp reset, not a clear.** `set()`
  and `delete()` both set `self._last_refresh = 0`, so the next
  read triggers `refresh_cache()` rather than waiting out the TTL.
- **Secret masking is opt-out, not opt-in.** `get_all()` masks any
  row with `is_secret=true` unless the caller explicitly passes
  `include_secrets=True`. The single-key `get()` returns the real
  value with no masking — secret-aware UI surfaces should use
  `get_all()` or check `is_secret` separately.
- **`get()` env-var fallback is uppercased.** `await svc.get("foo_bar")`
  falls through to `os.getenv("FOO_BAR")`. This matches `SiteConfig`
  but means setting `foo_bar` in the DB to `""` (empty string) will
  silently fall through to env — empty isn't the same as "no row."
- **Write fields with COALESCE.** The upsert SQL uses
  `COALESCE($N, app_settings.<col>)` for category, description,
  is_secret — so passing `None` preserves the existing value rather
  than overwriting with NULL.

## Configuration

`SettingsService` reads no `app_settings` keys itself. Its only
configuration is the constructor's `pool` argument and the
hardcoded `_cache_ttl = 60` constant.

The schema it operates against (`app_settings` table) has columns:
`key TEXT PRIMARY KEY, value TEXT, category TEXT DEFAULT 'general',
description TEXT, is_secret BOOLEAN DEFAULT FALSE, updated_at
TIMESTAMP`.

## Dependencies

- **Reads from:**
  - `app_settings` table (full table scan in `refresh_cache`).
- **Writes to:**
  - `app_settings` table (single-row upserts and deletes).
- **External APIs:** none.
- **Callers:**
  - `main.py` lifespan — instantiates and registers it.
  - `services.container` — stores it under key `"settings"` for DI.
  - `/api/settings` and `/api/settings/{key}` route handlers — use
    it for both reads (with masking) and writes.
  - OpenClaw setting-management UI — same routes.

## Failure modes

- **DB connection failure during `refresh_cache()`** — caught, logged
  as error, leaves `self._cache` in its previous state. Subsequent
  `get()` calls serve stale data. This is intentional — better to
  serve a 60-second-old value than to error out the request — but
  means a long DB outage will eventually serve very stale data with
  no alarm raised here. Watch the application-level DB-health alerts.
- **Write failure** — `set()` and `delete()` propagate `asyncpg`
  exceptions to the caller (no swallow). The cache is NOT invalidated
  on failure, so a failed write doesn't leave the cache in a bogus
  state.
- **Race between writers** — two concurrent `set()` calls are
  serialized by Postgres' `INSERT ... ON CONFLICT`. The cache reset
  flag is set unconditionally, so the next read always sees the
  winner.
- **Cache-vs-`SiteConfig` divergence** — if you write a value through
  `SettingsService` and immediately read through `SiteConfig`, you
  may see the old value until either (a) the calling layer triggers
  `SiteConfig.reload(pool)`, or (b) you restart the app. The
  `/api/settings` PUT handler should call `reload()` after a
  successful write — verify in the route code if you suspect this is
  out of sync.

## Common ops

- **Set a value via Python (one-off script):**
  ```python
  await services["settings"].set(
      "daily_spend_limit_usd", "5.00",
      category="cost_guard", description="Daily LLM spend cap"
  )
  ```
- **Set a value via CLI:** `poindexter set <key> <value>` — wraps
  this service through the API.
- **List everything (with secrets revealed):**
  ```python
  rows = await services["settings"].get_all(include_secrets=True)
  ```
  Don't log the result — secrets included means secrets in logs.
- **Force a cache refresh:**
  `await services["settings"].refresh_cache()`. Useful from a debug
  endpoint or one-off shell.
- **Find which categories exist:**
  `SELECT DISTINCT category FROM app_settings ORDER BY 1;`
- **Check the cache TTL:** it's hardcoded to 60s — if you need it
  shorter for development, change `self._cache_ttl` in
  `__init__`. (Long-term, this should be an `app_settings` key like
  `settings_service_cache_ttl_seconds` per the project's "config in
  DB, not code" stance.)

## See also

- `docs/architecture/services/site_config.md` — the **read** path
  used by every service. Read its "How callers should plumb the
  instance" section before adding a new consumer.
- `services.bootstrap_defaults.ensure_defaults` — populates new keys
  with sensible defaults at startup so a fresh DB doesn't need
  manual seeding.
- `~/.claude/projects/C--Users-mattm/memory/feedback_db_first_config.md`
  — the project's "everything tunable in DB" policy this service
  enforces.
- `~/.claude/projects/C--Users-mattm/memory/feedback_no_silent_defaults.md`
  — note that the env-var fallback in `get()` is the ONE silent
  default the project tolerates, kept only for migration
  compatibility.
