# System timezone awareness — design

**Issue:** _to file — `Glad-Labs/poindexter` (OSS; the clock module + tz-aware scheduler ship in the public mirror. The operator default value lands in the stripped overlay only.)_
**Date:** 2026-07-10
**Status:** Approved
**Surfaced by:** operator investigation 2026-07-10 — "why does my morning brief fire at 3am?" The `morning_brief` job is scheduled `0 7 * * *` and intended for 7am, but fires at 03:00 America/New_York.

## Problem

The stack has **no concept of the timezone it operates in.** There is no
`operator_timezone` setting and no `zoneinfo` usage anywhere in the tree. Every
wall-clock cron is authored by a human mentally converting to UTC, and every
operator-facing timestamp is rendered in UTC. This is silently wrong in two
ways:

1. **Scheduled jobs fire at the wrong local time.** APScheduler evaluates cron
   triggers in the scheduler's timezone, which defaults to the process/container
   local zone. The worker container sets `TZ=${TZ:-UTC}`
   (`docker-compose.local.yml`), so every cron is really a UTC cron. `0 7 * * *`
   = 07:00 UTC = **03:00 America/New_York (EDT)** — the reported bug.

2. **The "author cron in UTC in your head" convention is error-prone** — it has
   a 2-of-3 defect rate today:

   | Job                     | Schedule     | Comment claims                      | Actually fires (ET) |
   | ----------------------- | ------------ | ----------------------------------- | ------------------- |
   | `morning_brief`         | `0 7 * * *`  | "07:00 local container time"        | 3am ❌              |
   | `findings_daily_digest` | `0 9 * * *`  | "09:00 local"                       | 5am ❌              |
   | `run_dev_diary_post`    | `0 13 * * *` | "09:00 America/New_York during EDT" | 9am ✅              |

   Only the job whose author correctly hand-converted to UTC lands right, and
   even it drifts by an hour across DST (a fixed-UTC cron cannot track a local
   wall-clock time year-round).

Operator-facing timestamps have the same root gap: the `morning_brief` date
header is `datetime.now(timezone.utc)`, so a brief delivered at 7am ET can show
"tomorrow's" date near the UTC-midnight boundary.

## Root cause

A four-layer chain, each link verified in-tree:

1. **Job** — `MorningBriefJob.schedule = "0 7 * * *"`, comment claims "local"
   (`services/jobs/morning_brief.py:46`).
2. **Scheduler** — `CronTrigger.from_crontab(schedule)` on a bare
   `AsyncIOScheduler()` with no `timezone=` argument
   (`plugins/scheduler.py:109,134`) → cron is evaluated in the container local
   zone.
3. **Container** — `TZ: ${TZ:-UTC}` → the container local zone _is_ UTC
   (`docker-compose.local.yml:1851`).
4. **Offset** — 07:00 UTC − 4h (EDT) = 03:00 America/New_York.

The `run_dev_diary_post` comment ("Cron `0 13 * * *` UTC = 9am EDT") independently
corroborates that crons in this system are UTC-evaluated. The two contradictory
"local" comments are simply wrong.

## Goals

1. Give the system a single authoritative timezone it schedules and renders
   operator-facing time in, DST-correct year-round via IANA `zoneinfo`.
2. Fix the two mis-firing cron jobs and stop the class of bug at its source, so
   new wall-clock crons are authored in local time and mean it.
3. Render the acute operator-facing surfaces (morning brief, brain alerts) in the
   operator's zone.
4. Keep the timezone a DB-backed tunable (`app_settings`), reachable from both the
   DI worker and the bare-pool brain daemon. Nothing new on disk.
5. Fail loud on a misconfigured zone; never crash the worker or the self-healing
   brain over a display-setting typo.

## Non-goals

- **No change to storage or machine surfaces.** DB stays `timestamptz`/UTC; logs,
  metrics, traces stay UTC so they still correlate 1:1 with Loki / Prometheus /
  Tempo / Grafana.
- **Grafana dashboard timezone** — an optional infra follow-up (a dashboards JSON
  setting, not code); out of this spec.
- **Console (web UI) localization** — the browser already renders in the viewer's
  local zone; pinning it to `operator_timezone` for travel is a follow-up.
- **No hard lint ratchet** — a documented convention (use `clock` helpers)
  instead, to avoid ratchet noise.

## Design

### Scope boundary

**Store UTC, know one operator timezone, apply it only at the scheduling and
human-output edges.** The DB and all machine-facing telemetry stay UTC;
conversion happens exactly where a schedule or a human is involved.

### The setting

One new `app_settings` key:

- **`operator_timezone`** — an IANA name (e.g. `America/New_York`), non-secret,
  category `general`.
