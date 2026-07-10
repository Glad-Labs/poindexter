# System timezone

Poindexter operates in a single operator timezone: **store UTC, present and
schedule local.** The database (`timestamptz`), logs, metrics, and traces stay
UTC so they correlate 1:1 with Loki / Prometheus / Tempo / Grafana. Conversion
to the operator's zone happens in exactly two places: the scheduler's wall-clock
cron interpretation, and operator-facing timestamp rendering.

## The setting

`app_settings.operator_timezone` — an IANA name (e.g. `America/New_York`).
OSS default `UTC` (location-neutral); the operator overlay
(`services/operator_overrides.py`, stripped from the public mirror) sets the
real zone. Change it live with `poindexter settings set operator_timezone
<IANA>`; the cron _schedule_ binds at worker-registration time, so a zone change
takes effect on the next worker restart (human-facing formatting updates on the
minute-ly SiteConfig reload).

## The helper — `services/clock.py`

The only place code converts to the operator zone. Use it instead of
`datetime.now(timezone.utc).strftime(...)` for anything a human reads:

- DI worker / services: `site_config.timezone` (a `ZoneInfo`).
- Bare-pool contexts (brain, jobs, scheduler): `await get_operator_tz(pool)`.
- Formatting: `now_local(tz)`, `today_local(tz)`, `to_local(dt, tz)`,
  `format_local(dt, tz, fmt)`.

Invalid zone → logged + a deduped `invalid_timezone` finding, then degrades to
UTC (never crashes the worker or brain). Missing/empty → UTC silently.

## Cron authoring

Wall-clock cron jobs (`0 7 * * *`) are authored in **operator-local** time —
the scheduler evaluates them in `operator_timezone`, DST-correct via `zoneinfo`.
Do **not** hand-convert to UTC. Periodic crons (`0 */2 * * *`) express a cadence
and are zone-agnostic.
