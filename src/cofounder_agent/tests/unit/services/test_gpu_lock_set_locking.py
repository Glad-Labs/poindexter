"""Set-based GPU locking: the concurrency half (poindexter#3457 Phase 2).

This is the mutex. A partial-acquire or release-ordering bug here deadlocks
content generation, so every property the design leans on is pinned:

  * disjoint device sets run CONCURRENTLY (the point of the change)
  * overlapping sets still SERIALISE (the safety property)
  * gates are taken in ascending key order (the deadlock proof)
  * a failed acquire leaves NOTHING held (all-or-nothing)
  * an empty set takes no lock at all
  * scoping off behaves exactly as before

Scope resolution itself is tested in test_gpu_lock_device_scoping.py.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from services import gpu_scheduler as gs
from services.gpu_scheduler import GPU_ADVISORY_LOCK_KEY, GPUScheduler
from services.site_config import SiteConfig


def _scheduler():
    gpu = GPUScheduler()
    # Hermetic: no real asyncpg connection, no image-gen POST.
    gpu._unload_image_gen = AsyncMock()
    gpu._acquire_pg_advisory_lock = AsyncMock()
    gpu._release_pg_advisory_lock = AsyncMock()
    return gpu


@pytest.fixture
def scoped(monkeypatch):
    """Enable scoping with an explicit map + a pinned judge model."""

    def _apply(scopes=None, enabled="true"):
        cfg = SiteConfig(initial_config={
            # Pinned so the suite does not depend on whether the RUNNER is
            # containerised — ambient detection returns "" in a container,
            # which fails closed to the whole-GPU key and silently turns every
            # concurrency assertion below into a serialisation test.
            "gpu_lock_node_id": "test-node",
            "gpu_lock_per_device_enabled": enabled,
            "gpu_lock_scopes": json.dumps(
                scopes or {"render": [0], "qa_judge": [1], "llm_primary": [0]}
            ),
            "plugin.llm_provider.litellm": json.dumps(
                {"config": {"model_api_base_overrides": {
                    "ollama/judge-model": "http://x:11435"}}}
            ),
        })
        monkeypatch.setattr(gs, "_sc", lambda: cfg)
        return cfg

    return _apply


# --- the point of the change -------------------------------------------------


@pytest.mark.asyncio
async def test_disjoint_scopes_run_concurrently(scoped):
    """A judge on GPU 1 must not queue behind a render on GPU 0.

    This is the 238-skips-per-week defect in one test.
    """
    scoped()
    gpu = _scheduler()
    both_inside = asyncio.Event()

    async def render():
        async with gpu.lock("image_gen", model="sdxl"):
            await asyncio.wait_for(both_inside.wait(), timeout=2.0)

    async def judge():
        async with gpu.lock("ollama", model="judge-model"):
            both_inside.set()

    await asyncio.wait_for(asyncio.gather(render(), judge()), timeout=3.0)


@pytest.mark.asyncio
async def test_overlapping_scopes_serialise(scoped):
    """llm_primary and render both hold GPU 0 — they must NOT overlap."""
    scoped()
    gpu = _scheduler()
    concurrent = False
    inside = 0

    async def hold(owner, model):
        nonlocal concurrent, inside
        async with gpu.lock(owner, model=model):
            inside += 1
            concurrent = concurrent or inside > 1
            await asyncio.sleep(0.05)
            inside -= 1

    await asyncio.gather(hold("image_gen", "sdxl"), hold("ollama", "writer"))
    assert not concurrent, "two workloads sharing GPU 0 ran at the same time"


@pytest.mark.asyncio
async def test_scoping_disabled_serialises_everything(scoped):
    """The shipped default: one key, one gate, today's behaviour."""
    scoped(enabled="false")
    gpu = _scheduler()
    concurrent = False
    inside = 0

    async def hold(owner, model):
        nonlocal concurrent, inside
        async with gpu.lock(owner, model=model):
            inside += 1
            concurrent = concurrent or inside > 1
            await asyncio.sleep(0.05)
            inside -= 1

    await asyncio.gather(hold("image_gen", "sdxl"), hold("ollama", "judge-model"))
    assert not concurrent


