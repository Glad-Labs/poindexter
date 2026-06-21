"""Unit tests for brain/health_probes.py::probe_scheduled_tasks (#704).

The brain runs in a Linux container and can't enumerate the host's Windows
Task Scheduler, so the probe asks the host Recovery Agent (``GET /tasks``)
for the status of an operator-configured watch list, then pages when a
watched self-heal task is disabled, missing, or last-run-failed. It is
advisory (fail-open) when the agent URL/token are unset or the watch list is
empty — mirroring the host-recover fall-through in compose_drift_probe so an
un-configured operator never pages.

HTTP + settings I/O is mocked; the pool is a MagicMock with AsyncMock methods
seeded via ``setting_values``, mirroring test_mcp_http_probe.py. The probe is
a function-probe in the PROBES dict, so debounce/paging is the framework's
job (run_health_probes) — these tests only assert the per-cycle ok/detail.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from brain import health_probes as hp


def _make_pool(*, setting_values: dict[str, str] | None = None):
    """asyncpg-style mock pool that answers secret_reader.read_app_setting."""
    settings = dict(setting_values or {})
    pool = MagicMock()

    async def _fetchrow(query, *args):
        if "app_settings" in query and args:
            key = args[0]
            if key in settings:
                return {"value": settings[key], "is_secret": False}
            return None
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    return pool


def _http_factory(*, status_code=200, json_body=None, raise_exc=None):
    def factory():
        response = MagicMock()
        response.status_code = status_code
        response.json = MagicMock(return_value=json_body if json_body is not None else {})
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        if raise_exc is not None:
            client.get = AsyncMock(side_effect=raise_exc)
        else:
            client.get = AsyncMock(return_value=response)
        return client

    return factory


def _configured() -> dict[str, str]:
    return {
        hp.RECOVERY_URL_KEY: "http://host.docker.internal:9841/recover",
        hp.RECOVERY_TOKEN_KEY: "tok",
        hp.SCHED_TASKS_WATCH_KEY: "Poindexter MCP HTTP,DeployCheckoutSync",
    }


# --- pure evaluation -------------------------------------------------------


def test_evaluate_all_healthy():
    tasks = [
        {"name": "A", "exists": True, "enabled": True, "state": "Ready", "last_run_result": 0},
        {"name": "B", "exists": True, "enabled": True, "state": "Running", "last_run_result": 267009},
    ]
    ok, detail = hp._evaluate_scheduled_task_health(tasks)
    assert ok is True
    assert "healthy" in detail


def test_evaluate_disabled_task_unhealthy():
    tasks = [{"name": "A", "exists": True, "enabled": False, "state": "Disabled", "last_run_result": 0}]
    ok, detail = hp._evaluate_scheduled_task_health(tasks)
    assert ok is False
    assert "A: DISABLED" in detail


def test_evaluate_disabled_by_state_only_unhealthy():
    # Defensive: enabled flag absent but State reports Disabled.
    tasks = [{"name": "A", "exists": True, "enabled": None, "state": "Disabled", "last_run_result": 0}]
    ok, detail = hp._evaluate_scheduled_task_health(tasks)
    assert ok is False
    assert "DISABLED" in detail


def test_evaluate_failed_last_run_unhealthy():
    # 2147942402 = 0x80070002 (file not found) — a real failing HRESULT that
    # exceeds int32, so the parser must not choke on it.
    tasks = [{"name": "A", "exists": True, "enabled": True, "state": "Ready", "last_run_result": 2147942402}]
    ok, detail = hp._evaluate_scheduled_task_health(tasks)
    assert ok is False
    assert "last run failed" in detail and "2147942402" in detail


def test_evaluate_missing_task_unhealthy():
    tasks = [{"name": "Gone", "exists": False, "enabled": None, "state": None, "last_run_result": None}]
    ok, detail = hp._evaluate_scheduled_task_health(tasks)
    assert ok is False
    assert "Gone" in detail and "not found" in detail


def test_evaluate_has_not_run_is_healthy():
    # 267011 = SCHED_S_TASK_HAS_NOT_RUN — a fresh task that hasn't fired isn't a failure.
    tasks = [{"name": "A", "exists": True, "enabled": True, "state": "Ready", "last_run_result": 267011}]
    ok, _ = hp._evaluate_scheduled_task_health(tasks)
    assert ok is True


def test_evaluate_null_last_run_result_is_healthy():
    tasks = [{"name": "A", "exists": True, "enabled": True, "state": "Ready", "last_run_result": None}]
    ok, _ = hp._evaluate_scheduled_task_health(tasks)
    assert ok is True


def test_evaluate_lists_multiple_problems():
    tasks = [
        {"name": "A", "exists": True, "enabled": False, "state": "Disabled", "last_run_result": 0},
        {"name": "B", "exists": False, "enabled": None, "state": None, "last_run_result": None},
    ]
    ok, detail = hp._evaluate_scheduled_task_health(tasks)
    assert ok is False
    assert "2 watched task(s) unhealthy" in detail
    assert "A" in detail and "B" in detail


# --- _derive_tasks_url -----------------------------------------------------


def test_derive_tasks_url_swaps_recover_for_tasks():
    assert (
        hp._derive_tasks_url("http://host.docker.internal:9841/recover")
        == "http://host.docker.internal:9841/tasks"
    )


def test_derive_tasks_url_tolerates_trailing_slash():
    assert hp._derive_tasks_url("http://h:9841/recover/") == "http://h:9841/tasks"


# --- probe_scheduled_tasks (fail-open / advisory) --------------------------


@pytest.mark.asyncio
async def test_fail_open_when_url_unset():
    pool = _make_pool(setting_values={hp.RECOVERY_TOKEN_KEY: "tok", hp.SCHED_TASKS_WATCH_KEY: "A"})
    result = await hp.probe_scheduled_tasks(pool, http_client_factory=_http_factory())
    assert result["ok"] is True
    assert "advisory" in result["detail"]


@pytest.mark.asyncio
async def test_fail_open_when_token_unset():
    pool = _make_pool(
        setting_values={
            hp.RECOVERY_URL_KEY: "http://host.docker.internal:9841/recover",
            hp.SCHED_TASKS_WATCH_KEY: "A",
        }
    )
    result = await hp.probe_scheduled_tasks(pool, http_client_factory=_http_factory())
    assert result["ok"] is True
    assert "advisory" in result["detail"]


@pytest.mark.asyncio
async def test_fail_open_when_watch_list_empty():
    pool = _make_pool(
        setting_values={
            hp.RECOVERY_URL_KEY: "http://host.docker.internal:9841/recover",
            hp.RECOVERY_TOKEN_KEY: "tok",
        }
    )
    result = await hp.probe_scheduled_tasks(pool, http_client_factory=_http_factory())
    assert result["ok"] is True
    assert "no watched tasks" in result["detail"]


# --- probe_scheduled_tasks (end to end) ------------------------------------


@pytest.mark.asyncio
async def test_healthy_tasks_returns_ok():
    pool = _make_pool(setting_values=_configured())
    body = {
        "ok": True,
        "tasks": [
            {"name": "Poindexter MCP HTTP", "exists": True, "enabled": True, "state": "Ready", "last_run_result": 0},
            {"name": "DeployCheckoutSync", "exists": True, "enabled": True, "state": "Ready", "last_run_result": 0},
        ],
    }
    result = await hp.probe_scheduled_tasks(pool, http_client_factory=_http_factory(json_body=body))
    assert result["ok"] is True
    assert "healthy" in result["detail"]


@pytest.mark.asyncio
async def test_disabled_task_pages():
    pool = _make_pool(setting_values=_configured())
    body = {
        "ok": True,
        "tasks": [
            {"name": "Poindexter MCP HTTP", "exists": True, "enabled": False, "state": "Disabled", "last_run_result": 0},
        ],
    }
    result = await hp.probe_scheduled_tasks(pool, http_client_factory=_http_factory(json_body=body))
    assert result["ok"] is False
    assert "DISABLED" in result["detail"]
    assert "Poindexter MCP HTTP" in result.get("failed", [])


@pytest.mark.asyncio
async def test_failed_last_run_pages():
    # 2 = ERROR_FILE_NOT_FOUND — a real failure (1 = running/queued is an OK code).
    pool = _make_pool(setting_values=_configured())
    body = {
        "ok": True,
        "tasks": [
            {"name": "DeployCheckoutSync", "exists": True, "enabled": True, "state": "Ready", "last_run_result": 2},
        ],
    }
    result = await hp.probe_scheduled_tasks(pool, http_client_factory=_http_factory(json_body=body))
    assert result["ok"] is False
    assert "last run failed" in result["detail"]
    assert "exit 2" in result["detail"]


@pytest.mark.asyncio
async def test_agent_unreachable_pages():
    pool = _make_pool(setting_values=_configured())
    result = await hp.probe_scheduled_tasks(
        pool, http_client_factory=_http_factory(raise_exc=ConnectionError("refused"))
    )
    assert result["ok"] is False
    assert "unreachable" in result["detail"].lower()


@pytest.mark.asyncio
async def test_agent_non_2xx_pages():
    pool = _make_pool(setting_values=_configured())
    result = await hp.probe_scheduled_tasks(pool, http_client_factory=_http_factory(status_code=500))
    assert result["ok"] is False
    assert "500" in result["detail"]


@pytest.mark.asyncio
async def test_agent_payload_error_pages():
    # Agent reachable but its PowerShell query failed (ok:false body).
    pool = _make_pool(setting_values=_configured())
    body = {"ok": False, "error": "powershell not found"}
    result = await hp.probe_scheduled_tasks(pool, http_client_factory=_http_factory(json_body=body))
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_probe_sends_watch_list_as_name_params():
    pool = _make_pool(setting_values=_configured())
    captured: dict = {}

    def factory():
        response = MagicMock()
        response.status_code = 200
        response.json = MagicMock(return_value={"ok": True, "tasks": []})
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        async def _get(url, params=None, headers=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return response

        client.get = _get
        return client

    await hp.probe_scheduled_tasks(pool, http_client_factory=factory)
    assert captured["url"] == "http://host.docker.internal:9841/tasks"
    assert captured["params"] == {"name": ["Poindexter MCP HTTP", "DeployCheckoutSync"]}
    assert captured["headers"]["Authorization"] == "Bearer tok"
