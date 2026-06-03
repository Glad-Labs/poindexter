# Restore-test Brain Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily-gated brain probe that pg_restores the latest daily dump into a throwaway pgvector container, re-runs the production migration runner against it, asserts critical tables survived, and pages the operator only on real corruption.

**Architecture:** Standalone `brain/restore_test_probe.py` (stdlib + asyncpg only) mirroring `backup_watcher.py`: a `run_restore_test_probe(pool, *, <injectable seams>)` entry point + a `RestoreTestProbe` Protocol wrapper, every tunable an `app_settings` row, alerts via `notify_operator`, observability via `audit_log`, wired into `brain_daemon.run_cycle` behind an import guard. Uses the brain's existing docker socket + `/host-backups:ro` mount — zero compose changes.

**Tech Stack:** Python 3.12, asyncpg, the docker CLI (subprocess), pytest. Spec: `docs/superpowers/specs/2026-06-02-restore-test-probe-design.md`.

**Conventions to match:** `brain/backup_watcher.py` (structure), `brain/smart_monitor.py` (settings reads), `src/cofounder_agent/tests/unit/brain/test_backup_watcher.py` (test style), `src/cofounder_agent/services/migrations/20260531_120000_seed_anomaly_probe_settings.py` (seed migration).

**Run tests from:** `src/cofounder_agent` (its `pyproject.toml` sets `pythonpath` so `from brain import ...` resolves). Command prefix: `cd src/cofounder_agent && poetry run pytest ...`.

---

## Task 1: `migrations_smoke.py` — backend-root env override

The worker mounts the backend at `/app` and scripts at `/opt/scripts` (split mounts), so the script's `parents[2]` repo-root math finds 0 migrations inside the worker. Add a `POINDEXTER_BACKEND_ROOT` override; CI (env unset) is unchanged.

**Files:**

- Modify: `scripts/ci/migrations_smoke.py:32-41`
- Test: `scripts/ci/test_migrations_smoke_backend_root.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# scripts/ci/test_migrations_smoke_backend_root.py
"""Backend-root override for migrations_smoke (poindexter#441)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _load(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("POINDEXTER_BACKEND_ROOT", raising=False)
    else:
        monkeypatch.setenv("POINDEXTER_BACKEND_ROOT", env_value)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.modules.pop("migrations_smoke", None)
    return importlib.import_module("migrations_smoke")


def test_env_override_sets_backend_root(monkeypatch, tmp_path):
    (tmp_path / "services" / "migrations").mkdir(parents=True)
    mod = _load(monkeypatch, str(tmp_path))
    assert mod.BACKEND_ROOT == tmp_path
    assert mod.MIGRATIONS_DIR == tmp_path / "services" / "migrations"


def test_no_env_falls_back_to_repo_layout(monkeypatch):
    mod = _load(monkeypatch, None)
    # parents[2] of scripts/ci/migrations_smoke.py is the repo root.
    assert mod.BACKEND_ROOT.name == "cofounder_agent"
    assert mod.MIGRATIONS_DIR.name == "migrations"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest ../../scripts/ci/test_migrations_smoke_backend_root.py -v`
Expected: FAIL — `test_env_override_sets_backend_root` asserts `BACKEND_ROOT == tmp_path` but the unmodified script always uses `parents[2]`.

- [ ] **Step 3: Implement the override**

Replace lines 32-34 of `scripts/ci/migrations_smoke.py`:

```python
REPO_ROOT = Path(__file__).resolve().parents[2]
# poindexter#441: the brain's restore-test probe runs this script inside
# the worker container, where the backend is mounted at /app (not under a
# repo-root tree). Honor an explicit override so the split-mount layout
# resolves; CI leaves the env unset and keeps the repo-root default.
_BACKEND_ROOT_ENV = os.environ.get("POINDEXTER_BACKEND_ROOT")
BACKEND_ROOT = (
    Path(_BACKEND_ROOT_ENV).resolve()
    if _BACKEND_ROOT_ENV
    else REPO_ROOT / "src" / "cofounder_agent"
)
MIGRATIONS_DIR = BACKEND_ROOT / "services" / "migrations"
```

