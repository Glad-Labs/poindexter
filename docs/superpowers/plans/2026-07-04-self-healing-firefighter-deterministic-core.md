# Self-Healing Firefighter — Deterministic Core (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the brain an autonomous operational-recovery loop that, on a firing alert matching a deterministic `remediation_rules` row, runs a bounded reversible action (restart a container / run the stuck-task cleanup), verifies on the next cycle, and pages only if the alert is still firing — so successful recoveries are silent.

**Architecture:** Extends the brain's existing `alert_dispatcher` poll loop. A new `brain/remediation/` package holds an **action registry** (the shared seam), **rule matching + circuit breaker**, and an **engine** that executes and verifies. Actions wrap primitives the brain already has (`restart_service` → a new `docker_restart_container` helper; `auto_remediate`). All state rides existing tables — `remediation_rules` (new) for policy, `audit_log` for the action/verify trail, `alert_dedup_state` for the "still firing?" signal. No worker dependency and no LLM (that is Plan B).

**Tech Stack:** Python 3.11 (brain image: asyncpg + httpx + urllib + pyyaml only — **no `services/` imports**), asyncpg, PostgreSQL, pytest + pytest-asyncio, Grafana provisioned dashboards.

## Global Constraints

- **Brain image isolation:** brain code MUST NOT import from `services/` / `src/cofounder_agent/`. Resolve `brain_daemon` lazily (mirror `alert_dispatcher._resolve_brain_daemon_module`). (`brain/pyproject.toml`)
- **Config in DB, not code:** every tunable is an `app_settings` key with a default in `services/settings_defaults.py`. Never a literal in code. (`feedback_db_first_config`)
- **App-settings defaults go in `settings_defaults.py`, table DDL in a timestamped migration.** Never seed `app_settings` from a migration. (`feedback_seed_data_in_baseline`)
- **`app_settings.value` is TEXT and NOT NULL:** store bools/ints as strings; `''` is the unset sentinel, never `NULL`. (`feedback_app_settings_value_not_null`)
- **Fail loud, no silent defaults:** an unreadable required setting logs at WARNING; the firefighter fails _safe_ (does nothing, pages as today) rather than acting on a bad read. (`feedback_no_silent_defaults`)
- **Executors never raise into the poll loop:** every executor returns an `ActionResult`; the loop is best-effort and one bad action can't take the dispatcher down.
- **Actions are idempotent, reversible, blast-radius-bounded:** that is the admission criterion for the registry.
- **TDD:** every function gets a failing test first. Brain unit tests live under `src/cofounder_agent/tests/unit/brain/` and run with `poetry run pytest` from `src/cofounder_agent/`.
- **Ships enabled:** `ops_firefighter_enabled` defaults to `true`. The table ships empty, so an enabled firefighter with no rules is a safe no-op until rules are seeded (final step of Task 1).
- **Commit style:** end every commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Work on the current branch; never commit to `main`.
- **Brain deploy:** the brain image **bakes** `brain/`, so applying brain changes requires a rebuild + restart of `poindexter-brain-daemon`, not just a restart. (Out of scope for these tasks — noted for the deploy step.)

---

## File Structure

**New files:**

- `brain/remediation/__init__.py` — package marker.
- `brain/remediation/registry.py` — `ActionResult`, `RemediationContext`, the executors, `ACTION_REGISTRY`, `execute()`.
- `brain/remediation/rules.py` — `load_firefighter_config()`, `match_rule()`, `circuit_breaker_tripped()`, `global_rate_exceeded()`, JSON coercion helper.
- `brain/remediation/engine.py` — `RemediationDecision`, `evaluate_for_dispatch()`, `run_verify_scan()`, `record_action()`, `_write_audit()`, `_alert_still_firing()`.
- `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_remediation_rules.py` — the `remediation_rules` table.
- `src/cofounder_agent/tests/unit/brain/_remediation_fakes.py` — shared `FakePool` test double.
- `src/cofounder_agent/tests/unit/brain/test_remediation_registry.py`
- `src/cofounder_agent/tests/unit/brain/test_remediation_rules.py`
- `src/cofounder_agent/tests/unit/brain/test_remediation_engine.py`
- `src/cofounder_agent/tests/unit/brain/test_alert_dispatcher_remediation.py`

**Modified files:**

- `brain/brain_daemon.py` — add `docker_restart_container()` (factored from `restart_service`).
- `brain/alert_dispatcher.py` — load firefighter config in `_read_dedup_config`; hook `evaluate_for_dispatch` into `_dispatch_one`'s dispatch branch; run `run_verify_scan` once per cycle in `poll_and_dispatch`.
- `src/cofounder_agent/services/settings_defaults.py` — add the `ops_firefighter_*` keys.
- `infrastructure/grafana/dashboards/system-health.json` — add a "Self-Healing / Remediation" row.
- `docs/operations/self-healing.md` and `docs/integrations/webhook_alertmanager_dispatch.md` — document the loop + rule authoring.

---

### Task 1: `remediation_rules` table

**Files:**

- Create: `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_remediation_rules.py`

**Interfaces:**

- Produces: table `remediation_rules(id, alertname, match_regex, action_name, params jsonb, enabled, max_attempts_per_window, window_minutes, verify_after_seconds, description, created_at, updated_at)`.

- [ ] **Step 1: Generate the migration file**

Run: `cd src/cofounder_agent && python scripts/new-migration.py "remediation_rules table for the self-healing firefighter"`

This writes a timestamped stub (e.g. `20260704_171500_remediation_rules.py`) with `up(pool)` / `down(pool)`. Use the real generated filename below.

- [ ] **Step 2: Write the table DDL into `up()` and the drop into `down()`**

```python
"""Migration: remediation_rules — deterministic self-healing firefighter policy.

Declarative alert->action rules the brain's alert_dispatcher consults before
paging. Same data-plane shape as external_taps / qa_gates: enabled rows drive a
handler (the brain/remediation action registry). Ships EMPTY — an enabled
firefighter with no rows is a safe no-op. Operators seed rows per
docs/operations/self-healing.md.

stdlib-only so migrations-smoke applies it without a full app boot.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS remediation_rules (
    id                        SERIAL PRIMARY KEY,
    alertname                 TEXT,
    match_regex               TEXT,
    action_name               TEXT NOT NULL,
    params                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled                   BOOLEAN NOT NULL DEFAULT TRUE,
    max_attempts_per_window   INTEGER,
    window_minutes            INTEGER,
    verify_after_seconds      INTEGER,
    description               TEXT NOT NULL DEFAULT '',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT remediation_rules_match_present
        CHECK (alertname IS NOT NULL OR match_regex IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_remediation_rules_enabled
    ON remediation_rules (enabled) WHERE enabled;
CREATE UNIQUE INDEX IF NOT EXISTS idx_remediation_rules_alertname
    ON remediation_rules (alertname) WHERE alertname IS NOT NULL;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
    logger.info("remediation_rules up: table + indexes created (empty)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS remediation_rules")
    logger.info("remediation_rules down: table dropped")
```

