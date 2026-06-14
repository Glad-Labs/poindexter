# Branch-Drift Deploy Canary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a brain-daemon probe that pages the operator when the bind-mounted prod checkout has fallen behind `origin/main` — the structural blind spot that hid the #355 half-cutover.

**Architecture:** A standalone brain probe (`brain/branch_drift_probe.py`, modeled on `brain/pr_staleness_probe.py`) reads the running checkout's HEAD SHA from a new read-only `.git` mount, gets `origin/main`'s SHA + the behind-count from the GitHub API (private repo via the `gh_token` secret), and writes an `alert_events` row + `audit_log` event when prod is behind. Alert-only; never auto-deploys. All I/O is behind injectable seams for unit tests.

**Tech Stack:** Python 3.12, `asyncpg`, `httpx`, `subprocess` (git CLI), Docker Compose, Postgres `app_settings`/`alert_events`/`alert_dedup_state`/`audit_log` tables.

**Spec:** `docs/superpowers/specs/2026-06-02-branch-drift-canary-design.md`

**Branch / PR target:** work on `claude/magical-joliot-9b0197`; PR to `Glad-Labs/glad-labs-stack` (source of truth), never the public `poindexter` mirror.

---

## File Structure

| File                                                                                   | Create/Modify                                                                              | Responsibility                                                                                                   |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `brain/branch_drift_probe.py`                                                          | Create                                                                                     | The probe: config read, git HEAD read, GitHub SHA/compare, drift classification, alert/dedup/audit, entry point. |
| `src/cofounder_agent/tests/unit/brain/test_branch_drift_probe.py`                      | Create                                                                                     | Unit tests for all probe paths (the #942 acceptance test lives here).                                            |
| `brain/brain_daemon.py`                                                                | Modify (import block ~line 108–117; `run_cycle` after the pr-staleness wrapper ~line 2349) | Import the probe (flat + `brain.`-qualified shim) and call it each cycle.                                        |
| `src/cofounder_agent/services/migrations/<ts>_seed_branch_drift_probe_app_settings.py` | Create (via `scripts/new-migration.py`)                                                    | Seed the 5 `app_settings` tunables.                                                                              |
| `brain/Dockerfile`                                                                     | Modify (apt line ~28; `COPY` ~line 65; mirror-cp block ~71–95)                             | Install `git`; bake the new probe file into the image.                                                           |
| `docker-compose.local.yml`                                                             | Modify (brain-daemon `volumes:` ~line 255–297)                                             | Add the read-only `./.git:/host-git:ro` mount.                                                                   |
| `docs/operations/ci-deploy-chain.md`                                                   | Modify (near the `deploy-worker.ps1` entry ~line 146)                                      | Document the canary as the deploy-drift backstop.                                                                |

---

## Task 1: The probe module `brain/branch_drift_probe.py` (+ unit tests)

**Files:**

- Create: `brain/branch_drift_probe.py`
- Test: `src/cofounder_agent/tests/unit/brain/test_branch_drift_probe.py`

- [ ] **Step 1: Write the failing test file**

Create `src/cofounder_agent/tests/unit/brain/test_branch_drift_probe.py`:

```python
"""Unit tests for brain/branch_drift_probe.py.

Covers the probe's decision paths:
1. On main (local HEAD == origin/main SHA) -> no alert, ok=True.
2. Behind main (compare ahead_by > 0) -> one alert_events row + audit,
   ok=False. SECOND cycle same (head,main) -> dedup-suppressed.
3. Unpushed local HEAD (compare 404) -> degraded drift alert, ok=False.
4. Disabled -> ok=True, no work.
5. Fail-loud: missing gh_token / GitHub 5xx / git error -> ok=False,
   probe.branch_drift_failed audit, no alert_events spam.
6. Cadence gate: second call within poll interval does no GitHub call.

All external I/O (asyncpg pool, GitHub via httpx, git via subprocess) is
mocked through the probe's injection seams.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import branch_drift_probe as bdp


_FIXED_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
_LOCAL_HEAD = "abbad234cfa31863c8c43b4587784771d9a76612"
_MAIN_SHA = "80d9f033ca20fd1987f3f2821488f0115562ed83"


def _now_fn():
    return _FIXED_NOW


def _default_settings() -> dict[str, str]:
    return {
        bdp.ENABLED_KEY: "true",
        bdp.POLL_INTERVAL_MINUTES_KEY: "15",
        bdp.REPO_KEY: "Test-Org/test-repo",
        bdp.DEDUP_HOURS_KEY: "6",
        bdp.GIT_DIR_KEY: "/host-git",
        "gh_token": "test-token",
    }


def _make_pool(
    *,
    setting_values: Optional[dict[str, str]] = None,
    deduped_fingerprints: Optional[set[str]] = None,
):
    """asyncpg-style mock pool. Records every execute() in pool.executes."""
    pool = MagicMock()
    settings = {**_default_settings(), **(setting_values or {})}
    deduped = deduped_fingerprints or set()
    pool.executes = []

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _fetchrow(query, *args):
        # secret_reader.read_app_setting uses fetchrow(SELECT value, is_secret)
        if "app_settings" in query and args:
            key = args[0]
            if key in settings:
                return {"value": settings[key], "is_secret": key == "gh_token"}
            return None
        if "alert_dedup_state" in query and args:
            fp = args[0]
            if fp in deduped:
                return {"last_seen_at": _FIXED_NOW}
            return None
        return None

    async def _execute(query, *args):
        pool.executes.append((query, args))
        return "INSERT 0 1"

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock(side_effect=_execute)
    return pool


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeClient:
    """Async-context-manager httpx stand-in driven by a routes dict."""

    def __init__(self, routes: dict[str, _FakeResponse]):
        self._routes = routes
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.calls.append(url)
        for pattern, resp in self._routes.items():
            if pattern in url:
                return resp
        return _FakeResponse(404, {"message": "Not Found"})


def _client_factory(routes: dict[str, _FakeResponse]):
    return lambda token=None: _FakeClient(routes)


def _git_runner_ok(head=_LOCAL_HEAD, branch="feat/issue-auto-triage"):
    """Return a git_runner(git_dir) -> (sha, branch) stub."""
    def _run(git_dir):
        return (head, branch)
    return _run


def _git_runner_fail(_git_dir):
    raise RuntimeError("git rev-parse failed: not a git repository")


def _executes_to(pool, table: str) -> list:
    return [q for (q, _a) in pool.executes if table in q]


@pytest.mark.asyncio
async def test_on_main_no_alert():
    bdp._reset_state()
    pool = _make_pool()
    routes = {"/commits/main": _FakeResponse(200, {"sha": _MAIN_SHA})}
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory(routes),
        git_runner=_git_runner_ok(head=_MAIN_SHA),
        notify_fn=MagicMock(),
    )
    assert summary["ok"] is True
    assert summary["status"] == "no_drift"
    assert summary["behind"] == 0
    assert _executes_to(pool, "alert_events") == []


@pytest.mark.asyncio
async def test_behind_main_emits_one_alert_then_dedupes():
    bdp._reset_state()
    pool = _make_pool()
    routes = {
        "/commits/main": _FakeResponse(200, {"sha": _MAIN_SHA}),
        "/compare/": _FakeResponse(200, {"status": "diverged", "ahead_by": 13, "behind_by": 12}),
    }
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory(routes),
        git_runner=_git_runner_ok(),
        notify_fn=MagicMock(),
    )
    assert summary["ok"] is False
    assert summary["status"] == "drift_detected"
    assert summary["behind"] == 13
    assert summary["alert_emitted"] is True
    assert len(_executes_to(pool, "alert_events")) == 1

    # Second cycle, same (head, main) pair, dedup row now fresh -> suppressed.
    bdp._reset_state()  # clear cadence gate so it re-runs immediately
    fp = bdp._fingerprint_for("Test-Org/test-repo", _LOCAL_HEAD, _MAIN_SHA)
    pool2 = _make_pool(deduped_fingerprints={fp})
    summary2 = await bdp.run_branch_drift_probe(
        pool2,
        now_fn=_now_fn,
        http_client_factory=_client_factory(routes),
        git_runner=_git_runner_ok(),
        notify_fn=MagicMock(),
    )
    assert summary2["ok"] is False
    assert summary2["alert_emitted"] is False
    assert _executes_to(pool2, "alert_events") == []


@pytest.mark.asyncio
async def test_unpushed_head_404_degraded_alert():
    bdp._reset_state()
    pool = _make_pool()
    routes = {
        "/commits/main": _FakeResponse(200, {"sha": _MAIN_SHA}),
        "/compare/": _FakeResponse(404, {"message": "Not Found"}),
    }
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory(routes),
        git_runner=_git_runner_ok(head="deadbeef" * 5),
        notify_fn=MagicMock(),
    )
    assert summary["ok"] is False
    assert summary["status"] == "drift_detected"
    assert summary["behind"] is None  # uncomputable
    assert len(_executes_to(pool, "alert_events")) == 1


@pytest.mark.asyncio
async def test_disabled_does_no_work():
    bdp._reset_state()
    pool = _make_pool(setting_values={bdp.ENABLED_KEY: "false"})
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory({}),
        git_runner=_git_runner_ok(),
        notify_fn=MagicMock(),
    )
    assert summary["ok"] is True
    assert summary["status"] == "disabled"
    assert _executes_to(pool, "alert_events") == []


@pytest.mark.asyncio
async def test_missing_token_fails_loud():
    bdp._reset_state()
    pool = _make_pool(setting_values={"gh_token": ""})
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory({}),
        git_runner=_git_runner_ok(),
        notify_fn=MagicMock(),
    )
    assert summary["ok"] is False
    assert summary["status"] == "failed"
    assert any("branch_drift_failed" in str(a) for (_q, a) in pool.executes)
    assert _executes_to(pool, "alert_events") == []


@pytest.mark.asyncio
async def test_git_error_fails_loud():
    bdp._reset_state()
    pool = _make_pool()
    routes = {"/commits/main": _FakeResponse(200, {"sha": _MAIN_SHA})}
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory(routes),
        git_runner=_git_runner_fail,
        notify_fn=MagicMock(),
    )
    assert summary["ok"] is False
    assert summary["status"] == "failed"
    assert _executes_to(pool, "alert_events") == []


@pytest.mark.asyncio
async def test_github_5xx_fails_loud():
    bdp._reset_state()
    pool = _make_pool()
    routes = {"/commits/main": _FakeResponse(503, {"message": "unavailable"})}
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory(routes),
        git_runner=_git_runner_ok(),
        notify_fn=MagicMock(),
    )
    assert summary["ok"] is False
    assert summary["status"] == "failed"


@pytest.mark.asyncio
async def test_cadence_gate_skips_within_interval():
    bdp._reset_state()
    pool = _make_pool()
    routes = {"/commits/main": _FakeResponse(200, {"sha": _MAIN_SHA})}
    factory_client = _FakeClient(routes)

    def _factory(token=None):
        return factory_client

    # First call does real work.
    await bdp.run_branch_drift_probe(
        pool, now_fn=_now_fn, http_client_factory=_factory,
        git_runner=_git_runner_ok(head=_MAIN_SHA), notify_fn=MagicMock(),
    )
    calls_after_first = len(factory_client.calls)
    # Second call 1 minute later -> within 15-min gate -> no GitHub round-trip.
    summary = await bdp.run_branch_drift_probe(
        pool, now_fn=lambda: _FIXED_NOW + timedelta(minutes=1),
        http_client_factory=_factory,
        git_runner=_git_runner_ok(head=_MAIN_SHA), notify_fn=MagicMock(),
    )
    assert summary["status"] == "skipped"
    assert len(factory_client.calls) == calls_after_first
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_branch_drift_probe.py -q`
Expected: collection/import error — `ModuleNotFoundError: No module named 'brain.branch_drift_probe'`.

- [ ] **Step 3: Write the probe module**

Create `brain/branch_drift_probe.py`:

```python
"""Branch-drift deploy canary (glad-labs-stack#942).

The worker/brain/pipeline-bot/prefect-worker containers bind-mount the
host checkout's source live, so prod runs whatever branch that checkout
sits on. When it's parked on a stale branch, merged work on origin/main
never reaches production — the drift that hid the #355 atom-cutover.

The existing migration-drift signals (services/metrics_exporter.py +
the worker /api/health migrations block) are structurally BLIND to this:
they compare on-disk migration files (the bind-mounted checkout) to
schema_migrations rows, so a migration that exists on origin/main but
not on the stale branch isn't even on disk to be counted.

This probe reads ground truth from OUTSIDE the bind-mount:

1. The running checkout's HEAD SHA from a read-only ``.git`` mount
   (``branch_drift_git_dir``, default ``/host-git``) via ``git rev-parse``.
   This is the one fact nothing else in a container can supply.
2. origin/main's SHA + the behind-count from the GitHub REST API
   (``branch_drift_repo``, authed with the ``gh_token`` secret — the repo
   is private). The mount is read-only on purpose: the probe never
   fetches inside the container.

Alert-only — it NEVER runs git pull/deploy itself (a checkout move can
clobber WIP or pull breaking changes mid-pipeline). It writes an
``alert_events`` row pointing at ``pwsh ./scripts/deploy-worker.ps1``.

Design parity with brain/pr_staleness_probe.py: DB-configurable through
app_settings, standalone (stdlib + asyncpg + httpx + the git binary),
fail-loud per feedback_no_silent_defaults, and injectable seams
(``now_fn`` / ``http_client_factory`` / ``git_runner`` / ``notify_fn``)
plus a ``_reset_state()`` test hook.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified path
    from brain.operator_notifier import notify_operator

try:
    from secret_reader import read_app_setting as _shared_read_app_setting
except ImportError:  # pragma: no cover — package-qualified path
    from brain.secret_reader import read_app_setting as _shared_read_app_setting

logger = logging.getLogger("brain.branch_drift_probe")


# ---------------------------------------------------------------------------
# app_settings keys — every tunable lives in the DB (operator-adjustable).
# Defaults match the seed migration.
# ---------------------------------------------------------------------------

ENABLED_KEY = "branch_drift_probe_enabled"
POLL_INTERVAL_MINUTES_KEY = "branch_drift_poll_interval_minutes"
REPO_KEY = "branch_drift_repo"
DEDUP_HOURS_KEY = "branch_drift_dedup_hours"
GIT_DIR_KEY = "branch_drift_git_dir"

TOKEN_SETTING_KEY = "gh_token"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 15
DEFAULT_REPO = "Glad-Labs/glad-labs-stack"
DEFAULT_DEDUP_HOURS = 6
DEFAULT_GIT_DIR = "/host-git"

PROBE_INTERVAL_SECONDS = 5 * 60

HTTP_CONNECT_TIMEOUT_S = 5.0
HTTP_READ_TIMEOUT_S = 15.0
GIT_TIMEOUT_S = 10


# ---------------------------------------------------------------------------
# Module-level state — cadence gate across cycles (reset on restart is fine;
# per-(head,main) dedup is persisted in alert_dedup_state for restart-safety).
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {"last_real_pass_at": None}


def _reset_state() -> None:
    """Test hook — clear the cadence-gate memory."""
    _state["last_real_pass_at"] = None


# ---------------------------------------------------------------------------
# app_settings reads (direct asyncpg, mirrors brain/pr_staleness_probe).
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BRANCH_DRIFT] Could not read %s: %s — using default %r",
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


async def _read_config(pool: Any) -> dict[str, Any]:
    enabled = _coerce_bool(
        await _read_setting(pool, ENABLED_KEY, "true"), DEFAULT_ENABLED
    )
    poll_interval_minutes = _coerce_int(
        await _read_setting(pool, POLL_INTERVAL_MINUTES_KEY, DEFAULT_POLL_INTERVAL_MINUTES),
        DEFAULT_POLL_INTERVAL_MINUTES,
    )
    dedup_hours = _coerce_int(
        await _read_setting(pool, DEDUP_HOURS_KEY, DEFAULT_DEDUP_HOURS),
        DEFAULT_DEDUP_HOURS,
    )
    repo = str(await _read_setting(pool, REPO_KEY, DEFAULT_REPO)).strip() or DEFAULT_REPO
    git_dir = str(await _read_setting(pool, GIT_DIR_KEY, DEFAULT_GIT_DIR)).strip() or DEFAULT_GIT_DIR
    return {
        "enabled": enabled,
        "poll_interval_minutes": poll_interval_minutes,
        "dedup_hours": dedup_hours,
        "repo": repo,
        "git_dir": git_dir,
    }


async def _read_token(pool: Any) -> str:
    val = await _shared_read_app_setting(pool, TOKEN_SETTING_KEY, default="")
    if val:
        return str(val).strip()
    return os.getenv("GITHUB_TOKEN", "").strip()


# ---------------------------------------------------------------------------
# Local HEAD via the read-only .git mount (pure read, no network).
# ---------------------------------------------------------------------------


def _read_local_head(git_dir: str) -> tuple[str, str]:
    """Return (head_sha, branch_name) from the mounted git dir.

    Raises RuntimeError on any git failure (missing mount, not a repo,
    git binary absent) so the caller fails loud.
    """
    def _git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", "--git-dir", git_dir, *args],
                capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("git binary not on PATH in brain image") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"git {' '.join(args)} timed out") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} exit {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:200]}"
            )
        return (proc.stdout or "").strip()

    head = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if not head:
        raise RuntimeError("git rev-parse HEAD returned empty")
    return head, branch


# ---------------------------------------------------------------------------
# GitHub REST client (thin). Default factory builds an authed httpx client.
# ---------------------------------------------------------------------------


def _default_client_factory(token: str):
    def _make():
        return httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "Poindexter-BranchDriftProbe/1.0",
            },
            timeout=httpx.Timeout(HTTP_READ_TIMEOUT_S, connect=HTTP_CONNECT_TIMEOUT_S),
        )
    return _make


async def _fetch_main_sha(client: Any, repo: str) -> str:
    r = await client.get(f"https://api.github.com/repos/{repo}/commits/main")
    if r.status_code != 200:
        raise RuntimeError(
            f"GitHub /commits/main returned {r.status_code}: {(r.text or '')[:200]}"
        )
    data = r.json()
    sha = data.get("sha") if isinstance(data, dict) else None
    if not sha:
        raise RuntimeError("GitHub /commits/main returned no sha")
    return str(sha)


async def _compare_commits(client: Any, repo: str, base: str, head: str) -> Optional[dict[str, Any]]:
    """Return the compare payload, or None when GitHub can't resolve the
    pair (404 — typically an unpushed local HEAD). Raise on other errors."""
    r = await client.get(f"https://api.github.com/repos/{repo}/compare/{base}...{head}")
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(
            f"GitHub /compare returned {r.status_code}: {(r.text or '')[:200]}"
        )
    data = r.json()
    return data if isinstance(data, dict) else None


def _classify_drift(local_head: str, main_sha: str, compare: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Decide whether prod is behind origin/main.

    compare is GET /compare/{local_head}...{main_sha}: its ``ahead_by`` is
    the number of commits main has that local_head lacks = the behind count.
    None means GitHub couldn't resolve the pair (unpushed HEAD) -> drifted
    with an uncomputable count.
    """
    if local_head == main_sha:
        return {"drifted": False, "behind": 0, "branch_status": "on_main"}
    if compare is None:
        return {"drifted": True, "behind": None, "branch_status": "unknown_head"}
    behind = compare.get("ahead_by")
    behind = int(behind) if isinstance(behind, int) else 0
    if behind > 0:
        return {"drifted": True, "behind": behind, "branch_status": compare.get("status", "diverged")}
    # Differing SHAs but main is not ahead -> prod is ahead (unmerged local
    # work) or identical. Not "behind" — don't page.
    return {"drifted": False, "behind": 0, "branch_status": compare.get("status", "ahead")}


# ---------------------------------------------------------------------------
# Dedup (alert_dedup_state) — per (repo, local_head, main_sha) fingerprint.
# ---------------------------------------------------------------------------


def _fingerprint_for(repo: str, local_head: str, main_sha: str) -> str:
    return f"branch_drift_{repo}_{local_head[:12]}_{main_sha[:12]}"


async def _is_deduped(pool: Any, *, fingerprint: str, now_utc: datetime, dedup_hours: int) -> bool:
    if dedup_hours <= 0:
        return False
    try:
        row = await pool.fetchrow(
            "SELECT last_seen_at FROM alert_dedup_state WHERE fingerprint = $1",
            fingerprint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[BRANCH_DRIFT] dedup lookup failed for %s: %s", fingerprint, exc)
        return False
    if not row:
        return False
    last_seen = row["last_seen_at"]
    if not isinstance(last_seen, datetime):
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return (now_utc - last_seen) < timedelta(hours=dedup_hours)


async def _record_dedup(pool: Any, *, fingerprint: str, now_utc: datetime, message: str) -> None:
    try:
        await pool.execute(
            """
            INSERT INTO alert_dedup_state (
                fingerprint, first_seen_at, last_seen_at, repeat_count,
                severity, source, sample_message
            ) VALUES ($1, $2, $2, 1, 'warning', 'brain.branch_drift_probe', $3)
            ON CONFLICT (fingerprint) DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                repeat_count = alert_dedup_state.repeat_count + 1
            """,
            fingerprint, now_utc, message[:300],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BRANCH_DRIFT] dedup upsert failed for %s: %s", fingerprint, exc)


# ---------------------------------------------------------------------------
# audit_log + alert_events writes.
# ---------------------------------------------------------------------------


async def _emit_audit_event(pool: Any, event: str, detail: str, *, extra: Optional[dict[str, Any]] = None, severity: str = "info") -> None:
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ($1, $2, $3::jsonb, $4)",
            event, "brain.branch_drift_probe", json.dumps(payload), severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[BRANCH_DRIFT] audit_log insert failed: %s", exc)


async def _emit_drift_alert(pool: Any, *, repo: str, branch: str, local_head: str, main_sha: str, behind: Optional[int]) -> bool:
    behind_txt = f"{behind} commit(s) behind" if behind is not None else "behind (count unknown — HEAD not on origin)"
    alertname = f"branch_drift_{repo.replace('/', '_')}"
    labels = {
        "source": "brain.branch_drift_probe",
        "category": "branch_drift",
        "repo": repo,
        "branch": branch,
    }
    annotations = {
        "summary": f"prod checkout is {behind_txt} origin/main in {repo}",
        "description": (
            f"\U0001F7E1 [branch-drift] The running checkout is on '{branch}' "
            f"@ {local_head[:9]}, {behind_txt} origin/main @ {main_sha[:9]}.\n"
            f"Merged work is NOT deployed. Bring prod to main with:\n"
            f"    pwsh ./scripts/deploy-worker.ps1"
        ),
        "local_head": local_head,
        "main_sha": main_sha,
    }
    fingerprint = f"branch-drift-{alertname}-{local_head[:12]}-{main_sha[:12]}"
    try:
        await pool.execute(
            "INSERT INTO alert_events (alertname, severity, status, labels, "
            "annotations, starts_at, fingerprint) VALUES "
            "($1, 'warning', 'firing', $2::jsonb, $3::jsonb, NOW(), $4)",
            alertname, json.dumps(labels), json.dumps(annotations), fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BRANCH_DRIFT] failed to write firing alert %s: %s", alertname, exc)
        return False


# ---------------------------------------------------------------------------
# Top-level probe entry point.
# ---------------------------------------------------------------------------


async def run_branch_drift_probe(
    pool: Any,
    *,
    now_fn: Optional[Callable[[], datetime]] = None,
    notify_fn: Optional[Callable[..., Any]] = None,
    http_client_factory: Optional[Callable[..., Any]] = None,
    git_runner: Optional[Callable[[str], tuple[str, str]]] = None,
) -> dict[str, Any]:
    """One execution of the branch-drift canary; returns a structured summary."""
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    notify_fn = notify_fn or notify_operator
    git_runner = git_runner or _read_local_head

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "behind": 0, "alert_emitted": False,
                "detail": f"branch-drift probe disabled (app_settings.{ENABLED_KEY}=false)"}

    now_utc = now_fn()

    # Cadence gate — only do the real round-trip every poll_interval_minutes.
    last = _state["last_real_pass_at"]
    if isinstance(last, datetime):
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now_utc - last) < timedelta(minutes=config["poll_interval_minutes"]):
            return {"ok": True, "status": "skipped", "behind": 0, "alert_emitted": False,
                    "detail": "within poll interval"}

    async def _fail(detail: str) -> dict[str, Any]:
        logger.warning("[BRANCH_DRIFT] %s", detail)
        await _emit_audit_event(pool, "probe.branch_drift_failed", detail, severity="warning")
        return {"ok": False, "status": "failed", "behind": None, "alert_emitted": False, "detail": detail}

    token = await _read_token(pool)
    if not token:
        return await _fail(
            f"gh_token missing — cannot query private repo {config['repo']}. "
            f"Set it with `poindexter settings set gh_token <token> --secret`."
        )

    # Local HEAD (mounted .git, no network).
    try:
        local_head, branch = git_runner(config["git_dir"])
    except Exception as exc:  # noqa: BLE001
        return await _fail(f"could not read local HEAD from {config['git_dir']}: {exc}")

    # origin/main truth + compare (GitHub API).
    factory = http_client_factory or _default_client_factory(token)
    try:
        async with factory(token) as client:
            main_sha = await _fetch_main_sha(client, config["repo"])
            compare = None
            if local_head != main_sha:
                compare = await _compare_commits(client, config["repo"], local_head, main_sha)
    except Exception as exc:  # noqa: BLE001
        return await _fail(f"GitHub API error for {config['repo']}: {exc}")

    _state["last_real_pass_at"] = now_utc

    verdict = _classify_drift(local_head, main_sha, compare)
    if not verdict["drifted"]:
        await _emit_audit_event(
            pool, "probe.branch_drift_ok",
            f"on main: HEAD {local_head[:9]} == origin/main {main_sha[:9]}",
        )
        return {"ok": True, "status": "no_drift", "behind": 0, "alert_emitted": False,
                "branch": branch, "local_head": local_head, "main_sha": main_sha,
                "detail": "checkout matches origin/main"}

    behind = verdict["behind"]
    behind_txt = f"{behind} behind" if behind is not None else "behind (uncomputable)"
    detail = f"branch '{branch}' @ {local_head[:9]} is {behind_txt} origin/main @ {main_sha[:9]}"

    fingerprint = _fingerprint_for(config["repo"], local_head, main_sha)
    if await _is_deduped(pool, fingerprint=fingerprint, now_utc=now_utc, dedup_hours=config["dedup_hours"]):
        logger.info("[BRANCH_DRIFT] drift unchanged (%s) — dedup-suppressed", fingerprint)
        return {"ok": False, "status": "drift_detected", "behind": behind, "alert_emitted": False,
                "branch": branch, "local_head": local_head, "main_sha": main_sha, "detail": detail}

    await _emit_audit_event(pool, "probe.branch_drift_detected", detail,
                            extra={"branch": branch, "behind": behind}, severity="warning")
    emitted = await _emit_drift_alert(
        pool, repo=config["repo"], branch=branch, local_head=local_head,
        main_sha=main_sha, behind=behind,
    )
    if emitted:
        await _record_dedup(pool, fingerprint=fingerprint, now_utc=now_utc, message=detail)

    return {"ok": False, "status": "drift_detected", "behind": behind, "alert_emitted": emitted,
            "branch": branch, "local_head": local_head, "main_sha": main_sha, "detail": detail}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_branch_drift_probe.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add brain/branch_drift_probe.py src/cofounder_agent/tests/unit/brain/test_branch_drift_probe.py
git commit -m "feat(brain): branch-drift deploy canary probe (glad-labs-stack#942)"
```

---

## Task 2: Wire the probe into `brain_daemon.py`

**Files:**

- Modify: `brain/brain_daemon.py` (import shim near line 108–117; `run_cycle` call after the pr-staleness wrapper near line 2349)

- [ ] **Step 1: Add the import shim**

Find the compose-drift import shim (around lines 108–117) and add this block right after it:

```python
    # glad-labs-stack#942 — branch-drift deploy canary. Detects when the
    # bind-mounted prod checkout has fallen behind origin/main (the blind
    # spot the on-disk migration gauges can't see). Internal cadence gate.
    try:
        from branch_drift_probe import run_branch_drift_probe
        _HAS_BRANCH_DRIFT_PROBE = True
    except ImportError:  # pragma: no cover — package-qualified for tests
        try:
            from brain.branch_drift_probe import run_branch_drift_probe
            _HAS_BRANCH_DRIFT_PROBE = True
        except ImportError:
            _HAS_BRANCH_DRIFT_PROBE = False
```

(Match the surrounding shim style; if the siblings set their `_HAS_*` flag differently, mirror that exact pattern.)

- [ ] **Step 2: Add the run_cycle call**

In `run_cycle`, immediately after the `if _HAS_PR_STALENESS_PROBE:` block (ends ~line 2348), add:

```python
    # Branch-drift canary (glad-labs-stack#942). Dispatched every cycle;
    # the probe's internal cadence gate keeps the GitHub round-trip to
    # ~every 15 min. Alert-only — never auto-deploys.
    if _HAS_BRANCH_DRIFT_PROBE:
        try:
            bd_summary = await run_branch_drift_probe(pool)
            probe_results["branch_drift"] = {
                "ok": bool(bd_summary.get("ok", False)),
                "detail": bd_summary.get("detail", ""),
                "summary": bd_summary,
            }
        except Exception as e:
            logger.warning("[BRAIN] branch_drift probe failed: %s", e)
```

- [ ] **Step 3: Verify import resolves**

Run: `cd src/cofounder_agent && poetry run python -c "import sys; sys.path.insert(0, '../../brain'); import branch_drift_probe; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add brain/brain_daemon.py
git commit -m "feat(brain): dispatch branch-drift canary each cycle (glad-labs-stack#942)"
```

---

## Task 3: Seed migration for the app_settings tunables

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_seed_branch_drift_probe_app_settings.py`

- [ ] **Step 1: Generate the migration file**

Run: `cd src/cofounder_agent && python scripts/new-migration.py "seed branch drift probe app settings"`
This prints the created path (timestamped). Open it and replace its body with Step 2.

- [ ] **Step 2: Write the migration body**

Keep imports light (only `logging` — no service imports; the migrations-smoke env runs without langchain/langgraph):

```python
"""Migration: seed branch-drift canary app_settings (glad-labs-stack#942).

Five DB tunables for brain/branch_drift_probe.py. Idempotent
(ON CONFLICT DO NOTHING) so it no-ops on Matt's already-running prod.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ROWS = [
    ("branch_drift_probe_enabled", "true", "monitoring",
     "Master switch for the brain branch-drift deploy canary (#942). When true, the brain pages the operator if the bind-mounted prod checkout falls behind origin/main."),
    ("branch_drift_poll_interval_minutes", "15", "monitoring",
     "Internal cadence gate (minutes) for the branch-drift canary's GitHub round-trip. The probe is dispatched every brain cycle (~5 min) but does real work only this often."),
    ("branch_drift_repo", "Glad-Labs/glad-labs-stack", "monitoring",
     "owner/name of the source-of-truth repo the branch-drift canary compares against. Paired with the gh_token secret for private-repo access."),
    ("branch_drift_dedup_hours", "6", "monitoring",
     "Re-page interval (hours) for an unchanged branch-drift state. Dedup is keyed on (repo, local HEAD, origin/main SHA); a new commit on either side re-pages immediately."),
    ("branch_drift_git_dir", "/host-git", "monitoring",
     "git --git-dir path inside the brain container for reading the running checkout's HEAD. Matches the read-only ./.git:/host-git:ro mount in docker-compose.local.yml."),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description in _ROWS:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
    logger.info("Migration seed_branch_drift_probe_app_settings: applied (%d keys)", len(_ROWS))


async def down(pool) -> None:
    keys = [r[0] for r in _ROWS]
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM app_settings WHERE key = ANY($1::text[])", keys)
    logger.info("Migration seed_branch_drift_probe_app_settings down: removed %d keys", len(keys))
```

- [ ] **Step 3: Lint the migration**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_lint.py`
Expected: no collisions / interface errors reported for the new file.

- [ ] **Step 4: Smoke the migration against a fresh DB**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_smoke.py`
Expected: the new migration applies cleanly (exit 0).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/migrations/
git commit -m "feat(migrations): seed branch-drift canary app_settings (glad-labs-stack#942)"
```

---

## Task 4: Install `git` + bake the probe into the brain image

**Files:**

- Modify: `brain/Dockerfile` (apt line ~28; `COPY` list ~line 65; mirror-cp block ~71–95)

- [ ] **Step 1: Add `git` to the apt install line**

Change line ~28 from:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
```

to (adds `git`; note `git` must NOT be removed in the later `apt-get remove -y curl` cleanup — only curl is removed, so `git` survives):

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git && \
```

- [ ] **Step 2: Add the probe to the COPY list**

In the `COPY ... ./` line (~65), append `branch_drift_probe.py` to the file list (before `./`):

```dockerfile
COPY brain_daemon.py health_probes.py business_probes.py bootstrap.py operator_notifier.py operator_url_probe.py migration_drift_probe.py compose_drift_probe.py prometheus_secret_writer.py probe_interface.py seed_loader.py docker_utils.py alert_sync.py alert_dispatcher.py secret_reader.py oauth_client.py glitchtip_triage_probe.py prefect_stuck_flow_probe.py backup_watcher.py smart_monitor.py docker_port_forward_probe.py gate_auto_expire_probe.py gate_pending_summary_probe.py pr_staleness_probe.py discord_bot_probe.py mcp_http_probe.py branch_drift_probe.py ./
```

- [ ] **Step 3: Add the probe to the `/app/brain/` mirror block**

In the mirror-cp `RUN` block (~71–95), add a line alongside the others (before `touch /app/brain/__init__.py`):

```dockerfile
    cp /app/branch_drift_probe.py /app/brain/branch_drift_probe.py && \
```

- [ ] **Step 4: Verify the Dockerfile builds**

Run: `docker compose -f docker-compose.local.yml build brain-daemon`
Expected: build succeeds; the `git` install layer completes. (Confirm git is present: `docker run --rm --entrypoint git poindexter-brain-daemon --version` after the build — prints a git version.)

- [ ] **Step 5: Commit**

```bash
git add brain/Dockerfile
git commit -m "feat(brain): install git + bake branch_drift_probe into the image (glad-labs-stack#942)"
```

---

## Task 5: Mount the host `.git` read-only into the brain container

**Files:**

- Modify: `docker-compose.local.yml` (brain-daemon `volumes:` block, ~255–297)

- [ ] **Step 1: Add the mount**

In the `brain-daemon:` service's `volumes:` list, add (after the existing `./docker-compose.local.yml:/app/docker-compose.local.yml:ro` entry):

```yaml
# glad-labs-stack#942 — read-only host .git so brain/branch_drift_probe.py
# can read the running checkout's HEAD SHA (the one fact nothing else in a
# container can supply) and detect when prod has fallen behind origin/main.
# Top-level mount — NOT the forbidden ./.git:/app/.git:ro WORKER mount
# (that one broke under the worker's :ro /app via overlayfs child-mount
# rejection, #348). The brain's /app is the image filesystem, not a :ro
# bind, and the brain runs as root, so a read-only .git mount here is safe.
# Read-only on purpose: the probe never fetches/writes; it reads local HEAD
# and gets origin/main from the GitHub API.
- ./.git:/host-git:ro
```

Leave the worker service's existing "do NOT re-add this mount" note untouched — it remains correct for the worker.

- [ ] **Step 2: Validate compose config**

Run: `docker compose -f docker-compose.local.yml config --quiet`
Expected: no error (valid YAML + schema).

- [ ] **Step 3: Recreate the brain + verify the mount is readable**

Run:

```bash
docker compose -f docker-compose.local.yml up -d brain-daemon
docker exec poindexter-brain-daemon git --git-dir /host-git rev-parse --abbrev-ref HEAD
```

Expected: prints the host checkout's current branch (e.g. `main`), proving the mount + git binary work end-to-end inside the container.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.local.yml
git commit -m "feat(infra): mount host .git read-only into brain for drift canary (glad-labs-stack#942)"
```

---

## Task 6: Document the canary in the deploy chain

**Files:**

- Modify: `docs/operations/ci-deploy-chain.md` (near the `deploy-worker.ps1` reference, ~line 146)

- [ ] **Step 1: Add the paragraph**

After the `pwsh ./scripts/deploy-worker.ps1` reference, add:

```markdown
**Deploy-drift canary (glad-labs-stack#942).** Because the worker/brain
bind-mount the host checkout, "merged on main" does not mean "running in
prod" until you run the deploy above. The brain's `branch_drift_probe`
closes that loop: every ~15 min it reads the running checkout's HEAD from
a read-only `.git` mount, compares it to `origin/main` via the GitHub API,
and pages the operator (Telegram/Discord) when prod is behind — the signal
the on-disk `unapplied_migrations` gauge is structurally blind to. It is
alert-only; the remedy it points at is `pwsh ./scripts/deploy-worker.ps1`.
Tunables: `branch_drift_probe_enabled`, `branch_drift_poll_interval_minutes`,
`branch_drift_repo`, `branch_drift_dedup_hours`, `branch_drift_git_dir`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/ci-deploy-chain.md
git commit -m "docs(ops): document branch-drift canary in ci-deploy-chain (glad-labs-stack#942)"
```

---

## Task 7: Full verification + push

- [ ] **Step 1: Run the probe's unit tests once more**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/test_branch_drift_probe.py -q`
Expected: 8 passed.

- [ ] **Step 2: Run the brain test suite to catch regressions**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/brain/ -q`
Expected: all green (no import/wiring breakage from the brain_daemon edit).

- [ ] **Step 3: Live end-to-end sanity (optional, on the host)**

Temporarily set the probe to compare against a ref you know is ahead, or simply confirm the happy path on `main`:

```bash
docker exec poindexter-brain-daemon python -c "import asyncio,asyncpg,os; import branch_drift_probe as b; \
print(asyncio.run(b.run_branch_drift_probe.__wrapped__ if hasattr(b.run_branch_drift_probe,'__wrapped__') else (lambda:0)()))" 2>/dev/null || true
```

Better: tail the next brain cycle and confirm a `probe.branch_drift_ok` audit row appears:

```bash
docker exec poindexter-worker python - <<'PY'
import asyncio, asyncpg, os
async def m():
    c = await asyncpg.connect(os.environ.get("DATABASE_URL") or os.environ.get("LOCAL_DATABASE_URL"))
    rows = await c.fetch("SELECT event_type, details, created_at FROM audit_log WHERE event_type LIKE 'probe.branch_drift%' ORDER BY created_at DESC LIMIT 5")
    for r in rows: print(r["created_at"], r["event_type"], r["details"])
    await c.close()
asyncio.run(m())
PY
```

Expected: a recent `probe.branch_drift_ok` (host is on main) — confirming the probe runs in prod without erroring.

- [ ] **Step 4: Push the branch + open the PR**

```bash
git push -u origin claude/magical-joliot-9b0197
gh pr create --repo Glad-Labs/glad-labs-stack --base main \
  --title "feat(brain): branch-drift deploy canary (glad-labs-stack#942)" \
  --body "Closes the canary half of #942. Adds brain/branch_drift_probe.py: reads the running checkout HEAD from a read-only .git mount, compares to origin/main via the GitHub API (gh_token), pages on drift. Alert-only. Deploy: docker compose build brain-daemon && up -d brain-daemon. Spec: docs/superpowers/specs/2026-06-02-branch-drift-canary-design.md"
```

- [ ] **Step 5: Note the deploy requirement in the PR**

This change requires a **brain image rebuild** (`docker compose build brain-daemon && up -d brain-daemon`), not just `deploy-worker.ps1`'s restart — the Dockerfile + compose changed. Call this out in the PR so the host-side deploy does the rebuild.

---

## Self-Review

**Spec coverage:**

- Component `branch_drift_probe.py` (algorithm, lifecycle, config, alert/dedup, fail-loud) → Task 1. ✓
- Read-only `.git` mount → Task 5. ✓
- `git` in brain image + COPY/mirror → Task 4. ✓
- Seed migration (5 keys) → Task 3. ✓
- brain_daemon wiring → Task 2. ✓
- Tests incl. the #942 acceptance "a branch behind trips the canary" → Task 1 `test_behind_main_emits_one_alert_then_dedupes`. ✓
- Doc in ci-deploy-chain.md → Task 6. ✓
- Alert-only / never auto-deploy → enforced in `run_branch_drift_probe` (no restart/deploy path) + documented. ✓
- Optional migration-count enrichment → intentionally dropped (YAGNI; not load-bearing for the gate). Noted here so it isn't a silent gap.

**Type/name consistency:** constants (`ENABLED_KEY`, `POLL_INTERVAL_MINUTES_KEY`, `REPO_KEY`, `DEDUP_HOURS_KEY`, `GIT_DIR_KEY`) match between the test (Step 1) and module (Step 3). `_fingerprint_for(repo, local_head, main_sha)` signature matches its test call. `run_branch_drift_probe` seam kwargs (`now_fn`, `notify_fn`, `http_client_factory`, `git_runner`) match the test invocations. Summary `status` strings (`disabled`/`skipped`/`no_drift`/`drift_detected`/`failed`) match the asserts.

**Placeholder scan:** no TBD/TODO; every code step has complete code; commands have expected output.
