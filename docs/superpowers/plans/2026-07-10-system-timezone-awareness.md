# System Timezone Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the system a single authoritative `operator_timezone` it schedules cron jobs and renders operator-facing timestamps in (store-UTC / present-local), fixing the "morning brief fires at 3am" bug at its source.

**Architecture:** A new `services/clock.py` module owns the timezone concept behind one small interface, resolving an IANA name from the `operator_timezone` app_setting. The APScheduler wrapper becomes timezone-aware so wall-clock crons fire in the operator's zone (DST-correct via stdlib `zoneinfo`); the three intent-bearing cron jobs are rebased to local semantics, with a guarded convergence migration correcting the one pre-seeded prod row. The morning-brief date header is the one operator-facing render localized now; storage, logs, metrics, and traces stay UTC.

**Tech Stack:** Python 3.12, stdlib `zoneinfo` + `tzdata` (data only), APScheduler 3 (`AsyncIOScheduler` / `CronTrigger`), asyncpg, pytest + freezegun.

## Global Constraints

- **Store UTC, present/schedule local.** DB stays `timestamptz`/UTC; logs, metrics, traces stay UTC. Only scheduling + human output convert.
- **OSS default `operator_timezone = "UTC"`** in `settings_defaults.DEFAULTS`. The operator value `America/New_York` lives ONLY in `services/operator_overrides.py` (stripped from the public mirror) — never in a public seed (no-operator-info-in-public; enforced by `test_baseline_seeds_no_operator_leaks`).
- **New app_settings keys go in `settings_defaults.py`, not migration files** (the seeder runs every boot `ON CONFLICT DO NOTHING`; migrations run once). Migrations are for schema DDL + one-time data corrections.
- **Fail loud but degrade:** an invalid IANA name logs an error + emits a deduped `finding`, then falls back to UTC. Never crash the worker or brain over a timezone typo. Missing/empty → UTC silently (documented base).
- **Every change ships with contract tests + doc updates.**
- **All changes via PR** (this branch → draft PR #2247); **CI green is the merge gate**; **linear history** (squash/rebase, no merge commits).
- **Commits** use conventional-commit subjects and end with the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

| File                                                                          | Responsibility                                                             | Change     |
| ----------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ---------- |
| `src/cofounder_agent/services/clock.py`                                       | Owns the timezone concept: resolve + 4 format helpers + bare-pool accessor | **Create** |
| `src/cofounder_agent/pyproject.toml`                                          | Add `tzdata` runtime dep; ensure `freezegun` dev dep                       | Modify     |
| `src/cofounder_agent/services/settings_defaults.py`                           | Seed `operator_timezone=UTC` (OSS default)                                 | Modify     |
| `src/cofounder_agent/services/operator_overrides.py`                          | Operator value `operator_timezone=America/New_York`                        | Modify     |
| `src/cofounder_agent/services/site_config.py`                                 | `timezone` property (DI-worker accessor)                                   | Modify     |
| `src/cofounder_agent/plugins/scheduler.py`                                    | tz-aware `AsyncIOScheduler` + `_parse_schedule(schedule, tz)`              | Modify     |
| `src/cofounder_agent/services/jobs/run_dev_diary_post.py`                     | Rebase `0 13`→`0 9` + comment                                              | Modify     |
| `src/cofounder_agent/services/jobs/findings_daily_digest.py`                  | Comment fix (schedule unchanged)                                           | Modify     |
| `src/cofounder_agent/services/jobs/morning_brief.py`                          | Comment fix + localize date header                                         | Modify     |
| `src/cofounder_agent/services/migrations/<ts>_rebase_dev_diary_cron_local.py` | Guarded convergence: dev_diary DB row `0 13`→`0 9`                         | **Create** |
| `docs/architecture/system-timezone.md`                                        | Document the boundary + setting + helper                                   | **Create** |
| `CLAUDE.md`                                                                   | One-line pointer in the config section                                     | Modify     |
| Tests (per task)                                                              | Contract tests                                                             | **Create** |

**Not changed (verified during planning):** `brain/operator_notifier.py` and `brain/alert_dispatcher.py` have no operator-facing _formatted_ timestamp — the only renders are the UTC `alerts.log` write and internal dedup/audit fields, all of which stay UTC by design. `main.py` needs no change: the scheduler resolves the zone from the `site_config` it is already passed at [`main.py:531`](../../../src/cofounder_agent/main.py#L531).

---

### Task 1: The clock module

**Files:**

- Create: `src/cofounder_agent/services/clock.py`
- Modify: `src/cofounder_agent/pyproject.toml`
- Test: `src/cofounder_agent/tests/unit/services/test_clock.py`

**Interfaces:**

- Consumes: stdlib `zoneinfo`; `utils.findings.emit_finding` (lazy, error path only).
- Produces:
  - `DEFAULT_TZ: str = "UTC"`
  - `resolve_operator_tz(name: str | None) -> ZoneInfo`
  - `async get_operator_tz(pool) -> ZoneInfo`
  - `now_local(tz: ZoneInfo) -> datetime`
  - `today_local(tz: ZoneInfo) -> date`
  - `to_local(dt: datetime, tz: ZoneInfo) -> datetime`
  - `format_local(dt: datetime, tz: ZoneInfo, fmt: str) -> str`

- [ ] **Step 1: Ensure test deps.** Confirm `freezegun` is available; add if missing.

Run: `cd src/cofounder_agent && poetry run python -c "import freezegun"`
If it errors: `cd src/cofounder_agent && poetry add --group dev freezegun`

- [ ] **Step 2: Add the `tzdata` runtime dependency.** In `src/cofounder_agent/pyproject.toml`, under `[tool.poetry.dependencies]`, add:

```toml
tzdata = "*"  # ships the IANA tz database so stdlib zoneinfo resolves names on Windows hosts (Linux containers already have it)
```

Run: `cd src/cofounder_agent && poetry lock --no-update && poetry install`

- [ ] **Step 3: Write the failing tests.**

```python
# src/cofounder_agent/tests/unit/services/test_clock.py
"""Contract tests for the system clock (operator timezone)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from services import clock


def test_resolve_valid_name():
    tz = clock.resolve_operator_tz("America/New_York")
    assert tz.key == "America/New_York"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_resolve_missing_defaults_to_utc(value):
    assert clock.resolve_operator_tz(value).key == "UTC"


def test_resolve_invalid_is_loud_and_degrades(monkeypatch):
    calls = []
    monkeypatch.setattr(clock, "emit_finding", lambda **kw: calls.append(kw), raising=False)
    tz = clock.resolve_operator_tz("Amrica/New_York")  # typo
    assert tz.key == "UTC"                              # degraded, did not raise
    assert calls and calls[0]["severity"] == "warn"    # loud
    assert "Amrica/New_York" in calls[0]["body"]


def test_dst_summer_maps_7am_local_to_11utc():
    # 2026-07-01 is EDT (UTC-4): 07:00 local == 11:00 UTC.
    tz = clock.resolve_operator_tz("America/New_York")
    local_7am = datetime(2026, 7, 1, 7, 0, tzinfo=tz)
    assert local_7am.astimezone(timezone.utc).hour == 11


def test_dst_winter_maps_7am_local_to_12utc():
    # 2026-01-01 is EST (UTC-5): 07:00 local == 12:00 UTC.
    tz = clock.resolve_operator_tz("America/New_York")
    local_7am = datetime(2026, 1, 1, 7, 0, tzinfo=tz)
    assert local_7am.astimezone(timezone.utc).hour == 12


@freeze_time("2026-07-11T03:00:00Z")  # 11:00 PM prior day in ET
def test_today_local_crosses_utc_midnight():
    tz = clock.resolve_operator_tz("America/New_York")
    assert clock.today_local(tz).isoformat() == "2026-07-10"


def test_to_local_treats_naive_as_utc():
    tz = clock.resolve_operator_tz("America/New_York")
    naive = datetime(2026, 7, 1, 11, 0)  # no tzinfo -> assume UTC
    assert clock.to_local(naive, tz).hour == 7


def test_format_local():
    tz = clock.resolve_operator_tz("America/New_York")
    dt = datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)
    assert clock.format_local(dt, tz, "%Y-%m-%d %H:%M") == "2026-07-01 07:00"
```

- [ ] **Step 4: Run tests to verify they fail.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_clock.py -q`
Expected: FAIL / collection error — `services.clock` does not exist.

- [ ] **Step 5: Implement the module.**

```python
# src/cofounder_agent/services/clock.py
"""System clock — the operator timezone the whole system schedules and renders in.

Store-UTC / present-local: the DB, logs, metrics, and traces stay UTC. This
module is the ONLY place that converts to the operator's zone, for (a) the
scheduler's wall-clock cron interpretation and (b) operator-facing timestamp
formatting. The zone is a DB-backed tunable (`app_settings.operator_timezone`,
an IANA name); the OSS default is UTC and the operator overlay sets the real
zone.

Reachable from both runtimes: the DI worker via `SiteConfig.timezone`, and the
bare-pool brain / jobs / scheduler via `get_operator_tz(pool)`. Both funnel
through `resolve_operator_tz`, so there is exactly one validation path.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_TZ = "UTC"

# Bound at module scope (default None) so tests can monkeypatch it; the real
# import is lazy inside _warn_invalid_tz to avoid an import cycle at boot.
emit_finding = None  # type: ignore[assignment]


def resolve_operator_tz(name: str | None) -> ZoneInfo:
    """Parse an IANA timezone name into a ZoneInfo.

    Missing/empty -> UTC (documented base, no alert). Invalid name -> loud
    (log + deduped finding) but degrade to UTC; NEVER raises, so a timezone
    typo cannot crash the worker or the self-healing brain.
    """
    if not name or not name.strip():
        return ZoneInfo(DEFAULT_TZ)
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        _warn_invalid_tz(name.strip(), exc)
        return ZoneInfo(DEFAULT_TZ)


def _warn_invalid_tz(name: str, exc: Exception) -> None:
    logger.error("[clock] invalid operator_timezone %r (%s); falling back to UTC", name, exc)
    try:
        emit = emit_finding
        if emit is None:  # normal runtime path (tests monkeypatch the module attr)
            from utils.findings import emit_finding as emit
        emit(
            source="services.clock",
            kind="invalid_timezone",
            title="operator_timezone is not a valid IANA name",
            body=f"operator_timezone={name!r} did not resolve ({exc}); using UTC until fixed.",
            severity="warn",
            dedup_key=f"invalid-operator-timezone:{name}",
        )
    except Exception as e:  # noqa: BLE001 — resolve must never raise
        logger.debug("[clock] finding emit skipped: %s", e)


async def get_operator_tz(pool: Any) -> ZoneInfo:
    """Bare-pool accessor: read `app_settings.operator_timezone` and resolve.

    For the independent brain (Python + asyncpg, no DI container), scheduled
    jobs, and the scheduler. `operator_timezone` is non-secret, so a direct
    read is correct (no decryption needed). Any failure degrades to UTC.
    """
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = 'operator_timezone'"
            )
    except Exception as e:  # noqa: BLE001 — degrade, never raise
        logger.debug("[clock] operator_timezone read failed: %s", e)
        value = None
    return resolve_operator_tz(value)


def now_local(tz: ZoneInfo) -> datetime:
    """Current instant as an aware datetime in the operator zone."""
    return datetime.now(tz)


def today_local(tz: ZoneInfo) -> date:
    """Current calendar day in the operator zone."""
    return datetime.now(tz).date()


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    """Convert a stored datetime for display. Naive datetimes are assumed UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def format_local(dt: datetime, tz: ZoneInfo, fmt: str) -> str:
    """Render a stored datetime in the operator zone with `strftime`."""
    return to_local(dt, tz).strftime(fmt)
```

- [ ] **Step 6: Run tests to verify they pass.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_clock.py -q`
Expected: PASS (8 tests).

- [ ] **Step 7: Commit.**

```bash
git add src/cofounder_agent/services/clock.py src/cofounder_agent/tests/unit/services/test_clock.py src/cofounder_agent/pyproject.toml src/cofounder_agent/poetry.lock
git commit -m "$(cat <<'EOF'
feat(clock): add system operator-timezone module

resolve_operator_tz + bare-pool get_operator_tz + now/today/to/format_local
helpers. Store-UTC/present-local: the one place the system converts to the
operator zone. Invalid zone is loud-but-degrades to UTC (never crashes). Adds
tzdata so stdlib zoneinfo resolves on Windows hosts.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Seed the setting (OSS default + operator override)

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (add to `DEFAULTS`)
- Modify: `src/cofounder_agent/services/operator_overrides.py` (add to `OPERATOR_SETTING_OVERRIDES`)
- Test: `src/cofounder_agent/tests/unit/services/test_operator_timezone_seed.py`

**Interfaces:**

- Consumes: nothing.
- Produces: the `operator_timezone` app_setting (default `UTC`; operator overlay `America/New_York`).

- [ ] **Step 1: Write the failing test.**

```python
# src/cofounder_agent/tests/unit/services/test_operator_timezone_seed.py
"""operator_timezone: OSS default is UTC; operator overlay is America/New_York."""
from __future__ import annotations

from services.settings_defaults import DEFAULTS


def test_oss_default_is_utc():
    assert DEFAULTS["operator_timezone"] == "UTC"


def test_operator_overlay_sets_real_zone():
    # The overlay module is stripped from the public mirror; import guarded.
    from services.operator_overrides import OPERATOR_SETTING_OVERRIDES

    assert OPERATOR_SETTING_OVERRIDES["operator_timezone"] == "America/New_York"


def test_public_default_carries_no_operator_location():
    # Belt-and-suspenders against the no-operator-info-in-public rule.
    assert "America" not in DEFAULTS["operator_timezone"]
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_operator_timezone_seed.py -q`
Expected: FAIL — `KeyError: 'operator_timezone'`.

- [ ] **Step 3: Add the OSS default.** In `src/cofounder_agent/services/settings_defaults.py`, inside the `DEFAULTS` dict, add:

```python
    # ----- Localization -----
    # IANA timezone the system schedules cron jobs in and renders
    # operator-facing timestamps in (store-UTC / present-local, services/clock.py).
    # OSS default UTC — location-neutral; the operator overlay sets the real zone.
    'operator_timezone': 'UTC',
```

- [ ] **Step 4: Add the operator override.** In `src/cofounder_agent/services/operator_overrides.py`, inside `OPERATOR_SETTING_OVERRIDES`, add:

```python
    # Operator's real zone (OSS seed ships UTC). Drives cron fire-times +
    # operator-facing timestamp rendering. Applied over the UTC default by
    # apply_operator_overrides only while the row still holds the OSS default.
    "operator_timezone": "America/New_York",
```

- [ ] **Step 5: Run tests to verify they pass.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_operator_timezone_seed.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Verify the operator-leak guard still passes.**

Run: `cd src/cofounder_agent && poetry run pytest -q -k "operator_leak or baseline_seeds_no_operator or operator_overlay"`
Expected: PASS (UTC in the public seed carries no operator location).

- [ ] **Step 7: Commit.**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/services/operator_overrides.py src/cofounder_agent/tests/unit/services/test_operator_timezone_seed.py
git commit -m "$(cat <<'EOF'
feat(settings): seed operator_timezone (UTC OSS default, America/New_York overlay)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: SiteConfig.timezone property (DI-worker accessor)

**Files:**

- Modify: `src/cofounder_agent/services/site_config.py` (add property near `get_list`, ~line 311)
- Test: `src/cofounder_agent/tests/unit/services/test_site_config_timezone.py`

**Interfaces:**

- Consumes: `services.clock.resolve_operator_tz`, `services.clock.DEFAULT_TZ`.
- Produces: `SiteConfig.timezone -> ZoneInfo` property.

- [ ] **Step 1: Write the failing test.**

```python
# src/cofounder_agent/tests/unit/services/test_site_config_timezone.py
from __future__ import annotations

from services.site_config import SiteConfig


def test_timezone_from_config():
    cfg = SiteConfig(initial_config={"operator_timezone": "America/New_York"})
    assert cfg.timezone.key == "America/New_York"


def test_timezone_defaults_to_utc_when_absent():
    cfg = SiteConfig(initial_config={})
    assert cfg.timezone.key == "UTC"
```

- [ ] **Step 2: Run to verify it fails.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_site_config_timezone.py -q`
Expected: FAIL — `AttributeError: 'SiteConfig' object has no attribute 'timezone'`.

- [ ] **Step 3: Add the property.** In `src/cofounder_agent/services/site_config.py`, after `get_list` (ends ~line 311) and before the `is_loaded` property:

```python
    @property
    def timezone(self):
        """The operator's IANA timezone as a ZoneInfo (store-UTC/present-local).

        Reads the cached `operator_timezone` setting and resolves it via
        services.clock (invalid -> loud-degrade to UTC). Picks up changes on
        the minute-ly reload. Returns `zoneinfo.ZoneInfo`.
        """
        from services.clock import DEFAULT_TZ, resolve_operator_tz

        return resolve_operator_tz(self.get("operator_timezone", DEFAULT_TZ))
```

- [ ] **Step 4: Run to verify it passes.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_site_config_timezone.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit.**

```bash
git add src/cofounder_agent/services/site_config.py src/cofounder_agent/tests/unit/services/test_site_config_timezone.py
git commit -m "$(cat <<'EOF'
feat(site-config): SiteConfig.timezone accessor for the DI worker

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Timezone-aware scheduler

**Files:**

- Modify: `src/cofounder_agent/plugins/scheduler.py` (`_parse_schedule`, `PluginScheduler.__init__`, the `_parse_schedule` call in `register_job`)
- Test: `src/cofounder_agent/tests/unit/plugins/test_scheduler_timezone.py`

**Interfaces:**

- Consumes: `services.clock.resolve_operator_tz`, `services.clock.DEFAULT_TZ`.
- Produces: `_parse_schedule(schedule: str, tz)` (tz-aware cron); `PluginScheduler.__init__(pool, *, site_config=None, timezone=None)` storing `self._tz` and constructing `AsyncIOScheduler(timezone=self._tz)`.

- [ ] **Step 1: Write the failing tests.**

```python
# src/cofounder_agent/tests/unit/plugins/test_scheduler_timezone.py
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("apscheduler")

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from plugins import scheduler as sched


def test_cron_parsed_in_operator_tz():
    tz = ZoneInfo("America/New_York")
    trig = sched._parse_schedule("0 7 * * *", tz)
    assert isinstance(trig, CronTrigger)
    # Next fire after a fixed UTC instant lands at 07:00 local == 11:00 UTC (EDT).
    after = datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)
    nxt = trig.get_next_fire_time(None, after)
    assert nxt.astimezone(ZoneInfo("America/New_York")).hour == 7
    assert nxt.astimezone(timezone.utc).hour == 11


def test_interval_is_timezone_agnostic():
    tz = ZoneInfo("America/New_York")
    trig = sched._parse_schedule("every 30 minutes", tz)
    assert isinstance(trig, IntervalTrigger)


def test_scheduler_resolves_tz_from_site_config():
    class _Cfg:
        def get(self, key, default=""):
            return "America/New_York" if key == "operator_timezone" else default

    ps = sched.PluginScheduler(pool=object(), site_config=_Cfg())
    assert ps._tz.key == "America/New_York"


def test_scheduler_defaults_to_utc_without_site_config():
    ps = sched.PluginScheduler(pool=object())
    assert ps._tz.key == "UTC"
```

- [ ] **Step 2: Run to verify they fail.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_scheduler_timezone.py -q`
Expected: FAIL — `_parse_schedule()` takes 1 arg / `PluginScheduler` has no `_tz`.

- [ ] **Step 3: Make `_parse_schedule` tz-aware.** In `src/cofounder_agent/plugins/scheduler.py`, replace the `_parse_schedule` function (currently ~lines 94-111):

```python
def _parse_schedule(schedule: str, tz):
    """Convert a Job.schedule string to an apscheduler trigger in zone ``tz``.

    Returns ``None`` if the schedule string is unrecognized — caller
    should log + skip that Job. Interval triggers are relative and
    timezone-agnostic; only cron triggers bind to ``tz``.
    """
    m = _INTERVAL_RE.match(schedule)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower().rstrip("s")  # "second" / "minute" / "hour" / "day"
        kwargs = {f"{unit}s": n}
        return IntervalTrigger(**kwargs)

    # Cron fallback — parse the 5-field form in the operator timezone.
    try:
        return CronTrigger.from_crontab(schedule, timezone=tz)
    except Exception:
        return None
```

- [ ] **Step 4: Resolve + store the tz in `__init__` and build the scheduler with it.** In `PluginScheduler.__init__` (currently ~lines 117-145), change the signature and the two lines shown:

```python
    def __init__(self, pool: Any, *, site_config: Any = None, timezone: Any = None):
        """Create the scheduler bound to a DB pool.

        ``timezone`` (a ZoneInfo) is resolved from, in priority order: an
        explicit arg, ``site_config['operator_timezone']``, else UTC. Wall-clock
        cron jobs fire in this zone (services/clock.py; store-UTC/present-local).
        """
        if AsyncIOScheduler is None:  # apscheduler absent (lean image)
            raise ImportError(
                "PluginScheduler requires apscheduler — install the worker "
                "extras. Lean images (e.g. the voice agent) import `plugins` "
                "for plugins.secrets but never construct the scheduler.",
            ) from _APSCHEDULER_IMPORT_ERROR
        self._pool = pool
        self._site_config = site_config
        from services.clock import DEFAULT_TZ, resolve_operator_tz

        if timezone is not None:
            self._tz = timezone
        elif site_config is not None:
            self._tz = resolve_operator_tz(site_config.get("operator_timezone", DEFAULT_TZ))
        else:
            self._tz = resolve_operator_tz(None)
        self._scheduler = AsyncIOScheduler(timezone=self._tz)
```

(Leave the rest of `__init__` — the counters, `self._registered`, etc. — exactly as-is.)

- [ ] **Step 5: Pass the tz at the call site.** In `register_job` (currently ~line 175), change:

```python
        trigger = _parse_schedule(schedule, self._tz)
```

- [ ] **Step 6: Run to verify they pass.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_scheduler_timezone.py -q`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the existing scheduler suite for regressions.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/ tests/integration/test_plugin_scheduler.py -q`
Expected: PASS (no regressions; `main.py` needs no change — it already passes `site_config`).

- [ ] **Step 8: Commit.**

```bash
git add src/cofounder_agent/plugins/scheduler.py src/cofounder_agent/tests/unit/plugins/test_scheduler_timezone.py
git commit -m "$(cat <<'EOF'
feat(scheduler): evaluate cron jobs in the operator timezone

AsyncIOScheduler(timezone) + CronTrigger.from_crontab(..., timezone) resolved
from operator_timezone. Interval triggers unaffected. Fixes wall-clock crons
firing in UTC (root cause of morning_brief @ 3am).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Rebase the intent-bearing cron jobs (code + comments)

**Files:**

- Modify: `src/cofounder_agent/services/jobs/run_dev_diary_post.py:63` (`0 13`→`0 9` + comment)
- Modify: `src/cofounder_agent/services/jobs/findings_daily_digest.py:62` (comment)
- Modify: `src/cofounder_agent/services/jobs/morning_brief.py:43-46` (comment)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_cron_local_semantics.py`

**Interfaces:**

- Consumes: nothing.
- Produces: corrected code-default schedules — `morning_brief=0 7`, `findings_daily_digest=0 9`, `run_dev_diary_post=0 9` (all local).

- [ ] **Step 1: Write the failing test.**

```python
# src/cofounder_agent/tests/unit/services/jobs/test_cron_local_semantics.py
"""The three wall-clock jobs are authored in operator-local time now."""
from __future__ import annotations

from services.jobs.morning_brief import MorningBriefJob
from services.jobs.findings_daily_digest import FindingsDailyDigestJob
from services.jobs.run_dev_diary_post import RunDevDiaryPostJob


def test_morning_brief_is_7am_local():
    assert MorningBriefJob.schedule == "0 7 * * *"


def test_findings_digest_is_9am_local():
    assert FindingsDailyDigestJob.schedule == "0 9 * * *"


def test_dev_diary_rebased_to_9am_local_not_1pm():
    # Was "0 13 * * *" (pre-baked to UTC for 9am EDT); now local semantics.
    assert RunDevDiaryPostJob.schedule == "0 9 * * *"
```

- [ ] **Step 2: Run to verify it fails.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_cron_local_semantics.py -q`
Expected: FAIL — dev_diary is still `0 13 * * *`.

- [ ] **Step 3: Rebase dev_diary.** In `src/cofounder_agent/services/jobs/run_dev_diary_post.py`, change line 63:

```python
    schedule = "0 9 * * *"  # 09:00 operator-local (services/clock.py) — was 0 13 UTC pre-tz-aware scheduler
```

- [ ] **Step 4: Fix the findings comment.** In `src/cofounder_agent/services/jobs/findings_daily_digest.py`, change line 62:

```python
    # 09:00 operator-local (services/clock.py). Tunable via plugin.job.findings_daily_digest config.schedule.
```

- [ ] **Step 5: Fix the morning_brief comment.** In `src/cofounder_agent/services/jobs/morning_brief.py`, replace the schedule comment (lines 43-45):

```python
    # 0 7 * * * = 07:00 operator-local (services/clock.py resolves the scheduler
    # timezone from app_settings.operator_timezone). Fires 7am in the operator's
    # zone; the informational morning_brief_hour_local key mirrors the hour.
```

- [ ] **Step 6: Run to verify it passes.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_cron_local_semantics.py -q`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit.**

```bash
git add src/cofounder_agent/services/jobs/run_dev_diary_post.py src/cofounder_agent/services/jobs/findings_daily_digest.py src/cofounder_agent/services/jobs/morning_brief.py src/cofounder_agent/tests/unit/services/jobs/test_cron_local_semantics.py
git commit -m "$(cat <<'EOF'
fix(jobs): rebase wall-clock crons to operator-local semantics

dev_diary 0 13 -> 0 9 (was pre-baked to UTC); morning_brief/findings comments
corrected. With the tz-aware scheduler these now fire 7am/9am/9am local.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Guarded convergence migration for the dev_diary DB row

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_rebase_dev_diary_cron_local.py` (generate `<ts>` with the helper)
- Test: `src/cofounder_agent/tests/unit/services/migrations/test_rebase_dev_diary_cron_local.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `async def up(pool)` / `async def down(pool)` correcting the pre-seeded prod row.

**Why:** `_seed_job_config_if_absent` wrote `plugin.job.run_dev_diary_post` with `ON CONFLICT DO NOTHING` on first registration, so prod already holds `{"config": {"schedule": "0 13 * * *"}}`. The Task-5 code change to `0 9` never propagates (the DB override wins). This migration rewrites that one row **only if it still equals the old default**, so a hand-tuned schedule is never clobbered.

- [ ] **Step 1: Generate the migration file.**

Run: `cd src/cofounder_agent && poetry run python scripts/new-migration.py "rebase dev_diary cron to operator local"`
This creates `services/migrations/<ts>_rebase_dev_diary_cron_local.py`. Note the exact `<ts>` filename for later steps.

- [ ] **Step 2: Write the failing test.**

```python
# src/cofounder_agent/tests/unit/services/migrations/test_rebase_dev_diary_cron_local.py
"""The dev_diary cron-rebase migration is guarded: it only rewrites the stale default."""
from __future__ import annotations

import importlib

import pytest


def _load_migration():
    # Resolve the timestamped module by suffix so the test is filename-stable.
    import pkgutil
    import services.migrations as m

    name = next(
        n for _, n, _ in pkgutil.iter_modules(m.__path__)
        if n.endswith("rebase_dev_diary_cron_local")
    )
    return importlib.import_module(f"services.migrations.{name}")


class _RecordingConn:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


@pytest.mark.asyncio
async def test_up_issues_guarded_update():
    mod = _load_migration()
    conn = _RecordingConn()
    await mod.up(_Pool(conn))
    sql = " ".join(conn.executed).lower()
    assert "plugin.job.run_dev_diary_post" in sql
    assert "0 9 * * *" in sql
    # Guard present: only rewrite when the row still holds the old default.
    assert "0 13 * * *" in sql
    assert "config,schedule" in sql.replace(" ", "")
```

- [ ] **Step 3: Run to verify it fails.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/migrations/test_rebase_dev_diary_cron_local.py -q`
Expected: FAIL — the generated migration's `up` is an empty stub.

- [ ] **Step 4: Implement the migration body.** Replace the generated `up`/`down` in `services/migrations/<ts>_rebase_dev_diary_cron_local.py` with:

```python
"""Rebase the dev_diary job's DB schedule to operator-local semantics.

Convergence step for the tz-aware scheduler. `_seed_job_config_if_absent` wrote
`plugin.job.run_dev_diary_post` with `ON CONFLICT DO NOTHING` on first
registration, so existing installs hold `config.schedule = "0 13 * * *"` (the
old UTC-baked default). With the scheduler now evaluating crons in the operator
zone, that would fire at 1pm local. This rewrites the row to `"0 9 * * *"` ONLY
while it still equals the old default, so a hand-tuned schedule is never
clobbered. Idempotent (re-run after a fix finds no match). No-op on fresh
installs (the seeder now writes 0 9 from the Task-5 code default).

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
            SET value = jsonb_set(value::jsonb, '{config,schedule}', '"0 9 * * *"')::text
            WHERE key = 'plugin.job.run_dev_diary_post'
              AND value::jsonb #>> '{config,schedule}' = '0 13 * * *'
            """
        )
    logger.info(
        "rebase_dev_diary_cron_local up: rewrote dev_diary schedule 0 13 -> 0 9 "
        "where still at the old UTC default (no-op otherwise)"
    )


async def down(pool) -> None:
    # No-op: reversing to 0 13 would re-break the fire time under the tz-aware
    # scheduler. Same one-way posture as drop_pipeline_tasks_category.
    logger.info("rebase_dev_diary_cron_local down: no-op (refusing to re-introduce the UTC-baked schedule)")
```

- [ ] **Step 5: Run the unit test to verify it passes.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/migrations/test_rebase_dev_diary_cron_local.py -q`
Expected: PASS.

- [ ] **Step 6: Lint + smoke the migration.**

Run: `cd src/cofounder_agent && poetry run python scripts/ci/migrations_lint.py && poetry run python scripts/ci/migrations_smoke.py`
Expected: PASS — the migration applies cleanly to a fresh DB (no-op there) and passes the runner-interface lint.

- [ ] **Step 7: Commit.**

```bash
git add src/cofounder_agent/services/migrations/*rebase_dev_diary_cron_local.py src/cofounder_agent/tests/unit/services/migrations/test_rebase_dev_diary_cron_local.py
git commit -m "$(cat <<'EOF'
fix(db): converge dev_diary cron DB row to 0 9 (guarded)

One-shot guarded UPDATE: rewrites plugin.job.run_dev_diary_post config.schedule
0 13 -> 0 9 only where still the old UTC default, so the tz-aware scheduler
fires it at 9am local. Never clobbers a hand-tuned schedule.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Localize the morning-brief date header

**Files:**

- Modify: `src/cofounder_agent/services/jobs/morning_brief.py` (`run` passes tz; `_format_brief` uses `today_local`)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_morning_brief.py` (add a case)

**Interfaces:**

- Consumes: `services.clock.get_operator_tz`, `services.clock.today_local`.
- Produces: `_format_brief(data, lookback_hours, site_url, max_chars, tz)` (new trailing `tz` param).

- [ ] **Step 1: Write the failing test.** Append to `tests/unit/services/jobs/test_morning_brief.py`:

```python
from datetime import timezone as _tz
from zoneinfo import ZoneInfo
from freezegun import freeze_time
from services.jobs import morning_brief as mb


@freeze_time("2026-07-11T03:00:00Z")  # 11:00 PM ET on 2026-07-10
def test_brief_date_header_uses_operator_local_day():
    data = {
        "published_count": 0, "published_rows": [],
        "awaiting_count": 0, "awaiting_rows": [],
        "alerts_by_severity": {}, "top_alertname_by_severity": {},
        "cost_cloud_usd": 0.0, "cost_cloud_calls": 0, "cost_local_calls": 0,
        "cost_electricity_usd": 0.0,
        "failed_tasks_count": 0, "failed_rows": [],
        "open_prs": [], "brain_probe_failures": 0, "brain_probe_cycles": 0,
    }
    msg = mb._format_brief(data, 24, "", 1800, ZoneInfo("America/New_York"))
    assert "2026-07-10" in msg  # operator-local day, not the 07-11 UTC day
```

- [ ] **Step 2: Run to verify it fails.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_morning_brief.py::test_brief_date_header_uses_operator_local_day -q`
Expected: FAIL — `_format_brief()` takes 4 positional args (no `tz`), and the header shows `2026-07-11`.

- [ ] **Step 3: Thread tz into `run`.** In `morning_brief.py`, in `MorningBriefJob.run`, replace the format call (line ~105):

```python
        # ---- Format (operator-local date header; store-UTC/present-local) ----
        from services.clock import get_operator_tz

        tz = await get_operator_tz(pool)
        message = _format_brief(data, lookback_hours, site_url, max_chars, tz)
```

- [ ] **Step 4: Localize the header in `_format_brief`.** Change the signature and the `today` line (lines 464-468):

```python
def _format_brief(
    data: dict[str, Any], lookback_hours: int, site_url: str, max_chars: int, tz: Any,
) -> str:
    from services.clock import today_local

    today = today_local(tz).strftime("%Y-%m-%d")
    lines: list[str] = [f"\U0001F305 **Morning brief — {today}**", ""]
```

- [ ] **Step 5: Run the new test + the full morning_brief suite.**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_morning_brief.py -q`
Expected: PASS — the new case passes and no existing `_format_brief` caller test breaks (all callers are in `run`, updated in Step 3).

- [ ] **Step 6: Commit.**

```bash
git add src/cofounder_agent/services/jobs/morning_brief.py src/cofounder_agent/tests/unit/services/jobs/test_morning_brief.py
git commit -m "$(cat <<'EOF'
feat(morning-brief): render the date header in the operator timezone

The "what happened while I slept" digest now dates its header by the operator's
local day (store-UTC/present-local), fixing the near-midnight off-by-one.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Documentation

**Files:**

- Create: `docs/architecture/system-timezone.md`
- Modify: `CLAUDE.md` (one-line pointer in the Configuration section)

**Interfaces:** none (docs).

- [ ] **Step 1: Write the architecture doc.**

```markdown
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
```

- [ ] **Step 2: Add the CLAUDE.md pointer.** In `CLAUDE.md`, in the Configuration (`#198`) section, add one line near the `storage_*` / provider-agnostic note:

```markdown
**Time is UTC-stored, operator-local presented.** `app_settings.operator_timezone` (IANA; OSS default `UTC`, operator overlay `America/New_York`) drives cron fire-times and operator-facing timestamps via `services/clock.py`. Storage/logs/metrics stay UTC. See [`docs/architecture/system-timezone.md`](docs/architecture/system-timezone.md).
```

- [ ] **Step 3: Commit.**

```bash
git add docs/architecture/system-timezone.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: document system timezone (store-UTC / present-local)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification (before flipping PR #2247 out of draft)

- [ ] **Full backend unit suite** (migration-squash memory: run the whole suite, not a subset — orphaned integration_db collisions hide behind partial runs).

Run: `cd src/cofounder_agent && poetry run pytest tests/unit -q`
Expected: green (only known pre-existing skips).

- [ ] **Migrations smoke on a fresh DB.**

Run: `cd src/cofounder_agent && poetry run python scripts/ci/migrations_smoke.py`
Expected: PASS.

- [ ] **Lint + types.**

Run: `npm run lint && npm run type:check`
Expected: PASS.

- [ ] **Empirical (post-deploy, needs worker restart):** confirm the morning brief lands in Discord at **7am ET** (not 3am), and spot-check the scheduler next-fire times for `morning_brief` / `findings_daily_digest` / `run_dev_diary_post` are 7am/9am/9am ET via `job_run_state` or APScheduler `next_run_time`.

- [ ] **Admin:** file the OSS tracking issue on `Glad-Labs/poindexter`, mark PR #2247 ready for review.

---

## Notes / deferred (not in this plan)

- **Brain alerts:** no change — `operator_notifier.py` / `alert_dispatcher.py` have no operator-facing _formatted_ timestamp (only the UTC `alerts.log` write and internal dedup/audit fields, which stay UTC).
- **CLI timestamp output:** converts incrementally to `clock` helpers as call sites are touched; not forced in this PR.
- **Console web UI:** browser renders in the viewer's local zone already; pinning it to `operator_timezone` for travel is a follow-up.
- **Grafana dashboards:** optional infra follow-up to set dashboards' default tz to match (a dashboards JSON setting, not code).
- **`morning_brief_hour_local`:** left as a harmless informational mirror (description repointed at `operator_timezone`); not wired to generate the cron.
