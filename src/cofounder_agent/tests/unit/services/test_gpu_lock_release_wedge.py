"""A cancelled release must not leave the in-process gate held (poindexter#967).

2026-08-01, operator box: a long video render acquired `gpu.lock("video")`,
rendered clean in 167.5s and released. The SHORT render node then waited on the
same lock and timed out after the full 900s:

    GpuLockTimeoutError: gpu.lock('video') timed out after 900s
                         waiting for in-process holder None (None)

"held but holder None" is the signature of a half-completed release: holder
metadata is cleared at the top of the `finally`, so the gate release is the
step that did not run. `_release_pg_advisory_lock` catches TimeoutError and
Exception, so an ordinary failure cannot cause it — but `asyncio.CancelledError`
is a BaseException, and this path runs inside the media stages' `asyncio.wait_for`.
One cancellation wedged every later same-owner acquire in the process until a
worker restart.
"""

from __future__ import annotations

import asyncio

import pytest

from poindexter.services.gpu_scheduler import GPUScheduler

pytestmark = pytest.mark.unit


def _hermetic(gpu: GPUScheduler) -> GPUScheduler:
    """No real asyncpg, no image-gen POST, no unload."""
    async def _noop(*a, **k):
        return None

    gpu._acquire_pg_advisory_lock = _noop          # type: ignore[assignment]
    gpu._release_pg_advisory_lock = _noop          # type: ignore[assignment]
    gpu._unload_ollama_models = _noop              # type: ignore[assignment]
    gpu._unload_image_gen = _noop                  # type: ignore[assignment]
    return gpu


async def _hold_and_release(gpu: GPUScheduler, owner: str = "video") -> None:
    async with gpu.lock(owner, model="wan", phase="render"):
        pass


class TestReleaseNeverWedgesTheGate:
    async def test_cancelled_pg_release_still_frees_the_gate(self):
        """The #967 shape. A BaseException out of the pg step must not keep the
        in-process gate — the next acquirer would starve the full ceiling."""
        gpu = _hermetic(GPUScheduler())

        async def _cancelled(*a, **k):
            raise asyncio.CancelledError()

        gpu._release_pg_advisory_lock = _cancelled  # type: ignore[assignment]

        with pytest.raises(asyncio.CancelledError):
            await _hold_and_release(gpu)

        assert not gpu._lock.locked(), (
            "in-process gate still held after a cancelled release — this is "
            "the 900s starvation in poindexter#967"
        )
        assert gpu._current_owner is None

    async def test_raising_pg_release_still_frees_the_gate(self):
        gpu = _hermetic(GPUScheduler())

        async def _boom(*a, **k):
            raise RuntimeError("connection reset")

        gpu._release_pg_advisory_lock = _boom  # type: ignore[assignment]

        with pytest.raises(RuntimeError):
            await _hold_and_release(gpu)
        assert not gpu._lock.locked()

    async def test_a_second_acquirer_proceeds_after_a_cancelled_release(self):
        """The consequence the issue actually reports: the NEXT render.
        Without the fix this waits out `gpu_lock_acquire_timeout_seconds`."""
        gpu = _hermetic(GPUScheduler())

        async def _cancelled(*a, **k):
            raise asyncio.CancelledError()

        gpu._release_pg_advisory_lock = _cancelled  # type: ignore[assignment]
        with pytest.raises(asyncio.CancelledError):
            await _hold_and_release(gpu)

        # Restore a healthy release and take the lock again — must not block.
        async def _noop(*a, **k):
            return None

        gpu._release_pg_advisory_lock = _noop  # type: ignore[assignment]
        await asyncio.wait_for(_hold_and_release(gpu), timeout=5.0)
        assert not gpu._lock.locked()

    async def test_happy_path_still_releases_pg_before_the_gate(self):
        """Ordering intent is preserved: the cross-process barrier stays up
        until we are done, it just no longer gates in-process correctness."""
        gpu = _hermetic(GPUScheduler())
        order: list[str] = []

        async def _pg(*a, **k):
            order.append("pg")

        real_gates = gpu._release_gates

        def _gates(keys):
            order.append("gates")
            real_gates(keys)

        gpu._release_pg_advisory_lock = _pg     # type: ignore[assignment]
        gpu._release_gates = _gates             # type: ignore[assignment]

        await _hold_and_release(gpu)
        assert order == ["pg", "gates"]
        assert not gpu._lock.locked()


