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
    notify = MagicMock()
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory({}),
        git_runner=_git_runner_ok(),
        notify_fn=notify,
    )
    assert summary["ok"] is False
    assert summary["status"] == "failed"
    assert any("branch_drift_failed" in str(a) for (_q, a) in pool.executes)
    assert _executes_to(pool, "alert_events") == []
    # Config failure (missing token) must PAGE the operator — a canary that
    # can't run must not fail silently.
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_git_error_fails_loud():
    bdp._reset_state()
    pool = _make_pool()
    routes = {"/commits/main": _FakeResponse(200, {"sha": _MAIN_SHA})}
    notify = MagicMock()
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory(routes),
        git_runner=_git_runner_fail,
        notify_fn=notify,
    )
    assert summary["ok"] is False
    assert summary["status"] == "failed"
    assert _executes_to(pool, "alert_events") == []
    # Broken .git mount is a config failure — page the operator.
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_github_5xx_fails_loud():
    bdp._reset_state()
    pool = _make_pool()
    routes = {"/commits/main": _FakeResponse(503, {"message": "unavailable"})}
    notify = MagicMock()
    summary = await bdp.run_branch_drift_probe(
        pool,
        now_fn=_now_fn,
        http_client_factory=_client_factory(routes),
        git_runner=_git_runner_ok(),
        notify_fn=notify,
    )
    assert summary["ok"] is False
    assert summary["status"] == "failed"
    # Transient GitHub error stays audit-only — must NOT page (blip noise).
    notify.assert_not_called()


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


@pytest.mark.asyncio
async def test_default_client_factory_is_zero_arg_callable():
    # Regression guard: the production path uses the DEFAULT factory (no
    # injection), and the call site invokes it as `factory()`. The default
    # factory must therefore be zero-arg callable. The other tests inject a
    # token-accepting lambda and never exercise this path, so a TypeError
    # here would otherwise be invisible until prod.
    import httpx as _httpx  # brain/worker dependency, present in the test env

    factory = bdp._default_client_factory("tok")
    client = factory()  # must not raise (was: TypeError, 0-arg called with token)
    assert isinstance(client, _httpx.AsyncClient)
    await client.aclose()
