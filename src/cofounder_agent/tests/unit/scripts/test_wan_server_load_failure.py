"""wan-server load-failure handling — poindexter#907 defect 1.

`_load_pipeline_blocking` moves components to CUDA one at a time. Two things
went wrong when that OOM'd part-way through:

1. **Leak.** The already-moved components stayed resident with no handle to
   them — 14.9 GB still held by the process after a failed load (measured
   2026-07-26). Each retry then started with less room and failed sooner.
2. **Latch.** `state.degraded = True` was set for EVERY cause, including a
   transient OOM, and nothing cleared it. One unlucky render turned into a
   permanent 503 until someone restarted the container.

An OOM is a statement about the card at that moment, not about the model — the
crowding process (image-gen holding ~25 GB, defect 2) will have unloaded by the
next attempt. So OOM must clean up and stay retryable; a genuinely persistent
cause (bad model id, broken install) must still latch.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "wan-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/wan-server.py from " + str(start))


def _load_wan_server():
    """Import wan-server.py with a scoped torch stub (it has no torch here).

    The stub is popped after exec so a leaked bare ModuleType can't poison
    downstream `import torch` for the rest of the session — same contract as
    test_image_gen_server_unload.py's loader.
    """
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
    spec = importlib.util.spec_from_file_location("wan_server_under_test", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


wan = _load_wan_server()


# ---------------------------------------------------------------------------
# Retryable classification
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("exc", [
    # torch's real OOM type name, without importing torch here.
    type("OutOfMemoryError", (RuntimeError,), {})("CUDA out of memory. Tried to allocate 108.00 MiB"),
    RuntimeError("CUDA error: out of memory"),
    RuntimeError("CUDA out of memory"),
])
def test_oom_is_retryable(exc):
    """The exact shapes seen in production on 07-26, 07-28 and 07-29."""
    assert wan._is_retryable_load_failure(exc) is True


@pytest.mark.unit
@pytest.mark.parametrize("exc", [
    OSError("No such file or directory: model.safetensors"),
    ValueError("unknown model id"),
    RuntimeError("Failed to import diffusers"),
])
def test_persistent_failures_still_latch(exc):
    """A broken install or a bad model id will not fix itself — those must
    keep latching, or the server retries a hopeless load on every request."""
    assert wan._is_retryable_load_failure(exc) is False


# ---------------------------------------------------------------------------
# Partial-load cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_release_partial_load_drops_the_handle_and_empties_cache():
    wan.state.pipeline = object()
    empty = MagicMock()
    with patch.object(wan.torch.cuda, "empty_cache", empty):
        wan._release_partial_load()

    assert wan.state.pipeline is None
    empty.assert_called_once()


@pytest.mark.unit
def test_release_partial_load_survives_a_cleanup_error():
    """We are already on the failure path — a cleanup error must not mask the
    original load exception the caller is about to re-raise."""
    wan.state.pipeline = object()
    with patch.object(
        wan.torch.cuda, "empty_cache", MagicMock(side_effect=RuntimeError("boom")),
    ):
        wan._release_partial_load()  # must not raise

    assert wan.state.pipeline is None


# ---------------------------------------------------------------------------
# The combined contract on _ensure_pipeline_loaded
# ---------------------------------------------------------------------------


def _fail_load_with(exc):
    async def _to_thread(fn, *a, **kw):
        raise exc
    return _to_thread


@pytest.mark.unit
def test_oom_cleans_up_and_does_not_latch_degraded():
    """The regression: an OOM used to leave the partial load resident AND
    latch degraded, so the next /generate 503'd without even trying."""
    async def body():
        # Must be None, or _ensure_pipeline_loaded early-returns the cached
        # pipeline and never attempts (or fails) a load.
        wan.state.pipeline = None
        wan.state.degraded = False
        oom = type("OutOfMemoryError", (RuntimeError,), {})("CUDA out of memory")
        with patch.object(wan.asyncio, "to_thread", _fail_load_with(oom)), \
             patch.object(wan.torch.cuda, "empty_cache", MagicMock()):
            with pytest.raises(RuntimeError):
                await wan._ensure_pipeline_loaded()

        assert wan.state.pipeline is None, "partial load must be released"
        assert wan.state.degraded is False, "an OOM must stay retryable"
        assert wan.state.degraded_reason is None

    asyncio.run(body())


@pytest.mark.unit
def test_persistent_failure_latches_degraded_with_a_reason():
    async def body():
        wan.state.pipeline = None
        wan.state.degraded = False
        with patch.object(
            wan.asyncio, "to_thread", _fail_load_with(ValueError("bad model id")),
        ), patch.object(wan.torch.cuda, "empty_cache", MagicMock()):
            with pytest.raises(ValueError):
                await wan._ensure_pipeline_loaded()

        assert wan.state.degraded is True
        assert "ValueError" in (wan.state.degraded_reason or "")

    asyncio.run(body())


@pytest.mark.unit
def test_successful_load_clears_a_previous_latch():
    async def body():
        wan.state.pipeline = None
        wan.state.degraded = True
        wan.state.degraded_reason = "stale"
        sentinel = object()

        async def _ok(fn, *a, **kw):
            return sentinel

        with patch.object(wan.asyncio, "to_thread", _ok):
            got = await wan._ensure_pipeline_loaded()

        assert got is sentinel
        assert wan.state.degraded is False
        assert wan.state.degraded_reason is None

    asyncio.run(body())