- **OSS default `UTC`** — seeded in `services/settings_defaults.py` and the
  baseline seeds. A location-neutral base keeps the public mirror free of
  operator location and gives fresh installs sane behavior.
- **Operator value `America/New_York`** — set via the stripped
  `operator_overrides.py` seam so it never reaches the public mirror
  (no-operator-info-in-public).

The vestigial `morning_brief_hour_local` key (currently informational-only)
becomes fully redundant once `0 7 * * *` means 7am local; its description is
repointed at `operator_timezone` (kept as a harmless mirror, not wired to
generate the cron — the cron is now correct on its own).

### The clock module — `services/clock.py`

Generic substrate (like `prompt_manager`); the engine never imports business
code. It owns the entire concept behind one small interface:

```python
DEFAULT_TZ = "UTC"

def resolve_operator_tz(name: str | None) -> ZoneInfo   # parse + validate; loud-degrade to UTC on invalid
async def get_operator_tz(pool) -> ZoneInfo             # bare-pool accessor: read app_settings, resolve
def now_local(tz: ZoneInfo) -> datetime                 # aware datetime in operator tz
def today_local(tz: ZoneInfo) -> date                   # calendar day in operator tz
def to_local(dt: datetime, tz: ZoneInfo) -> datetime    # convert a stored UTC dt for display
def format_local(dt: datetime, tz: ZoneInfo, fmt: str) -> str
```

**Two accessors, two runtimes** — both funnel through `resolve_operator_tz`, so
there is exactly one validation path:

- **DI worker** → a `SiteConfig.timezone` property resolving the cached
  `operator_timezone` string (route handlers, services). Picks up changes on the
  minute-ly `SiteConfig` reload.
- **Bare-pool contexts** → `await get_operator_tz(pool)` reading `app_settings`
  directly, for the independent **brain** (Python + asyncpg only, no DI container)
  and for scheduler registration.

**Dependency:** add `tzdata` to `pyproject.toml`. Stdlib `zoneinfo` reads the OS
tz database; the Linux containers have it, but the Windows dev host does not, so
without `tzdata` a valid name raises `ZoneInfoNotFoundError` on Matt's PC (CLI +
test suite) while passing in CI. `zoneinfo` itself is stdlib — `tzdata` is only
the data, no third-party tz _library_.

### Scheduler integration

Two changes in `plugins/scheduler.py`:

- Resolve the operator tz once at scheduler construction (the bootstrap already
  holds the pool) and build `AsyncIOScheduler(timezone=tz)`.
- Thread it through `_parse_schedule(schedule, tz)` →
  `CronTrigger.from_crontab(expr, timezone=tz)`. Interval triggers (`every N …`)
  are relative and stay untouched.

The cron _schedule_ binds to the tz at worker-registration time, so a change to
`operator_timezone` takes effect on the next worker restart (rare enough to be
acceptable; human-facing formatting updates live).

### Cron rebase + guarded data fix

The moment the scheduler localizes crons, every UTC-authored wall-clock cron
changes meaning. The three intent-bearing jobs:

| Job                     | Before       | After                                      | Fires  |
| ----------------------- | ------------ | ------------------------------------------ | ------ |
| `morning_brief`         | `0 7 * * *`  | `0 7 * * *` (unchanged; now _means_ local) | 7am ✅ |
| `findings_daily_digest` | `0 9 * * *`  | `0 9 * * *` (unchanged; now local)         | 9am ✅ |
| `run_dev_diary_post`    | `0 13 * * *` | **`0 9 * * *`** (was pre-baked to UTC)     | 9am ✅ |

Comments on all three are corrected to state the local intent.

**Periodic crons** (`0 */2 * * *`, `0 */6 * * *`, …) express a _cadence_, not an
appointment — reinterpreting the anchor hour across zones is functionally
equivalent, so they are left as-is. A complete sweep of both code-default
schedules **and** any `plugin.job.*` DB `config.schedule` overrides is done at
implementation time to confirm the full list.

**The DB-override catch.** `_seed_job_config_if_absent` writes a `plugin.job.<name>`
row `ON CONFLICT DO NOTHING` on first registration, so prod already holds a
`plugin.job.run_dev_diary_post` row pinned at `0 13`; a code change to `0 9` never
propagates because the DB override wins. So the `dev_diary` rebase needs a
one-time, **guarded** data fix delivered as a one-shot convergence migration
(`YYYYMMDD_HHMMSS_rebase_dev_diary_cron_local.py`, same pattern as the
`drop_pipeline_tasks_category` convergence step): a JSON-guarded `UPDATE` that
rewrites the row's `config.schedule` to `0 9` **only if it still equals the old
`0 13` default**, so a hand-tuned schedule is never clobbered. This is a
one-time _correction_ of a stale row after a semantics change, not a default
seed — so it belongs in a migration, not `settings_defaults` (which only
`INSERT … ON CONFLICT DO NOTHING` and cannot `UPDATE`). `morning_brief` and
`findings_daily_digest` keep their strings, so their existing rows need no touch.

