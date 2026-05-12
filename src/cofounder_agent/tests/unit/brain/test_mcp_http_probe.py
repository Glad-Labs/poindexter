"""Unit tests for brain/mcp_http_probe.py (poindexter#434)."""

from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import mcp_http_probe as mhp


def _default_settings() -> dict[str, str]:
    return {
        mhp.ENABLED_KEY: "true",
        mhp.POLL_INTERVAL_MINUTES_KEY: "5",
        mhp.HTTP_TIMEOUT_SECONDS_KEY: "3",
        mhp.DEDUP_HOURS_KEY: "1",
        mhp.BASE_URL_KEY: "http://127.0.0.1:8004",
        mhp.DISCOVERY_PATH_KEY: "/.well-known/oauth-protected-resource",
        mhp.LAUNCHER_PATH_KEY: "",
        mhp.RESTART_CAP_KEY: "3",
        mhp.RESTART_WINDOW_MINUTES_KEY: "60",
    }


def _make_pool(*, setting_values: Optional[dict[str, str]] = None):
    settings = {**_default_settings(), **(setting_values or {})}
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


def _make_http_factory(*, status_code: int = 200, raise_exc: Exception | None = None):
    def factory():
        response = MagicMock()
        response.status_code = status_code
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        if raise_exc is not None:
            client.get = AsyncMock(side_effect=raise_exc)
        else:
            client.get = AsyncMock(return_value=response)
        return client

    return factory


def _alert_rows(pool) -> list:
    out = []
    for call in pool.execute.call_args_list:
        sql = call.args[0] if call.args else ""
        if "INSERT INTO alert_events" in sql:
            out.append(call.args)
    return out


@pytest.fixture(autouse=True)
def _reset_probe_state():
    mhp._reset_state()
    yield
    mhp._reset_state()


class TestMcpHttpProbe:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits(self):
        pool = _make_pool(setting_values={mhp.ENABLED_KEY: "false"})
        result = await mhp.run_mcp_http_probe(
            pool, http_client_factory=_make_http_factory(), now_fn=lambda: 0.0,
        )
        assert result == {"ok": True, "status": "disabled", "detail": "mcp_http_probe disabled"}

    @pytest.mark.asyncio
    async def test_200_returns_ok(self):
        pool = _make_pool()
        result = await mhp.run_mcp_http_probe(
            pool, http_client_factory=_make_http_factory(status_code=200), now_fn=lambda: 0.0,
        )
        assert result["ok"] is True
        assert result["status"] == "ok"
        assert result["status_code"] == 200
        assert result["url"].endswith("/.well-known/oauth-protected-resource")
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_503_writes_alert(self):
        pool = _make_pool()
        result = await mhp.run_mcp_http_probe(
            pool, http_client_factory=_make_http_factory(status_code=503), now_fn=lambda: 0.0,
        )
        assert result["ok"] is False
        assert result["status"] == "unreachable"
        assert result["status_code"] == 503
        rows = _alert_rows(pool)
        assert len(rows) == 1
        assert rows[0][3] == "discord"  # channel_hint

    @pytest.mark.asyncio
    async def test_network_error_writes_alert(self):
        pool = _make_pool()
        result = await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(raise_exc=ConnectionError("refused")),
            now_fn=lambda: 0.0,
        )
        assert result["ok"] is False
        assert "ConnectionError" in result["detail"]
        # Unlike Discord, localhost network errors mean the process is down — alert.
        assert len(_alert_rows(pool)) == 1

    @pytest.mark.asyncio
    async def test_dedup_window_suppresses_repeat_alert(self):
        pool = _make_pool()
        factory = _make_http_factory(status_code=503)
        clock = [1000.0]
        await mhp.run_mcp_http_probe(
            pool, http_client_factory=factory, now_fn=lambda: clock[0],
        )
        assert len(_alert_rows(pool)) == 1
        # Past cadence (5 min), inside dedup (1h).
        clock[0] = 1000.0 + 6 * 60
        await mhp.run_mcp_http_probe(
            pool, http_client_factory=factory, now_fn=lambda: clock[0],
        )
        assert len(_alert_rows(pool)) == 1

    @pytest.mark.asyncio
    async def test_cadence_gate_skips_within_interval(self):
        pool = _make_pool()
        factory_calls = []

        def factory():
            factory_calls.append(1)
            response = MagicMock()
            response.status_code = 200
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)
            client.get = AsyncMock(return_value=response)
            return client

        await mhp.run_mcp_http_probe(pool, http_client_factory=factory, now_fn=lambda: 1000.0)
        await mhp.run_mcp_http_probe(pool, http_client_factory=factory, now_fn=lambda: 1060.0)
        assert len(factory_calls) == 1  # second skipped

    @pytest.mark.asyncio
    async def test_launcher_invoked_when_configured(self):
        pool = _make_pool(setting_values={mhp.LAUNCHER_PATH_KEY: "C:\\fake\\launcher.cmd"})
        launcher_calls = []

        def fake_launcher(path):
            launcher_calls.append(path)
            return (True, f"dispatched {path}")

        result = await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            launcher_fn=fake_launcher,
            now_fn=lambda: 1000.0,
        )
        assert launcher_calls == ["C:\\fake\\launcher.cmd"]
        assert "dispatched" in result.get("recovery_detail", "")

    @pytest.mark.asyncio
    async def test_launcher_restart_cap_enforced(self):
        pool = _make_pool(
            setting_values={
                mhp.LAUNCHER_PATH_KEY: "C:\\fake\\launcher.cmd",
                mhp.RESTART_CAP_KEY: "2",
                mhp.RESTART_WINDOW_MINUTES_KEY: "60",
            }
        )
        launcher_calls = []

        def fake_launcher(path):
            launcher_calls.append(path)
            return (True, "dispatched")

        factory = _make_http_factory(status_code=503)
        # Far apart enough to clear cadence but inside the 60-min restart window.
        clock = [1000.0]
        for offset in (0, 6 * 60, 12 * 60, 18 * 60):
            clock[0] = 1000.0 + offset
            await mhp.run_mcp_http_probe(
                pool, http_client_factory=factory, launcher_fn=fake_launcher,
                now_fn=lambda: clock[0],
            )
        # Cap=2 → only first 2 cycles invoke the launcher.
        assert len(launcher_calls) == 2
