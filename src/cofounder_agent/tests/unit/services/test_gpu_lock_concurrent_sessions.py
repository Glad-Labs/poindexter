"""Concurrent sessions on different cards in ONE process keep their own state.

Device scoping (#3457 Phase 2) lets a GPU-0 render and a GPU-1 judge hold the
lock at the same time in the same process. Everything a release needed, though,
lived in single instance slots: ``_held_keys``, ``_pg_lock_conn`` / keys /
shared flag, and ``_acquired_at``. The second session to acquire overwrote the
first's, so:

* whichever session released first released the slot, which held the
  OTHER session's gate when the judge acquired first. That handed the render's
  card to the next caller mid-render, and left the judge's gate held for good;
* the first session's pg connection lost its only strong reference. asyncpg's
  protocol references its connection weakly, so ``Connection.__del__``
  terminated it on the spot and Postgres released the render's card to every
  other container while the render was still running;
* the first session's hold duration was measured from the second session's
  acquire, which corrupted the ``gpu_lease_stats`` p90 admission reads.

Each test below fails on the single-slot code.
"""

from __future__ import annotations

import asyncio
import gc
import json
import weakref
from unittest.mock import AsyncMock

import pytest

from poindexter.services import gpu_scheduler as gs
from poindexter.services.gpu_scheduler import (
    GPU_ADVISORY_LOCK_KEY,
    GpuLockTimeoutError,
    GPUScheduler,
)
from poindexter.services.site_config import SiteConfig

NODE = "test-node"
GPU0 = gs.device_lock_key(NODE, 0)
GPU1 = gs.device_lock_key(NODE, 1)


@pytest.fixture
def scoped(monkeypatch):
    """Scoping on, render/llm_primary on GPU 0, the judge model on GPU 1."""

    def _apply(**extra):
        values = {
            "gpu_lock_node_id": NODE,
            "gpu_lock_per_device_enabled": "true",
            "gpu_lock_scopes": json.dumps(
                {"render": [0], "qa_judge": [1], "llm_primary": [0]}
            ),
            "plugin.llm_provider.litellm": json.dumps(
                {"config": {"model_api_base_overrides": {
                    "ollama/judge-model": "http://x:11435"}}}
            ),
        }
        values.update(extra)
        cfg = SiteConfig(initial_config=values)
        monkeypatch.setattr(gs, "_sc", lambda: cfg)
        return cfg

    return _apply


def _scheduler() -> GPUScheduler:
    gpu = GPUScheduler()
    gpu._unload_ollama_models = AsyncMock()
    gpu._wait_for_gaming_clear = AsyncMock()
    return gpu


class _Hold:
    """Drive one gpu.lock() session from the outside: enter, then exit on cue."""

    def __init__(self, gpu, owner, model, phase=None):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.task = asyncio.create_task(self._run(gpu, owner, model, phase))

    async def _run(self, gpu, owner, model, phase):
        async with gpu.lock(owner, model=model, phase=phase):
            self.entered.set()
            await self.release.wait()

    async def enter(self):
        await asyncio.wait_for(self.entered.wait(), timeout=2.0)

    async def exit(self):
        self.release.set()
        await asyncio.wait_for(self.task, timeout=2.0)


# ---------------------------------------------------------------------------
# In-process gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("first_out", ["render", "judge"])
async def test_both_sessions_release_their_own_gates(scoped, first_out):
    scoped()
    gpu = _scheduler()
    render = _Hold(gpu, "image_gen", "sdxl")
    await render.enter()
    judge = _Hold(gpu, "ollama", "judge-model")
    await judge.enter()

    order = [render, judge] if first_out == "render" else [judge, render]
    for session in order:
        await session.exit()

    locked = {key: gate.locked() for key, gate in gpu._gates.items()}
    assert not any(locked.values()), f"gates still held after both released: {locked}"
    assert gpu._held_keys == []
    assert gpu._sessions == []
    # And the render's card is genuinely usable again in this process.
    writer = _Hold(gpu, "ollama", "writer")
    await writer.enter()
    await writer.exit()