### Human-output consumers

- **Lands in this change (acute surfaces):**
  - **`morning_brief`** — date header `datetime.now(timezone.utc).strftime(...)`
    (`services/jobs/morning_brief.py:467`) → `today_local(tz)`. The lookback is
    relative (`NOW() - interval`) and already correct. Bare-pool →
    `get_operator_tz(pool)`.
  - **Brain operator alerts** — timestamps rendered into Telegram/Discord in
    `brain/operator_notifier.py` / `brain/alert_dispatcher.py` localize.
    Bare-pool.
- **Adopts the pattern incrementally** (documented, not forced in this PR): CLI
  timestamp output and other operator renders convert as touched, all calling the
  same `clock` helpers.
- **Out of scope:** the console web UI (different runtime) and all machine
  surfaces (UTC by design).

### Error handling

- **Missing / empty `operator_timezone`** → default to **UTC**. A documented safe
  base, not a silent dangerous fallback — no alert.
- **Invalid IANA name** (typo, or `tzdata` absent for a valid name) → **loud but
  degrade**: log error + `notify_operator()` + emit a `finding`, then fall back to
  UTC. A display/scheduling-setting typo must not take down content generation or
  the self-healing brain; the alert nags until it is fixed.

### DST handling

`zoneinfo` + APScheduler fire at the wall-clock hour year-round (7am stays 7am
across the March/November transitions). The three jobs sit at 7am/9am, clear of
the 2–3am spring-forward gap, so there is no ambiguous-time edge.

## Testing

- **`resolve_operator_tz`**: valid → correct zone; empty/None → UTC; invalid →
  UTC **and** the notify/finding path fires.
- **DST correctness** (the test that would have caught this bug): freeze a January
  date and a July date; assert `0 7 * * *` in `America/New_York` resolves to
  **12:00 UTC in winter** and **11:00 UTC in summer**.
- **Scheduler**: `_parse_schedule("0 7 * * *", tz)` yields a next-fire at 7am
  local; interval schedules provably unaffected.
- **Rebase regression**: the three jobs resolve to 7am/9am/9am local; explicit
  guard that dev_diary is `0 9`, not `0 13`.
- **Guarded data fix**: `0 13`→`0 9` rewrite fires; a hand-tuned value is left
  untouched.
- **morning_brief off-by-one**: around a UTC-midnight boundary (11pm ET = next-day
  UTC), assert the brief's date header shows the ET day.
- **Bare-pool accessor**: `get_operator_tz(pool)` reads `app_settings` and
  resolves (the brain path).

## Rollout & verification

- Ships as a PR; CI-green merges; timezone binds at registration, so it needs a
  worker **restart** to take effect.
- **Empirical proof** (observe real output, not config): the morning brief lands
  in Discord at **7am ET, not 3am**, plus a spot-check of the scheduler's
  next-fire times for the three jobs (`job_run_state` / APScheduler `next_run_time`).

## Docs

- `docs/architecture/system-timezone.md` — the store-UTC/present-local boundary,
  the `operator_timezone` setting, and `clock` as the way to render operator time.
- One-line pointer in CLAUDE.md's configuration section.

## File-change inventory (for the plan)

- **New:** `services/clock.py`; `docs/architecture/system-timezone.md`; unit tests
  (`tests/unit/services/test_clock.py`, scheduler tz test, morning_brief date
  test).
- **Edit:** `plugins/scheduler.py` (tz-aware scheduler + `_parse_schedule`);
  `services/site_config.py` (`timezone` property); `services/settings_defaults.py`
  - baseline seeds (`operator_timezone=UTC`); `operator_overrides.py`
    (`operator_timezone=America/New_York`); `services/jobs/morning_brief.py`
    (date header + comment); `services/jobs/run_dev_diary_post.py` (`0 13`→`0 9` +
    comment); `services/jobs/findings_daily_digest.py` (comment);
    `brain/operator_notifier.py` + `brain/alert_dispatcher.py` (localized
    timestamps); `pyproject.toml` (`tzdata`).
- **Migration:** one-shot guarded convergence migration
  (`YYYYMMDD_HHMMSS_rebase_dev_diary_cron_local.py`) rewriting the
  `plugin.job.run_dev_diary_post` row `0 13`→`0 9` only if unchanged from default.
