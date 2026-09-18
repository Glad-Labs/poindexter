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

        def _gates():
            order.append("gates")
            real_gates()

        gpu._release_pg_advisory_lock = _pg     # type: ignore[assignment]
        gpu._release_gates = _gates             # type: ignore[assignment]

        await _hold_and_release(gpu)
        assert order == ["pg", "gates"]
        assert not gpu._lock.locked()
