"""wan-server /unload — hard-unload contract (poindexter#962).

The wan idle unloader drops the pipeline OBJECTS on its timer, but torch's
caching allocator keeps the multi-GB reserved pool + CUDA context until the
process exits — observed 10,240 MiB still held ~6.5h after the last render,
pinning the render GPU under dispatch_media_pipeline's free-VRAM gate all
night. ``POST /unload {"hard": true}`` mirrors image-gen's contract:

- exits the process (Docker ``restart: unless-stopped`` revives it) when the
  reserved pool clears the ``WAN_HARD_UNLOAD_MIN_RESERVED_MB`` floor;
- declines (``nothing_to_reclaim``) below the floor so a repeat reclaim never
  pays a pointless cold-start window;
- measures RESERVED, not allocated — the unload just dropped every live
  tensor, so allocated is ~0 by construction and can never gate the exit;
- exits even when the pipeline object is already None (the idle unloader
  usually beat us to it — the CUDA context is what actually squats).

Loader mirrors test_wan_server_load_failure.py (scoped torch stub, popped
after exec so a bare ModuleType can't poison later ``import torch``).
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
    spec = importlib.util.spec_from_file_location("wan_server_unload_under_test", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


wan = _load_wan_server()


def _patch_reserved(mb: int):
    """Point the module's torch.cuda.memory_reserved at ``mb``."""
    return patch.object(
        wan.torch.cuda, "memory_reserved", lambda idx=0: mb * 1024 * 1024,
    )


@pytest.mark.unit
def test_default_unload_request_is_soft():
    assert wan.UnloadRequest().hard is False


@pytest.mark.unit
def test_soft_unload_does_not_exit_process():
    """Default /unload (no body) must never exit — the GPU scheduler's soft
    callers just want the pipelines dropped."""
    async def body():
        with patch.object(wan, "_unload_pipeline_blocking") as unload_mock, \
             patch.object(os, "_exit") as mock_exit:
            result = await wan.unload()

        unload_mock.assert_called_once()
        mock_exit.assert_not_called()
        assert result["status"] == "unloaded"

    asyncio.run(body())


@pytest.mark.unit
def test_hard_unload_exits_when_reserved_above_floor():
    """{"hard": true} with a fat reserved pool → unload, then os._exit(0)."""
    async def body():
        with patch.object(wan, "_unload_pipeline_blocking") as unload_mock, \
             _patch_reserved(4096), \
             patch.object(os, "_exit") as mock_exit:
            await wan.unload(wan.UnloadRequest(hard=True))

        unload_mock.assert_called_once()
        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


@pytest.mark.unit
def test_hard_unload_declines_below_floor():
    """Below WAN_HARD_UNLOAD_MIN_RESERVED_MB the endpoint answers
    nothing_to_reclaim and does NOT exit — repeat reclaims stay cheap (the
    image-gen lesson: ~24 consecutive no-op exits before its gate)."""
    async def body():
        with patch.object(wan, "_unload_pipeline_blocking"), \
             _patch_reserved(100), \
             patch.object(os, "_exit") as mock_exit:
            result = await wan.unload(wan.UnloadRequest(hard=True))

        mock_exit.assert_not_called()
        assert result["status"] == "nothing_to_reclaim"
        assert result["vram_reserved_mb"] == 100
        assert result["min_reserved_mb"] == wan.HARD_UNLOAD_MIN_RESERVED_MB

    asyncio.run(body())


@pytest.mark.unit
def test_hard_unload_exits_when_pipeline_already_none():
    """The production shape: the idle unloader already dropped the pipeline
    objects, so state.pipeline is None while the CUDA reserved pool (what
    actually squats the card) is still fat — the exit must not be skipped."""
    async def body():
        wan.state.pipeline = None
        wan.state.t2v_pipeline = None
        with _patch_reserved(10240), patch.object(os, "_exit") as mock_exit:
            await wan.unload(wan.UnloadRequest(hard=True))

        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


# ---------------------------------------------------------------------------
# _idle_unload_tick — the self-driven half of the hard unload (2026-08-01: a
# post-render soft unload left a 10.3 GB reserved pool squatting for hours
# because no render was pending to trigger the reclaim rung, OOMing every
# Ollama load on the shared card).
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_idle_tick_noop_when_recently_used():
    """A warm pipeline inside IDLE_TIMEOUT_S is left alone."""
    async def body():
        wan.state.last_used = time.time()
        wan.state.pipeline = object()
        wan.state.t2v_pipeline = None
        with patch.object(wan, "_unload_pipeline_blocking") as unload_mock, \
             patch.object(os, "_exit") as mock_exit:
            await wan._idle_unload_tick()

        unload_mock.assert_not_called()
        mock_exit.assert_not_called()

    asyncio.run(body())


@pytest.mark.unit
def test_idle_tick_soft_unloads_then_hard_exits_above_floor():
    """Idle + loaded + fat reserved pool → soft unload, then process exit:
    the VRAM returns to the card without waiting for a consumer reclaim."""
    async def body():
        wan.state.last_used = 0.0
        wan.state.pipeline = object()
        wan.state.t2v_pipeline = None
        with patch.object(wan, "_unload_pipeline_blocking") as unload_mock, \
             _patch_reserved(10240), \
             patch.object(os, "_exit") as mock_exit:
            await wan._idle_unload_tick()

        unload_mock.assert_called_once()
        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


@pytest.mark.unit
def test_idle_tick_below_floor_unloads_without_exit():
    """Idle + loaded but a lean pool → soft unload only; no cold-start is
    paid when an exit would reclaim nothing."""
    async def body():
        wan.state.last_used = 0.0
        wan.state.pipeline = object()
        wan.state.t2v_pipeline = None
        with patch.object(wan, "_unload_pipeline_blocking") as unload_mock, \
             _patch_reserved(100), \
             patch.object(os, "_exit") as mock_exit:
            await wan._idle_unload_tick()

        unload_mock.assert_called_once()
        mock_exit.assert_not_called()

    asyncio.run(body())


@pytest.mark.unit
def test_idle_tick_cold_idle_short_circuits():
    """Nothing loaded and no reserved pool: the tick returns before touching
    the GPU lock — the every-30s steady state stays free."""
    async def body():
        wan.state.last_used = 0.0
        wan.state.pipeline = None
        wan.state.t2v_pipeline = None
        with patch.object(wan, "_unload_pipeline_blocking") as unload_mock, \
             _patch_reserved(0), \
             patch.object(os, "_exit") as mock_exit:
            await wan._idle_unload_tick()

        unload_mock.assert_not_called()
        mock_exit.assert_not_called()

    asyncio.run(body())


@pytest.mark.unit
def test_idle_tick_exits_when_pool_squats_after_earlier_unload():
    """The 2026-08-01 shape: pipelines already None (the post-render unload
    ran) while the reserved pool still holds ~10 GB — the tick hard-exits on
    its own instead of waiting for a render-gate reclaim that may never come."""
    async def body():
        wan.state.last_used = 0.0
        wan.state.pipeline = None
        wan.state.t2v_pipeline = None
        with patch.object(wan, "_unload_pipeline_blocking"), \
             _patch_reserved(10240), \
             patch.object(os, "_exit") as mock_exit:
            await wan._idle_unload_tick()

        mock_exit.assert_called_once_with(0)

    asyncio.run(body())
