"""Hard-unload endpoint tests for scripts/image-gen-server.py.

Root cause (2026-07-12 video-render VRAM gate investigation): torch's CUDA
context/reserved pool is NOT returned to the host by
torch.cuda.empty_cache() under WSL2 — only a process exit does (confirmed:
soft /unload freed 0 GB; a container restart freed ~7 GB). The render-GPU
VRAM reclaim path (dispatch_media_pipeline) needs a REAL reclaim, so /unload
gained a hard mode that exits the process; Docker's restart:unless-stopped
brings it back and it lazy-loads on the next /generate.

These tests pin: hard=true calls os._exit(0); the default (soft) path never
does, preserving the pre-existing /unload contract.
"""
import asyncio
import importlib.util
import os
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "image-gen-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/image-gen-server.py from " + str(start))


def _load_image_gen_server():
    # Mirrors test_image_gen_self_heal.py's loader — see that file for why the
    # torch stub must be scoped (a leaked bare ModuleType poisons downstream
    # `import torch` for the rest of the test session).
    stub_installed = False
    if "torch" not in sys.modules:
        torch_stub = types.ModuleType("torch")
        torch_stub.__spec__ = importlib.util.spec_from_loader("torch", loader=None)
        torch_stub.float16 = "float16"
        torch_stub.cuda = types.SimpleNamespace(
            is_available=lambda: False,
            memory_allocated=lambda idx=0: 0,
            # memory_reserved is what the hard-unload gate reads: after
            # unload_pipeline() drops the tensors, ALLOCATED is 0 by
            # construction, so only RESERVED can tell whether an exit would
            # actually return anything to the host.
            memory_reserved=lambda idx=0: 0,
        )
        sys.modules["torch"] = torch_stub
        stub_installed = True

    server_path = _find_repo_root(Path(__file__)) / "scripts" / "image-gen-server.py"
    spec = importlib.util.spec_from_file_location(
        "img_gen_server_unload_under_test", server_path,
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


img_gen_server = _load_image_gen_server()


def test_soft_unload_does_not_exit_process():
    """Default /unload (no body, or {"hard": false}) must never call
    os._exit — the GPU scheduler relies on this when just switching the GPU
    over to Ollama; it does not want the image-gen process to die."""
    async def body():
        img_gen_server.unload_pipeline = lambda: None
        img_gen_server.state.pipeline = object()

        with patch.object(os, "_exit") as mock_exit:
            result = await img_gen_server.unload()

        mock_exit.assert_not_called()
        assert result["status"] == "unloaded"

    asyncio.run(body())


def _patch_reserved(mb: int):
    """Point torch.cuda.memory_reserved at `mb`, and pin the threshold read.

    The threshold read hits Postgres; tests must not depend on a live DB (or
    on which value happens to be seeded), so it is always stubbed here.
    """
    return (
        patch.object(
            img_gen_server.torch.cuda, "memory_reserved",
            lambda idx=0: mb * 1024 * 1024,
        ),
        patch.object(
            img_gen_server, "read_hard_unload_min_reserved_mb",
            new=_async_return(512),
        ),
    )


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


def test_hard_unload_exits_process():
    """{"hard": true} must call os._exit(0) so the CUDA context actually
    returns to the host — torch.cuda.empty_cache() alone doesn't under WSL2
    (2026-07-12 finding). Gated on there being VRAM worth reclaiming."""
    async def body():
        img_gen_server.unload_pipeline = lambda: None
        img_gen_server.state.pipeline = object()

        reserved, threshold = _patch_reserved(4096)
        with reserved, threshold, patch.object(os, "_exit") as mock_exit:
            await img_gen_server.unload(img_gen_server.UnloadRequest(hard=True))

        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


def test_hard_unload_exits_even_when_already_unloaded():
    """A hard unload must still exit even if state.pipeline is already None
    (idle_unloader may have dropped it already) — the stuck CUDA context can
    outlive the pipeline object, so the reclaim must not skip the exit.

    That context is exactly what shows up as RESERVED-but-not-allocated, which
    is why the gate reads memory_reserved: this case still exits."""
    async def body():
        img_gen_server.unload_pipeline = lambda: None
        img_gen_server.state.pipeline = None

        reserved, threshold = _patch_reserved(7000)
        with reserved, threshold, patch.object(os, "_exit") as mock_exit:
            await img_gen_server.unload(img_gen_server.UnloadRequest(hard=True))

        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


def test_hard_unload_skips_exit_when_nothing_reserved():
    """The regression this gate exists for (2026-07-27): image-gen was
    hard-unloaded every 5 minutes while holding nothing, so each exit freed
    zero VRAM and only opened a cold-start window in which /generate failed
    and article images silently downgraded to stock. Below the threshold the
    process must stay up."""
    async def body():
        img_gen_server.unload_pipeline = lambda: None
        img_gen_server.state.pipeline = None

        reserved, threshold = _patch_reserved(0)
        with reserved, threshold, patch.object(os, "_exit") as mock_exit:
            result = await img_gen_server.unload(
                img_gen_server.UnloadRequest(hard=True),
            )

        mock_exit.assert_not_called()
        assert result["status"] == "nothing_to_reclaim"
        assert result["vram_reserved_mb"] == 0

    asyncio.run(body())


def test_hard_unload_skips_just_below_threshold_and_exits_at_it():
    """The gate is a threshold, not a zero-check — a few hundred MB of
    residue is not worth a restart, the multi-GB model pool is."""
    async def body():
        img_gen_server.unload_pipeline = lambda: None
        img_gen_server.state.pipeline = None

        reserved, threshold = _patch_reserved(511)
        with reserved, threshold, patch.object(os, "_exit") as mock_exit:
            result = await img_gen_server.unload(
                img_gen_server.UnloadRequest(hard=True),
            )
        mock_exit.assert_not_called()
        assert result["status"] == "nothing_to_reclaim"

        reserved, threshold = _patch_reserved(512)
        with reserved, threshold, patch.object(os, "_exit") as mock_exit:
            await img_gen_server.unload(img_gen_server.UnloadRequest(hard=True))
        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


def test_default_unload_request_is_soft():
    """UnloadRequest() with no args must default hard=False, so an existing
    caller that omits the body keeps getting the pre-existing soft contract."""
    assert img_gen_server.UnloadRequest().hard is False


# ---------------------------------------------------------------------------
# _idle_unloader_tick — factored out of the startup loop so the loop can wrap
# it in a blanket except (the bare loop died silently on 2026-07-30 and the
# loaded pipeline squatted 19 GB through the following night).
# ---------------------------------------------------------------------------


def test_idle_unloader_tick_unloads_when_idle():
    """IDLE_TIMEOUT elapsed with a loaded pipeline → the tick unloads and
    still runs the piggybacked OCR-gate settings refresh."""
    async def body():
        img_gen_server.state.pipeline = object()
        img_gen_server.state.last_used = 0.0
        refreshed = []

        async def fake_refresh():
            refreshed.append(True)

        with patch.object(img_gen_server, "unload_pipeline") as unload_mock, \
             patch.object(img_gen_server, "reload_ocr_gate_config", fake_refresh):
            await img_gen_server._idle_unloader_tick()

        unload_mock.assert_called_once()
        assert refreshed

    asyncio.run(body())


def test_idle_unloader_tick_keeps_warm_pipeline():
    """A pipeline used within IDLE_TIMEOUT stays loaded; the OCR-gate refresh
    still happens on the shared cadence."""
    async def body():
        img_gen_server.state.pipeline = object()
        img_gen_server.state.last_used = time.time()
        refreshed = []

        async def fake_refresh():
            refreshed.append(True)

        with patch.object(img_gen_server, "unload_pipeline") as unload_mock, \
             patch.object(img_gen_server, "reload_ocr_gate_config", fake_refresh):
            await img_gen_server._idle_unloader_tick()

        unload_mock.assert_not_called()
        assert refreshed

    asyncio.run(body())
