"""stable-audio /unload — hard-unload contract (poindexter#999).

The third instance of one defect, and the most expensive: this server's idle
unloader drops the model objects but the process keeps torch's caching-allocator
pool, and unlike wan / image-gen it had **no hard-unload contract and no seat on
the reclaim ladder** — so nothing in the system could reach it.

Measured on the operator box 2026-08-07:

===========================  ==========
step                         GPU0
===========================  ==========
before                       13071 MiB
after soft ``POST /unload``  13068 MiB   (freed 3 MiB)
after process restart         2107 MiB   (freed 10.96 GiB)
===========================  ==========

…all of it while ``/health`` reported ``model_loaded: false``. wan peaks at
25.4 GiB on a 31.8 GiB card, so that ghost by itself made every hero render
arithmetically impossible, and it is why ``vram_reclaim_ineffective`` kept
firing: the ladder was faithfully evicting four services that between them held
almost nothing.

Loader mirrors test_wan_server_unload.py (scoped torch stub, popped after exec
so a bare ModuleType can't poison later ``import torch``).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "stable-audio-server.py").exists():
            return parent
    raise RuntimeError(
        "could not locate scripts/stable-audio-server.py from " + str(start)
    )


def _load_server():
    stub_installed = False
    if "torch" not in sys.modules:
        torch_stub = types.ModuleType("torch")
        torch_stub.__spec__ = importlib.util.spec_from_loader("torch", loader=None)
        torch_stub.float16 = "float16"
        torch_stub.bfloat16 = "bfloat16"
        torch_stub.cuda = types.SimpleNamespace(
            is_available=lambda: True,
            memory_allocated=lambda idx=0: 0,
            memory_reserved=lambda idx=0: 0,
            empty_cache=lambda: None,
            get_device_name=lambda idx=0: "stub",
        )
        sys.modules["torch"] = torch_stub
        stub_installed = True

    path = _find_repo_root(Path(__file__)) / "scripts" / "stable-audio-server.py"
    spec = importlib.util.spec_from_file_location(
        "stable_audio_server_unload_under_test", path,
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


sa = _load_server()


def _patch_reserved(mb: int):
    return patch.object(
        sa.torch.cuda, "memory_reserved", lambda idx=0: mb * 1024 * 1024,
    )


@pytest.fixture(autouse=True)
def _reset_state():
    sa._state.inflight = 0
    yield
    sa._state.inflight = 0


@pytest.mark.unit
def test_default_unload_request_is_soft():
    assert sa.UnloadRequest().hard is False


@pytest.mark.unit
def test_soft_unload_does_not_exit_process():
    """The pre-existing no-body contract must be unchanged — soft callers just
    want the model dropped."""
    async def body():
        with patch.object(sa, "_unload_model") as unload_mock, \
             patch.object(os, "_exit") as mock_exit:
            result = await sa.unload()
        unload_mock.assert_called_once()
        mock_exit.assert_not_called()
        assert result["status"] == "unloaded"

    asyncio.run(body())


@pytest.mark.unit
def test_hard_unload_exits_when_reserved_above_floor():
    """The whole point: a fat reserved pool is only returned by a process exit."""
    async def body():
        with patch.object(sa, "_unload_model") as unload_mock, \
             _patch_reserved(10952), \
             patch.object(os, "_exit") as mock_exit:
            await sa.unload(sa.UnloadRequest(hard=True))
        unload_mock.assert_called_once()
        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


@pytest.mark.unit
def test_hard_unload_declines_below_floor():
    """Below the floor an exit reclaims nothing and buys a cold start — the
    image-gen lesson (~24 consecutive no-op exits before its gate existed)."""
    async def body():
        with patch.object(sa, "_unload_model"), \
             _patch_reserved(64), \
             patch.object(os, "_exit") as mock_exit:
            result = await sa.unload(sa.UnloadRequest(hard=True))
        mock_exit.assert_not_called()
        assert result["status"] == "nothing_to_reclaim"
        assert result["vram_reserved_mb"] == 64

    asyncio.run(body())


@pytest.mark.unit
def test_hard_unload_gates_on_reserved_not_allocated():
    """``_unload_model`` just dropped every live tensor, so allocated is ~0 by
    construction — gating on it would make the exit unreachable, which is
    exactly how 10.96 GiB stayed pinned."""
    async def body():
        with patch.object(sa, "_unload_model"), \
             patch.object(sa.torch.cuda, "memory_allocated", lambda idx=0: 0), \
             _patch_reserved(10952), \
             patch.object(os, "_exit") as mock_exit:
            await sa.unload(sa.UnloadRequest(hard=True))
        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


@pytest.mark.unit
def test_hard_unload_exits_even_when_model_already_none():
    """The observed state: ``model_loaded: false`` and 10,952 MiB still held.
    The idle unloader usually wins the race — the CUDA context is what squats,
    so an already-None model must NOT short-circuit the exit."""
    async def body():
        sa._state.model = None
        with _patch_reserved(10952), patch.object(os, "_exit") as mock_exit:
            await sa.unload(sa.UnloadRequest(hard=True))
        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


@pytest.mark.unit
@pytest.mark.parametrize("hard", [True, False])
def test_unload_declines_while_a_generation_is_in_flight(hard):
    """The reclaim ladder fires hard=True whenever the render-GPU gate looks
    unhealthy — and a generation in progress IS part of that state. Obeying
    would kill the work the reclaim exists to make room for, which is exactly
    how the wan-server discarded every hero clip on 2026-08-06/-07."""
    async def body():
        sa._state.inflight = 1
        with patch.object(sa, "_unload_model") as unload_mock, \
             _patch_reserved(10952), \
             patch.object(os, "_exit") as mock_exit:
            result = await sa.unload(sa.UnloadRequest(hard=hard))
        unload_mock.assert_not_called()
        mock_exit.assert_not_called()
        assert result["status"] == "busy_generation_in_flight"
        assert result["inflight"] == 1

    asyncio.run(body())


class _StopWatchdog(Exception):
    """Break the watchdog's infinite loop after one pass."""


