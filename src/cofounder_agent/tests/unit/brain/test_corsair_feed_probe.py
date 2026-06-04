"""Unit tests for ``brain/corsair_feed_probe.py`` (Glad-Labs/glad-labs-stack#868).

Pins the iCUE corsair_csv feed-freshness watchdog: it emits an
``audit_log`` ``finding`` (severity warning, kind ``sensor_feed_stale``,
stable ``dedup_key``) on the fresh->stale edge only, stays quiet while the
feed is healthy, doesn't re-fire on a persistent stall, and never alarms
when the operator has no corsair_csv data at all.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain.corsair_feed_probe import run_corsair_feed_probe

_LATEST = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)


def _pool(*, latest, age_min, prev_state=None, threshold="120"):
    """asyncpg pool stub. fetchval -> threshold; fetchrow sequenced as the
    probe calls them (sensor_samples, then brain_knowledge prev-state)."""
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=threshold)
    sensor_row = {"latest": latest, "age_min": age_min}
    if latest is None:
        pool.fetchrow = AsyncMock(side_effect=[sensor_row])
    else:
        bk_row = {"value": prev_state} if prev_state is not None else None
        pool.fetchrow = AsyncMock(side_effect=[sensor_row, bk_row])
    pool.execute = AsyncMock()
    return pool


def _finding_call(pool):
    """The pool.execute call that inserts the finding, or None."""
    for c in pool.execute.call_args_list:
        sql = c.args[0] if c.args else ""
        if "audit_log" in sql and "finding" in sql:
            return c.args
    return None


@pytest.mark.asyncio
async def test_no_data_is_not_assessed():
    """Operators without the iCUE tap have zero corsair_csv rows — silence."""
    pool = _pool(latest=None, age_min=None)
    res = await run_corsair_feed_probe(pool)
    assert res["ok"] is True
    assert res["assessed"] is False
    assert _finding_call(pool) is None
    pool.execute.assert_not_called()  # no state write, no finding


@pytest.mark.asyncio
async def test_fresh_feed_emits_no_finding():
    pool = _pool(latest=_LATEST, age_min=30.0, prev_state="fresh")
    res = await run_corsair_feed_probe(pool)
    assert res["ok"] is True
    assert res["stale"] is False
    assert _finding_call(pool) is None  # only the state write happened


@pytest.mark.asyncio
async def test_stale_transition_emits_finding():
    """fresh -> stale edge writes a warning finding with the right shape."""
    pool = _pool(latest=_LATEST, age_min=200.0, prev_state="fresh")
    res = await run_corsair_feed_probe(pool)
    assert res["ok"] is False
    assert res["stale"] is True

    call = _finding_call(pool)
    assert call is not None, "expected a finding insert"
    assert "'warning'" in call[0] and "event_type" in call[0]
    details = json.loads(call[1])
    assert details["kind"] == "sensor_feed_stale"
    assert details["dedup_key"] == "corsair_csv_feed_stale"
    assert "title" in details and "body" in details


@pytest.mark.asyncio
async def test_stale_on_boot_emits_finding():
    """No prior state (fresh brain) + already stale -> surface it."""
    pool = _pool(latest=_LATEST, age_min=200.0, prev_state=None)
    await run_corsair_feed_probe(pool)
    assert _finding_call(pool) is not None


@pytest.mark.asyncio
async def test_persistent_stale_does_not_refire():
    """Already-stale last cycle -> no second page (dispatcher would dedup
    anyway, but we don't even write a duplicate finding)."""
    pool = _pool(latest=_LATEST, age_min=300.0, prev_state="stale")
    res = await run_corsair_feed_probe(pool)
    assert res["stale"] is True
    assert _finding_call(pool) is None


@pytest.mark.asyncio
async def test_recovery_emits_no_finding():
    pool = _pool(latest=_LATEST, age_min=20.0, prev_state="stale")
    res = await run_corsair_feed_probe(pool)
    assert res["ok"] is True
    assert _finding_call(pool) is None


@pytest.mark.asyncio
async def test_threshold_is_configurable():
    """A 60m operator threshold makes a 90m-old feed stale."""
    pool = _pool(latest=_LATEST, age_min=90.0, prev_state="fresh", threshold="60")
    res = await run_corsair_feed_probe(pool)
    assert res["threshold_minutes"] == 60
    assert res["stale"] is True
    assert _finding_call(pool) is not None