# --- the deadlock proof ------------------------------------------------------


@pytest.mark.asyncio
async def test_gates_are_acquired_in_ascending_key_order(scoped):
    """Two callers with overlapping sets must contend on their lowest shared
    key first, or each can hold half of the other's pair and deadlock."""
    scoped({"render": [0], "qa_judge": [1], "llm_primary": [3, 1, 0, 2]})
    gpu = _scheduler()
    order: list[int] = []
    original = gpu._gate_for

    def _spy(key):
        order.append(key)
        return original(key)

    gpu._gate_for = _spy
    async with gpu.lock("ollama", model="writer"):
        pass
    assert order == sorted(order), f"gates taken out of order: {order}"
    assert len(order) == 4


@pytest.mark.asyncio
async def test_multi_key_caller_holds_every_key_in_its_set(scoped):
    scoped({"render": [0], "qa_judge": [1], "llm_primary": [0, 1]})
    gpu = _scheduler()
    async with gpu.lock("ollama", model="writer"):
        assert len(gpu._held_keys) == 2
        assert gpu._held_keys == sorted(gpu._held_keys)
    assert gpu._held_keys == []


# --- all-or-nothing ----------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_acquire_releases_everything_already_held(scoped):
    """If the 2nd of 3 gates times out, the 1st must not stay held.

    A leaked gate wedges every later caller in this process on a lock nobody
    owns — the worst outcome available here.
    """
    scoped({"llm_primary": [0, 1, 2]})
    gpu = _scheduler()
    keys = gs.resolve_lock_keys("ollama", "writer")
    assert len(keys) == 3

    # Pre-hold the MIDDLE key from "another caller" so our acquire stalls there.
    blocker = gpu._gate_for(keys[1])
    await blocker.acquire(rank=0)

    with pytest.raises((TimeoutError, gs.GpuLockTimeoutError)):
        await gpu._acquire_gates(keys, rank=0, timeout_s=0.15)

    assert not gpu._gate_for(keys[0]).locked(), "first gate leaked after rollback"
    assert not gpu._gate_for(keys[2]).locked(), "third gate should never be taken"
    assert gpu._held_keys == []
    blocker.release()


@pytest.mark.asyncio
async def test_rollback_leaves_the_scheduler_usable(scoped):
    """After a failed acquire the next caller must still get through."""
    scoped({"llm_primary": [0, 1]})
    gpu = _scheduler()
    keys = gs.resolve_lock_keys("ollama", "writer")
    blocker = gpu._gate_for(keys[1])
    await blocker.acquire(rank=0)
    with pytest.raises((TimeoutError, gs.GpuLockTimeoutError)):
        await gpu._acquire_gates(keys, rank=0, timeout_s=0.15)
    blocker.release()

    async with gpu.lock("ollama", model="writer"):
        pass  # must not hang


# --- empty scope -------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_scope_takes_no_lock_and_never_blocks(scoped):
    """Managed API / serverless / CPU judge: nothing to contend for.

    Two such callers must run concurrently, and no pg connection is opened.
    """
    scoped({"render": [0], "qa_judge": [], "llm_primary": [0]})
    gpu = _scheduler()
    both = asyncio.Event()

    async def judge(first):
        async with gpu.lock("ollama", model="judge-model"):
            if first:
                await asyncio.wait_for(both.wait(), timeout=2.0)
            else:
                both.set()

    await asyncio.wait_for(asyncio.gather(judge(True), judge(False)), timeout=3.0)
    gpu._acquire_pg_advisory_lock.assert_not_awaited()


# --- compatibility -----------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_lock_attribute_is_the_whole_gpu_gate():
    """Tests and callers poke ``gpu._lock`` directly; it must stay that gate."""
    gpu = _scheduler()
    assert gpu._gates[GPU_ADVISORY_LOCK_KEY] is gpu._lock