def _run_one_watchdog_pass():
    """Drive exactly one iteration of the real ``_watchdog`` loop.

    It sleeps first, so the second sleep call is the end of pass one.
    """
    calls = {"n": 0}

    async def _fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > 1:
            raise _StopWatchdog

    async def body():
        with patch.object(sa.asyncio, "sleep", _fake_sleep):
            try:
                await sa._watchdog()
            except _StopWatchdog:
                pass

    asyncio.run(body())


@pytest.mark.unit
def test_idle_watchdog_hard_exits_the_reserved_pool():
    """Self-driven reclaim — the path that actually heals the squat.

    Without it the pool only clears if some consumer happens to call /unload,
    and for ~11 GiB sitting on the render GPU nothing ever did: the reclaim
    ladder did not know this service existed.
    """
    sa._state.model = None
    sa._state.degraded = False
    sa._state.last_used = 1.0  # long past the idle timeout
    with patch.object(sa.time, "monotonic", lambda: 1.0 + sa.IDLE_TIMEOUT + 60), \
         patch.object(sa, "_hard_exit_if_reserved_pool") as hard_mock:
        _run_one_watchdog_pass()
    hard_mock.assert_called_once_with(quiet_skip=True)


@pytest.mark.unit
def test_idle_watchdog_leaves_an_in_flight_generation_alone():
    """A render in progress is not idle, however stale ``last_used`` looks —
    stamping it happens on response exit, so mid-generation it is always old."""
    sa._state.model = None
    sa._state.degraded = False
    sa._state.last_used = 1.0
    sa._state.inflight = 1
    with patch.object(sa.time, "monotonic", lambda: 1.0 + sa.IDLE_TIMEOUT + 60), \
         patch.object(sa, "_hard_exit_if_reserved_pool") as hard_mock:
        _run_one_watchdog_pass()
    hard_mock.assert_not_called()


@pytest.mark.unit
def test_health_exposes_the_reserved_pool():
    """The number that mattered and wasn't visible: health said
    ``model_loaded: false`` while the process held 10,952 MiB, so 'is it
    holding VRAM?' needed nvidia-smi and a PID lookup to answer."""
    async def body():
        with _patch_reserved(10952):
            out = await sa.health()
        assert out["vram_reserved_mb"] == 10952
        assert out["hard_unload_min_reserved_mb"] == sa.HARD_UNLOAD_MIN_RESERVED_MB
        assert out["inflight"] == 0

    asyncio.run(body())
