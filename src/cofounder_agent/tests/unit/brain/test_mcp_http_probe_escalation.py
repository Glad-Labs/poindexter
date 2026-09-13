"""An outage that outlives the dedup window keeps paging (poindexter#1048).

The 1h dedup window is right for flapping and wrong for a server that stays
down after the restart cap: the 2026-09-11 connector crash loop ran 27 hours
behind "dedup window suppresses page". After the cap (or with no recovery
path at all) the probe now pages hourly with a fresh fingerprint and
critical severity, and stops the moment the server answers.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import mcp_http_probe as mhp

SETTINGS = {
    mhp.ENABLED_KEY: "true", mhp.POLL_INTERVAL_MINUTES_KEY: "5", mhp.HTTP_TIMEOUT_SECONDS_KEY: "3",
    mhp.DEDUP_HOURS_KEY: "1", mhp.BASE_URL_KEY: "http://127.0.0.1:8004",
    mhp.DISCOVERY_PATH_KEY: "/healthz", mhp.LAUNCHER_PATH_KEY: "", mhp.RESTART_CAP_KEY: "2",
    mhp.RESTART_WINDOW_MINUTES_KEY: "60", mhp.RECOVERY_URL_KEY: "http://agent/recover",
    mhp.RECOVERY_TOKEN_KEY: "t", mhp.MIN_CONSECUTIVE_FAILURES_KEY: "1", mhp.ESCALATION_HOURS_KEY: "1",
}


def _pool(overrides=None):
    settings = {**SETTINGS, **(overrides or {})}
    pool = MagicMock()

    async def _fetchrow(query, *args):
        if "app_settings" in query and args and args[0] in settings:
            return {"value": settings[args[0]], "is_secret": False}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    return pool


def _http(status=503):
    def factory():
        resp = MagicMock()
        resp.status_code = status
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=resp)
        return client
    return factory


def _alerts(pool):
    rows = []
    for call in pool.execute.call_args_list:
        sql = call.args[0] if call.args else ""
        if "INSERT INTO alert_events" in sql:
            rows.append(call.args)
    return rows


def _escalations(pool):
    return [r for r in _alerts(pool) if ":down-" in " ".join(str(a) for a in r)]


T0 = 10_000.0  # a clock of 0.0 reads as "never alerted" in the probe's dedup check


@pytest.fixture(autouse=True)
def _reset():
    mhp._reset_state()
    yield
    mhp._reset_state()


async def _recover_ok(*_a, **_k):
    return True, "recovery agent responded HTTP 200"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pages_hourly_after_the_cap_with_a_fresh_fingerprint():
    pool = _pool()
    clock = [0.0]
    async def cycle(t):
        clock[0] = t
        return await mhp.run_mcp_http_probe(pool, http_client_factory=_http(503), now_fn=lambda: clock[0], recovery_fn=_recover_ok)
    await cycle(T0)            # first confirmed failure: initial alert + restart 1
    await cycle(T0 + 600)      # restart 2 -> cap (2) spent
    r = await cycle(T0 + 1200)  # cap reached, 20 min in: no escalation yet
    assert "restart cap reached" in r["recovery_detail"] and _escalations(pool) == []
    assert len(_alerts(pool)) == 1
    r = await cycle(T0 + 3700)  # > 1h into the outage: escalate
    assert "escalated" in r["recovery_detail"]
    esc = _escalations(pool)
    assert len(esc) == 1
    joined = " ".join(str(a) for a in esc[0])
    assert "critical" in joined and ":down-1h" in joined and "still down after 1h" in joined
    await cycle(T0 + 4300)     # 10 minutes later: still within the hourly cadence
    assert len(_escalations(pool)) == 1
    await cycle(T0 + 7400)     # 2h in: a second escalation, distinct fingerprint
    assert len(_escalations(pool)) == 2 and ":down-2h" in " ".join(str(a) for a in _escalations(pool)[-1])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_recovery_path_still_escalates():
    pool = _pool({mhp.RECOVERY_URL_KEY: "", mhp.LAUNCHER_PATH_KEY: ""})
    clock = [0.0]
    async def cycle(t):
        clock[0] = t
        return await mhp.run_mcp_http_probe(pool, http_client_factory=_http(503), now_fn=lambda: clock[0])
    await cycle(T0)
    assert len(_alerts(pool)) == 1 and _escalations(pool) == []
    r = await cycle(T0 + 3700)
    assert "escalated (no auto-recovery configured)" in r["recovery_detail"]
    assert len(_escalations(pool)) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recovery_closes_the_outage_window():
    pool = _pool()
    clock = [0.0]
    async def cycle(t, status):
        clock[0] = t
        return await mhp.run_mcp_http_probe(pool, http_client_factory=_http(status), now_fn=lambda: clock[0], recovery_fn=_recover_ok)
    await cycle(T0, 503)
    await cycle(T0 + 600, 503)
    await cycle(T0 + 3700, 503)
    assert len(_escalations(pool)) == 1
    r = await cycle(T0 + 4000, 200)
    assert r["ok"] is True and mhp._outage_started_at == 0.0 and mhp._last_escalation_at == 0.0
    await cycle(T0 + 4400, 503)  # a NEW outage starts its own clock: no immediate escalation
    assert len(_escalations(pool)) == 1
