"""wan-server idle unloader — never kill an in-flight generation's response.

The idle unloader hard-exits the process to return the CUDA context to the
host. Its trigger is ``time.time() - state.last_used > IDLE_TIMEOUT_S``, and
``last_used`` was only stamped when a generation FINISHED — but a 480p i2v
hero takes ~147s against the 120s default timeout. So mid-generation the tick
read a stale ``last_used``, decided to unload, blocked on ``gpu_lock`` for the
rest of the run, and hard-exited the instant the lock freed — before FastAPI
had sent the response.

The clip was on disk and the caller could see it, but the worker got a dropped
connection, logged "wan provider returned no result", and fell back to a Ken
Burns still. Every hero of the 2026-08-06/-07 renders was lost this way at
~147 GPU-seconds each, while the server logs said "generated i2v".

Guards under test:
1. ``state.inflight > 0`` short-circuits the tick outright;
2. the same condition is RE-checked after acquiring ``gpu_lock`` (a request
   can arrive while the tick waits for the lock);
3. ``last_used`` is stamped on the way OUT of the handler, so the response
   stream is inside the protected window too.

Loader mirrors test_wan_server_unload.py (scoped torch stub, popped after
exec so a bare ModuleType can't poison later ``import torch``).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

import pytest


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "wan-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/wan-server.py from " + str(start))


def _load_wan_server():
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

    path = _find_repo_root(Path(__file__)) / "scripts" / "wan-server.py"
    spec = importlib.util.spec_from_file_location(
        "wan_server_inflight_under_test", path,
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


wan = _load_wan_server()


def _stale_last_used():
    """Make the idle timer look long expired — the mid-generation condition."""
    return patch.object(wan.state, "last_used", time.time() - 10_000)


def _fat_reserved():
    return patch.object(
        wan.torch.cuda, "memory_reserved", lambda idx=0: 4096 * 1024 * 1024,
    )


@pytest.fixture(autouse=True)
def _reset_inflight():
    wan.state.inflight = 0
    yield
    wan.state.inflight = 0


@pytest.mark.unit
def test_idle_tick_skips_while_a_generation_is_in_flight():
    """The regression: stale last_used + fat reserved pool would have exited."""
    async def body():
        wan.state.inflight = 1
        with _stale_last_used(), _fat_reserved(), \
                patch.object(wan, "_unload_pipeline_blocking") as unload_mock, \
                patch.object(os, "_exit") as mock_exit:
            await wan._idle_unload_tick()

        mock_exit.assert_not_called()
        unload_mock.assert_not_called()

    asyncio.run(body())


@pytest.mark.unit
def test_idle_tick_rechecks_inflight_after_taking_the_lock():
    """A request that lands while the tick waits on gpu_lock must still be
    safe — the pre-check passed, so only the under-lock re-check can save it."""
    async def body():
        real_lock = wan.state.gpu_lock

        class _LockThatAdmitsARequest:
            async def __aenter__(self):
                await real_lock.acquire()
                wan.state.inflight = 1  # request arrives while we waited
                return self

            async def __aexit__(self, *exc):
                real_lock.release()
                return False

        with _stale_last_used(), _fat_reserved(), \
                patch.object(wan.state, "gpu_lock", _LockThatAdmitsARequest()), \
                patch.object(wan, "_unload_pipeline_blocking") as unload_mock, \
                patch.object(os, "_exit") as mock_exit:
            await wan._idle_unload_tick()

        mock_exit.assert_not_called()
        unload_mock.assert_not_called()

    asyncio.run(body())


@pytest.mark.unit
def test_idle_tick_still_unloads_when_genuinely_idle():
    """The guard must not defeat the reclaim it protects — with nothing in
    flight and the timer expired, the hard exit still fires."""
    async def body():
        with _stale_last_used(), _fat_reserved(), \
                patch.object(wan, "_unload_pipeline_blocking") as unload_mock, \
                patch.object(os, "_exit") as mock_exit:
            await wan._idle_unload_tick()

        unload_mock.assert_called_once()
        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


@pytest.mark.unit
def test_generate_brackets_inflight_and_restamps_last_used():
    """``generate`` must increment/decrement around the whole request and
    stamp last_used on exit, so the FileResponse stream is protected too."""
    async def body():
        seen: dict[str, int] = {}

        async def _fake_inner(req):
            seen["inflight"] = wan.state.inflight
            return "response"

        wan.state.last_used = 0.0
        with patch.object(wan, "_generate_inner", _fake_inner):
            result = await wan.generate(wan.GenerateRequest(prompt="a robot"))

        assert result == "response"
        assert seen["inflight"] == 1        # elevated during the request
        assert wan.state.inflight == 0      # released after
        assert wan.state.last_used > 0.0    # window restarts at response end

    asyncio.run(body())


@pytest.mark.unit
def test_generate_releases_inflight_even_when_the_request_fails():
    """A failed generation must not leak the counter — a leak would disable
    the idle unloader permanently and re-strand VRAM on the card."""
    async def body():
        async def _boom(req):
            raise RuntimeError("pipeline exploded")

        with patch.object(wan, "_generate_inner", _boom):
            with pytest.raises(RuntimeError):
                await wan.generate(wan.GenerateRequest(prompt="a robot"))

        assert wan.state.inflight == 0

    asyncio.run(body())
