"""Unit tests for brain/mcp_http_probe.py (poindexter#434)."""

from __future__ import annotations

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
        mhp.RECOVERY_URL_KEY: "",
        mhp.RECOVERY_TOKEN_KEY: "",
        # Tests call the probe once and assert an alert fires — set the
        # consecutive-failure threshold to 1 so that behaviour is preserved.
        # The new test class below tests the default threshold of 3.
        mhp.MIN_CONSECUTIVE_FAILURES_KEY: "1",
    }


def _make_pool(*, setting_values: dict[str, str] | None = None):
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
        # New schema: (sql, alertname, labels_json, annotations_json, fingerprint).
        # Discord routing isn't picked here — the dispatcher derives it from
        # severity (warning -> Discord per feedback_telegram_vs_discord).
        assert rows[0][1] == "mcp_http_server_unreachable"
        assert "mcp_http_probe:http_503" in rows[0][4]

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
    async def test_kill_switch_fails_closed_on_missing_enabled_row(self):
        """Glad-Labs/poindexter#468: an absent `mcp_http_probe_enabled`
        row must NOT fall back to `DEFAULT_ENABLED=True` — that's the silent
        fail-open path that caused 10 false-positive alerts/24h after the
        operator had set the kill-switch to 'false' five days earlier.
        """
        pool = MagicMock()

        async def _fetchrow(query, *args):
            # Mimic the bug: every read for the enabled key returns None,
            # which `read_app_setting` translates into the caller's default.
            if args and args[0] == mhp.ENABLED_KEY:
                return None
            return {"value": "5", "is_secret": False}

        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.execute = AsyncMock()

        result = await mhp.run_mcp_http_probe(
            pool, http_client_factory=_make_http_factory(status_code=503),
            now_fn=lambda: 0.0,
        )
        assert result == {
            "ok": True, "status": "disabled",
            "detail": "mcp_http_probe disabled",
        }
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_kill_switch_fails_closed_on_db_error(self):
        """A swallowed asyncpg exception during the kill-switch read must
        leave the probe disabled — not silently re-enable it for one cycle.
        """
        pool = MagicMock()

        async def _fetchrow(query, *args):
            raise RuntimeError("simulated DB hiccup")

        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.execute = AsyncMock()

        result = await mhp.run_mcp_http_probe(
            pool, http_client_factory=_make_http_factory(status_code=503),
            now_fn=lambda: 0.0,
        )
        assert result["status"] == "disabled"
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_kill_switch_treats_unparseable_value_as_disabled(self):
        """Garbage in the kill-switch row (operator typo, type drift) must
        also fail closed rather than coercing to truthy via `bool('maybe')`.
        """
        pool = _make_pool(setting_values={mhp.ENABLED_KEY: "maybe"})
        result = await mhp.run_mcp_http_probe(
            pool, http_client_factory=_make_http_factory(status_code=503),
            now_fn=lambda: 0.0,
        )
        assert result["status"] == "disabled"
        assert _alert_rows(pool) == []

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


class TestMcpHttpProbeHttpRecovery:
    """HTTP recovery path (recovery_fn injection, no subprocess)."""

    @pytest.mark.asyncio
    async def test_http_recovery_invoked_on_failure(self):
        pool = _make_pool(
            setting_values={
                mhp.RECOVERY_URL_KEY: "http://host.docker.internal:9841/recover",
                mhp.RECOVERY_TOKEN_KEY: "tok",
            }
        )
        recovery_calls: list[tuple[str, str]] = []

        async def fake_recovery(url: str, token: str) -> tuple[bool, str]:
            recovery_calls.append((url, token))
            return True, "recovery dispatched"

        result = await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            recovery_fn=fake_recovery,
            now_fn=lambda: 1000.0,
        )
        assert result["ok"] is False
        assert recovery_calls == [
            ("http://host.docker.internal:9841/recover", "tok")
        ]
        assert "recovery dispatched" in result.get("recovery_detail", "")

    @pytest.mark.asyncio
    async def test_http_recovery_not_invoked_when_url_empty(self):
        """Empty recovery_url → no attempt, no recovery_detail."""
        pool = _make_pool(
            setting_values={
                mhp.RECOVERY_URL_KEY: "",
                mhp.RECOVERY_TOKEN_KEY: "tok",
            }
        )
        recovery_calls: list = []

        async def fake_recovery(url: str, token: str) -> tuple[bool, str]:
            recovery_calls.append((url, token))
            return True, "should not be called"

        result = await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            recovery_fn=fake_recovery,
            now_fn=lambda: 1000.0,
        )
        assert recovery_calls == []
        assert "recovery_detail" not in result

    @pytest.mark.asyncio
    async def test_http_recovery_restart_cap_enforced(self):
        """HTTP recovery obeys the same restart_cap/window as the subprocess path."""
        pool = _make_pool(
            setting_values={
                mhp.RECOVERY_URL_KEY: "http://host.docker.internal:9841/recover",
                mhp.RECOVERY_TOKEN_KEY: "tok",
                mhp.RESTART_CAP_KEY: "2",
                mhp.RESTART_WINDOW_MINUTES_KEY: "60",
            }
        )
        recovery_calls: list = []

        async def fake_recovery(url: str, token: str) -> tuple[bool, str]:
            recovery_calls.append((url, token))
            return True, "dispatched"

        clock = [1000.0]
        factory = _make_http_factory(status_code=503)
        for offset in (0, 6 * 60, 12 * 60, 18 * 60):
            clock[0] = 1000.0 + offset
            await mhp.run_mcp_http_probe(
                pool,
                http_client_factory=factory,
                recovery_fn=fake_recovery,
                now_fn=lambda: clock[0],
            )
        # Cap=2 → only first 2 cycles invoke recovery.
        assert len(recovery_calls) == 2

    @pytest.mark.asyncio
    async def test_launcher_takes_priority_over_http_recovery(self):
        """If launcher_path is set, it is preferred over recovery_url."""
        pool = _make_pool(
            setting_values={
                mhp.LAUNCHER_PATH_KEY: "C:\\fake\\launcher.cmd",
                mhp.RECOVERY_URL_KEY: "http://host.docker.internal:9841/recover",
                mhp.RECOVERY_TOKEN_KEY: "tok",
            }
        )
        launcher_calls: list = []
        recovery_calls: list = []

        def fake_launcher(path: str) -> tuple[bool, str]:
            launcher_calls.append(path)
            return True, f"dispatched {path}"

        async def fake_recovery(url: str, token: str) -> tuple[bool, str]:
            recovery_calls.append((url, token))
            return True, "should not be called"

        await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            launcher_fn=fake_launcher,
            recovery_fn=fake_recovery,
            now_fn=lambda: 1000.0,
        )
        assert launcher_calls == ["C:\\fake\\launcher.cmd"]
        assert recovery_calls == []

