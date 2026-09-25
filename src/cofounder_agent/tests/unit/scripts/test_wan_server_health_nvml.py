"""wan-server /health reads device free VRAM without a CUDA context (2026-09-25).

Measured in a throwaway container of the wan-server image, per PID from the
host's nvidia-smi, one step at a time:

- ``import torch``, ``is_available``, ``get_device_name``,
  ``get_device_properties``, ``memory_allocated`` / ``memory_reserved``,
  ``device_count``: 0 MiB;
- NVML init, handle by UUID, ``nvmlDeviceGetMemoryInfo``: 0 MiB;
- ``torch.cuda.mem_get_info(0)``: 498 MiB, with 0 MB reserved and allocated.

``device_free_mb`` came from ``mem_get_info``, and /health is polled every 5 s
by the hero plate gate and every 30 s by the Docker healthcheck, so an idle
server kept that context for good. The hard unload's floor measures
``memory_reserved``, which does not include a context, so it could never
reclaim it. /health now reads NVML (same instant, same number: 21673 MiB both
ways) and goes through ``mem_get_info`` only when NVML is unusable.

Under test:

1. NVML path: ``device_free_mb`` from NVML with ``device_free_source="nvml"``,
   ``mem_get_info`` never called, the NVML handle matched to CUDA device 0 by
   UUID, resolved once.
2. Fail-soft: a missing binding or an NVML error falls back to the old
   ``mem_get_info`` read (``"cuda"``), warns once, and stays on it for the
   process.
3. The idle surface (startup, /health, the idle tick, both /unload modes)
   calls only the torch.cuda functions the measurement found context-free.
4. ``vram_used_mb`` keeps its meaning (this process's torch allocations).
5. The image installs the binding.

Loader mirrors test_wan_server_unload.py (scoped torch stub, popped after
exec so a bare ModuleType can't poison later ``import torch``). Every test
then swaps the module's ``torch`` for a fake it controls.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

MIB = 1024 * 1024
_UUID = "aa4eb63a-0720-4c1d-1755-fc255ede6780"


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "wan-server.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/wan-server.py from " + str(start))


REPO_ROOT = _find_repo_root(Path(__file__))


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

    path = REPO_ROOT / "scripts" / "wan-server.py"
    spec = importlib.util.spec_from_file_location("wan_server_nvml_under_test", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stub_installed:
            sys.modules.pop("torch", None)
    return module


wan = _load_wan_server()


class _CuUuid:
    """Stands in for ``torch._C._CUuuid``: ``str()`` gives the bare UUID."""

    def __init__(self, text: str) -> None:
        self._text = text

    def __str__(self) -> str:
        return self._text


class _ContextFreeCuda:
    """``torch.cuda`` exposing ONLY what the 2026-09-25 measurement found holds
    0 MiB, plus ``mem_get_info`` (the call that creates the context), counted.

    Any other attribute fails the test: a new torch.cuda call on these paths
    is unmeasured, and the only way to know it is context-free is to measure
    it the same way (per-PID nvidia-smi in a throwaway container).
    """

    def __init__(self, *, available: bool = True, cuda_free_mb: int = 21000,
                 allocated_mb: int = 0, reserved_mb: int = 0,
                 uuid: str = _UUID) -> None:
        self.available = available
        self.cuda_free_mb = cuda_free_mb
        self.allocated_mb = allocated_mb
        self.reserved_mb = reserved_mb
        self.uuid = uuid
        self.mem_get_info_calls = 0

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)  # protocol probes, not torch calls
        raise AssertionError(
            f"torch.cuda.{name} is not on the measured context-free list"
        )

    def is_available(self) -> bool:
        return self.available

    def device_count(self) -> int:
        return 1

    def get_device_name(self, idx: int = 0) -> str:
        return "NVIDIA GeForce RTX 5090"

    def get_device_properties(self, idx: int = 0):
        # 32088 MiB: what torch reports for the 5090 (totalGlobalMem). NVML's
        # total is 32607 MiB; the difference is driver-reserved memory.
        return SimpleNamespace(uuid=_CuUuid(self.uuid), total_memory=32088 * MIB)

    def memory_allocated(self, idx: int = 0) -> int:
        return self.allocated_mb * MIB

    def memory_reserved(self, idx: int = 0) -> int:
        return self.reserved_mb * MIB

    def mem_get_info(self, idx: int = 0) -> tuple[int, int]:
        self.mem_get_info_calls += 1
        return self.cuda_free_mb * MIB, 32088 * MIB


class _NVMLError(Exception):
    """Shaped like pynvml.NVMLError: the binding raises its own class."""


def _fake_pynvml(*, free_mb: int = 21673, init_error: Exception | None = None,
                 read_error: Exception | None = None) -> types.ModuleType:
    mod = types.ModuleType("pynvml")
    mod.calls = {"init": 0, "by_uuid": [], "meminfo": 0}

    def nvmlInit() -> None:
        mod.calls["init"] += 1
        if init_error is not None:
            raise init_error

    def nvmlDeviceGetHandleByUUID(uuid: str) -> str:
        mod.calls["by_uuid"].append(uuid)
        return "handle-for-" + uuid

    def nvmlDeviceGetMemoryInfo(handle: str):
        mod.calls["meminfo"] += 1
        if read_error is not None:
            raise read_error
        return SimpleNamespace(
            total=32607 * MIB, free=free_mb * MIB, used=(32607 - free_mb) * MIB,
        )

    mod.nvmlInit = nvmlInit
    mod.nvmlDeviceGetHandleByUUID = nvmlDeviceGetHandleByUUID
    mod.nvmlDeviceGetMemoryInfo = nvmlDeviceGetMemoryInfo
    return mod


@pytest.fixture(autouse=True)
def _fresh_server_state():
    """The module keeps one ServerState for the life of the process; give each
    test a cold, idle one."""
    wan.state.nvml = None
    wan.state.nvml_error = None
    wan.state.pipeline = None
    wan.state.t2v_pipeline = None
    wan.state.degraded = False
    wan.state.degraded_reason = None
    wan.state.inflight = 0
    wan.state.last_used = 0.0
    yield
    wan.state.nvml = None
    wan.state.nvml_error = None


@pytest.fixture
def cuda(monkeypatch):
    fake = _ContextFreeCuda()
    monkeypatch.setattr(wan, "torch", SimpleNamespace(cuda=fake))
    return fake


@pytest.fixture
def nvml(monkeypatch):
    """A working NVML binding. ``monkeypatch.setitem`` restores only this key
    (patch.dict on sys.modules re-populates a snapshot and can drop torch)."""
    fake = _fake_pynvml()
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    return fake


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


# ---------------------------------------------------------------------------
# 1. The NVML path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_health_reads_device_free_from_nvml_not_torch(cuda, nvml):
    body = asyncio.run(wan.health())

    assert body["device_free_mb"] == 21673
    assert body["device_free_source"] == "nvml"
    assert cuda.mem_get_info_calls == 0


@pytest.mark.unit
def test_nvml_handle_is_cuda_device_0_matched_by_uuid(cuda, nvml):
    """By UUID, not by index: CUDA and NVML can enumerate differently
    (CUDA_DEVICE_ORDER, CUDA_VISIBLE_DEVICES, a second visible card). torch
    prints the UUID bare; NVML wants the ``GPU-`` prefix."""
    asyncio.run(wan.health())

    assert nvml.calls["by_uuid"] == [f"GPU-{_UUID}"]


@pytest.mark.unit
def test_uuid_that_already_carries_a_prefix_is_used_as_is(cuda):
    cuda.uuid = f"GPU-{_UUID}"
    assert wan._cuda_device_uuid(0) == f"GPU-{_UUID}"
    cuda.uuid = "MIG-1234"
    assert wan._cuda_device_uuid(0) == "MIG-1234"


@pytest.mark.unit
def test_nvml_resolves_once_and_reads_every_poll(cuda, nvml):
    for _ in range(3):
        asyncio.run(wan.health())

    assert nvml.calls["init"] == 1
    assert len(nvml.calls["by_uuid"]) == 1
    assert nvml.calls["meminfo"] == 3


@pytest.mark.unit
def test_vram_used_mb_is_still_this_process_torch_allocations(cuda, nvml):
    """The renderer adds ``vram_used_mb`` back to ``device_free_mb`` (wan can
    reuse its own pool), so it must stay torch's allocated figure."""
    cuda.allocated_mb = 23000

    body = asyncio.run(wan.health())

    assert body["vram_used_mb"] == 23000
    assert body["device_free_mb"] == 21673
    assert body["vram_total_mb"] == 32088


