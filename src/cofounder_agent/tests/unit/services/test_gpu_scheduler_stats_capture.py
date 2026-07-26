"""gpu_scheduler release path → gpu_lease_stats capture (poindexter#914 P0,
plan Task A3). Every release — task-attributed or not — must fold into the
rolling stats, and a capture failure must never break the lock lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import services.gpu_lease_stats as stats_mod
from services.gpu_scheduler import GPUScheduler

pytestmark = pytest.mark.asyncio


def _quiet(gpu: GPUScheduler):
    """Stub the pg/gaming/eviction seams the existing suite stubs."""
    gpu._acquire_pg_advisory_lock = AsyncMock()
    gpu._release_pg_advisory_lock = AsyncMock()
    gpu._wait_for_gaming_clear = AsyncMock()
    gpu._unload_ollama_models = AsyncMock()
    return gpu


async def _drain_created_tasks():
    """Let fire-and-forget capture tasks run to completion."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_release_records_stats_without_task_id(monkeypatch):
    """The key behavior gpu_task_sessions does NOT have: capture fires for
    task-less sessions too (background jobs are most of the ETA data)."""
    calls: list[tuple] = []

    async def _rec(owner, phase, duration_ms):
        calls.append((owner, phase, duration_ms))

    monkeypatch.setattr(stats_mod, "record_release", _rec)
    gpu = _quiet(GPUScheduler())

    async with gpu.lock("ollama", model="m", phase="writer"):
        pass
    await _drain_created_tasks()

    assert len(calls) == 1
    owner, phase, duration_ms = calls[0]
    assert owner == "ollama"
    assert phase == "writer"
    assert duration_ms >= 0.0


async def test_phase_defaults_to_owner(monkeypatch):
    calls: list[tuple] = []

    async def _rec(owner, phase, duration_ms):
        calls.append((owner, phase, duration_ms))

    monkeypatch.setattr(stats_mod, "record_release", _rec)
    gpu = _quiet(GPUScheduler())

    async with gpu.lock("video"):
        pass
    await _drain_created_tasks()

    assert calls[0][0] == "video"
    assert calls[0][1] == "video"  # phase or owner


async def test_capture_failure_never_breaks_release(monkeypatch):
    """A raising record_release must leave the lock reacquirable."""

    async def _boom(owner, phase, duration_ms):
        raise RuntimeError("stats db down")

    monkeypatch.setattr(stats_mod, "record_release", _boom)
    gpu = _quiet(GPUScheduler())

    async with gpu.lock("ollama"):
        pass
    await _drain_created_tasks()

    # Lock must be fully released and reacquirable.
    async with gpu.lock("ollama"):
        pass
    await _drain_created_tasks()


async def test_reentrant_session_records_once(monkeypatch):
    """A nested gpu.lock() is a pass-through no-op — only the outer session
    is a real hold, so exactly one sample folds."""
    calls: list[tuple] = []

    async def _rec(owner, phase, duration_ms):
        calls.append((owner, phase, duration_ms))

    monkeypatch.setattr(stats_mod, "record_release", _rec)
    gpu = _quiet(GPUScheduler())

    async with gpu.lock("ollama", phase="outer"):
        async with gpu.lock("ollama", phase="inner"):
            pass
    await _drain_created_tasks()

    assert len(calls) == 1
    assert calls[0][1] == "outer"


async def test_capture_still_runs_alongside_task_session(monkeypatch):
    """task_id sessions record BOTH the economics row and the stats fold."""
    calls: list[tuple] = []

    async def _rec(owner, phase, duration_ms):
        calls.append((owner, phase, duration_ms))

    monkeypatch.setattr(stats_mod, "record_release", _rec)
    gpu = _quiet(GPUScheduler())

    with patch.object(gpu, "_record_task_session", new=AsyncMock()) as econ:
        async with gpu.lock("ollama", model="m", task_id="t-1", phase="writer"):
            pass
        await _drain_created_tasks()

    assert len(calls) == 1
    econ.assert_awaited_once()
