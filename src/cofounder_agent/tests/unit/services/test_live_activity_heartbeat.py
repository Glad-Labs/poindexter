"""live_activity.heartbeat — keeps a long-running producer's row fresh.

Root cause of the "pulse goes blank while the GPU is busy" bug: the scheduler
seam bracketed a job with begin/finish but never bumped updated_at during the
run, so get_live_activity's freshness window hid a still-running multi-minute
job (an LLM topic sweep) after live_activity_freshness_seconds. The fix is a
sidecar heartbeat that bumps updated_at on an interval while the producer runs.
"""
import asyncio
import contextlib

import pytest

from services import live_activity

pytestmark = pytest.mark.asyncio


async def test_heartbeat_bumps_until_cancelled(monkeypatch):
    """The heartbeat calls update() repeatedly so the freshness window stays
    satisfied while a long producer runs; cancellation stops it cleanly."""
    beats = []

    async def fake_update(pool, aid, **kw):
        beats.append(aid)

    monkeypatch.setattr(live_activity, "update", fake_update)
    task = asyncio.create_task(
        live_activity.heartbeat(object(), 42, interval_seconds=0.01)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert len(beats) >= 2, "heartbeat should bump updated_at multiple times"
    assert set(beats) == {42}


async def test_heartbeat_none_id_returns_immediately(monkeypatch):
    """A None activity id (begin failed) makes heartbeat a no-op — it must not
    spin forever calling update on a row that never opened."""
    beats = []

    async def fake_update(pool, aid, **kw):
        beats.append(aid)

    monkeypatch.setattr(live_activity, "update", fake_update)
    await asyncio.wait_for(
        live_activity.heartbeat(object(), None, interval_seconds=0.01),
        timeout=1.0,
    )
    assert beats == []


async def test_heartbeat_swallows_update_errors(monkeypatch):
    """A failing update() (transient DB blip) must not kill the loop or escape
    — best-effort, like every other ledger write."""
    beats = []

    async def flaky_update(pool, aid, **kw):
        beats.append(aid)
        raise RuntimeError("db blip")

    monkeypatch.setattr(live_activity, "update", flaky_update)
    task = asyncio.create_task(
        live_activity.heartbeat(object(), 5, interval_seconds=0.01)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert len(beats) >= 2, "heartbeat kept trying despite update() raising"