class TestCancelledAcquireNeverWedgesTheGate:
    """The same wedge on the ACQUIRE side.

    `lock()` takes the in-process gates, then parks at the pg step behind
    whoever holds the card in another container. A cancellation there (a
    stage's asyncio.wait_for expiring, a flow cancel) is a CancelledError. The
    `except GpuLockTimeoutError` branch never saw it, so the gates stayed held
    with no session left to release them, and every later caller for those
    cards in the process waited out the full ceiling.
    """

    async def test_cancel_while_parked_at_the_pg_step_frees_the_gate(self):
        gpu = _hermetic(GPUScheduler())
        parked = asyncio.Event()

        async def _parked_at_pg(*a, **k):
            parked.set()
            await asyncio.Event().wait()  # the cross-process holder never lets go

        gpu._acquire_pg_advisory_lock = _parked_at_pg  # type: ignore[assignment]
        waiter = asyncio.create_task(_hold_and_release(gpu))
        await asyncio.wait_for(parked.wait(), timeout=2.0)
        assert gpu._any_gate_locked(), "precondition: the gate is held while parked"

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert not gpu._any_gate_locked()
        assert gpu._held_keys == []
        # The next caller gets straight in.
        gpu._acquire_pg_advisory_lock = _hermetic(GPUScheduler())._acquire_pg_advisory_lock  # type: ignore[assignment]
        await asyncio.wait_for(_hold_and_release(gpu), timeout=2.0)

    @pytest.mark.gpu_lock_real_db
    async def test_cancel_mid_pg_wait_terminates_the_connection(self, monkeypatch):
        """Left alone, the half-acquired connection was dropped to the garbage
        collector while the traceback kept it alive, still holding the shared
        base key it had already taken."""
        import asyncpg

        from poindexter.brain import bootstrap

        class _Conn:
            def __init__(self):
                self.terminated = False
                self.calls = 0

            async def execute(self, sql, key):
                self.calls += 1
                if self.calls > 1:  # shared base granted, device key never is
                    await asyncio.Event().wait()

            async def close(self):
                raise AssertionError("a parked session must be terminated, not closed")

            def terminate(self):
                self.terminated = True

        conn = _Conn()

        async def _connect(dsn, **kwargs):
            return conn

        monkeypatch.setattr(asyncpg, "connect", _connect)
        monkeypatch.setattr(bootstrap, "resolve_database_url", lambda *a, **k: "postgresql://fake/db")

        gpu = GPUScheduler()
        task = asyncio.create_task(
            gpu._acquire_pg_advisory_lock(timeout_s=None, keys=[111, 222])
        )
        for _ in range(50):
            if conn.calls >= 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert conn.terminated
        assert gpu._pg_holds == []

    async def test_cancel_during_the_mirror_cleanup_after_acquire_drops_it_all(
        self, monkeypatch,
    ):
        """The narrow window: pg acquired, then the task is cancelled inside the
        waiter-mirror cleanup, before the session's own try/finally exists."""
        from unittest.mock import MagicMock

        import poindexter.services.gpu_scheduler as gs

        gpu = _hermetic(GPUScheduler())
        conn = MagicMock()
        hold = gs._PgHold(conn=conn, keys=[gs.GPU_ADVISORY_LOCK_KEY], shared_base=False)

        async def _acquired(*a, **k):
            gpu._pg_holds.append(hold)
            return hold

        async def _cancelled_cleanup(task, state):
            raise asyncio.CancelledError()

        gpu._acquire_pg_advisory_lock = _acquired  # type: ignore[assignment]
        monkeypatch.setattr(gs, "_finish_waiter_mirror", _cancelled_cleanup)

        with pytest.raises(asyncio.CancelledError):
            await _hold_and_release(gpu)

        conn.terminate.assert_called_once()
        assert gpu._pg_holds == []
        assert not gpu._any_gate_locked()
        assert gpu._sessions == []