# ---------------------------------------------------------------------------
# 2. Fail-soft to the old read
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_binding_falls_back_to_mem_get_info(cuda, monkeypatch, caplog):
    """An image built before nvidia-ml-py was added: ``import pynvml`` fails,
    /health answers exactly as it used to, and says which read it used."""
    monkeypatch.setitem(sys.modules, "pynvml", None)  # import raises
    cuda.cuda_free_mb = 20500

    with caplog.at_level(logging.WARNING, logger="wan-server"):
        body = asyncio.run(wan.health())

    assert body["device_free_mb"] == 20500
    assert body["device_free_source"] == "cuda"
    assert cuda.mem_get_info_calls == 1
    assert wan.state.nvml_error is not None
    assert any("NVML unusable" in m for m in _warnings(caplog))


@pytest.mark.unit
def test_nvml_failure_is_sticky_and_warns_once(cuda, monkeypatch, caplog):
    """One warning, not one per 5 s gate poll, and no re-init every call."""
    broken = _fake_pynvml(init_error=_NVMLError("Driver Not Loaded"))
    monkeypatch.setitem(sys.modules, "pynvml", broken)

    with caplog.at_level(logging.WARNING, logger="wan-server"):
        sources = [asyncio.run(wan.health())["device_free_source"] for _ in range(3)]

    assert sources == ["cuda", "cuda", "cuda"]
    assert broken.calls["init"] == 1
    assert cuda.mem_get_info_calls == 3
    assert sum("NVML unusable" in m for m in _warnings(caplog)) == 1
    assert "Driver Not Loaded" in wan.state.nvml_error