@pytest.mark.asyncio
async def test_is_busy_reflects_any_card(scoped):
    scoped()
    gpu = _scheduler()
    assert not gpu._any_gate_locked()
    async with gpu.lock("ollama", model="judge-model"):
        assert gpu._any_gate_locked(), "a held judge gate must read as busy"
    assert not gpu._any_gate_locked()


@pytest.mark.asyncio
async def test_nested_lock_still_does_not_deadlock(scoped):
    """Reentrancy must survive the multi-gate rewrite."""
    scoped()
    gpu = _scheduler()

    async def nested():
        async with gpu.lock("ollama", model="writer"):
            async with gpu.lock("ollama", model="writer"):
                return "ok"

    assert await asyncio.wait_for(nested(), timeout=2.0) == "ok"


# --- the scoped/unscoped transition (the gap that let the live bug through) --


class _FakeConn:
    """Records the advisory-lock SQL the REAL _acquire_pg_advisory_lock runs.

    Deliberately stubs at the asyncpg boundary, not at
    `_acquire_pg_advisory_lock` — an earlier version of these tests replaced
    that method with a fake and then asserted on the fake, so removing the
    shared-base acquire entirely still "passed".
    """

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    async def execute(self, sql, *args):
        mode = (
            "shared" if "advisory_lock_shared" in sql
            else "unlock_shared" if "unlock_shared" in sql
            else "unlock" if "unlock" in sql
            else "exclusive"
        )
        self.calls.append((mode, args[0] if args else None))

    async def close(self):
        pass

    def terminate(self):
        pass


async def _record_acquire(gpu, keys):
    conn = _FakeConn()
    import services.gpu_scheduler as _gs

    async def _connect(dsn):
        return conn

    orig_connect = None
    import asyncpg as _asyncpg

    orig_connect = _asyncpg.connect
    _asyncpg.connect = _connect
    try:
        _gs.resolve_database_url = lambda: "postgres://stub"  # noqa: ARG005
        await gpu._acquire_pg_advisory_lock(timeout_s=None, keys=keys)
    finally:
        _asyncpg.connect = orig_connect
    return conn.calls


@pytest.mark.asyncio
@pytest.mark.gpu_lock_real_db
async def test_scoped_session_also_holds_the_base_key_in_shared_mode(scoped):
    """Flipping the setting is NOT atomic across processes.

    Each process reads its own SiteConfig cache, so for a window one worker
    takes device keys while another still takes the whole-GPU key — and two
    different keys exclude nothing. A scoped session therefore also holds the
    base key SHARED: shared holders do not block each other (scoped
    concurrency survives) but an unscoped EXCLUSIVE holder blocks them and is
    blocked by them.

    Verified against real Postgres semantics: exclusive try-lock fails while a
    shared holder exists, and two shared holders coexist.
    """
    scoped()
    gpu = _scheduler()
    del gpu._acquire_pg_advisory_lock  # use the REAL implementation
    calls = await _record_acquire(gpu, [111, 222])
    assert ("shared", GPU_ADVISORY_LOCK_KEY) in calls, (
        "a scoped session must hold the base key SHARED so an unscoped caller "
        "still serialises against it"
    )
    assert ("exclusive", GPU_ADVISORY_LOCK_KEY) not in calls, (
        "taking the base key exclusively would serialise every scoped caller"
    )
    assert [c for c in calls if c[0] == "exclusive"] == [
        ("exclusive", 111), ("exclusive", 222)
    ]


@pytest.mark.asyncio
@pytest.mark.gpu_lock_real_db
async def test_unscoped_session_takes_the_base_key_exclusively(scoped):
    """With scoping off there is one key and it is exclusive — today's shape."""
    scoped(enabled="false")
    gpu = _scheduler()
    del gpu._acquire_pg_advisory_lock
    calls = await _record_acquire(gpu, [GPU_ADVISORY_LOCK_KEY])
    assert calls == [("exclusive", GPU_ADVISORY_LOCK_KEY)], (
        "an unscoped session must NOT take the shared base — it has to block "
        "scoped sessions, not coexist with them"
    )