@pytest.mark.asyncio
async def test_a_newer_session_releasing_does_not_free_an_older_sessions_card(scoped):
    """The judge acquires first, the render second, the judge finishes first.
    The single-slot code then released `_held_keys`, which was the RENDER's
    GPU-0 gate, so a writer walked onto GPU 0 in the middle of the render."""
    scoped()
    gpu = _scheduler()
    judge = _Hold(gpu, "ollama", "judge-model")
    await judge.enter()
    render = _Hold(gpu, "image_gen", "sdxl")
    await render.enter()
    await judge.exit()

    writer = _Hold(gpu, "ollama", "writer")  # llm_primary → GPU 0
    await asyncio.sleep(0.1)
    assert not writer.entered.is_set(), "a GPU-0 caller entered during the GPU-0 render"

    await render.exit()
    await writer.enter()
    await writer.exit()


# ---------------------------------------------------------------------------
# Cross-process pg holds (real acquire/release against fake connections)
# ---------------------------------------------------------------------------


class _FakeConn:
    """Enough of an asyncpg connection to hold advisory locks."""

    def __init__(self, name: str):
        self.name = name
        self.executed: list[tuple[str, int]] = []
        self.closed = False
        self.terminated = False

    async def execute(self, sql, key):
        self.executed.append((sql, key))

    async def close(self):
        self.closed = True

    def terminate(self):
        self.terminated = True


@pytest.fixture
def fake_pg(monkeypatch):
    """Route the real `_acquire_pg_advisory_lock` to fake connections.

    Connections are created by a plain function (not a mock that would keep a
    reference to each return value), so the only strong reference to a live
    session's connection is whatever the scheduler itself keeps.
    """
    import asyncpg

    from poindexter.brain import bootstrap

    names = iter(["first", "second", "third", "fourth"])
    made: list[weakref.ref] = []

    async def _connect(dsn, **kwargs):
        conn = _FakeConn(next(names))
        made.append(weakref.ref(conn))
        return conn

    monkeypatch.setattr(asyncpg, "connect", _connect)
    monkeypatch.setattr(bootstrap, "resolve_database_url", lambda *a, **k: "postgresql://fake/db")
    return made


@pytest.mark.asyncio
@pytest.mark.gpu_lock_real_db
async def test_a_second_session_does_not_drop_the_first_sessions_connection(scoped, fake_pg):
    """The slot overwrite dropped the render's connection. A real asyncpg
    connection with no strong reference is terminated by its __del__, which
    released the render's GPU-0 key to every other container mid-render."""
    scoped()
    gpu = _scheduler()
    render = _Hold(gpu, "image_gen", "sdxl")
    await render.enter()
    judge = _Hold(gpu, "ollama", "judge-model")
    await judge.enter()

    gc.collect()
    first, second = (ref() for ref in fake_pg)
    assert first is not None, "the render's lock connection was garbage-collected mid-render"
    assert not first.closed and not first.terminated
    assert {h.conn.name for h in gpu._pg_holds} == {"first", "second"}

    await judge.exit()
    await render.exit()


@pytest.mark.asyncio
@pytest.mark.gpu_lock_real_db
@pytest.mark.parametrize("first_out", ["render", "judge"])
async def test_each_session_unlocks_its_own_keys_on_its_own_connection(
    scoped, fake_pg, first_out,
):
    scoped()
    gpu = _scheduler()
    render = _Hold(gpu, "image_gen", "sdxl")
    await render.enter()
    judge = _Hold(gpu, "ollama", "judge-model")
    await judge.enter()
    render_conn, judge_conn = (ref() for ref in fake_pg)

    for session in ([render, judge] if first_out == "render" else [judge, render]):
        await session.exit()

    def unlocked(conn):
        return [(sql.split("(")[0].split()[-1], key) for sql, key in conn.executed
                if "unlock" in sql]

    assert unlocked(render_conn) == [
        ("pg_advisory_unlock_shared", GPU_ADVISORY_LOCK_KEY), ("pg_advisory_unlock", GPU0),
    ]
    assert unlocked(judge_conn) == [
        ("pg_advisory_unlock_shared", GPU_ADVISORY_LOCK_KEY), ("pg_advisory_unlock", GPU1),
    ]
    assert render_conn.closed and judge_conn.closed
    assert gpu._pg_holds == []
    assert gpu.status["pg_advisory_lock_held"] is False