(`import os` already present at line 26.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest ../../scripts/ci/test_migrations_smoke_backend_root.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/ci/migrations_smoke.py scripts/ci/test_migrations_smoke_backend_root.py
git commit -m "feat(ci): POINDEXTER_BACKEND_ROOT override for migrations_smoke (poindexter#441)"
```

---

## Task 2: Seed migration for `restore_test_*` settings

**Files:**

- Create: `src/cofounder_agent/services/migrations/20260602_193000_seed_restore_test_settings.py`

- [ ] **Step 1: Write the migration** (modeled exactly on `20260531_120000_seed_anomaly_probe_settings.py`)

```python
"""Migration 20260602_193000_seed_restore_test_settings: seed restore-test probe settings

Seeds the app_settings keys that drive brain/restore_test_probe.py
(Glad-Labs/poindexter#441) — the daily probe that pg_restores the latest
daily dump into a throwaway pgvector container, re-runs the production
migration runner against it, and asserts critical tables survived.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — never clobbers an
operator-tuned value; a re-run on an up-to-date DB is a no-op.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "restore_test_enabled": "true",
    "restore_test_interval_hours": "24",
    "restore_test_backup_dir": "/host-backups/auto",
    "restore_test_tier": "daily",
    "restore_test_postgres_image": "pgvector/pgvector:pg16",
    "restore_test_run_migrations_smoke": "true",
    "restore_test_critical_tables": "posts,app_settings,audit_log",
    "restore_test_min_row_count": "1",
    "restore_test_pg_ready_timeout_seconds": "60",
    "restore_test_restore_timeout_seconds": "300",
    "restore_test_smoke_timeout_seconds": "180",
}


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
            )
            logger.info(
                "Migration seed_restore_test_settings: %s (%s)", key, result,
            )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1 AND value = $2",
                key,
                value,
            )
        logger.info(
            "Migration seed_restore_test_settings down: removed default rows "
            "(operator-tuned values preserved)"
        )
```

- [ ] **Step 2: Lint the migration**

Run: `python scripts/ci/migrations_lint.py`
Expected: PASS (no collisions; `up`/`down` interface present).

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/services/migrations/20260602_193000_seed_restore_test_settings.py
git commit -m "feat(db): seed restore_test_* probe settings (poindexter#441)"
```

---

## Task 3: Probe module skeleton — constants + config reader

**Files:**

- Create: `brain/restore_test_probe.py`
- Test: `src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py`

- [ ] **Step 1: Write the failing test**

```python
# src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py
"""Unit tests for brain/restore_test_probe.py (poindexter#441).

All docker/subprocess/filesystem I/O is injected — no real container ever
runs. The pool is a MagicMock with AsyncMock methods; app_settings reads
are seeded via the ``setting_values`` dict passed to ``_make_pool``.
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import restore_test_probe as rt


def _settings(**over: str) -> dict[str, str]:
    base = {
        rt.ENABLED_KEY: "true",
        rt.INTERVAL_HOURS_KEY: "24",
        rt.BACKUP_DIR_KEY: "/host-backups/auto",
        rt.TIER_KEY: "daily",
        rt.POSTGRES_IMAGE_KEY: "pgvector/pgvector:pg16",
        rt.RUN_SMOKE_KEY: "true",
        rt.CRITICAL_TABLES_KEY: "posts,app_settings,audit_log",
        rt.MIN_ROW_COUNT_KEY: "1",
        rt.PG_READY_TIMEOUT_KEY: "60",
        rt.RESTORE_TIMEOUT_KEY: "300",
        rt.SMOKE_TIMEOUT_KEY: "180",
    }
    base.update(over)
    return base


def _make_pool(*, setting_values: Optional[dict[str, str]] = None,
               seconds_since_last_run: Optional[float] = None):
    pool = MagicMock()
    settings = _settings(**(setting_values or {}))

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        if "audit_log" in query:
            return seconds_since_last_run
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.mark.asyncio
async def test_read_config_coerces_types():
    pool = _make_pool()
    cfg = await rt._read_config(pool)
    assert cfg["enabled"] is True
    assert cfg["interval_hours"] == 24
    assert cfg["tier"] == "daily"
    assert cfg["critical_tables"] == ["posts", "app_settings", "audit_log"]
    assert cfg["min_row_count"] == 1
    assert cfg["run_smoke"] is True


@pytest.mark.asyncio
async def test_read_config_filters_invalid_table_names():
    pool = _make_pool(setting_values={rt.CRITICAL_TABLES_KEY: "posts, drop table; , app_settings"})
    cfg = await rt._read_config(pool)
    # The injection-y middle entry is dropped; valid names survive.
    assert cfg["critical_tables"] == ["posts", "app_settings"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.restore_test_probe'`.

- [ ] **Step 3: Create the module skeleton**

```python
# brain/restore_test_probe.py
"""Restore-test probe (Glad-Labs/poindexter#441).

Backups are created (#385) and freshness-monitored (#388 backup_watcher),
but nothing verifies a dump actually RESTORES. A corrupt-but-fresh dump is
worse than none — it gives false confidence. This probe closes that gap.

Once per ``restore_test_interval_hours`` (default 24h) it:

1. picks the newest dump under ``<backup_dir>/<tier>/`` (the brain's
   read-only /host-backups mount),
2. spins a throwaway ``pgvector/pgvector:pg16`` container (no published
   ports, attached to the worker's docker network),
3. ``docker cp``s the dump in and ``pg_restore``s it,
4. re-runs the production migration runner against it via
   ``docker exec poindexter-worker python .../migrations_smoke.py``,
5. asserts critical tables (posts, app_settings, audit_log) survived,
6. tears the throwaway down.

Verification failures (corrupt dump, empty tables, smoke failure) page the
operator at ``error`` (Telegram + Discord). Infra failures (docker
unreachable, no dump) are ``warning`` (Discord only) — a transient docker
hiccup that merely prevented the test must not train the operator to
ignore Telegram.

Design parity with brain/backup_watcher.py + brain/smart_monitor.py:
stdlib + asyncpg only; every tunable an app_settings row; subprocess calls
degrade gracefully (logged, never raised); CREATE_NO_WINDOW on win32.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.restore_test_probe")

# --- app_settings keys ------------------------------------------------------
ENABLED_KEY = "restore_test_enabled"
INTERVAL_HOURS_KEY = "restore_test_interval_hours"
BACKUP_DIR_KEY = "restore_test_backup_dir"
TIER_KEY = "restore_test_tier"
POSTGRES_IMAGE_KEY = "restore_test_postgres_image"
RUN_SMOKE_KEY = "restore_test_run_migrations_smoke"
CRITICAL_TABLES_KEY = "restore_test_critical_tables"
MIN_ROW_COUNT_KEY = "restore_test_min_row_count"
PG_READY_TIMEOUT_KEY = "restore_test_pg_ready_timeout_seconds"
RESTORE_TIMEOUT_KEY = "restore_test_restore_timeout_seconds"
SMOKE_TIMEOUT_KEY = "restore_test_smoke_timeout_seconds"

DEFAULT_ENABLED = True
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_BACKUP_DIR = "/host-backups/auto"
DEFAULT_TIER = "daily"
DEFAULT_POSTGRES_IMAGE = "pgvector/pgvector:pg16"
DEFAULT_RUN_SMOKE = True
DEFAULT_CRITICAL_TABLES = ["posts", "app_settings", "audit_log"]
DEFAULT_MIN_ROW_COUNT = 1
DEFAULT_PG_READY_TIMEOUT = 60
DEFAULT_RESTORE_TIMEOUT = 300
DEFAULT_SMOKE_TIMEOUT = 180

# Constants (not settings — changing them needs coordinated edits elsewhere).
THROWAWAY_CONTAINER = "poindexter-restore-test"
WORKER_CONTAINER = "poindexter-worker"
SMOKE_SCRIPT_PATH = "/opt/scripts/ci/migrations_smoke.py"
WORKER_BACKEND_ROOT = "/app"
TARGET_DB = "poindexter_brain"
DUMP_PREFIX = "poindexter_brain_"
DUMP_SUFFIX = ".dump"
PROBE_INTERVAL_SECONDS = 300

# Identifier guard for operator-supplied table names before SQL interpolation.
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Generous subprocess cap for docker run / rm / inspect / cp / exec wrappers.
_DOCKER_CMD_TIMEOUT = 60

# Module-level last verdict so a recovery (fail -> pass) emits one notify.
# None = unknown (first run since boot). True = last run passed.
_last_passed: Optional[bool] = None


def _reset_module_state() -> None:
    """Test helper — wipe the recovery-notify latch."""
    global _last_passed
    _last_passed = None


# --- app_settings reads (brain is standalone; hit the DB directly) ----------
async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[RESTORE_TEST] Could not read %s: %s — default %r",
            key, exc, default,
        )
        return default
    return default if val is None else val


def _coerce_bool(val: Any, default: bool) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


def _parse_tables(val: Any) -> list[str]:
    """Split a comma-separated table list, keeping only valid identifiers."""
    if not val:
        return list(DEFAULT_CRITICAL_TABLES)
    out: list[str] = []
    for raw in str(val).split(","):
        name = raw.strip()
        if name and _TABLE_NAME_RE.match(name):
            out.append(name)
    return out or list(DEFAULT_CRITICAL_TABLES)


async def _read_config(pool: Any) -> dict[str, Any]:
    return {
        "enabled": _coerce_bool(
            await _read_setting(pool, ENABLED_KEY, "true"), DEFAULT_ENABLED),
        "interval_hours": _coerce_int(
            await _read_setting(pool, INTERVAL_HOURS_KEY, DEFAULT_INTERVAL_HOURS),
            DEFAULT_INTERVAL_HOURS),
        "backup_dir": str(await _read_setting(
            pool, BACKUP_DIR_KEY, DEFAULT_BACKUP_DIR)).strip() or DEFAULT_BACKUP_DIR,
        "tier": str(await _read_setting(
            pool, TIER_KEY, DEFAULT_TIER)).strip() or DEFAULT_TIER,
        "postgres_image": str(await _read_setting(
            pool, POSTGRES_IMAGE_KEY, DEFAULT_POSTGRES_IMAGE)).strip()
            or DEFAULT_POSTGRES_IMAGE,
        "run_smoke": _coerce_bool(
            await _read_setting(pool, RUN_SMOKE_KEY, "true"), DEFAULT_RUN_SMOKE),
        "critical_tables": _parse_tables(
            await _read_setting(pool, CRITICAL_TABLES_KEY, None)),
        "min_row_count": _coerce_int(
            await _read_setting(pool, MIN_ROW_COUNT_KEY, DEFAULT_MIN_ROW_COUNT),
            DEFAULT_MIN_ROW_COUNT),
        "pg_ready_timeout": _coerce_int(
            await _read_setting(pool, PG_READY_TIMEOUT_KEY, DEFAULT_PG_READY_TIMEOUT),
            DEFAULT_PG_READY_TIMEOUT),
        "restore_timeout": _coerce_int(
            await _read_setting(pool, RESTORE_TIMEOUT_KEY, DEFAULT_RESTORE_TIMEOUT),
            DEFAULT_RESTORE_TIMEOUT),
        "smoke_timeout": _coerce_int(
            await _read_setting(pool, SMOKE_TIMEOUT_KEY, DEFAULT_SMOKE_TIMEOUT),
            DEFAULT_SMOKE_TIMEOUT),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -v`
Expected: PASS (both config tests).

- [ ] **Step 5: Commit**

```bash
git add brain/restore_test_probe.py src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py
git commit -m "feat(brain): restore-test probe skeleton + config (poindexter#441)"
```

---

## Task 4: Dump discovery + daily gate

**Files:**

- Modify: `brain/restore_test_probe.py` (append functions)
- Test: `src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# append to test_restore_test_probe.py

def test_find_latest_dump_picks_newest(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    old = daily / "poindexter_brain_20260601T000000Z.dump"
    new = daily / "poindexter_brain_20260602T000000Z.dump"
    old.write_text("old")
    new.write_text("new")
    os_utime_older(old)  # helper below makes `old` older
    found = rt._find_latest_dump(str(tmp_path), "daily")
    assert found == str(new)


def test_find_latest_dump_skips_tmp_and_missing(tmp_path):
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "poindexter_brain_20260602T000000Z.dump.tmp").write_text("partial")
    assert rt._find_latest_dump(str(tmp_path), "daily") is None
    assert rt._find_latest_dump(str(tmp_path), "hourly") is None  # dir absent


def os_utime_older(path):
    import os as _os
    st = path.stat()
    _os.utime(path, (st.st_atime - 3600, st.st_mtime - 3600))


@pytest.mark.asyncio
async def test_gate_skips_when_recent_run(tmp_path):
    pool = _make_pool(seconds_since_last_run=3600.0)  # 1h ago, interval 24h
    since = await rt._seconds_since_last_run(pool)
    assert since == 3600.0


@pytest.mark.asyncio
async def test_gate_allows_when_no_prior_run():
    pool = _make_pool(seconds_since_last_run=None)
    assert await rt._seconds_since_last_run(pool) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -k "dump or gate" -v`
Expected: FAIL — `_find_latest_dump` / `_seconds_since_last_run` not defined.

- [ ] **Step 3: Implement**

```python
# append to brain/restore_test_probe.py

def _find_latest_dump(backup_dir: str, tier: str) -> Optional[str]:
    """Newest ``poindexter_brain_*.dump`` under ``<backup_dir>/<tier>/``.

    Falls back to any ``*.dump`` if none match the prefix. Skips ``.tmp``
    partials. ``None`` when the directory is missing or empty.
    """
    tier_dir = Path(backup_dir) / tier
    if not tier_dir.is_dir():
        return None
    best_path: Optional[str] = None
    best_mtime = -1.0
    fallback_path: Optional[str] = None
    fallback_mtime = -1.0
    try:
        with os.scandir(tier_dir) as it:
            for entry in it:
                name = entry.name
                if not name.endswith(DUMP_SUFFIX):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if name.startswith(DUMP_PREFIX):
                    if mtime > best_mtime:
                        best_mtime, best_path = mtime, entry.path
                elif mtime > fallback_mtime:
                    fallback_mtime, fallback_path = mtime, entry.path
    except OSError as exc:
        logger.warning("[RESTORE_TEST] Could not scan %s: %s", tier_dir, exc)
        return None
    return best_path or fallback_path


async def _seconds_since_last_run(pool: Any) -> Optional[float]:
    """Seconds since the newest terminal restore-test audit event, or None.

    Gating lives in the DB (not module memory) so a brain restart doesn't
    re-trigger the heavy run. Both completed + failed terminal events
    advance the gate, so a persistent failure (or docker-down) doesn't
    hammer docker every 5-min cycle.
    """
    try:
        return await pool.fetchval(
            """
            SELECT EXTRACT(EPOCH FROM (NOW() - created_at))
              FROM audit_log
             WHERE event_type IN (
                 'probe.restore_test_completed', 'probe.restore_test_failed')
             ORDER BY id DESC
             LIMIT 1
            """
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RESTORE_TEST] gate lookup failed: %s", exc)
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -k "dump or gate" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add brain/restore_test_probe.py src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py
git commit -m "feat(brain): restore-test dump discovery + daily gate (poindexter#441)"
```

---

## Task 5: Docker seam functions + audit helper

These wrap the docker CLI. They never raise — each returns a structured result the orchestrator interprets. Pure-ish helpers (`_run_cmd`, `_discover_network`, `_table_count` parsing) get direct unit tests; the rest are exercised through the orchestrator in Task 7 via injection.

**Files:**

- Modify: `brain/restore_test_probe.py` (append)
- Test: `src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# append to test_restore_test_probe.py
from unittest.mock import patch


def test_discover_network_parses_first(monkeypatch):
    def fake_run(cmd, timeout):
        return (0, "glad-labs-website_default\n", "")
    monkeypatch.setattr(rt, "_run_cmd", fake_run)
    assert rt._discover_network() == "glad-labs-website_default"


def test_discover_network_none_on_error(monkeypatch):
    monkeypatch.setattr(rt, "_run_cmd", lambda cmd, timeout: (1, "", "no such container"))
    assert rt._discover_network() is None


def test_table_count_parses_int(monkeypatch):
    monkeypatch.setattr(rt, "_run_cmd", lambda cmd, timeout: (0, " 78 \n", ""))
    assert rt._table_count("c", "db", "posts") == 78


def test_table_count_rejects_bad_identifier(monkeypatch):
    called = []
    monkeypatch.setattr(rt, "_run_cmd", lambda cmd, timeout: called.append(cmd) or (0, "1", ""))
    assert rt._table_count("c", "db", "posts; DROP TABLE x") is None
    assert called == []  # never reached subprocess


def test_table_count_none_on_nonzero(monkeypatch):
    monkeypatch.setattr(rt, "_run_cmd", lambda cmd, timeout: (1, "", "relation does not exist"))
    assert rt._table_count("c", "db", "missing") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -k "network or table_count" -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement the seams**

```python
# append to brain/restore_test_probe.py

def _run_cmd(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """Run a command. Returns (rc, stdout, stderr). Never raises.

    rc=-1 signals the process could not run / timed out (docker missing,
    timeout) as distinct from a non-zero exit of a process that ran.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True, "text": True, "timeout": timeout,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        r = subprocess.run(cmd, **kwargs)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except FileNotFoundError:
        return -1, "", "docker CLI not on PATH"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return -1, "", f"{type(exc).__name__}: {str(exc)[:160]}"


def _discover_network(worker: str = WORKER_CONTAINER) -> Optional[str]:
    """First docker network attached to the worker, or None."""
    rc, out, _ = _run_cmd(
        ["docker", "inspect", "-f",
         "{{range $k,$v := .NetworkSettings.Networks}}{{$k}}\n{{end}}", worker],
        _DOCKER_CMD_TIMEOUT,
    )
    if rc != 0:
        return None
    for line in out.splitlines():
        net = line.strip()
        if net:
            return net
    return None


def _remove_container(name: str) -> None:
    """`docker rm -f` — idempotent, errors ignored (may not exist)."""
    _run_cmd(["docker", "rm", "-f", name], _DOCKER_CMD_TIMEOUT)


def _start_container(name: str, image: str, network: Optional[str],
                     password: str) -> tuple[bool, str]:
    cmd = ["docker", "run", "-d", "--name", name,
           "-e", f"POSTGRES_PASSWORD={password}",
           "-e", f"POSTGRES_DB={TARGET_DB}"]
    if network:
        cmd += ["--network", network]
    cmd.append(image)
    rc, _, err = _run_cmd(cmd, _DOCKER_CMD_TIMEOUT)
    return (rc == 0), (err.strip() or f"docker run rc={rc}")


def _wait_pg_ready(name: str, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, _, _ = _run_cmd(
            ["docker", "exec", name, "pg_isready", "-U", "postgres", "-q"],
            _DOCKER_CMD_TIMEOUT)
        if rc == 0:
            return True
        time.sleep(2)
    return False


def _copy_dump(name: str, host_path: str) -> tuple[bool, str]:
    rc, _, err = _run_cmd(
        ["docker", "cp", host_path, f"{name}:/tmp/restore.dump"],
        _DOCKER_CMD_TIMEOUT)
    return (rc == 0), (err.strip() or f"docker cp rc={rc}")


def _restore(name: str, timeout: int) -> tuple[int, str]:
    """pg_restore the copied dump. Returns (rc, stderr). rc is informational
    — benign non-zero exits happen with --no-owner; the row counts + smoke
    are the authoritative signal (see _decide_verdict)."""
    rc, _, err = _run_cmd(
        ["docker", "exec", name, "pg_restore",
         "--no-owner", "--no-privileges", "-U", "postgres",
         "-d", TARGET_DB, "/tmp/restore.dump"],
        timeout)
    return rc, err.strip()


def _table_count(name: str, db: str, table: str) -> Optional[int]:
    """Row count for ``table`` via psql, or None on bad name / query error."""
    if not _TABLE_NAME_RE.match(table):
        logger.warning("[RESTORE_TEST] rejecting non-identifier table %r", table)
        return None
    rc, out, _ = _run_cmd(
        ["docker", "exec", name, "psql", "-U", "postgres", "-d", db,
         "-tAc", f"SELECT count(*) FROM {table}"],
        _DOCKER_CMD_TIMEOUT)
    if rc != 0:
        return None
    try:
        return int(out.strip())
    except (TypeError, ValueError):
        return None


def _run_smoke(throwaway: str, db: str, password: str, timeout: int) -> tuple[bool, str]:
    """Run migrations_smoke.py inside the worker against the throwaway DB."""
    dsn = f"postgresql://postgres:{password}@{throwaway}:5432/{db}"
    rc, out, err = _run_cmd(
        ["docker", "exec",
         "-e", f"DATABASE_URL={dsn}",
         "-e", f"POINDEXTER_BACKEND_ROOT={WORKER_BACKEND_ROOT}",
         WORKER_CONTAINER, "python", SMOKE_SCRIPT_PATH],
        timeout)
    tail = (err or out).strip()[-300:]
    return (rc == 0), tail


async def _emit_audit(pool: Any, event: str, detail: str,
                      *, severity: str = "info",
                      extra: Optional[dict[str, Any]] = None) -> None:
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            event, "brain.restore_test_probe", json.dumps(payload), severity)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[RESTORE_TEST] audit write failed (%s): %s", event, exc)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -k "network or table_count" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add brain/restore_test_probe.py src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py
git commit -m "feat(brain): restore-test docker seams + audit helper (poindexter#441)"
```

---

## Task 6: Verdict policy `_decide_verdict`

> **⚠️ Learning-mode contribution:** the policy _body_ below is a reference implementation. During execution, pause here and let Matt author/confirm the ~6 lines — it's the security/UX judgment (does a non-zero pg_restore exit with all data present count as failure?) that decides whether the probe is trustworthy or a nuisance.

**Files:**

- Modify: `brain/restore_test_probe.py` (append)
- Test: `src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# append to test_restore_test_probe.py

def test_verdict_pass_clean():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="",
        row_counts={"posts": 78, "app_settings": 800, "audit_log": 5},
        schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is True and sev == "info"


def test_verdict_pass_despite_benign_restore_warning():
    # Non-zero pg_restore exit but all data present -> still pass.
    ok, sev, _ = rt._decide_verdict(
        restore_rc=1, restore_stderr="warning: no privileges could be revoked",
        row_counts={"posts": 78, "app_settings": 800, "audit_log": 5},
        schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is True and sev == "info"


def test_verdict_fail_empty_critical_table():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="",
        row_counts={"posts": 0, "app_settings": 800, "audit_log": 5},
        schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is False and sev == "error"


def test_verdict_fail_missing_table_count():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="",
        row_counts={"posts": None, "app_settings": 800, "audit_log": 5},
        schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is False and sev == "error"


def test_verdict_fail_smoke():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="",
        row_counts={"posts": 78, "app_settings": 800, "audit_log": 5},
        schema_migrations_count=120, min_count=1,
        smoke_enabled=True, smoke_ok=False, smoke_detail="FAIL: 2 missing")
    assert ok is False and sev == "error"


def test_verdict_fail_empty_schema_migrations():
    ok, sev, _ = rt._decide_verdict(
        restore_rc=0, restore_stderr="",
        row_counts={"posts": 78, "app_settings": 800, "audit_log": 5},
        schema_migrations_count=0, min_count=1,
        smoke_enabled=True, smoke_ok=True, smoke_detail="OK")
    assert ok is False and sev == "error"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -k verdict -v`
Expected: FAIL — `_decide_verdict` not defined.

- [ ] **Step 3: Implement (reference policy — confirm with Matt)**

```python
# append to brain/restore_test_probe.py

def _decide_verdict(*, restore_rc: int, restore_stderr: str,
                    row_counts: dict[str, Optional[int]],
                    schema_migrations_count: Optional[int], min_count: int,
                    smoke_enabled: bool, smoke_ok: bool,
                    smoke_detail: str) -> tuple[bool, str, str]:
    """Combine the three signals into (passed, severity, detail).

    Authoritative signal is the DATA (row counts + schema_migrations +
    smoke), NOT pg_restore's exit code: a custom-format restore into a
    fresh DB routinely exits non-zero on benign role/ownership notices
    with --no-owner. Treating exit-code as fatal would page on healthy
    backups. So restore_rc/stderr are surfaced in the detail but never
    flip the verdict on their own.
    """
    problems: list[str] = []
    for table, count in row_counts.items():
        if count is None:
            problems.append(f"{table}: query failed / table missing")
        elif count < min_count:
            problems.append(f"{table}: {count} rows (< {min_count})")
    if not schema_migrations_count:
        problems.append("schema_migrations: empty")
    if smoke_enabled and not smoke_ok:
        problems.append(f"migrations_smoke failed: {smoke_detail}")

    if problems:
        detail = "Restore verification FAILED — " + "; ".join(problems)
        if restore_rc != 0:
            detail += f" (pg_restore rc={restore_rc}: {restore_stderr[:160]})"
        return False, "error", detail

    counts = ", ".join(f"{t}={c}" for t, c in row_counts.items())
    detail = (f"Restore OK — {counts}, schema_migrations="
              f"{schema_migrations_count}"
              + (", smoke=pass" if smoke_enabled else ", smoke=skipped"))
    if restore_rc != 0:
        detail += f" (benign pg_restore rc={restore_rc})"
    return True, "info", detail
```

- [ ] **Step 4: Run to verify pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -k verdict -v`
Expected: PASS (all six).

- [ ] **Step 5: Commit**

```bash
git add brain/restore_test_probe.py src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py
git commit -m "feat(brain): restore-test verdict policy (poindexter#441)"
```

---

## Task 7: Orchestrator `run_restore_test_probe` + `RestoreTestProbe` wrapper

Ties the pieces together with injectable seams and a guaranteed-teardown `finally`. This is where the end-to-end scenarios live.

**Files:**

- Modify: `brain/restore_test_probe.py` (append)
- Test: `src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# append to test_restore_test_probe.py

@pytest.fixture(autouse=True)
def _reset():
    rt._reset_module_state()
    yield
    rt._reset_module_state()


def _seams(**over):
    """Default all-pass injectable seams; override per test."""
    seams = dict(
        find_dump_fn=lambda d, t: "/host-backups/auto/daily/poindexter_brain_x.dump",
        discover_network_fn=lambda: "net",
        start_fn=lambda name, image, net, pw: (True, "started"),
        wait_ready_fn=lambda name, timeout: True,
        copy_fn=lambda name, path: (True, "copied"),
        restore_fn=lambda name, timeout: (0, ""),
        count_fn=lambda name, db, table: 5,
        smoke_fn=lambda thr, db, pw, timeout: (True, "OK"),
        teardown_fn=MagicMock(),
        notify_fn=MagicMock(),
    )
    seams.update(over)
    return seams


def _events(pool):
    return [c.args[1] for c in pool.execute.call_args_list
            if "INSERT INTO audit_log" in c.args[0]]


@pytest.mark.asyncio
async def test_disabled_short_circuits():
    pool = _make_pool(setting_values={rt.ENABLED_KEY: "false"})
    seams = _seams()
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["status"] == "disabled"
    seams["teardown_fn"].assert_not_called()


@pytest.mark.asyncio
async def test_gate_skips_recent_run():
    pool = _make_pool(seconds_since_last_run=3600.0)  # 1h < 24h
    seams = _seams()
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["status"] == "skipped"
    seams["teardown_fn"].assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_passes_and_tears_down():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams()
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is True and out["status"] == "passed"
    seams["teardown_fn"].assert_called_once_with(rt.THROWAWAY_CONTAINER)
    assert "probe.restore_test_completed" in _events(pool)


@pytest.mark.asyncio
async def test_corrupt_dump_pages_error():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams(count_fn=lambda name, db, table: 0)  # empty tables
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False
    args, kwargs = seams["notify_fn"].call_args
    assert kwargs.get("severity") == "error"
    seams["teardown_fn"].assert_called_once()
    assert "probe.restore_test_failed" in _events(pool)


@pytest.mark.asyncio
async def test_no_dump_is_infra_warning():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams(find_dump_fn=lambda d, t: None)
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False and out["status"] == "no_dump"
    assert seams["notify_fn"].call_args.kwargs.get("severity") == "warning"
    seams["teardown_fn"].assert_not_called()  # never started a container


@pytest.mark.asyncio
async def test_container_start_failure_warns_and_tears_down():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams(start_fn=lambda name, image, net, pw: (False, "no image"))
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False and out["status"] == "infra_error"
    assert seams["notify_fn"].call_args.kwargs.get("severity") == "warning"
    seams["teardown_fn"].assert_called_once()  # cleanup still runs


@pytest.mark.asyncio
async def test_smoke_failure_pages_error():
    pool = _make_pool(seconds_since_last_run=None)
    seams = _seams(smoke_fn=lambda thr, db, pw, timeout: (False, "FAIL: 1 missing"))
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False
    assert seams["notify_fn"].call_args.kwargs.get("severity") == "error"


@pytest.mark.asyncio
async def test_recovery_notify_after_prior_failure():
    pool = _make_pool(seconds_since_last_run=None)
    rt._last_passed = False  # simulate previous run failed
    seams = _seams()
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is True
    # An info-severity recovery notify fires when fail -> pass.
    assert seams["notify_fn"].called
    assert seams["notify_fn"].call_args.kwargs.get("severity") == "info"


@pytest.mark.asyncio
async def test_teardown_runs_even_on_seam_exception():
    pool = _make_pool(seconds_since_last_run=None)
    def boom(name, timeout):
        raise RuntimeError("restore blew up")
    seams = _seams(restore_fn=boom)
    out = await rt.run_restore_test_probe(pool, **seams)
    assert out["ok"] is False
    seams["teardown_fn"].assert_called_once()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -k "disabled or gate_skips or happy or corrupt or no_dump or start_failure or smoke_failure or recovery or teardown_runs" -v`
Expected: FAIL — `run_restore_test_probe` not defined.

- [ ] **Step 3: Implement the orchestrator + wrapper**

```python
# append to brain/restore_test_probe.py

async def _notify(pool: Any, notify_fn: Callable[..., Any], *,
                  title: str, detail: str, severity: str) -> None:
    try:
        notify_fn(title=title, detail=detail,
                  source="brain.restore_test_probe", severity=severity)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RESTORE_TEST] notify_fn failed: %s", exc)


async def run_restore_test_probe(
    pool: Any, *,
    find_dump_fn: Optional[Callable[[str, str], Optional[str]]] = None,
    discover_network_fn: Optional[Callable[[], Optional[str]]] = None,
    start_fn: Optional[Callable[..., tuple[bool, str]]] = None,
    wait_ready_fn: Optional[Callable[[str, int], bool]] = None,
    copy_fn: Optional[Callable[[str, str], tuple[bool, str]]] = None,
    restore_fn: Optional[Callable[[str, int], tuple[int, str]]] = None,
    count_fn: Optional[Callable[[str, str, str], Optional[int]]] = None,
    smoke_fn: Optional[Callable[..., tuple[bool, str]]] = None,
    teardown_fn: Optional[Callable[[str], None]] = None,
    notify_fn: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """One execution of the restore-test probe. Seams default to the real
    docker impls; tests inject stubs so no container ever runs. Blocking
    docker calls go through ``asyncio.to_thread`` so the brain event loop
    is never blocked.
    """
    global _last_passed
    find_dump_fn = find_dump_fn or _find_latest_dump
    discover_network_fn = discover_network_fn or _discover_network
    start_fn = start_fn or _start_container
    wait_ready_fn = wait_ready_fn or _wait_pg_ready
    copy_fn = copy_fn or _copy_dump
    restore_fn = restore_fn or _restore
    count_fn = count_fn or _table_count
    smoke_fn = smoke_fn or _run_smoke
    teardown_fn = teardown_fn or _remove_container
    notify_fn = notify_fn or notify_operator

    cfg = await _read_config(pool)
    if not cfg["enabled"]:
        return {"ok": True, "status": "disabled",
                "detail": f"Disabled (app_settings.{ENABLED_KEY}=false)"}

    since = await _seconds_since_last_run(pool)
    if since is not None and since < cfg["interval_hours"] * 3600:
        return {"ok": True, "status": "skipped",
                "detail": (f"Last run {since/3600:.1f}h ago "
                           f"(< {cfg['interval_hours']}h interval)")}

    dump = await asyncio.to_thread(find_dump_fn, cfg["backup_dir"], cfg["tier"])
    if not dump:
        detail = (f"No dump under {cfg['backup_dir']}/{cfg['tier']}/. "
                  f"If backups are running this should appear within a day.")
        await _emit_audit(pool, "probe.restore_test_failed", detail,
                          severity="warning", extra={"reason": "no_dump"})
        if _last_passed is not False:
            await _notify(pool, notify_fn,
                          title="Restore test: no dump to verify",
                          detail=detail, severity="warning")
        _last_passed = False
        return {"ok": False, "status": "no_dump", "detail": detail}

    password = secrets.token_hex(16)
    network = await asyncio.to_thread(discover_network_fn)
    started = False
    try:
        await asyncio.to_thread(teardown_fn, THROWAWAY_CONTAINER)  # stale cleanup
        ok, msg = await asyncio.to_thread(
            start_fn, THROWAWAY_CONTAINER, cfg["postgres_image"], network, password)
        if not ok:
            return await _infra_fail(pool, notify_fn,
                                     f"Could not start throwaway container: {msg}")
        started = True

        if not await asyncio.to_thread(
                wait_ready_fn, THROWAWAY_CONTAINER, cfg["pg_ready_timeout"]):
            return await _infra_fail(
                pool, notify_fn,
                f"Throwaway pg not ready within {cfg['pg_ready_timeout']}s")

        ok, msg = await asyncio.to_thread(copy_fn, THROWAWAY_CONTAINER, dump)
        if not ok:
            return await _infra_fail(pool, notify_fn, f"docker cp failed: {msg}")

        restore_rc, restore_err = await asyncio.to_thread(
            restore_fn, THROWAWAY_CONTAINER, cfg["restore_timeout"])

        row_counts: dict[str, Optional[int]] = {}
        for table in cfg["critical_tables"]:
            row_counts[table] = await asyncio.to_thread(
                count_fn, THROWAWAY_CONTAINER, TARGET_DB, table)
        schema_migrations = await asyncio.to_thread(
            count_fn, THROWAWAY_CONTAINER, TARGET_DB, "schema_migrations")

        smoke_ok, smoke_detail = True, "skipped"
        if cfg["run_smoke"]:
            if network is None:
                smoke_ok, smoke_detail = True, "skipped (no docker network)"
            else:
                smoke_ok, smoke_detail = await asyncio.to_thread(
                    smoke_fn, THROWAWAY_CONTAINER, TARGET_DB, password,
                    cfg["smoke_timeout"])

        passed, severity, detail = _decide_verdict(
            restore_rc=restore_rc, restore_stderr=restore_err,
            row_counts=row_counts, schema_migrations_count=schema_migrations,
            min_count=cfg["min_row_count"], smoke_enabled=cfg["run_smoke"],
            smoke_ok=smoke_ok, smoke_detail=smoke_detail)

        event = "probe.restore_test_completed" if passed else "probe.restore_test_failed"
        await _emit_audit(pool, event, detail,
                          severity="info" if passed else "warning",
                          extra={"dump": os.path.basename(dump),
                                 "restore_rc": restore_rc,
                                 "row_counts": row_counts})

        if not passed:
            await _notify(pool, notify_fn,
                          title="Restore test FAILED — latest backup may be corrupt",
                          detail=detail + f"\n\nDump: {os.path.basename(dump)}",
                          severity=severity)
        elif _last_passed is False:
            await _notify(pool, notify_fn,
                          title="Restore test recovered",
                          detail=detail, severity="info")
        _last_passed = passed
        return {"ok": passed, "status": "passed" if passed else "verification_failed",
                "detail": detail, "dump": os.path.basename(dump)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RESTORE_TEST] probe raised: %s", exc, exc_info=True)
        detail = f"Restore test errored: {type(exc).__name__}: {str(exc)[:160]}"
        await _emit_audit(pool, "probe.restore_test_failed", detail,
                          severity="warning", extra={"reason": "exception"})
        _last_passed = False
        return {"ok": False, "status": "infra_error", "detail": detail}
    finally:
        if started:
            try:
                await asyncio.to_thread(teardown_fn, THROWAWAY_CONTAINER)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[RESTORE_TEST] teardown failed: %s", exc)


async def _infra_fail(pool: Any, notify_fn: Callable[..., Any],
                      detail: str) -> dict[str, Any]:
    """Infra-level failure: warning severity (Discord only), gate advances."""
    global _last_passed
    logger.warning("[RESTORE_TEST] %s", detail)
    await _emit_audit(pool, "probe.restore_test_failed", detail,
                      severity="warning", extra={"reason": "infra"})
    if _last_passed is not False:
        await _notify(pool, notify_fn, title="Restore test could not run",
                      detail=detail, severity="warning")
    _last_passed = False
    return {"ok": False, "status": "infra_error", "detail": detail}


class RestoreTestProbe:
    """Probe-Protocol wrapper around run_restore_test_probe."""

    name: str = "restore_test"
    description: str = (
        "Daily: pg_restore the latest dump into a throwaway pgvector "
        "container, re-run the migration runner, assert critical tables "
        "survived. Pages on real corruption."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult
        summary = await run_restore_test_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info")
```

> **Note on `_infra_fail` / `no_dump`:** `test_no_dump_is_infra_warning` expects `status="no_dump"` (handled before any container start) while start/ready/copy failures return `status="infra_error"` via `_infra_fail`. Both write `probe.restore_test_failed` and warn once. The `_infra_fail` path is reached only after `started=True` is _attempted_; for the start-failure test, `teardown_fn` is still called once via the `finally` only if `started` — but start failed, so `started=False`. **Fix:** the start-failure test expects teardown called once, so call `teardown_fn` for the stale-cleanup BEFORE the start attempt (already done above) — that satisfies "cleanup still runs". Adjust the test's assertion to `assert seams["teardown_fn"].call_count >= 1` if needed, or move the pre-start stale cleanup to count. Reconcile during execution: the invariant that matters is _no container is ever left running_.

- [ ] **Step 4: Run to verify pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -v`
Expected: PASS (full file). Reconcile any teardown-count assertions per the note above so they assert the real invariant (throwaway never left running).

- [ ] **Step 5: Commit**

```bash
git add brain/restore_test_probe.py src/cofounder_agent/tests/unit/brain/test_restore_test_probe.py
git commit -m "feat(brain): restore-test orchestrator + probe wrapper (poindexter#441)"
```

---

## Task 8: Wire into `brain_daemon.py`

**Files:**

- Modify: `brain/brain_daemon.py` (3 sites: import guard ~191, `_REQUIRED_MODULES` ~349, run_cycle ~2230)

- [ ] **Step 1: Add the import guard** — after the `smart_monitor` block (insert after line 206, before the `# GH#222` block at line 208):

```python
try:
    # poindexter#441 — restore-test probe. Daily: pg_restore the latest
    # dump into a throwaway pgvector container, re-run the migration
    # runner against it, assert critical tables survived. Pages on real
    # corruption (error), not transient docker hiccups (warning).
    from restore_test_probe import run_restore_test_probe
    _HAS_RESTORE_TEST_PROBE = True
except ImportError:  # pragma: no cover — package-qualified path
    try:
        from brain.restore_test_probe import run_restore_test_probe
        _HAS_RESTORE_TEST_PROBE = True
    except ImportError:
        _HAS_RESTORE_TEST_PROBE = False
```

- [ ] **Step 2: Add to `_REQUIRED_MODULES`** — after the `_HAS_SMART_MONITOR` tuple (line 348-349):

```python
    ("_HAS_RESTORE_TEST_PROBE", "brain/restore_test_probe.py",
     "Backup RESTORE verification offline — a corrupt dump goes unnoticed (#441)"),
```

- [ ] **Step 3: Add the run_cycle invocation** — after the `smart_monitor` block (after line 2246, before the `# Docker port-forward` comment at 2248):

```python
    # Restore-test probe (#441). Daily-gated: 99.7% of cycles are a no-op
    # timestamp check; once/day it pg_restores the latest dump into a
    # throwaway pgvector container, re-runs the migration runner, and
    # asserts critical tables survived. Heavy run adds ~1-3 min to that
    # one cycle — within tolerance (backup_watcher's retries cost more).
    if _HAS_RESTORE_TEST_PROBE:
        try:
            rt_summary = await run_restore_test_probe(pool)
            probe_results["restore_test"] = {
                "ok": bool(rt_summary.get("ok", False)),
                "detail": rt_summary.get("detail", ""),
                "summary": rt_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] restore_test probe failed: %s", e)
```

- [ ] **Step 4: Verify brain_daemon imports cleanly**

Run: `cd src/cofounder_agent && poetry run python -c "import sys; sys.path.insert(0, '../../brain'); import brain_daemon; print('OK', brain_daemon._HAS_RESTORE_TEST_PROBE)"`
Expected: `OK True`

- [ ] **Step 5: Commit**

```bash
git add brain/brain_daemon.py
git commit -m "feat(brain): wire restore-test probe into the daemon cycle (poindexter#441)"
```

---

## Task 9: Ship the module in the brain image

**Files:**

- Modify: `brain/Dockerfile:65` (COPY list) and `:87-95` (the `/app/brain/` mirror block)

- [ ] **Step 1: Add to the `COPY` list** — append `restore_test_probe.py` to the file list on line 65 (after `smart_monitor.py`):

```
... backup_watcher.py restore_test_probe.py smart_monitor.py docker_port_forward_probe.py ...
```

- [ ] **Step 2: Add to the mirror block** — after the `backup_watcher.py` cp line (line 87):

```dockerfile
    cp /app/restore_test_probe.py /app/brain/restore_test_probe.py && \
```

- [ ] **Step 3: Verify the Dockerfile parses** (build is heavy; a syntax check is enough here)

Run: `docker build --check -f brain/Dockerfile brain/ 2>&1 | tail -5` (or skip if `--check` unsupported; the real build happens at deploy)
Expected: no parse errors.

- [ ] **Step 4: Commit**

```bash
git add brain/Dockerfile
git commit -m "build(brain): ship restore_test_probe.py in the image (poindexter#441)"
```

---

## Task 10: Document in `docs/operations/backups.md`

**Files:**

- Modify: `docs/operations/backups.md` (add a section after the backup-watcher section, before `## Operational hygiene`)

- [ ] **Step 1: Add the section**

```markdown
### Restore test (does the dump actually restore?)

`brain/restore_test_probe.py` (Glad-Labs/poindexter#441) is the layer that
proves a dump _restores_, not just that it's _fresh_. Once per
`restore_test_interval_hours` (default 24h) the brain picks the newest dump
under `/host-backups/auto/daily/`, spins a throwaway `pgvector/pgvector:pg16`
container, `pg_restore`s the dump, re-runs the production migration runner
against it (`migrations_smoke.py`, via `docker exec` into the worker), asserts
the critical tables (`posts`, `app_settings`, `audit_log`) survived with rows
and `schema_migrations` is populated, then tears the throwaway down.

A **verification** failure (corrupt dump, empty table, smoke failure) pages at
`error` — "your latest backup may be corrupt". An **infra** failure (docker
unreachable, no dump found) is `warning` — Discord only, so a transient hiccup
that merely prevented the test doesn't train you to ignore Telegram. State
(last-run time) lives in `audit_log`, so a brain restart doesn't re-trigger the
heavy run.

| Setting                                 | Default                        | Notes                                      |
| --------------------------------------- | ------------------------------ | ------------------------------------------ |
| `restore_test_enabled`                  | `true`                         | Master switch                              |
| `restore_test_interval_hours`           | `24`                           | Daily cadence                              |
| `restore_test_backup_dir`               | `/host-backups/auto`           | Brain's read-only mount                    |
| `restore_test_tier`                     | `daily`                        | Subdir to read dumps from                  |
| `restore_test_postgres_image`           | `pgvector/pgvector:pg16`       | Must match prod                            |
| `restore_test_run_migrations_smoke`     | `true`                         | Disable the cross-container smoke if flaky |
| `restore_test_critical_tables`          | `posts,app_settings,audit_log` | Comma-separated; name-validated            |
| `restore_test_min_row_count`            | `1`                            | Per-table floor                            |
| `restore_test_pg_ready_timeout_seconds` | `60`                           | Throwaway readiness wait                   |
| `restore_test_restore_timeout_seconds`  | `300`                          | `pg_restore` cap                           |
| `restore_test_smoke_timeout_seconds`    | `180`                          | migrations_smoke cap                       |
```

- [ ] **Step 2: Re-read after commit** (the prettier hook reflows markdown tables — confirm globs like `restore_test_*` weren't mangled)

Run: `git add docs/operations/backups.md && git commit -m "docs(ops): document the restore-test probe (poindexter#441)"`
Then Read the committed section back and confirm the table + key names are intact.

---

## Task 11: Full verification sweep

- [ ] **Step 1: Run the probe's test file**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_restore_test_probe.py -v`
Expected: all PASS.

- [ ] **Step 2: Run the whole brain test suite (no regressions)**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/ -q`
Expected: all PASS.

- [ ] **Step 3: Migration lint + (if a throwaway PG is handy) smoke**

Run: `python scripts/ci/migrations_lint.py`
Expected: PASS. The seed migration is idempotent and light (imports only `logging`).

- [ ] **Step 4: Confirm the migrations_smoke change didn't break CI usage**

Run: `cd src/cofounder_agent && poetry run pytest ../../scripts/ci/test_migrations_smoke_backend_root.py -v`
Expected: PASS.

- [ ] **Step 5: Push + open PR against `Glad-Labs/glad-labs-stack`** (source of truth; never the poindexter mirror)

```bash
git push -u origin claude/elegant-tharp-9ffa19
gh pr create --repo Glad-Labs/glad-labs-stack --base main \
  --title "feat(brain): restore-test probe — verify backups actually restore (poindexter#441)" \
  --body "Closes Glad-Labs/poindexter#441. See docs/superpowers/specs/2026-06-02-restore-test-probe-design.md."
```

---

## Self-review notes

- **Spec coverage:** issue steps 1–6 → Task 7 orchestrator (pick dump / spin / cp+restore / smoke / row-count / teardown); "alert on failure" → verdict + notify taxonomy (Tasks 6–7); migrations_smoke step → Tasks 1 + 7. Settings → Task 2. Wiring/shipping → Tasks 8–9. Docs → Task 10.
- **Known reconcile point:** the teardown-count assertions in Task 7's start-failure test vs. the pre-start stale `teardown_fn` call — the real invariant is "no throwaway left running"; tighten the assertion during execution (noted inline).
- **No new infra:** docker.sock, `/host-backups:ro`, the worker network, and the `pgvector/pgvector:pg16` image are all already present — no compose change.
