"""_PriorityGate contract tests (poindexter#914 P1, Task B3).

The gate replaces the scheduler's bare ``asyncio.Lock``. Its P1 obligation is
NEUTRALITY: every legacy caller acquires at one rank, so wake order must be
strict FIFO — behaviorally identical to ``asyncio.Lock`` (pinned here against
a real ``asyncio.Lock`` run side-by-side). Priority classes, aging promotion,
and the cancellation grant-chain race are the P1 additions, inert until
callers pass ``priority=``.
"""

from __future__ import annotations

import asyncio

import pytest

from services.gpu_scheduler import _PriorityGate


async def _spawn_waiter(gate: _PriorityGate, order: list[str], name: str, rank: int = 0):
    """Task that acquires at ``rank``, records its name, then releases."""

    async def _run():
        await gate.acquire(rank=rank)
        order.append(name)
        gate.release()

    task = asyncio.ensure_future(_run())
    await asyncio.sleep(0)  # let it park
    return task


@pytest.mark.asyncio
async def test_uncontended_acquire_is_immediate_and_locked():
    gate = _PriorityGate(aging_seconds=lambda: 0)
    assert not gate.locked()
    await gate.acquire()
    assert gate.locked()
    gate.release()
    assert not gate.locked()


@pytest.mark.asyncio
async def test_release_unheld_raises():
    with pytest.raises(RuntimeError):
        _PriorityGate(aging_seconds=lambda: 0).release()


@pytest.mark.asyncio
async def test_single_class_fifo_matches_asyncio_lock():
    """Same interleaved acquire sequence through both primitives → same order.
    This equivalence IS the P1 neutrality proof for legacy callers."""

    async def _drive(make_acquire, make_release):
        order: list[str] = []
        await make_acquire()  # holder
        tasks = []
        for name in ("a", "b", "c", "d"):
            async def _run(n=name):
                await make_acquire()
                order.append(n)
                make_release()

            tasks.append(asyncio.ensure_future(_run()))
            await asyncio.sleep(0)
        make_release()  # holder releases → chain drains
        await asyncio.gather(*tasks)
        return order

    lock = asyncio.Lock()
    baseline = await _drive(lock.acquire, lock.release)

    gate = _PriorityGate(aging_seconds=lambda: 0)
    got = await _drive(gate.acquire, gate.release)

    assert got == baseline == ["a", "b", "c", "d"]


@pytest.mark.asyncio
async def test_higher_class_jumps_queue():
    """rank 0 (pipeline) parked after rank 2 (background) wakes first; FIFO
    within each class is preserved."""
    gate = _PriorityGate(aging_seconds=lambda: 0)
    await gate.acquire()
    order: list[str] = []
    tasks = [
        await _spawn_waiter(gate, order, "bg-1", rank=2),
        await _spawn_waiter(gate, order, "bg-2", rank=2),
        await _spawn_waiter(gate, order, "pipe-1", rank=0),
        await _spawn_waiter(gate, order, "op-1", rank=1),
    ]
    gate.release()
    await asyncio.gather(*tasks)
    assert order == ["pipe-1", "op-1", "bg-1", "bg-2"]


@pytest.mark.asyncio
async def test_aging_promotes_background_past_later_pipeline():
    """A background waiter parked longer than the aging window outranks a
    pipeline waiter that arrived after it — starvation-proof."""
    gate = _PriorityGate(aging_seconds=lambda: 300)
    await gate.acquire()
    order: list[str] = []
    bg = await _spawn_waiter(gate, order, "bg", rank=2)
    pipe = await _spawn_waiter(gate, order, "pipe", rank=0)
    # Backdate the background waiter two full aging periods: rank 2 → 0, and
    # its earlier enqueue seq then wins the FIFO tie-break against "pipe".
    gate._waiters[0].enqueued -= 650
    gate.release()
    await asyncio.gather(bg, pipe)
    assert order == ["bg", "pipe"]


@pytest.mark.asyncio
async def test_aging_never_jumps_same_rank_elder():
    """Aging promotes ACROSS classes; within a class the earlier seq wins
    regardless of wait time (no churn among equals)."""
    gate = _PriorityGate(aging_seconds=lambda: 300)
    await gate.acquire()
    order: list[str] = []
    first = await _spawn_waiter(gate, order, "first", rank=0)
    second = await _spawn_waiter(gate, order, "second", rank=0)
    gate._waiters[1].enqueued -= 10_000  # even absurd waiting can't reorder rank 0
    gate.release()
    await asyncio.gather(first, second)
    assert order == ["first", "second"]


@pytest.mark.asyncio
async def test_cancelled_parked_waiter_removed_without_wedging():
    gate = _PriorityGate(aging_seconds=lambda: 0)
    await gate.acquire()
    order: list[str] = []
    doomed = await _spawn_waiter(gate, order, "doomed")
    survivor = await _spawn_waiter(gate, order, "survivor")
    doomed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await doomed
    gate.release()
    await survivor
    assert order == ["survivor"]
    assert not gate.locked()


@pytest.mark.asyncio
async def test_grant_concurrent_with_cancellation_passes_to_next():
    """The asyncio.Lock race: a waiter granted and cancelled in the same tick
    must hand the gate to the next waiter, not strand it held."""
    gate = _PriorityGate(aging_seconds=lambda: 0)
    await gate.acquire()
    order: list[str] = []
    racer = await _spawn_waiter(gate, order, "racer")
    survivor = await _spawn_waiter(gate, order, "survivor")
    gate.release()  # resolves racer's future…
    racer.cancel()  # …and cancels it before it resumes
    with pytest.raises(asyncio.CancelledError):
        await racer
    await survivor
    assert order == ["survivor"]
    assert not gate.locked()


@pytest.mark.asyncio
async def test_wait_for_timeout_leaves_gate_consistent():
    """asyncio.wait_for cancellation (the lock()-timeout path) removes the
    waiter; the holder can still release and re-acquire normally."""
    gate = _PriorityGate(aging_seconds=lambda: 0)
    await gate.acquire()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(gate.acquire(), timeout=0.05)
    gate.release()
    assert not gate.locked()
    await gate.acquire()  # sane after the timeout
    gate.release()


@pytest.mark.asyncio
async def test_broken_aging_callable_degrades_to_no_aging():
    def _boom() -> int:
        raise RuntimeError("cfg down")

    gate = _PriorityGate(aging_seconds=_boom)
    await gate.acquire()
    order: list[str] = []
    a = await _spawn_waiter(gate, order, "a", rank=2)
    b = await _spawn_waiter(gate, order, "b", rank=0)
    gate.release()
    await asyncio.gather(a, b)
    assert order == ["b", "a"]  # ranks still honored; only aging disabled