@pytest.mark.asyncio
@pytest.mark.gpu_lock_real_db
async def test_status_reports_every_live_hold(scoped, fake_pg):
    scoped()
    gpu = _scheduler()
    render = _Hold(gpu, "image_gen", "sdxl")
    await render.enter()
    judge = _Hold(gpu, "ollama", "judge-model")
    await judge.enter()
    try:
        assert gpu.status["pg_advisory_lock_held"] is True
        assert gpu.status["pg_advisory_lock_keys"] == sorted([GPU0, GPU1])
    finally:
        await judge.exit()
        await render.exit()


# ---------------------------------------------------------------------------
# Durations, introspection, and naming the right holder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_hold_duration_is_measured_from_its_own_acquire(scoped, monkeypatch):
    """The render's duration came from `_acquired_at`, which the judge had
    overwritten, so a 0.3 s hold was recorded as 0.2 s. That sample feeds the
    p90 admission's ETA reads."""
    scoped()
    recorded: dict[str, float] = {}

    async def _record(owner, phase, duration_ms):
        recorded[owner] = duration_ms / 1000.0

    monkeypatch.setattr("poindexter.services.gpu_lease_stats.record_release", _record)
    gpu = _scheduler()
    render = _Hold(gpu, "image_gen", "sdxl")
    await render.enter()
    await asyncio.sleep(0.15)
    judge = _Hold(gpu, "ollama", "judge-model")
    await judge.enter()
    await asyncio.sleep(0.05)
    await judge.exit()
    await asyncio.sleep(0.1)
    await render.exit()
    await asyncio.sleep(0.05)  # let the fire-and-forget capture tasks run

    # The render held for ~0.3 s. Measured from the judge's acquire it read
    # ~0.15 s; a slow runner only makes the true value larger.
    assert recorded["image_gen"] >= 0.28, recorded
    assert recorded["ollama"] < recorded["image_gen"], recorded


@pytest.mark.asyncio
async def test_introspection_falls_back_to_the_session_still_running(scoped):
    scoped()
    gpu = _scheduler()
    render = _Hold(gpu, "image_gen", "sdxl", phase="featured_image")
    await render.enter()
    judge = _Hold(gpu, "ollama", "judge-model", phase="qa_judge")
    await judge.enter()
    assert gpu.status["owner"] == "ollama"  # the newest session

    await judge.exit()
    # The single-slot code cleared these while the render was still running.
    assert gpu._current_owner == "image_gen"
    assert gpu._current_phase == "featured_image"
    assert gpu.status["owner"] == "image_gen"

    await render.exit()
    assert gpu._current_owner is None
    assert gpu.status["owner"] is None


@pytest.mark.asyncio
async def test_timeout_names_the_session_on_the_callers_card(scoped):
    """The judge on GPU 1 is the newest session, but a GPU-0 writer is waiting
    for the render. The message must name the render."""
    scoped(gpu_lock_acquire_timeout_seconds="1")
    gpu = _scheduler()
    gpu._emit_lock_timeout_finding = lambda **kw: None
    render = _Hold(gpu, "image_gen", "sdxl")
    await render.enter()
    judge = _Hold(gpu, "ollama", "judge-model")
    await judge.enter()
    try:
        with pytest.raises(GpuLockTimeoutError) as err:
            async with gpu.lock("ollama", model="writer"):
                pytest.fail("the writer must not get GPU 0 during the render")
        assert "in-process holder 'image_gen' ('sdxl')" in str(err.value)
    finally:
        await judge.exit()
        await render.exit()


@pytest.mark.asyncio
async def test_admission_weighs_the_session_on_the_callers_card(scoped, monkeypatch):
    scoped()
    monkeypatch.setattr(
        "poindexter.services.gpu_lease_stats.read_stats", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(gs, "list_pg_holders", AsyncMock(return_value=[]))
    gpu = _scheduler()

    def _no_telemetry():
        # Only the ETA gate is under test; no VRAM reads means no fit gate.
        raise RuntimeError("no VRAM telemetry in this test")

    gpu._get_registry = _no_telemetry
    render = _Hold(gpu, "image_gen", "sdxl", phase="featured_image")
    await render.enter()
    judge = _Hold(gpu, "ollama", "judge-model", phase="qa_judge")
    await judge.enter()
    try:
        inputs = await gpu._assemble_admission_inputs(
            model=None, max_wait_s=120.0, lock_keys=[GPU0],
        )
        assert inputs.holder_key == ("image_gen", "featured_image")
        assert inputs.holder_source == "in_process"
    finally:
        await judge.exit()
        await render.exit()
