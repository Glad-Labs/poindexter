"""live_activity.track() — the reusable begin→heartbeat→finish bracket every
producer rents. Best-effort throughout; a None id (begin failed) degrades the
whole bracket to a silent no-op."""
import asyncio

import pytest

from services import live_activity

pytestmark = pytest.mark.asyncio


async def _ret(v):
    return v


async def test_track_brackets_begin_and_finish_ok(monkeypatch):
    calls = []

    async def fake_begin(pool, **kw):
        calls.append(("begin", kw["kind"]))
        return 11

    async def fake_finish(pool, aid, **kw):
        calls.append(("finish", aid, kw.get("status")))

    monkeypatch.setattr(live_activity, "begin", fake_begin)
    monkeypatch.setattr(live_activity, "finish", fake_finish)

    async with live_activity.track(object(), kind="media", ref_id="t1", title="X") as act:
        assert act.activity_id == 11
    assert calls == [("begin", "media"), ("finish", 11, "ok")]


async def test_track_marks_fail_on_handle_fail(monkeypatch):
    calls = []
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(7))

    async def fake_finish(pool, aid, **kw):
        calls.append((aid, kw.get("status")))

    monkeypatch.setattr(live_activity, "finish", fake_finish)

    async with live_activity.track(object(), kind="media", ref_id="t", title="X") as act:
        act.fail()
    assert calls == [(7, "fail")]


async def test_track_marks_fail_and_reraises_on_exception(monkeypatch):
    calls = []
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(3))

    async def fake_finish(pool, aid, **kw):
        calls.append((aid, kw.get("status")))

    monkeypatch.setattr(live_activity, "finish", fake_finish)

    with pytest.raises(RuntimeError):
        async with live_activity.track(object(), kind="media", ref_id="t", title="X"):
            raise RuntimeError("boom")
    assert calls == [(3, "fail")]


async def test_track_heartbeats_a_slow_body(monkeypatch):
    beats = []
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(9))
    monkeypatch.setattr(live_activity, "finish", lambda pool, aid, **kw: _ret(None))

    async def fake_update(pool, aid, **kw):
        beats.append(aid)

    monkeypatch.setattr(live_activity, "update", fake_update)

    async with live_activity.track(
        object(), kind="media", ref_id="t", title="X", heartbeat_seconds=0.01
    ):
        await asyncio.sleep(0.05)
    assert len(beats) >= 1 and set(beats) == {9}


async def test_track_none_id_skips_heartbeat_and_finishes(monkeypatch):
    """begin failed → None id → no heartbeat task spins, and finish is still
    called (with None, which finish itself no-ops)."""
    finishes = []
    beats = []
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(None))

    async def fake_finish(pool, aid, **kw):
        finishes.append(aid)

    monkeypatch.setattr(live_activity, "finish", fake_finish)

    async def counting_update(pool, aid, **kw):
        beats.append(aid)

    monkeypatch.setattr(live_activity, "update", counting_update)

    async with live_activity.track(
        object(), kind="media", ref_id="t", title="X", heartbeat_seconds=0.01
    ) as act:
        assert act.activity_id is None
        await asyncio.sleep(0.03)  # a spinning heartbeat would append to beats
    assert beats == []
    assert finishes == [None]


async def test_handle_update_none_id_is_safe(monkeypatch):
    """act.update() with a None id must no-op via the real update()'s guard —
    it must not touch the (bogus) pool."""
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(None))
    monkeypatch.setattr(live_activity, "finish", lambda pool, aid, **kw: _ret(None))

    async with live_activity.track(object(), kind="media", ref_id="t", title="X") as act:
        await act.update(step="x", pct=5)  # real update guards None → no raise


async def test_handle_update_delegates(monkeypatch):
    seen = {}
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(5))
    monkeypatch.setattr(live_activity, "finish", lambda pool, aid, **kw: _ret(None))

    async def fake_update(pool, aid, *, step=None, pct=None):
        seen["args"] = (aid, step, pct)

    monkeypatch.setattr(live_activity, "update", fake_update)

    async with live_activity.track(object(), kind="media", ref_id="t", title="X") as act:
        await act.update(step="shot 2/5", pct=40)
    assert seen["args"] == (5, "shot 2/5", 40)


def test_resolve_heartbeat_seconds():
    assert live_activity.resolve_heartbeat_seconds(None) == 30.0

    class _C:
        def get(self, k, d=None):
            return "5" if k == "live_activity_heartbeat_seconds" else d

    assert live_activity.resolve_heartbeat_seconds(_C()) == 5.0

    class _Bad:
        def get(self, k, d=None):
            return "not-a-number"

    assert live_activity.resolve_heartbeat_seconds(_Bad()) == 30.0
