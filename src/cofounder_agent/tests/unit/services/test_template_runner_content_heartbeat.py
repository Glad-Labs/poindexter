"""Content-run sidecar heartbeat (gap #2 of the console live-activity pulse).

The per-node ``_persist_record`` callback only bumps the content live_activity
row's ``updated_at`` as each node COMPLETES. A single node longer than the
freshness window (a full writer draft, an image-gen call) would let the row go
stale mid-node and be hidden from the pulse even though the run is still going.
``_ainvoke_with_content_heartbeat`` wraps the graph ``ainvoke`` in a sidecar
heartbeat, torn down on every exit path.
"""
import asyncio

import pytest

from services import template_runner as tr

pytestmark = pytest.mark.asyncio


class _SiteConfig:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, key, default=None):
        return self._m.get(key, default)


class _SlowGraph:
    """Compiled-graph stand-in whose ainvoke runs longer than the interval."""

    def __init__(self, delay):
        self._delay = delay

    async def ainvoke(self, invoke_input, config):
        await asyncio.sleep(self._delay)
        return {"ok": True}


class _BoomGraph:
    async def ainvoke(self, invoke_input, config):
        raise RuntimeError("graph boom")


async def test_heartbeat_fires_during_long_ainvoke(monkeypatch):
    """A node longer than the heartbeat interval keeps the row fresh: update()
    fires at least once DURING the ainvoke so the freshness window won't hide a
    still-running content run mid-node."""
    beats = []

    async def fake_update(pool, aid, **kw):
        beats.append(aid)

    monkeypatch.setattr(tr.live_activity, "update", fake_update)

    result = await tr._ainvoke_with_content_heartbeat(
        _SlowGraph(0.05), {}, {}, pool=object(), activity_id=7, interval_seconds=0.01
    )
    assert result == {"ok": True}
    assert len(beats) >= 1 and set(beats) == {7}


async def test_no_heartbeat_when_activity_id_none(monkeypatch):
    """A None content aid (begin failed) means no sidecar — the graph still
    runs, but nothing beats."""
    beats = []

    async def fake_update(pool, aid, **kw):
        beats.append(aid)

    monkeypatch.setattr(tr.live_activity, "update", fake_update)

    result = await tr._ainvoke_with_content_heartbeat(
        _SlowGraph(0.03), {}, {}, pool=object(), activity_id=None, interval_seconds=0.01
    )
    assert result == {"ok": True}
    assert beats == []


async def test_heartbeat_torn_down_on_exception(monkeypatch):
    """The graph raising still tears the sidecar down (finally) and re-raises —
    no leaked heartbeat that would keep a dead row 'alive' forever."""
    async def fake_update(pool, aid, **kw):
        pass

    monkeypatch.setattr(tr.live_activity, "update", fake_update)

    with pytest.raises(RuntimeError, match="graph boom"):
        await tr._ainvoke_with_content_heartbeat(
            _BoomGraph(), {}, {}, pool=object(), activity_id=3, interval_seconds=0.01
        )


async def test_interval_reads_site_config():
    """The interval comes from live_activity_heartbeat_seconds, defaulting to 30
    when unset / no site_config / unparseable."""
    assert (
        tr._content_heartbeat_interval_seconds(
            _SiteConfig({"live_activity_heartbeat_seconds": "45"})
        )
        == 45.0
    )
    assert tr._content_heartbeat_interval_seconds(_SiteConfig({})) == 30.0
    assert tr._content_heartbeat_interval_seconds(None) == 30.0
    assert (
        tr._content_heartbeat_interval_seconds(
            _SiteConfig({"live_activity_heartbeat_seconds": "bad"})
        )
        == 30.0
    )


class _FakeTasksDatabase:
    """Stand-in for services.tasks_db.TasksDatabase — tracks heartbeat_task
    calls without touching a real pool/DB."""

    instances: list["_FakeTasksDatabase"] = []

    def __init__(self, pool):
        self.pool = pool
        self.calls: list[str] = []
        _FakeTasksDatabase.instances.append(self)

    async def heartbeat_task(self, task_id: str) -> bool:
        self.calls.append(task_id)
        return True


@pytest.fixture(autouse=True)
def _reset_fake_tasks_db():
    _FakeTasksDatabase.instances.clear()
    yield
    _FakeTasksDatabase.instances.clear()


class TestPipelineTaskHeartbeat:
    """poindexter#757 / GH-90: ``heartbeat_task`` was built to keep
    ``pipeline_tasks.updated_at`` fresh during long stages so the stale-sweep
    wouldn't reclaim genuinely-running work, but its only caller
    (``TaskExecutor``) was deleted in the 2026-05-16 Prefect cutover
    (poindexter#410 Stage 4) — it went dark. This re-wires it as a second
    sidecar alongside the existing ``live_activity`` heartbeat.
    """

    async def test_fires_during_long_ainvoke(self, monkeypatch):
        monkeypatch.setattr(
            "services.tasks_db.TasksDatabase", _FakeTasksDatabase,
        )

        result = await tr._ainvoke_with_content_heartbeat(
            _SlowGraph(0.05), {}, {}, pool=object(), activity_id=None,
            interval_seconds=0.01, task_id="task-757",
        )

        assert result == {"ok": True}
        assert len(_FakeTasksDatabase.instances) == 1
        calls = _FakeTasksDatabase.instances[0].calls
        assert len(calls) >= 1 and set(calls) == {"task-757"}

    async def test_no_heartbeat_when_task_id_none(self, monkeypatch):
        monkeypatch.setattr(
            "services.tasks_db.TasksDatabase", _FakeTasksDatabase,
        )

        result = await tr._ainvoke_with_content_heartbeat(
            _SlowGraph(0.03), {}, {}, pool=object(), activity_id=None,
            interval_seconds=0.01, task_id=None,
        )

        assert result == {"ok": True}
        assert _FakeTasksDatabase.instances == []

    async def test_torn_down_on_exception(self, monkeypatch):
        monkeypatch.setattr(
            "services.tasks_db.TasksDatabase", _FakeTasksDatabase,
        )

        with pytest.raises(RuntimeError, match="graph boom"):
            await tr._ainvoke_with_content_heartbeat(
                _BoomGraph(), {}, {}, pool=object(), activity_id=None,
                interval_seconds=0.01, task_id="task-boom",
            )
        # No assertion on call count (the graph may boom before the first
        # beat) — the real guard is that the test reaches this line at all
        # instead of hanging on a leaked, never-cancelled sidecar task.

    async def test_both_sidecars_fire_together(self, monkeypatch):
        """The pre-existing live_activity heartbeat and the new pipeline_task
        heartbeat are independent sidecars — adding one must not disturb the
        other."""
        beats: list[int] = []

        async def fake_update(pool, aid, **kw):
            beats.append(aid)

        monkeypatch.setattr(tr.live_activity, "update", fake_update)
        monkeypatch.setattr(
            "services.tasks_db.TasksDatabase", _FakeTasksDatabase,
        )

        result = await tr._ainvoke_with_content_heartbeat(
            _SlowGraph(0.05), {}, {}, pool=object(), activity_id=7,
            interval_seconds=0.01, task_id="task-both",
        )

        assert result == {"ok": True}
        assert len(beats) >= 1 and set(beats) == {7}
        assert len(_FakeTasksDatabase.instances) == 1
        assert set(_FakeTasksDatabase.instances[0].calls) == {"task-both"}
