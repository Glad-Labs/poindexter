"""A fake ``pynvml`` for the GPU sidecars' exit gate (2026-09-25).

RIFE, chatterbox, stable-audio and wan decide whether to exit by what the
driver counts for their own process: ``nvmlDeviceGetComputeRunningProcesses``
on every visible GPU, filtered to ``os.getpid()``. Measured on driver 595.84
in a throwaway container: NVML lists only that container's processes, under
their in-container PIDs. A server without a CUDA context is absent, and with
one it saw itself as ``(pid 1, 612 MiB)``, the figure the host's nvidia-smi
gave for its host PID. This fake lists processes the same way: this process
appears only while ``own_mb`` is set, beside whatever ``others`` lists.

It also answers the device-level calls wan's /health makes
(``nvmlDeviceGetHandleByUUID`` / ``nvmlDeviceGetMemoryInfo``), so one fake
serves every sidecar test.

Install it with ``monkeypatch.setitem(sys.modules, "pynvml", fake)``, which
restores only that key (``patch.dict`` on ``sys.modules`` re-populates a
snapshot and can drop torch). ``sys.modules["pynvml"] = None`` makes the
import raise, which is how an image without nvidia-ml-py behaves.
"""

from __future__ import annotations

import os
import types
from collections.abc import Iterable
from types import SimpleNamespace

MIB = 1024 * 1024


class NVMLError(Exception):
    """Shaped like ``pynvml.NVMLError``: the binding raises its own class."""


def fake_pynvml(
    *,
    own_mb: int | None = None,
    others: Iterable[tuple[int, int]] = (),
    devices: int = 1,
    own_device: int = 0,
    init_error: Exception | None = None,
    read_error: Exception | None = None,
    free_mb: int = 21673,
) -> types.ModuleType:
    """A working (or deliberately broken) NVML binding.

    ``own_mb``: MiB this process holds on ``own_device``; ``None`` leaves it
    unlisted, which is what the driver reports for a process with no CUDA
    context. Mutable on the returned module, so a test can change it between
    calls (``fake.own_mb = 622`` once a request has run).
    ``own_unavailable``: set on the module to list this process with
    ``usedGpuMemory=None``, as NVML does where per-process accounting is
    unavailable (Windows WDDM, WSL2).
    ``others``: ``(pid, mib)`` entries on device 0 that are not this process.
    """
    mod = types.ModuleType("pynvml")
    mod.own_mb = own_mb
    mod.own_unavailable = False
    mod.others = list(others)
    mod.calls = {"init": 0, "count": 0, "procs": 0, "by_uuid": [], "meminfo": 0}

    def nvmlInit() -> None:
        mod.calls["init"] += 1
        if init_error is not None:
            raise init_error

    def nvmlDeviceGetCount() -> int:
        mod.calls["count"] += 1
        return devices

    def nvmlDeviceGetHandleByIndex(index: int) -> str:
        return f"handle-{index}"

    def nvmlDeviceGetComputeRunningProcesses(handle: str) -> list[SimpleNamespace]:
        mod.calls["procs"] += 1
        if read_error is not None:
            raise read_error
        procs: list[SimpleNamespace] = []
        if handle == "handle-0":
            procs.extend(
                SimpleNamespace(pid=pid, usedGpuMemory=mib * MIB) for pid, mib in mod.others
            )
        if handle == f"handle-{own_device}":
            if mod.own_unavailable:
                procs.append(SimpleNamespace(pid=os.getpid(), usedGpuMemory=None))
            elif mod.own_mb is not None:
                procs.append(SimpleNamespace(pid=os.getpid(), usedGpuMemory=mod.own_mb * MIB))
        return procs

    def nvmlDeviceGetHandleByUUID(uuid: str) -> str:
        mod.calls["by_uuid"].append(uuid)
        return "handle-for-" + uuid

    def nvmlDeviceGetMemoryInfo(handle: str) -> SimpleNamespace:
        mod.calls["meminfo"] += 1
        if read_error is not None:
            raise read_error
        return SimpleNamespace(
            total=32607 * MIB, free=free_mb * MIB, used=(32607 - free_mb) * MIB,
        )

    mod.nvmlInit = nvmlInit
    mod.nvmlDeviceGetCount = nvmlDeviceGetCount
    mod.nvmlDeviceGetHandleByIndex = nvmlDeviceGetHandleByIndex
    mod.nvmlDeviceGetComputeRunningProcesses = nvmlDeviceGetComputeRunningProcesses
    mod.nvmlDeviceGetHandleByUUID = nvmlDeviceGetHandleByUUID
    mod.nvmlDeviceGetMemoryInfo = nvmlDeviceGetMemoryInfo
    return mod