class TestMcpHttpProbeConsecutiveDebounce:
    """Consecutive-failure gate: only page after N failures, not on the first."""

    @pytest.mark.asyncio
    async def test_first_failure_does_not_alert_with_threshold_3(self):
        """A single probe failure does not write an alert_events row when
        mcp_http_probe_min_consecutive_failures=3 (the production default)."""
        pool = _make_pool(setting_values={mhp.MIN_CONSECUTIVE_FAILURES_KEY: "3"})
        result = await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            now_fn=lambda: 1000.0,
        )
        assert result["ok"] is False
        assert result["status"] == "unreachable"
        assert result.get("consecutive_failures") == 1
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_second_failure_does_not_alert(self):
        """Two consecutive failures with threshold=3 still produces no alert."""
        pool = _make_pool(setting_values={mhp.MIN_CONSECUTIVE_FAILURES_KEY: "3"})
        factory = _make_http_factory(status_code=503)
        clock = [1000.0]
        for offset in (0, 6 * 60):
            clock[0] = 1000.0 + offset
            await mhp.run_mcp_http_probe(
                pool, http_client_factory=factory, now_fn=lambda: clock[0],
            )
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_third_failure_fires_alert(self):
        """The third consecutive failure reaches the default threshold and writes the alert."""
        pool = _make_pool(setting_values={mhp.MIN_CONSECUTIVE_FAILURES_KEY: "3"})
        factory = _make_http_factory(status_code=503)
        clock = [1000.0]
        for offset in (0, 6 * 60, 12 * 60):
            clock[0] = 1000.0 + offset
            await mhp.run_mcp_http_probe(
                pool, http_client_factory=factory, now_fn=lambda: clock[0],
            )
        rows = _alert_rows(pool)
        assert len(rows) == 1
        assert rows[0][1] == "mcp_http_server_unreachable"

    @pytest.mark.asyncio
    async def test_recovery_resets_counter(self):
        """A successful probe resets the consecutive-failure counter so the
        next single failure does not re-alert immediately."""
        pool = _make_pool(setting_values={mhp.MIN_CONSECUTIVE_FAILURES_KEY: "3"})
        clock = [1000.0]
        # Two failures (below threshold — no alert)
        for offset in (0, 6 * 60):
            clock[0] = 1000.0 + offset
            await mhp.run_mcp_http_probe(
                pool,
                http_client_factory=_make_http_factory(status_code=503),
                now_fn=lambda: clock[0],
            )
        # Success — resets the counter
        clock[0] = 1000.0 + 12 * 60
        result = await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=200),
            now_fn=lambda: clock[0],
        )
        assert result["ok"] is True
        # One more failure — counter starts fresh, should not alert
        clock[0] = 1000.0 + 18 * 60
        await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            now_fn=lambda: clock[0],
        )
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_threshold_1_alerts_on_first_failure(self):
        """threshold=1 restores the original fire-on-first-failure behaviour."""
        pool = _make_pool(setting_values={mhp.MIN_CONSECUTIVE_FAILURES_KEY: "1"})
        result = await mhp.run_mcp_http_probe(
            pool,
            http_client_factory=_make_http_factory(status_code=503),
            now_fn=lambda: 1000.0,
        )
        assert result["ok"] is False
        rows = _alert_rows(pool)
        assert len(rows) == 1