- [ ] **Step 3: Run the migrations smoke test to verify it applies on a fresh DB**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_smoke.py`
Expected: PASS — the new migration applies with no error; output ends with the smoke-test success line.

- [ ] **Step 4: Lint the migration**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_lint.py`
Expected: PASS — no collisions, runner interface present (`up`/`down`).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/*_remediation_rules.py
git commit -m "feat(firefighter): remediation_rules table (empty, ships enabled)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> **Seeding note (done in Task 10 after the engine is live + real alertnames are confirmed):** rows are seeded operationally, not in this migration — query `SELECT DISTINCT alertname FROM alert_events ORDER BY 1` for real names, then insert rules mapping stuck-task / failure-rate alerts → `run_auto_remediate` and confirmed service-down alerts → `restart_container` with the container names from `restart_service._container_map` (`poindexter-worker`, `poindexter-image-gen-server`). Never seed a rule whose action does not actually fix that alert (it would fail verify every cycle and page).

---

### Task 2: app_settings defaults

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Test: `src/cofounder_agent/tests/unit/test_settings_defaults_firefighter.py` (create)

**Interfaces:**

- Produces keys: `ops_firefighter_enabled`, `ops_firefighter_max_attempts_per_window`, `ops_firefighter_window_minutes`, `ops_firefighter_verify_after_seconds`, `ops_firefighter_max_actions_per_hour`, `ops_firefighter_action_allowlist` (all TEXT-valued in `DEFAULTS`).

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/test_settings_defaults_firefighter.py
from services.settings_defaults import DEFAULTS


def test_firefighter_defaults_present_and_typed():
    assert DEFAULTS["ops_firefighter_enabled"] == "true"
    assert DEFAULTS["ops_firefighter_max_attempts_per_window"] == "3"
    assert DEFAULTS["ops_firefighter_window_minutes"] == "60"
    assert DEFAULTS["ops_firefighter_verify_after_seconds"] == "120"
    assert DEFAULTS["ops_firefighter_max_actions_per_hour"] == "10"
    # Empty CSV = "all registered actions allowed" (not NULL — value_not_null rule)
    assert DEFAULTS["ops_firefighter_action_allowlist"] == ""
    # All values are strings (app_settings.value is TEXT)
    for k in [k for k in DEFAULTS if k.startswith("ops_firefighter_")]:
        assert isinstance(DEFAULTS[k], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/test_settings_defaults_firefighter.py -v`
Expected: FAIL with `KeyError: 'ops_firefighter_enabled'`.

- [ ] **Step 3: Add the keys to `DEFAULTS`**

Add a new section inside the `DEFAULTS` dict in `services/settings_defaults.py` (anywhere among the existing entries):

```python
    # ----- Self-healing firefighter (deterministic core, Plan A) -----
    # Master switch. Ships enabled; table is empty so it's a safe no-op
    # until remediation_rules are seeded. Off = pages exactly as today.
    "ops_firefighter_enabled": "true",
    # Circuit breaker: at most N attempts of the same (alert, action) inside
    # the window before the firefighter stops trying and pages.
    "ops_firefighter_max_attempts_per_window": "3",
    "ops_firefighter_window_minutes": "60",
    # Grace period before the verify scan re-checks whether the alert cleared.
    "ops_firefighter_verify_after_seconds": "120",
    # Global backstop across ALL actions, per rolling hour.
    "ops_firefighter_max_actions_per_hour": "10",
    # CSV of enabled action_names; empty = all registered actions allowed.
    # Per-action-type kill switch (e.g. "restart_container" to allow only that).
    "ops_firefighter_action_allowlist": "",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/test_settings_defaults_firefighter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/test_settings_defaults_firefighter.py
git commit -m "feat(firefighter): app_settings defaults for the firefighter loop

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Action registry + shared test double

**Files:**

- Create: `brain/remediation/__init__.py`, `brain/remediation/registry.py`
- Create: `src/cofounder_agent/tests/unit/brain/_remediation_fakes.py`
- Test: `src/cofounder_agent/tests/unit/brain/test_remediation_registry.py`

**Interfaces:**

- Produces:
  - `ActionResult(status: str, detail: str = "", latency_ms: int = 0)` — `status ∈ {"ok","failed","skipped"}`.
  - `RemediationContext(pool, alert: dict, logger)`.
  - `Executor = Callable[[dict, RemediationContext], Awaitable[ActionResult]]`.
  - `ACTION_REGISTRY: dict[str, Executor]`.
  - `async execute(action_name: str, params: dict, ctx: RemediationContext) -> ActionResult` — unknown name → `skipped`; executor exception → `failed` (never raises).
  - `FakePool` test double with `.executed`, `.set_fetch(...)`, `.set_fetchval(...)`, `.set_fetchrow(...)`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/brain/test_remediation_registry.py
import pytest

from brain.remediation.registry import (
    ACTION_REGISTRY,
    ActionResult,
    RemediationContext,
    execute,
)


def _ctx():
    import logging
    return RemediationContext(pool=object(), alert={}, logger=logging.getLogger("t"))


@pytest.mark.asyncio
async def test_unknown_action_is_skipped_not_raised():
    result = await execute("no_such_action", {}, _ctx())
    assert result.status == "skipped"
    assert "no_such_action" in result.detail


@pytest.mark.asyncio
async def test_executor_exception_becomes_failed_result():
    async def boom(params, ctx):
        raise RuntimeError("kaboom")

    ACTION_REGISTRY["_test_boom"] = boom
    try:
        result = await execute("_test_boom", {}, _ctx())
    finally:
        ACTION_REGISTRY.pop("_test_boom", None)
    assert result.status == "failed"
    assert "kaboom" in result.detail


@pytest.mark.asyncio
async def test_known_action_dispatches_with_params():
    seen = {}

    async def rec(params, ctx):
        seen.update(params)
        return ActionResult(status="ok", detail="did it")

    ACTION_REGISTRY["_test_rec"] = rec
    try:
        result = await execute("_test_rec", {"k": "v"}, _ctx())
    finally:
        ACTION_REGISTRY.pop("_test_rec", None)
    assert result.status == "ok"
    assert seen == {"k": "v"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_remediation_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'brain.remediation'`.

- [ ] **Step 3: Create the package + registry**

```python
# brain/remediation/__init__.py
"""Self-healing firefighter — deterministic remediation package (Plan A)."""
```

```python
# brain/remediation/registry.py
"""Action registry — the single seam through which every remediation action
runs. Executors wrap primitives the brain already owns. They MUST be
idempotent, reversible, and blast-radius-bounded, and MUST NOT raise into the
caller — return an ActionResult(status="failed", ...) instead.

Brain-image isolation: this module resolves brain_daemon lazily (flat OR
package path) exactly like alert_dispatcher, and imports nothing from services/.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ActionResult:
    status: str  # "ok" | "failed" | "skipped"
    detail: str = ""
    latency_ms: int = 0


@dataclass
class RemediationContext:
    pool: Any
    alert: dict[str, Any]
    logger: Any


Executor = Callable[[dict[str, Any], RemediationContext], Awaitable[ActionResult]]


def _resolve_brain_daemon() -> Any | None:
    """Find brain.brain_daemon across the flat / package import paths.

    Mirrors alert_dispatcher._resolve_brain_daemon_module so the registry
    never hard-imports the daemon at module load (avoids import cycles + keeps
    the module importable in unit tests that don't load the daemon).
    """
    mod = sys.modules.get("brain_daemon") or sys.modules.get("brain.brain_daemon")
    if mod is not None:
        return mod
    try:
        import brain_daemon as mod  # type: ignore
        return mod
    except ImportError:
        try:
            from brain import brain_daemon as mod  # type: ignore
            return mod
        except ImportError:
            return None


async def _restart_container(params: dict[str, Any], ctx: RemediationContext) -> ActionResult:
    """Docker-restart a named container via brain_daemon.docker_restart_container."""
    container = str(params.get("container") or "").strip()
    if not container:
        return ActionResult(status="skipped", detail="restart_container: no 'container' param")
    mod = _resolve_brain_daemon()
    if mod is None or not hasattr(mod, "docker_restart_container"):
        return ActionResult(status="failed", detail="brain_daemon.docker_restart_container unavailable")
    started = time.monotonic()
    ok, detail = await mod.docker_restart_container(container, pool=ctx.pool)
    latency = int((time.monotonic() - started) * 1000)
    return ActionResult(status="ok" if ok else "failed", detail=detail, latency_ms=latency)


async def _run_auto_remediate(params: dict[str, Any], ctx: RemediationContext) -> ActionResult:
    """Run the brain's stuck-task / stale-approval cleanup sweep."""
    mod = _resolve_brain_daemon()
    if mod is None or not hasattr(mod, "auto_remediate"):
        return ActionResult(status="failed", detail="brain_daemon.auto_remediate unavailable")
    started = time.monotonic()
    try:
        await mod.auto_remediate(ctx.pool)
    except Exception as e:  # noqa: BLE001 — executors never raise into the loop
        return ActionResult(
            status="failed",
            detail=f"auto_remediate raised: {e}"[:400],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return ActionResult(
        status="ok", detail="auto_remediate completed",
        latency_ms=int((time.monotonic() - started) * 1000),
    )


ACTION_REGISTRY: dict[str, Executor] = {
    "restart_container": _restart_container,
    "run_auto_remediate": _run_auto_remediate,
}


async def execute(action_name: str, params: dict[str, Any], ctx: RemediationContext) -> ActionResult:
    """Run a registered action. Unknown name -> skipped. Executor blow-up -> failed.

    Never raises: the poll loop is best-effort.
    """
    executor = ACTION_REGISTRY.get(action_name)
    if executor is None:
        return ActionResult(status="skipped", detail=f"unknown action: {action_name}")
    try:
        return await executor(params or {}, ctx)
    except Exception as e:  # noqa: BLE001
        return ActionResult(status="failed", detail=f"executor raised: {e}"[:400])
```

- [ ] **Step 4: Create the shared FakePool test double**

```python
# src/cofounder_agent/tests/unit/brain/_remediation_fakes.py
"""Shared asyncpg-pool test double for the firefighter unit tests.

Records executes for assertion, and lets each test register canned results
for fetch / fetchval / fetchrow keyed by a substring of the SQL so a single
pool can serve several distinct queries in call order.
"""
from __future__ import annotations

from typing import Any, Callable


class FakePool:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._fetch: Callable[[str, tuple], list] | None = None
        self._fetchval: Callable[[str, tuple], Any] | None = None
        self._fetchrow: Callable[[str, tuple], Any] | None = None

    def set_fetch(self, fn: Callable[[str, tuple], list]) -> None:
        self._fetch = fn

    def set_fetchval(self, fn: Callable[[str, tuple], Any]) -> None:
        self._fetchval = fn

    def set_fetchrow(self, fn: Callable[[str, tuple], Any]) -> None:
        self._fetchrow = fn

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list:
        return list(self._fetch(sql, args)) if self._fetch else []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return self._fetchval(sql, args) if self._fetchval else None

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return self._fetchrow(sql, args) if self._fetchrow else None
```

- [ ] **Step 5: Run tests + commit**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_remediation_registry.py -v`
Expected: PASS (3 tests).

```bash
git add brain/remediation/__init__.py brain/remediation/registry.py \
  src/cofounder_agent/tests/unit/brain/_remediation_fakes.py \
  src/cofounder_agent/tests/unit/brain/test_remediation_registry.py
git commit -m "feat(firefighter): action registry seam + executors

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `docker_restart_container` brain primitive

**Files:**

- Modify: `brain/brain_daemon.py` (add function near `restart_service`, ~line 1594)
- Test: `src/cofounder_agent/tests/unit/brain/test_docker_restart_container.py` (create)

**Interfaces:**

- Consumes: nothing new.
- Produces: `async def docker_restart_container(container: str, *, pool=None) -> tuple[bool, str]` — inspect-then-restart an arbitrary container; returns `(ok, detail)`. Does **not** call `notify()` (silent on success; the engine records to `audit_log`).

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/brain/test_docker_restart_container.py
import subprocess
import types

import pytest

import brain.brain_daemon as bd


class _CP:
    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


@pytest.mark.asyncio
async def test_restart_ok(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if args[1] == "inspect":
            return _CP(0)
        return _CP(0)  # docker restart succeeds

    monkeypatch.setattr(bd.subprocess, "run", fake_run)
    ok, detail = await bd.docker_restart_container("poindexter-worker")
    assert ok is True
    assert "poindexter-worker" in detail
    assert ["docker", "restart", "poindexter-worker"] in calls


@pytest.mark.asyncio
async def test_restart_missing_container_is_not_ok(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)

    def fake_run(args, **kw):
        return _CP(1, stderr="No such object")  # inspect fails

    monkeypatch.setattr(bd.subprocess, "run", fake_run)
    ok, detail = await bd.docker_restart_container("ghost")
    assert ok is False
    assert "not found" in detail


@pytest.mark.asyncio
async def test_not_docker_returns_false(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", False, raising=False)
    ok, detail = await bd.docker_restart_container("poindexter-worker")
    assert ok is False
    assert "docker" in detail.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_docker_restart_container.py -v`
Expected: FAIL with `AttributeError: module 'brain.brain_daemon' has no attribute 'docker_restart_container'`.

- [ ] **Step 3: Add the function to `brain/brain_daemon.py`**

Insert immediately above `async def restart_service(` (~line 1594):

```python
async def docker_restart_container(container: str, *, pool=None) -> tuple[bool, str]:
    """Docker-restart a named container. Inspect-then-restart to avoid racing a
    mid-recreate window (the same guard restart_service uses). Returns
    (ok, detail). Silent on success — the firefighter engine records the
    outcome to audit_log; this helper does NOT page.

    Distinct from restart_service (which maps a handful of *logical* names to
    containers and notifies): the firefighter needs to restart an *arbitrary*
    container named in a remediation_rules row.
    """
    del pool  # reserved for future settings-driven behavior; unused today
    if not IS_DOCKER:
        return (False, "not running in docker; no container-restart path")
    try:
        inspect = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
            capture_output=True, text=True, timeout=10,
        )
        if inspect.returncode != 0:
            return (False, f"container {container} not found (likely mid-recreate)")
        result = subprocess.run(
            ["docker", "restart", container],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("[BRAIN] firefighter docker-restarted container %s", container)
            return (True, f"restarted {container}")
        return (False, f"docker restart failed for {container}: {result.stderr[:200]}")
    except FileNotFoundError:
        return (False, "docker CLI not available in brain container")
    except Exception as e:  # noqa: BLE001
        return (False, f"docker restart error for {container}: {e}"[:200])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_docker_restart_container.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add brain/brain_daemon.py src/cofounder_agent/tests/unit/brain/test_docker_restart_container.py
git commit -m "feat(firefighter): docker_restart_container brain primitive

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Rule matching + circuit breaker + rate cap

**Files:**

- Create: `brain/remediation/rules.py`
- Test: `src/cofounder_agent/tests/unit/brain/test_remediation_rules.py`

**Interfaces:**

- Consumes: `FakePool` (Task 3).
- Produces:
  - `async load_firefighter_config(pool) -> dict` — keys: `enabled: bool`, `max_attempts_per_window: int`, `window_minutes: int`, `verify_after_seconds: int`, `max_actions_per_hour: int`, `action_allowlist: list[str]`.
  - `async match_rule(pool, *, alertname: str, fingerprint: str) -> dict | None` — normalized rule dict with keys `id, alertname, match_regex, action_name, params(dict), max_attempts_per_window, window_minutes, verify_after_seconds`.
  - `async circuit_breaker_tripped(pool, *, fingerprint, action_name, max_attempts, window_minutes) -> bool`.
  - `async global_rate_exceeded(pool, *, max_actions_per_hour) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/brain/test_remediation_rules.py
import pytest

from brain.remediation import rules as R
from tests.unit.brain._remediation_fakes import FakePool


@pytest.mark.asyncio
async def test_match_rule_exact_alertname():
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [
        {"id": 1, "alertname": "WorkerDown", "match_regex": None,
         "action_name": "restart_container", "params": '{"container": "poindexter-worker"}',
         "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None},
    ])
    rule = await R.match_rule(pool, alertname="WorkerDown", fingerprint="fp1")
    assert rule is not None
    assert rule["action_name"] == "restart_container"
    assert rule["params"] == {"container": "poindexter-worker"}  # JSON string coerced to dict


@pytest.mark.asyncio
async def test_match_rule_regex_over_fingerprint():
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [
        {"id": 2, "alertname": None, "match_regex": "topic.batch.stuck",
         "action_name": "run_auto_remediate", "params": {},
         "max_attempts_per_window": 2, "window_minutes": 30, "verify_after_seconds": 90},
    ])
    rule = await R.match_rule(pool, alertname="Whatever", fingerprint="topic_batch_stuck:glad-labs")
    assert rule is not None
    assert rule["action_name"] == "run_auto_remediate"
    assert rule["max_attempts_per_window"] == 2


@pytest.mark.asyncio
async def test_match_rule_none_when_no_match():
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [])
    assert await R.match_rule(pool, alertname="X", fingerprint="y") is None


@pytest.mark.asyncio
async def test_circuit_breaker_trips_at_threshold():
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: 3)  # 3 prior attempts
    assert await R.circuit_breaker_tripped(
        pool, fingerprint="fp", action_name="restart_container",
        max_attempts=3, window_minutes=60,
    ) is True


@pytest.mark.asyncio
async def test_circuit_breaker_open_below_threshold():
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: 1)
    assert await R.circuit_breaker_tripped(
        pool, fingerprint="fp", action_name="restart_container",
        max_attempts=3, window_minutes=60,
    ) is False


@pytest.mark.asyncio
async def test_global_rate_cap():
    pool = FakePool()
    pool.set_fetchval(lambda sql, args: 10)
    assert await R.global_rate_exceeded(pool, max_actions_per_hour=10) is True
    pool.set_fetchval(lambda sql, args: 4)
    assert await R.global_rate_exceeded(pool, max_actions_per_hour=10) is False


@pytest.mark.asyncio
async def test_load_config_parses_types():
    pool = FakePool()
    store = {
        "ops_firefighter_enabled": "true",
        "ops_firefighter_max_attempts_per_window": "3",
        "ops_firefighter_window_minutes": "60",
        "ops_firefighter_verify_after_seconds": "120",
        "ops_firefighter_max_actions_per_hour": "10",
        "ops_firefighter_action_allowlist": "restart_container, run_auto_remediate",
    }
    pool.set_fetchval(lambda sql, args: store.get(args[0]))
    cfg = await R.load_firefighter_config(pool)
    assert cfg["enabled"] is True
    assert cfg["max_attempts_per_window"] == 3
    assert cfg["action_allowlist"] == ["restart_container", "run_auto_remediate"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_remediation_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'brain.remediation.rules'`.

- [ ] **Step 3: Implement `brain/remediation/rules.py`**

```python
# brain/remediation/rules.py
"""Deterministic rule matching + circuit breaker + rate cap for the firefighter.

All reads are non-secret app_settings / plain tables, so we use pool.fetchval
directly (no secret_reader needed). Every helper is best-effort: a DB error
returns the safe answer (no match / breaker-open-but-caller-still-gated).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("brain.remediation.rules")


def _coerce_json(value: Any) -> dict[str, Any]:
    """JSONB may arrive as a dict (codec set) or a str (default). Normalise."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _normalize_rule(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "alertname": row.get("alertname"),
        "match_regex": row.get("match_regex"),
        "action_name": row.get("action_name"),
        "params": _coerce_json(row.get("params")),
        "max_attempts_per_window": row.get("max_attempts_per_window"),
        "window_minutes": row.get("window_minutes"),
        "verify_after_seconds": row.get("verify_after_seconds"),
    }


async def _read_str(pool: Any, key: str, default: str) -> str:
    try:
        value = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[firefighter] read %s failed: %s — default", key, e)
        return default
    return default if value is None else str(value)


async def _read_int(pool: Any, key: str, default: int) -> int:
    raw = await _read_str(pool, key, str(default))
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return default


async def load_firefighter_config(pool: Any) -> dict[str, Any]:
    """Snapshot the firefighter knobs once per cycle."""
    enabled_raw = await _read_str(pool, "ops_firefighter_enabled", "true")
    allowlist_raw = await _read_str(pool, "ops_firefighter_action_allowlist", "")
    allowlist = [p.strip() for p in allowlist_raw.split(",") if p.strip()]
    return {
        "enabled": enabled_raw.strip().lower() in ("true", "1", "yes", "on"),
        "max_attempts_per_window": await _read_int(pool, "ops_firefighter_max_attempts_per_window", 3),
        "window_minutes": await _read_int(pool, "ops_firefighter_window_minutes", 60),
        "verify_after_seconds": await _read_int(pool, "ops_firefighter_verify_after_seconds", 120),
        "max_actions_per_hour": await _read_int(pool, "ops_firefighter_max_actions_per_hour", 10),
        "action_allowlist": allowlist,
    }


async def match_rule(pool: Any, *, alertname: str, fingerprint: str) -> dict[str, Any] | None:
    """First enabled rule whose alertname matches exactly, else whose regex
    matches the alertname or fingerprint. None if nothing matches."""
    try:
        rows = await pool.fetch(
            """
            SELECT id, alertname, match_regex, action_name, params,
                   max_attempts_per_window, window_minutes, verify_after_seconds
            FROM remediation_rules
            WHERE enabled = TRUE
            ORDER BY id ASC
            """
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[firefighter] remediation_rules fetch failed: %s", e)
        return None
    for r in rows:
        rd = dict(r)
        if rd.get("alertname") and rd["alertname"] == alertname:
            return _normalize_rule(rd)
    for r in rows:
        rd = dict(r)
        rx = rd.get("match_regex")
        if not rx:
            continue
        try:
            if re.search(rx, alertname or "") or (fingerprint and re.search(rx, fingerprint)):
                return _normalize_rule(rd)
        except re.error as e:
            logger.warning("[firefighter] bad match_regex on rule %s: %s", rd.get("id"), e)
            continue
    return None


async def circuit_breaker_tripped(
    pool: Any, *, fingerprint: str, action_name: str,
    max_attempts: int, window_minutes: int,
) -> bool:
    """True when >= max_attempts of this (fingerprint, action) ran in-window."""
    if max_attempts <= 0 or window_minutes <= 0:
        return False
    try:
        cnt = await pool.fetchval(
            """
            SELECT count(*) FROM audit_log
            WHERE event_type = 'remediation_action'
              AND details->>'fingerprint' = $1
              AND details->>'action_name' = $2
              AND timestamp >= now() - make_interval(mins => $3)
            """,
            fingerprint, action_name, window_minutes,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[firefighter] circuit-breaker count failed: %s — allowing", e)
        return False
    return int(cnt or 0) >= max_attempts


async def global_rate_exceeded(pool: Any, *, max_actions_per_hour: int) -> bool:
    """True when >= max_actions_per_hour remediation_action rows in the last hour."""
    if max_actions_per_hour <= 0:
        return False
    try:
        cnt = await pool.fetchval(
            """
            SELECT count(*) FROM audit_log
            WHERE event_type = 'remediation_action'
              AND timestamp >= now() - interval '1 hour'
            """
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[firefighter] global-rate count failed: %s — allowing", e)
        return False
    return int(cnt or 0) >= max_actions_per_hour
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_remediation_rules.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add brain/remediation/rules.py src/cofounder_agent/tests/unit/brain/test_remediation_rules.py
git commit -m "feat(firefighter): rule matching + circuit breaker + rate cap

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Engine — evaluate & act on the dispatch branch

**Files:**

- Create: `brain/remediation/engine.py`
- Test: `src/cofounder_agent/tests/unit/brain/test_remediation_engine.py`

**Interfaces:**

- Consumes: `registry.execute`, `registry.ActionResult`, `registry.RemediationContext`; `rules.match_rule`, `rules.circuit_breaker_tripped`, `rules.global_rate_exceeded`; `FakePool`.
- Produces:
  - `RemediationDecision(acted: bool, action_name: str|None, params: dict, source: str|None, run_id: str|None, result: ActionResult|None, reason: str)`.
  - `async _write_audit(pool, *, event_type, source, severity, details: dict, task_id=None) -> None` — inserts one `audit_log` row (`event_type, source, task_id, details::jsonb, severity`).
  - `async evaluate_for_dispatch(pool, *, alert: dict, fingerprint: str, config: dict, logger) -> RemediationDecision`. `acted=True` ⇒ an action ran OK and the caller must **hold the page**; `acted=False` ⇒ caller pages as usual.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/brain/test_remediation_engine.py
import json
import logging

import pytest

from brain.remediation import engine as E
from brain.remediation import rules as R
from brain.remediation.registry import ActionResult
from tests.unit.brain._remediation_fakes import FakePool

LOG = logging.getLogger("t")
CFG = {
    "enabled": True, "max_attempts_per_window": 3, "window_minutes": 60,
    "verify_after_seconds": 120, "max_actions_per_hour": 10, "action_allowlist": [],
}
ALERT = {"labels": {"alertname": "WorkerDown", "severity": "critical"}, "annotations": {}}


@pytest.mark.asyncio
async def test_no_rule_means_not_acted(monkeypatch):
    monkeypatch.setattr(R, "match_rule", _acoro(None))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is False
    assert d.reason == "no rule"


@pytest.mark.asyncio
async def test_disabled_short_circuits(monkeypatch):
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config={**CFG, "enabled": False}, logger=LOG)
    assert d.acted is False
    assert d.reason == "disabled"


@pytest.mark.asyncio
async def test_rule_ok_action_holds_page_and_writes_pending_audit(monkeypatch):
    rule = {"id": 7, "action_name": "restart_container", "params": {"container": "poindexter-worker"},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="ok", detail="restarted", latency_ms=42)))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is True
    assert d.action_name == "restart_container"
    assert d.run_id
    # One remediation_action row written, no verify row yet (pending)
    inserts = [e for e in pool.executed if "audit_log" in e[0]]
    assert len(inserts) == 1
    details = json.loads(inserts[0][1][3])
    assert details["fingerprint"] == "fp"
    assert details["action_name"] == "restart_container"
    assert details["execution"]["status"] == "ok"
    assert details["verify_after_seconds"] == 120


@pytest.mark.asyncio
async def test_failed_action_pages_now_and_records_terminal_verify(monkeypatch):
    rule = {"id": 7, "action_name": "restart_container", "params": {"container": "ghost"},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(False))
    monkeypatch.setattr(R, "global_rate_exceeded", _acoro(False))
    monkeypatch.setattr(E, "execute", _acoro(ActionResult(status="failed", detail="not found")))
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is False  # page now
    # Both an action row AND a terminal verify row (so the verify scan skips it)
    events = [json.loads(e[1][3]) for e in pool.executed if "audit_log" in e[0]]
    kinds = [e for e in pool.executed if "audit_log" in e[0]]
    assert len(kinds) == 2
    assert any(ev.get("result") == "action_failed" for ev in events)


@pytest.mark.asyncio
async def test_breaker_tripped_pages_no_execute(monkeypatch):
    rule = {"id": 7, "action_name": "restart_container", "params": {},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    monkeypatch.setattr(R, "circuit_breaker_tripped", _acoro(True))
    called = {"n": 0}
    async def _exec(*a, **k):
        called["n"] += 1
        return ActionResult(status="ok")
    monkeypatch.setattr(E, "execute", _exec)
    pool = FakePool()
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=CFG, logger=LOG)
    assert d.acted is False
    assert "breaker" in d.reason
    assert called["n"] == 0  # never executed


@pytest.mark.asyncio
async def test_action_not_in_allowlist_pages(monkeypatch):
    rule = {"id": 7, "action_name": "restart_container", "params": {},
            "max_attempts_per_window": None, "window_minutes": None, "verify_after_seconds": None}
    monkeypatch.setattr(R, "match_rule", _acoro(rule))
    pool = FakePool()
    cfg = {**CFG, "action_allowlist": ["run_auto_remediate"]}
    d = await E.evaluate_for_dispatch(pool, alert=ALERT, fingerprint="fp", config=cfg, logger=LOG)
    assert d.acted is False
    assert "allowlist" in d.reason


def _acoro(value):
    async def _f(*a, **k):
        return value
    return _f
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_remediation_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'brain.remediation.engine'`.

- [ ] **Step 3: Implement the engine (evaluate half)**

```python
# brain/remediation/engine.py
"""Firefighter engine — decide + act + record. Verify lives in run_verify_scan
(added in Task 7). Brain-side only; writes audit_log directly (emit_finding is
worker-side and unavailable here).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from brain.remediation import rules as R
from brain.remediation.registry import ActionResult, RemediationContext, execute


@dataclass
class RemediationDecision:
    acted: bool
    action_name: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    source: str | None = None  # "rule" (Plan A); "llm" (Plan B)
    run_id: str | None = None
    result: ActionResult | None = None
    reason: str = ""


async def _write_audit(
    pool: Any, *, event_type: str, source: str, severity: str,
    details: dict[str, Any], task_id: str | None = None,
) -> None:
    """Insert one audit_log row. Matches services.audit_log.AuditLogger.log shape."""
    await pool.execute(
        "INSERT INTO audit_log (event_type, source, task_id, details, severity) "
        "VALUES ($1, $2, $3, $4::jsonb, $5)",
        event_type, source, task_id, json.dumps(details, default=str), severity,
    )


async def evaluate_for_dispatch(
    pool: Any, *, alert: dict[str, Any], fingerprint: str,
    config: dict[str, Any], logger: Any,
) -> RemediationDecision:
    """Deterministic rule path, evaluated when an alert is about to be paged.

    acted=True  -> an action ran OK; the dispatcher must HOLD the page and let
                   the verify scan resolve/escalate it later.
    acted=False -> page as usual (no rule, disabled, breaker/rate tripped, or
                   the action failed to run so waiting to verify is pointless).
    """
    if not config.get("enabled"):
        return RemediationDecision(acted=False, reason="disabled")

    alertname = (alert.get("labels", {}).get("alertname") or "").strip()
    rule = await R.match_rule(pool, alertname=alertname, fingerprint=fingerprint)
    if rule is None:
        return RemediationDecision(acted=False, reason="no rule")

    action_name = rule["action_name"]
    allowlist = config.get("action_allowlist") or []
    if allowlist and action_name not in allowlist:
        return RemediationDecision(acted=False, reason=f"action {action_name} not in allowlist")

    max_attempts = rule["max_attempts_per_window"] or config["max_attempts_per_window"]
    window_minutes = rule["window_minutes"] or config["window_minutes"]
    if await R.circuit_breaker_tripped(
        pool, fingerprint=fingerprint, action_name=action_name,
        max_attempts=max_attempts, window_minutes=window_minutes,
    ):
        return RemediationDecision(acted=False, action_name=action_name, reason="circuit breaker tripped")

    if await R.global_rate_exceeded(pool, max_actions_per_hour=config["max_actions_per_hour"]):
        return RemediationDecision(acted=False, action_name=action_name, reason="global rate cap")

    run_id = str(uuid.uuid4())
    verify_after = rule["verify_after_seconds"] or config["verify_after_seconds"]
    ctx = RemediationContext(pool=pool, alert=alert, logger=logger)
    result = await execute(action_name, rule["params"], ctx)

    source_label = f"firefighter:{alertname or 'alert'}"
    await _write_audit(
        pool, event_type="remediation_action", source=source_label, severity="info",
        details={
            "remediation_run_id": run_id, "fingerprint": fingerprint, "alertname": alertname,
            "action_name": action_name, "params": rule["params"], "source": "rule",
            "rule_id": rule["id"], "verify_after_seconds": verify_after,
            "execution": {"status": result.status, "detail": result.detail, "latency_ms": result.latency_ms},
        },
    )

    if result.status == "ok":
        logger.info(
            "[firefighter] acted alert=%s action=%s run=%s — holding page for verify",
            alertname, action_name, run_id[:8],
        )
        return RemediationDecision(
            acted=True, action_name=action_name, params=rule["params"],
            source="rule", run_id=run_id, result=result, reason="rule matched",
        )

    # Action did not run OK -> nothing to verify; write a terminal verify row so
    # the verify scan skips it, and page now.
    await _write_audit(
        pool, event_type="remediation_verify", source=source_label, severity="warning",
        details={
            "remediation_run_id": run_id, "result": "action_failed",
            "checked_at": datetime.now(UTC).isoformat(),
            "detail": result.detail,
        },
    )
    return RemediationDecision(
        acted=False, action_name=action_name, source="rule", run_id=run_id,
        result=result, reason=f"action {result.status}: {result.detail}"[:200],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_remediation_engine.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add brain/remediation/engine.py src/cofounder_agent/tests/unit/brain/test_remediation_engine.py
git commit -m "feat(firefighter): engine evaluate_for_dispatch (rule path)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Engine — verify scan

**Files:**

- Modify: `brain/remediation/engine.py`
- Test: `src/cofounder_agent/tests/unit/brain/test_remediation_verify.py` (create)

**Interfaces:**

- Consumes: `_write_audit`; `alert_dedup_state.last_seen_at`.
- Produces:
  - `async _alert_still_firing(pool, *, fingerprint: str, since: datetime) -> bool` — True iff `alert_dedup_state.last_seen_at > since` (the alert re-fired after we acted).
  - `async run_verify_scan(pool, *, config: dict, logger, notify_fn=None) -> dict` — resolves pending `remediation_action` rows past their grace period; returns `{"verified": N, "resolved": N, "still_firing": N}`. On still-firing, pages via `notify_fn(message, critical=False)`.

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/brain/test_remediation_verify.py
import json
from datetime import UTC, datetime, timedelta

import pytest

from brain.remediation import engine as E
from tests.unit.brain._remediation_fakes import FakePool

import logging
LOG = logging.getLogger("t")
CFG = {"verify_after_seconds": 120}


def _pending_row(run_id, fingerprint, acted_at, verify_after=120):
    return {
        "id": 1, "timestamp": acted_at,
        "details": json.dumps({
            "remediation_run_id": run_id, "fingerprint": fingerprint,
            "alertname": "WorkerDown", "action_name": "restart_container",
            "verify_after_seconds": verify_after, "source": "rule",
        }),
    }


@pytest.mark.asyncio
async def test_resolved_writes_verify_and_does_not_page():
    acted = datetime.now(UTC) - timedelta(seconds=200)  # past grace
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r1", "fp1", acted)])
    # dedup_state.last_seen_at BEFORE we acted -> not re-fired -> resolved
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": acted - timedelta(seconds=5)})
    paged = []
    async def notify(msg, critical=False):
        paged.append(msg)
    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)
    assert out["resolved"] == 1 and out["still_firing"] == 0
    assert paged == []
    verify_rows = [json.loads(e[1][3]) for e in pool.executed if "audit_log" in e[0]]
    assert any(v.get("result") == "resolved" for v in verify_rows)


@pytest.mark.asyncio
async def test_still_firing_pages_and_writes_verify():
    acted = datetime.now(UTC) - timedelta(seconds=200)
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r2", "fp2", acted)])
    # last_seen_at AFTER we acted -> re-fired -> still firing
    pool.set_fetchrow(lambda sql, args: {"last_seen_at": acted + timedelta(seconds=30)})
    paged = []
    async def notify(msg, critical=False):
        paged.append(msg)
    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=notify)
    assert out["still_firing"] == 1 and out["resolved"] == 0
    assert len(paged) == 1 and "still firing" in paged[0]
    verify_rows = [json.loads(e[1][3]) for e in pool.executed if "audit_log" in e[0]]
    assert any(v.get("result") == "still_firing" for v in verify_rows)


@pytest.mark.asyncio
async def test_not_yet_due_is_skipped():
    acted = datetime.now(UTC) - timedelta(seconds=10)  # inside grace
    pool = FakePool()
    pool.set_fetch(lambda sql, args: [_pending_row("r3", "fp3", acted)])
    out = await E.run_verify_scan(pool, config=CFG, logger=LOG, notify_fn=None)
    assert out == {"verified": 0, "resolved": 0, "still_firing": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_remediation_verify.py -v`
Expected: FAIL with `AttributeError: module 'brain.remediation.engine' has no attribute 'run_verify_scan'`.

- [ ] **Step 3: Append the verify functions to `brain/remediation/engine.py`**

```python
def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _coerce_details(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


async def _alert_still_firing(pool: Any, *, fingerprint: str, since: datetime) -> bool:
    """True iff the alert re-fired after we acted.

    The dispatcher bumps alert_dedup_state.last_seen_at on every (suppressed)
    repeat, keyed by the SAME fingerprint the engine stored. So last_seen_at
    advancing past `since` means the problem is still live; no advance means it
    stopped firing (resolved). No dedup row -> treat as resolved (fail toward
    silence; the next real fire re-pages through the normal path).
    """
    try:
        row = await pool.fetchrow(
            "SELECT last_seen_at FROM alert_dedup_state WHERE fingerprint = $1",
            fingerprint,
        )
    except Exception:  # noqa: BLE001
        # Can't tell -> assume still firing so we page rather than silently drop.
        return True
    if not row:
        return False
    last_seen = _coerce_dt(row.get("last_seen_at") if isinstance(row, dict) else row["last_seen_at"])
    if last_seen is None:
        return False
    return last_seen > since


_VERIFY_PENDING_SQL = """
SELECT a.id, a.timestamp, a.details
FROM audit_log a
WHERE a.event_type = 'remediation_action'
  AND NOT EXISTS (
      SELECT 1 FROM audit_log v
      WHERE v.event_type = 'remediation_verify'
        AND v.details->>'remediation_run_id' = a.details->>'remediation_run_id'
  )
ORDER BY a.id ASC
LIMIT 50
"""


async def run_verify_scan(
    pool: Any, *, config: dict[str, Any], logger: Any, notify_fn: Any = None,
) -> dict[str, int]:
    """Resolve pending remediation actions past their grace period.

    Pending = a remediation_action row with no remediation_verify sharing its
    run_id. For each past its verify_after_seconds: resolved -> silent; still
    firing -> page + write the verify row (so the breaker counts it next time).
    Best-effort: never raises into the poll loop.
    """
    summary = {"verified": 0, "resolved": 0, "still_firing": 0}
    try:
        rows = await pool.fetch(_VERIFY_PENDING_SQL)
    except Exception as e:  # noqa: BLE001
        logger.warning("[firefighter] verify scan poll failed: %s", e)
        return summary

    now = datetime.now(UTC)
    for r in rows:
        rd = dict(r)
        details = _coerce_details(rd.get("details"))
        run_id = details.get("remediation_run_id")
        acted_at = _coerce_dt(rd.get("timestamp")) or now
        verify_after = int(details.get("verify_after_seconds") or config["verify_after_seconds"])
        if (now - acted_at).total_seconds() < verify_after:
            continue  # not yet due
        fingerprint = details.get("fingerprint") or ""
        alertname = details.get("alertname") or "firefighter"
        action = details.get("action_name") or "?"
        summary["verified"] += 1
        try:
            still = await _alert_still_firing(pool, fingerprint=fingerprint, since=acted_at)
        except Exception as e:  # noqa: BLE001
            logger.warning("[firefighter] still-firing check failed for run=%s: %s", run_id, e)
            still = True
        if still:
            summary["still_firing"] += 1
            await _write_audit(
                pool, event_type="remediation_verify", source=alertname, severity="warning",
                details={"remediation_run_id": run_id, "result": "still_firing", "checked_at": now.isoformat()},
            )
            msg = (
                f"[FIREFIGHTER] auto-remediation did not resolve {alertname}: "
                f"attempted {action}, still firing after {verify_after}s"
            )
            if notify_fn is not None:
                try:
                    await notify_fn(msg, critical=False)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[firefighter] verify page failed for run=%s: %s", run_id, e)
        else:
            summary["resolved"] += 1
            await _write_audit(
                pool, event_type="remediation_verify", source=alertname, severity="info",
                details={"remediation_run_id": run_id, "result": "resolved", "checked_at": now.isoformat()},
            )
            logger.info("[firefighter] resolved alert=%s action=%s run=%s (silent)", alertname, action, str(run_id)[:8])
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_remediation_verify.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add brain/remediation/engine.py src/cofounder_agent/tests/unit/brain/test_remediation_verify.py
git commit -m "feat(firefighter): verify scan (resolve silent, page on still-firing)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Wire the engine into the alert dispatcher

**Files:**

- Modify: `brain/alert_dispatcher.py` — `_read_dedup_config` (load firefighter config), `_dispatch_one` (hook on the dispatch branch), `poll_and_dispatch` (verify scan each cycle).
- Test: `src/cofounder_agent/tests/unit/brain/test_alert_dispatcher_remediation.py` (create)

**Interfaces:**

- Consumes: `engine.evaluate_for_dispatch`, `engine.run_verify_scan`, `rules.load_firefighter_config`.
- Produces: `dedup_config["firefighter_config"]`; a new `summary["remediated"]` counter; held pages recorded as `dispatch_result = 'remediating: <action> (run <id8>)'`.

- [ ] **Step 1: Write the failing integration test**

```python
# src/cofounder_agent/tests/unit/brain/test_alert_dispatcher_remediation.py
import pytest
from unittest.mock import AsyncMock

import brain.alert_dispatcher as ad
from brain.remediation.engine import RemediationDecision
from brain.remediation.registry import ActionResult


def _make_row(row_id=1, alertname="WorkerDown", severity="critical"):
    return {
        "id": row_id, "alertname": alertname, "status": "firing",
        "severity": severity, "category": "infrastructure",
        "labels": {"alertname": alertname, "severity": severity},
        "annotations": {}, "fingerprint": "fp-worker",
    }


class _Pool:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []
    async def fetch(self, sql, *a):
        if "alert_events" in sql and "dispatched_at IS NULL" in sql:
            return self._rows
        return []
    async def execute(self, sql, *a):
        self.executed.append((sql, a))
    async def fetchval(self, sql, *a):
        return None
    async def fetchrow(self, sql, *a):
        return None


@pytest.mark.asyncio
async def test_firefighter_acts_holds_the_page(monkeypatch):
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    # Dedup disabled path is fine; force config enabled + a decision that acts.
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro({
        "suppress_window_minutes": 0, "summarize_threshold_minutes": 0,
        "force_telegram_set": frozenset(), "triage_retry_max": 1, "triage_backoff": [0.0],
        "firefighter_config": {"enabled": True},
    }))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    monkeypatch.setattr(ad, "run_verify_scan_hook", _acoro({"verified": 0, "resolved": 0, "still_firing": 0}), raising=False)
    monkeypatch.setattr(
        ad, "evaluate_for_dispatch_hook",
        _acoro(RemediationDecision(acted=True, action_name="restart_container",
                                   run_id="abcd1234ef", result=ActionResult(status="ok"))),
        raising=False,
    )
    summary = await ad.poll_and_dispatch(pool, notify_fn=notify)
    assert summary.get("remediated") == 1
    assert notify.await_count == 0  # page HELD
    assert any("remediating:" in str(a[1]) for a in pool.executed)


@pytest.mark.asyncio
async def test_no_rule_pages_as_usual(monkeypatch):
    pool = _Pool([_make_row()])
    notify = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ad, "_read_dedup_config", _acoro({
        "suppress_window_minutes": 0, "summarize_threshold_minutes": 0,
        "force_telegram_set": frozenset(), "triage_retry_max": 1, "triage_backoff": [0.0],
        "firefighter_config": {"enabled": True},
    }))
    monkeypatch.setattr(ad, "_read_triage_enabled", _acoro(False))
    monkeypatch.setattr(
        ad, "evaluate_for_dispatch_hook",
        _acoro(RemediationDecision(acted=False, reason="no rule")), raising=False,
    )
    summary = await ad.poll_and_dispatch(pool, notify_fn=notify)
    assert summary["sent"] == 1
    assert notify.await_count == 1  # paged


def _acoro(value):
    async def _f(*a, **k):
        return value
    return _f
```

> **Note on the `*_hook` indirection:** the test monkeypatches module-level names `evaluate_for_dispatch_hook` / `run_verify_scan_hook`. Bind those names at import in `alert_dispatcher.py` (Step 3) so tests can swap them without importing the engine's real DB paths. This mirrors the existing `notify_fn` injection seam.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_alert_dispatcher_remediation.py -v`
Expected: FAIL — `AttributeError` on `evaluate_for_dispatch_hook` / `remediated` count missing.

- [ ] **Step 3: Wire the dispatcher**

3a. Near the top of `brain/alert_dispatcher.py`, after the imports, bind the engine hooks lazily so the module still imports when the engine isn't present (tests / partial images):

```python
# Firefighter engine hooks — bound as module names so tests can monkeypatch
# them, and so a brain image without brain/remediation still imports the
# dispatcher (the hooks resolve to no-ops that page as usual).
try:
    from brain.remediation.engine import evaluate_for_dispatch as evaluate_for_dispatch_hook
    from brain.remediation.engine import run_verify_scan as run_verify_scan_hook
    from brain.remediation.rules import load_firefighter_config as _load_firefighter_config
except Exception:  # noqa: BLE001
    async def evaluate_for_dispatch_hook(*a, **k):  # type: ignore
        from brain.remediation.engine import RemediationDecision
        return RemediationDecision(acted=False, reason="engine unavailable")
    async def run_verify_scan_hook(*a, **k):  # type: ignore
        return {"verified": 0, "resolved": 0, "still_firing": 0}
    async def _load_firefighter_config(pool):  # type: ignore
        return {"enabled": False}
```

3b. In `_read_dedup_config`, add the firefighter config to the returned dict (just before `return {`):

```python
    firefighter_config = await _load_firefighter_config(pool)
```

and add to the returned dict literal:

```python
        "firefighter_config": firefighter_config,
```

3c. In `_dispatch_one`, insert the firefighter hook as the **final statements inside the first `if dedup_config is not None:` block** — immediately after the `# action == "dispatch" -- fall through to severity-routed send.` comment, at the same 12-space indent. Placing it inside that block guarantees both `decision` and a non-None `dedup_config` are in scope:

```python
            # --- Firefighter: try deterministic remediation before paging. ---
            ff_cfg = dedup_config.get("firefighter_config") or {}
            if ff_cfg.get("enabled"):
                ff = await evaluate_for_dispatch_hook(
                    pool, alert=alert, fingerprint=decision["fingerprint"],
                    config=ff_cfg, logger=logger,
                )
                if ff.acted:
                    await pool.execute(
                        _MARK_ERROR_SQL, row_id,
                        f"remediating: {ff.action_name} (run {str(ff.run_id)[:8]})",
                    )
                    summary.setdefault("remediated", 0)
                    summary["remediated"] += 1
                    logger.info(
                        "[alert_dispatcher] firefighter acted row=%s action=%s — page held",
                        row_id, ff.action_name,
                    )
                    return None  # page HELD; verify scan will resolve or escalate
```

3d. In `poll_and_dispatch`, run the verify scan once per cycle. It must run even when there are no new rows, so add it right after `summary["polled"] = len(rows)` is NOT reached — instead place it after the notify_fn resolution but guard against `not rows` returning first. Concretely: move the verify scan ABOVE the `if not rows: return summary` early-return. Replace:

```python
    if not rows:
        return summary
```

with:

```python
    # Verify pending remediations every cycle (independent of new alert rows).
    # Use a SEPARATE local — do NOT reassign notify_fn, or the
    # `notify_fn_injected = notify_fn is not None` line below would wrongly flip
    # to True on the production path and break the #420 severity routing.
    verify_notify_fn = notify_fn if notify_fn is not None else await _resolve_notify_fn(pool=pool)
    try:
        ff_config = await _load_firefighter_config(pool)
        if ff_config.get("enabled"):
            vsummary = await run_verify_scan_hook(
                pool, config=ff_config, logger=logger, notify_fn=verify_notify_fn,
            )
            for k in ("verified", "resolved", "still_firing"):
                if vsummary.get(k):
                    summary[k] = summary.get(k, 0) + vsummary[k]
    except Exception as e:  # noqa: BLE001
        logger.warning("[alert_dispatcher] verify scan failed: %s", e)

    if not rows:
        return summary
```

> The existing `notify_fn_injected = notify_fn is not None` + `if notify_fn is None:` block below stays untouched — the verify scan uses its own `verify_notify_fn` local precisely so injection detection is preserved.

3e. Extend the cycle log line's condition + fields to surface remediation counts (optional but recommended for observability parity). In the final `if summary["sent"] or ...:` block add `remediated`/`resolved` to the logged fields.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_alert_dispatcher_remediation.py tests/unit/brain/test_alert_dispatcher_dedup.py -v`
Expected: PASS — the new tests pass AND the existing dedup tests still pass (regression check).

- [ ] **Step 5: Run the full brain suite + commit**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/ -q`
Expected: all green.

```bash
git add brain/alert_dispatcher.py src/cofounder_agent/tests/unit/brain/test_alert_dispatcher_remediation.py
git commit -m "feat(firefighter): wire engine into alert dispatcher (hold page + verify)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Grafana "Self-Healing / Remediation" row

**Files:**

- Modify: `infrastructure/grafana/dashboards/system-health.json` (confirm exact filename via `ls infrastructure/grafana/dashboards/`; the System Health board is the target per the spec)

**Interfaces:**

- Consumes: `audit_log` rows `event_type IN ('remediation_action','remediation_verify')`.

- [ ] **Step 1: Locate the dashboard file + a datasource example**

Run: `ls infrastructure/grafana/dashboards/`
Then open the System Health board JSON and note (a) the Postgres datasource `uid` used by an existing SQL panel, (b) the max `panel.id`, (c) the `gridPos` convention (24-col grid).

- [ ] **Step 2: Add a row + four panels**

Append these panels to the board's `panels` array (replace `PG_UID` with the real datasource uid, and give each panel an unused integer `id`; put the row at the next free `gridPos.y`). Keep panels 960px-friendly (width `w: 12`).

```json
{
  "type": "row",
  "title": "Self-Healing / Remediation",
  "gridPos": { "h": 1, "w": 24, "x": 0, "y": 900 },
  "id": 9001,
  "collapsed": false
}
```

```json
{
  "type": "stat",
  "title": "Auto-recoveries (silent, last 24h)",
  "datasource": { "type": "postgres", "uid": "PG_UID" },
  "gridPos": { "h": 6, "w": 12, "x": 0, "y": 901 },
  "id": 9002,
  "targets": [
    {
      "rawSql": "SELECT count(*) AS resolved FROM audit_log WHERE event_type='remediation_verify' AND details->>'result'='resolved' AND timestamp >= now() - interval '24 hours'",
      "format": "table",
      "refId": "A"
    }
  ]
}
```

```json
{
  "type": "stat",
  "title": "Still-firing after action / paged (last 24h)",
  "datasource": { "type": "postgres", "uid": "PG_UID" },
  "gridPos": { "h": 6, "w": 12, "x": 12, "y": 901 },
  "id": 9003,
  "targets": [
    {
      "rawSql": "SELECT count(*) AS still_firing FROM audit_log WHERE event_type='remediation_verify' AND details->>'result'='still_firing' AND timestamp >= now() - interval '24 hours'",
      "format": "table",
      "refId": "A"
    }
  ]
}
```

```json
{
  "type": "piechart",
  "title": "Actions by type (last 24h)",
  "datasource": { "type": "postgres", "uid": "PG_UID" },
  "gridPos": { "h": 8, "w": 12, "x": 0, "y": 907 },
  "id": 9004,
  "targets": [
    {
      "rawSql": "SELECT details->>'action_name' AS action, count(*) AS n FROM audit_log WHERE event_type='remediation_action' AND timestamp >= now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC",
      "format": "table",
      "refId": "A"
    }
  ]
}
```

```json
{
  "type": "table",
  "title": "Latest remediation actions",
  "datasource": { "type": "postgres", "uid": "PG_UID" },
  "gridPos": { "h": 8, "w": 12, "x": 12, "y": 907 },
  "id": 9005,
  "targets": [
    {
      "rawSql": "SELECT timestamp, details->>'alertname' AS alert, details->>'action_name' AS action, details->>'source' AS src, details->'execution'->>'status' AS exec_status FROM audit_log WHERE event_type='remediation_action' ORDER BY timestamp DESC LIMIT 25",
      "format": "table",
      "refId": "A"
    }
  ]
}
```

- [ ] **Step 3: Validate the JSON**

Run: `python -c "import json; json.load(open('infrastructure/grafana/dashboards/system-health.json'))"`
Expected: no output (valid JSON). If your repo has a dashboard-lint CI script, run it too.

- [ ] **Step 4: Commit**

```bash
git add infrastructure/grafana/dashboards/system-health.json
git commit -m "feat(firefighter): System Health remediation panels

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Docs + seed the first rules

**Files:**

- Modify: `docs/operations/self-healing.md`
- Modify: `docs/integrations/webhook_alertmanager_dispatch.md`

**Interfaces:** none (docs + operational seeding).

- [ ] **Step 1: Document the loop + rule authoring in `self-healing.md`**

Add a section "## Deterministic firefighter (detect → act → verify → escalate)" covering: the `remediation_rules` table, the two v1 actions (`restart_container`, `run_auto_remediate`), the `ops_firefighter_*` knobs, the circuit breaker + rate cap, and that successful recoveries are silent (audit_log + Grafana, no page). Include a worked rule-insert example:

```sql
-- Restart the image-gen server when its down-alert fires.
INSERT INTO remediation_rules (alertname, action_name, params, description)
VALUES ('ImageGenServerDown', 'restart_container',
        '{"container": "poindexter-image-gen-server"}'::jsonb,
        'Auto-restart image-gen on its down alert');

-- Run the stuck-task sweep when the stuck-tasks alert fires.
INSERT INTO remediation_rules (alertname, action_name, params, description)
VALUES ('StuckTasks', 'run_auto_remediate', '{}'::jsonb,
        'Sweep stuck pipeline_tasks / stale approvals');
```

- [ ] **Step 2: Update the alertmanager-dispatch doc**

In `docs/integrations/webhook_alertmanager_dispatch.md`, replace the "Concrete remediation execution is intentionally deferred" language in the Remediation-hook section with a pointer: the brain now runs deterministic remediation via `remediation_rules` + `brain/remediation/`, verify-then-page; see `docs/operations/self-healing.md`.

- [ ] **Step 3: Seed the first real rules (operational, against the live DB)**

Run: `SELECT DISTINCT alertname FROM alert_events ORDER BY 1;` (via `mcp__postgres__query` or `poindexter`), confirm the real alertnames, then `INSERT` rules only for (alert → action) pairs where the action genuinely fixes that alert. Start conservative (1–3 rules). Never seed a rule whose action can't resolve the alert.

- [ ] **Step 4: Commit the docs**

```bash
git add docs/operations/self-healing.md docs/integrations/webhook_alertmanager_dispatch.md
git commit -m "docs(firefighter): deterministic self-heal loop + rule authoring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5: Full-suite gate + deploy note**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/ tests/unit/test_settings_defaults_firefighter.py -q`
Expected: all green.

Deploy (post-merge, per `feedback_rebuild_authority`): the brain image bakes `brain/`, so `docker compose ... up -d --build poindexter-brain-daemon` to apply. Then confirm `[firefighter]` log lines appear and the System Health row populates.

---

## Notes for the implementer

- **Consumes/Produces contract across tasks:** `ActionResult` / `RemediationContext` (Task 3) → used by executors (Task 4 target) and the engine (Task 6). `RemediationDecision.acted` (Task 6) is the single signal the dispatcher (Task 8) uses to hold-or-page. `remediation_run_id` correlates the `remediation_action` row (Task 6) with its `remediation_verify` row (Task 7); the verify scan's "pending" query depends on that pairing.
- **The `fingerprint` must be the dedup fingerprint** (`decision["fingerprint"]` from `_evaluate_dedup_decision`), not the raw `alert_events.fingerprint` column — that's the key `alert_dedup_state` is stored under, which the verify scan reads.
- **Plan B (LLM long-tail) builds on this:** it adds the worker `POST /api/remediation/select` route, a brain-side selector client (clone of `_request_summary_diagnosis`), a hook on the SUPPRESS branch for persistent un-ruled alerts, `source="llm"` on the audit rows, and `remediation_candidate_rule` findings on verified-resolved LLM actions. No Plan A interface changes are required.
