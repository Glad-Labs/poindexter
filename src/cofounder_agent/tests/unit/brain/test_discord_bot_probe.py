"""Unit tests for brain/discord_bot_probe.py (poindexter#435).

Scenarios covered:

1. Master-switch off → ok=True, status='disabled', no HTTP call.
2. Master-switch on but token empty → ok=False, status='unconfigured'.
3. Token set, Discord returns 200 → ok=True, status='ok'.
4. Discord returns 401 → ok=False, alert_events row written.
5. Discord returns 401 within dedup window → ok=False but NO new alert row.
6. Network error (transient) → ok=False, status='transient', no alert.
7. Cadence gate: a real check within the interval is skipped with
   status='skipped_cadence'.

All external I/O (asyncpg pool, httpx client) is mocked.
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import discord_bot_probe as dbp


# ---------------------------------------------------------------------------
# Mock builders
# ---------------------------------------------------------------------------


def _default_settings() -> dict[str, str]:
    return {
        dbp.ENABLED_KEY: "true",
        dbp.POLL_INTERVAL_MINUTES_KEY: "5",
        dbp.HTTP_TIMEOUT_SECONDS_KEY: "5",
        dbp.DEDUP_HOURS_KEY: "1",
        dbp.TOKEN_KEY: "test-bot-token",
    }


def _make_pool(*, setting_values: Optional[dict[str, str]] = None):
    """Mock asyncpg pool. ``fetchrow`` returns rows matching the
    brain.secret_reader contract: ``{"value": ..., "is_secret": False}``."""
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
    pool.fetchval = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


def _make_http_factory(*, status_code: int = 200, raise_exc: Exception | None = None):
    """Return an http_client_factory that yields a single-use AsyncClient
    mock whose ``.get(...)`` resolves to a response with ``.status_code``."""

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
    """Return every alert_events INSERT the probe made."""
    out = []
    for call in pool.execute.call_args_list:
        sql = call.args[0] if call.args else ""
        if "INSERT INTO alert_events" in sql:
            out.append(call.args)
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_probe_state():
    """Each test gets a fresh module-level state (cadence + dedup)."""
    dbp._reset_state()
    yield
    dbp._reset_state()


class TestDiscordBotProbe:
    @pytest.mark.asyncio
    async def test_disabled_skips_http_call(self):
        pool = _make_pool(setting_values={dbp.ENABLED_KEY: "false"})
        factory = _make_http_factory(status_code=200)
        result = await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: 1000.0,
        )
        assert result == {
            "ok": True,
            "status": "disabled",
            "detail": "discord_bot_probe disabled",
        }

    @pytest.mark.asyncio
    async def test_unconfigured_when_token_empty(self):
        pool = _make_pool(setting_values={dbp.TOKEN_KEY: ""})
        factory = _make_http_factory(status_code=200)
        result = await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: 1000.0,
        )
        assert result["ok"] is False
        assert result["status"] == "unconfigured"
        assert dbp.TOKEN_KEY in result["detail"]

    @pytest.mark.asyncio
    async def test_returns_ok_on_200(self):
        pool = _make_pool()
        factory = _make_http_factory(status_code=200)
        result = await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: 1000.0,
        )
        assert result["ok"] is True
        assert result["status"] == "ok"
        assert result["status_code"] == 200
        # No alert_events row.
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_401_writes_alert_event(self):
        pool = _make_pool()
        factory = _make_http_factory(status_code=401)
        result = await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: 1000.0,
        )
        assert result["ok"] is False
        assert result["status"] == "auth_failed"
        assert result["status_code"] == 401
        rows = _alert_rows(pool)
        assert len(rows) == 1
        # Verify fingerprint includes the status_code so reusing the
        # dedup key requires the same status to recur.
        fingerprint = rows[0][4]
        assert "401" in fingerprint
        # Channel hint forces Discord routing.
        assert rows[0][3] == "discord"

    @pytest.mark.asyncio
    async def test_401_within_dedup_window_suppresses_repeat(self):
        pool = _make_pool()
        factory = _make_http_factory(status_code=401)
        clock = [1000.0]

        await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: clock[0],
        )
        # First call should have written 1 alert.
        assert len(_alert_rows(pool)) == 1

        # Advance well past the cadence gate (default 5 min) but stay
        # well inside the 1-hour dedup window.
        clock[0] = 1000.0 + 6 * 60  # +6 min
        await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: clock[0],
        )

        # Still exactly 1 alert row — dedup window suppressed the second.
        assert len(_alert_rows(pool)) == 1

    @pytest.mark.asyncio
    async def test_transient_network_error_no_alert(self):
        pool = _make_pool()
        factory = _make_http_factory(raise_exc=RuntimeError("connection refused"))
        result = await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: 1000.0,
        )
        assert result["ok"] is False
        assert result["status"] == "transient"
        assert "RuntimeError" in result["detail"]
        # Network blip never pages — only auth_failed (401/403) does.
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_5xx_treated_as_transient(self):
        pool = _make_pool()
        factory = _make_http_factory(status_code=503)
        result = await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: 1000.0,
        )
        assert result["ok"] is False
        assert result["status"] == "transient"
        assert result["status_code"] == 503
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_cadence_gate_skips_within_interval(self):
        pool = _make_pool()
        factory_calls = []

        def factory():
            factory_calls.append(1)
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)
            response = MagicMock()
            response.status_code = 200
            client.get = AsyncMock(return_value=response)
            return client

        # First call: runs the real check.
        await dbp.run_discord_bot_probe(pool, http_client_factory=factory, now_fn=lambda: 1000.0)
        assert len(factory_calls) == 1

        # Second call inside the 5-minute interval: skipped.
        result = await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: 1000.0 + 60.0,
        )
        assert result["status"] == "skipped_cadence"
        assert len(factory_calls) == 1  # unchanged

        # Third call past the interval: runs again.
        await dbp.run_discord_bot_probe(
            pool, http_client_factory=factory, now_fn=lambda: 1000.0 + 6 * 60.0,
        )
        assert len(factory_calls) == 2