@pytest.mark.unit
def test_nvml_read_failure_after_init_falls_back(cuda, monkeypatch):
    broken = _fake_pynvml(read_error=_NVMLError("GPU is lost"))
    monkeypatch.setitem(sys.modules, "pynvml", broken)

    first = asyncio.run(wan.health())
    second = asyncio.run(wan.health())

    assert first["device_free_source"] == second["device_free_source"] == "cuda"
    assert broken.calls["meminfo"] == 1  # not retried after the failure
    assert wan.state.nvml is None


@pytest.mark.unit
def test_no_cuda_reports_zero_without_probing(cuda, nvml):
    cuda.available = False

    body = asyncio.run(wan.health())

    assert body["device_free_mb"] == 0
    assert body["device_free_source"] is None
    assert nvml.calls["init"] == 0
    assert cuda.mem_get_info_calls == 0


# ---------------------------------------------------------------------------
# 3. Nothing on the idle surface creates a context
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_idle_surface_calls_only_context_free_cuda_functions(cuda, nvml, monkeypatch):
    """Startup, /health, the idle unloader's tick and both /unload modes on a
    cold server: every torch.cuda call must be one the measurement found
    context-free (``_ContextFreeCuda`` fails on anything else) and
    ``mem_get_info`` must never run."""

    def _no_exit(code):
        raise AssertionError(f"idle server must not exit (os._exit({code}))")

    monkeypatch.setattr(os, "_exit", _no_exit)

    async def body():
        await wan.on_startup()  # asyncio.run cancels its idle_unloader task
        for _ in range(3):
            await wan.health()
        await wan._idle_unload_tick()  # last_used=0: long past the timeout
        soft = await wan.unload()
        hard = await wan.unload(wan.UnloadRequest(hard=True))
        return soft, hard

    soft, hard = asyncio.run(body())

    assert soft["status"] == "unloaded"
    assert hard["status"] == "nothing_to_reclaim"
    assert hard["vram_reserved_mb"] == 0
    assert cuda.mem_get_info_calls == 0


@pytest.mark.unit
def test_startup_resolves_nvml_and_names_the_read_in_its_log(cuda, nvml, caplog):
    async def body():
        await wan.on_startup()

    with caplog.at_level(logging.INFO, logger="wan-server"):
        asyncio.run(body())

    assert nvml.calls["init"] == 1  # resolved at boot, not at the first poll
    boot = [r.getMessage() for r in caplog.records if "Wan server starting" in r.getMessage()]
    assert boot and "via NVML (21673 MiB free now, no CUDA context)" in boot[0]


@pytest.mark.unit
def test_startup_log_names_the_fallback_when_nvml_is_unusable(cuda, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "pynvml", None)

    async def body():
        await wan.on_startup()

    with caplog.at_level(logging.INFO, logger="wan-server"):
        asyncio.run(body())

    boot = [r.getMessage() for r in caplog.records if "Wan server starting" in r.getMessage()]
    assert boot and "torch.cuda.mem_get_info (NVML unusable" in boot[0]
    assert cuda.mem_get_info_calls == 0  # the log does not create a context


# ---------------------------------------------------------------------------
# 4. The image carries the binding
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_wan_image_installs_the_nvml_binding():
    """Without it /health fails soft to the context-holding read and only a
    container log line says so, which is how an idle server would go back to
    squatting 0.5 GB on the render GPU unnoticed."""
    dockerfile = (REPO_ROOT / "scripts" / "Dockerfile.wan").read_text(encoding="utf-8")
    run_blocks = re.findall(r"^RUN\s(?:.*\\\n)*.*$", dockerfile, flags=re.MULTILINE)

    assert any(
        "pip install" in block and re.search(r"\bnvidia-ml-py\b", block)
        for block in run_blocks
    ), "scripts/Dockerfile.wan must pip install nvidia-ml-py (provides pynvml)"

    server = (REPO_ROOT / "scripts" / "wan-server.py").read_text(encoding="utf-8")
    assert re.search(r"^\s*import pynvml\b", server, flags=re.MULTILINE)
