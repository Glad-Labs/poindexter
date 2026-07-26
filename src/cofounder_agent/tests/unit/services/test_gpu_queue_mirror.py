"""gpu_queue mirroring — module seam + lock() wiring (poindexter#914 P0,
plan Task A4). The uncontended fast path stays zero-I/O; contended waits
insert a row and delete it on acquire, timeout, and cancellation alike."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

import services.gpu_queue_mirror as mirror
from services.gpu_scheduler import GpuLockTimeoutError, GPUScheduler

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Module seam (stubbed connection)
# ---------------------------------------------------------------------------


class _Conn:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.fetched: list[str] = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetch(self, sql, *args):
        self.fetched.append(sql)
        return []

    async def close(self):
        return None


async def test_enqueue_inserts_and_reaps(monkeypatch):
    conn = _Conn()

    async def _fake_connect():
        return conn

    monkeypatch.setattr(mirror, "_connect", _fake_connect)
    row_id = await mirror.enqueue("ollama", model="m", phase="writer", priority="pipeline")

    assert row_id is not None
    uuid.UUID(row_id)  # valid uuid
    sqls = [s for s, _ in conn.executed]
    assert any("DELETE FROM gpu_queue WHERE enqueued_at" in s for s in sqls)  # reap
    assert any("INSERT INTO gpu_queue" in s for s in sqls)
    ins_args = next(a for s, a in conn.executed if "INSERT" in s)
    assert ins_args[2] == "ollama" and ins_args[4] == "writer"


async def test_enqueue_unavailable_returns_none(monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(mirror, "_connect", _none)
    assert await mirror.enqueue("ollama") is None


async def test_dequeue_none_is_noop(monkeypatch):
    called = False

    async def _connect():
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(mirror, "_connect", _connect)
    await mirror.dequeue(None)
    assert not called  # short-circuits before any I/O


async def test_list_waiters_honest_empty_on_failure(monkeypatch):
    async def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mirror, "_connect", _boom)
    assert await mirror.list_waiters() == []


# ---------------------------------------------------------------------------
# lock() wiring
# ---------------------------------------------------------------------------


def _quiet(gpu: GPUScheduler) -> GPUScheduler:
    gpu._acquire_pg_advisory_lock = AsyncMock()
    gpu._release_pg_advisory_lock = AsyncMock()
    gpu._wait_for_gaming_clear = AsyncMock()
    gpu._unload_ollama_models = AsyncMock()
    return gpu


async def test_uncontended_path_never_touches_mirror(monkeypatch):
    enq = AsyncMock(return_value="row-1")
    deq = AsyncMock()
    monkeypatch.setattr(mirror, "enqueue", enq)
    monkeypatch.setattr(mirror, "dequeue", deq)
    gpu = _quiet(GPUScheduler())

    async with gpu.lock("ollama"):
        pass

    enq.assert_not_awaited()
    deq.assert_not_awaited()


async def test_contended_wait_enqueues_then_dequeues_on_acquire(monkeypatch):
    enq = AsyncMock(return_value="row-2")
    deq = AsyncMock()
    monkeypatch.setattr(mirror, "enqueue", enq)
    monkeypatch.setattr(mirror, "dequeue", deq)
    gpu = _quiet(GPUScheduler())

    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with gpu.lock("image_gen"):
            entered.set()
            await release.wait()

    async def waiter():
        await entered.wait()
        async with gpu.lock("ollama", model="m", phase="writer"):
            pass

    h = asyncio.create_task(holder())
    w = asyncio.create_task(waiter())
    await entered.wait()
    await asyncio.sleep(0.05)  # let the waiter reach the contended branch
    release.set()
    await asyncio.gather(h, w)

    enq.assert_awaited_once()
    assert enq.await_args.args == ("ollama",)
    assert enq.await_args.kwargs["phase"] == "writer"
    deq.assert_awaited_once_with("row-2")


async def test_timeout_still_dequeues(monkeypatch):
    enq = AsyncMock(return_value="row-3")
    deq = AsyncMock()
    monkeypatch.setattr(mirror, "enqueue", enq)
    monkeypatch.setattr(mirror, "dequeue", deq)
    gpu = _quiet(GPUScheduler())
    # 1s acquire ceiling so the test is fast.
    monkeypatch.setattr(
        "services.gpu_scheduler._cfg_int", lambda key, default: 1
    )

    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with gpu.lock("image_gen"):
            entered.set()
            await release.wait()

    h = asyncio.create_task(holder())
    await entered.wait()

    with pytest.raises(GpuLockTimeoutError):
        async with gpu.lock("ollama"):
            pass  # pragma: no cover — never acquired

    release.set()
    await h

    enq.assert_awaited_once()
    deq.assert_awaited_once_with("row-3")


async def test_mirror_failure_never_blocks_the_wait(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("mirror db down")

    monkeypatch.setattr(mirror, "enqueue", _boom)
    monkeypatch.setattr(mirror, "dequeue", AsyncMock())
    gpu = _quiet(GPUScheduler())

    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with gpu.lock("image_gen"):
            entered.set()
            await release.wait()

    h = asyncio.create_task(holder())
    await entered.wait()

    async def waiter():
        async with gpu.lock("ollama"):
            return "acquired"

    w = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    release.set()
    assert await w == "acquired"
    await h
