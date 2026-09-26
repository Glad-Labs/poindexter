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

**The reserved-pool floor was itself half-blind (2026-09-25).** Once the model
is really dropped, ``empty_cache()`` returns the pool near 0, and what remains
is the CUDA context — 670 MiB, measured per PID from the host's nvidia-smi in a
throwaway container — which ``memory_reserved`` cannot see at all. The watchdog
logged "skipped — 40 MB reserved is below the 512 MB floor" and kept that
context for good. ``_exit_gate`` now measures the driver's count for this
process (NVML) and falls back to ``memory_reserved`` only when NVML is
unusable — which is the default in this test file (no fake installed), so the
tests above are unchanged: they exercise the fallback path exactly as before.
``TestExitGate`` below exercises the NVML path.

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

from tests.unit.scripts._fake_nvml import fake_pynvml


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
def _fresh_state(monkeypatch):
    # A new _State per test, not a reset one: it owns an asyncio.Lock, and
    # every test here runs its own event loop. NVML is unusable unless a test
    # installs a fake (`_nvml`): no `pynvml` is on the backend test env's
    # sys.path, so `import pynvml` raises, and the gate falls back to
    # `memory_reserved` exactly as it did before the gate existed.
    monkeypatch.setattr(sa, "_state", sa._State())


def _nvml(monkeypatch, **kwargs):
    fake = fake_pynvml(**kwargs)
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    return fake


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
         patch.object(sa, "_hard_exit_if_reclaimable") as hard_mock:
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
         patch.object(sa, "_hard_exit_if_reclaimable") as hard_mock:
        _run_one_watchdog_pass()
    hard_mock.assert_not_called()


@pytest.mark.unit
def test_a_failed_watchdog_pass_does_not_end_the_watchdog():
    """Nothing else unloads an idle model or heals a degraded server, so an
    exception in one pass (a CUDA error mid-unload, say) must cost that pass,
    not the loop — an escaped one ended the task, silently, for good."""
    passes = []

    async def failing_tick():
        passes.append(True)
        raise RuntimeError("CUDA error: unspecified launch failure")

    calls = {"n": 0}

    async def _fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > 2:
            raise _StopWatchdog

    async def body():
        with patch.object(sa.asyncio, "sleep", _fake_sleep), \
             patch.object(sa, "_idle_unload_tick", failing_tick):
            try:
                await sa._watchdog()
            except _StopWatchdog:
                pass

    asyncio.run(body())
    assert passes == [True, True]


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


@pytest.mark.unit
class TestExitGate:
    """What the driver counts for this process (2026-09-25), preferred over
    torch's reserved pool whenever NVML can answer."""

    def test_gate_prefers_nvml_over_the_reserved_pool(self, monkeypatch):
        _nvml(monkeypatch, own_mb=None)  # the driver says: nothing held

        with _patch_reserved(999_999):  # would gate reclaimable=True on its own
            gate = sa._exit_gate()

        assert gate["vram_process_source"] == "nvml"
        assert gate["reclaimable"] is False, "the driver's answer wins, not the stale reserved figure"

    def test_hard_unload_exits_for_the_context_the_reserved_pool_cannot_see(self, monkeypatch):
        """The measured state: model dropped, reserved 0 (a real
        ``empty_cache()``), the driver still counting 670 MiB against this
        process. Only an exit returns that."""
        _nvml(monkeypatch, own_mb=670)

        async def body():
            with patch.object(sa, "_unload_model"), \
                 _patch_reserved(0), \
                 patch.object(os, "_exit") as mock_exit:
                result = await sa.unload(sa.UnloadRequest(hard=True))
            mock_exit.assert_called_once_with(0)
            assert result["vram_process_mb"] == 670
            assert result["vram_process_source"] == "nvml"

        asyncio.run(body())

    def test_hard_unload_holding_nothing_declines_in_the_drivers_terms(self, monkeypatch):
        """A cold process is not listed by the driver at all. The decline
        names its source, which is what lets the GPU scheduler trust it
        instead of reading a short render GPU as proof of a squat."""
        _nvml(monkeypatch, own_mb=None)

        async def body():
            with patch.object(sa, "_unload_model"), \
                 _patch_reserved(0), \
                 patch.object(os, "_exit") as mock_exit:
                result = await sa.unload(sa.UnloadRequest(hard=True))
            mock_exit.assert_not_called()
            assert result["status"] == "nothing_to_reclaim"
            assert result["vram_process_mb"] == 0
            assert result["vram_process_source"] == "nvml"
            assert result["min_process_mb"] == sa.HARD_UNLOAD_MIN_PROCESS_MB

        asyncio.run(body())

    def test_gate_falls_back_to_reserved_pool_when_nvml_is_unusable_and_warns_once(
        self, monkeypatch, caplog,
    ):
        monkeypatch.setitem(sys.modules, "pynvml", None)  # import raises

        with _patch_reserved(700), \
             caplog.at_level("WARNING", logger="stable-audio-server"):
            gates = [sa._exit_gate() for _ in range(3)]

        assert all(g["vram_process_source"] is None for g in gates)
        assert all(g["vram_reserved_mb"] == 700 and g["reclaimable"] for g in gates)
        assert sum("unusable" in r.getMessage() for r in caplog.records) == 1

    def test_nvml_initialises_once_and_reads_every_time(self, monkeypatch):
        fake = _nvml(monkeypatch, own_mb=670)

        for _ in range(3):
            sa._process_vram_mb()

        assert fake.calls["init"] == 1
        assert fake.calls["procs"] == 3

    def test_a_process_listed_without_its_memory_falls_back_instead_of_reading_zero(
        self, monkeypatch,
    ):
        """Where per-process accounting is unavailable (WDDM, WSL2) NVML lists
        the process with no figure; reading that as 0 would call a
        context-holding process empty."""
        fake = _nvml(monkeypatch)
        fake.own_unavailable = True

        assert sa._process_vram_mb() is None
        assert "per-process accounting" in sa._state.nvml_error

    def test_health_reports_what_the_driver_counts(self, monkeypatch):
        _nvml(monkeypatch, own_mb=670)

        async def body():
            with _patch_reserved(0):
                out = await sa.health()
            assert out["vram_process_mb"] == 670
            assert out["vram_reserved_mb"] == 0
            assert out["hard_unload_min_process_mb"] == sa.HARD_UNLOAD_MIN_PROCESS_MB

        asyncio.run(body())

    def test_idle_watchdog_exits_for_the_context_a_soft_unload_left(self, monkeypatch):
        """The measured shape once the gate could see it: model already None
        (the ladder's soft ``/unload`` beat the watchdog to it), reserved 0,
        670 MiB still counted against this process."""
        _nvml(monkeypatch, own_mb=670)
        sa._state.model = None
        sa._state.degraded = False
        sa._state.last_used = 1.0

        with _patch_reserved(0), \
             patch.object(sa.time, "monotonic", lambda: 1.0 + sa.IDLE_TIMEOUT + 60), \
             patch.object(os, "_exit") as mock_exit:
            _run_one_watchdog_pass()

        mock_exit.assert_called_once_with(0)
