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


def test_hard_unload_exits_process():
    """{"hard": true} must call os._exit(0) so the CUDA context actually
    returns to the host — torch.cuda.empty_cache() alone doesn't under WSL2
    (2026-07-12 finding)."""
    async def body():
        img_gen_server.unload_pipeline = lambda: None
        img_gen_server.state.pipeline = object()

        with patch.object(os, "_exit") as mock_exit:
            await img_gen_server.unload(img_gen_server.UnloadRequest(hard=True))

        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


def test_hard_unload_exits_even_when_already_unloaded():
    """A hard unload must still exit even if state.pipeline is already None
    (idle_unloader may have dropped it already) — the stuck CUDA context can
    outlive the pipeline object, so the reclaim must not skip the exit."""
    async def body():
        img_gen_server.unload_pipeline = lambda: None
        img_gen_server.state.pipeline = None

        with patch.object(os, "_exit") as mock_exit:
            await img_gen_server.unload(img_gen_server.UnloadRequest(hard=True))

        mock_exit.assert_called_once_with(0)

    asyncio.run(body())


def test_default_unload_request_is_soft():
    """UnloadRequest() with no args must default hard=False, so an existing
    caller that omits the body keeps getting the pre-existing soft contract."""
    assert img_gen_server.UnloadRequest().hard is False
