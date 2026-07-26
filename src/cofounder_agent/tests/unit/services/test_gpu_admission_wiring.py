"""Admission wiring into gpu.lock() (poindexter#914 P1, Tasks B4 + B6).

Pins the DOUBLE-inertness contract (flag off OR no budget ⇒ the calculator is
never even consulted), the reject path (GpuBusyError pre-wait + info finding),
the grant_after_unload eviction, the wait cap, and the grep-proof that no
production call site has opted into the contract yet.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gpu_admission import AdmissionDecision, GpuBusyError
from services.gpu_scheduler import GPUScheduler


def _quiet(gpu: GPUScheduler) -> GPUScheduler:
    """Stub out I/O side channels so lock() runs hermetically."""
    gpu._acquire_pg_advisory_lock = AsyncMock()
    gpu._release_pg_advisory_lock = AsyncMock()
    gpu._wait_for_gaming_clear = AsyncMock()
    gpu._unload_ollama_models = AsyncMock()
    return gpu


def _cfg_bool_map(**over):
    def _get(key, default=False):
        return over.get(key, default)

    return _get


# ---------------------------------------------------------------------------
# Double inertness — the calculator is never consulted unless BOTH the flag
# is on and the caller declared a budget.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_never_consults_decide_even_with_budget():
    gpu = _quiet(GPUScheduler())
    with patch("services.gpu_admission.decide") as spy:
        with patch(
            "services.gpu_scheduler._cfg_bool",
            _cfg_bool_map(gpu_sched_enabled=False),
        ):
            async with gpu.lock("ollama", model="m", max_wait_s=30.0):
                pass
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_no_budget_never_consults_decide_even_with_flag_on():
    gpu = _quiet(GPUScheduler())
    with patch("services.gpu_admission.decide") as spy:
        with patch(
            "services.gpu_scheduler._cfg_bool",
            _cfg_bool_map(gpu_sched_enabled=True),
        ):
            async with gpu.lock("ollama", model="m"):
                pass
    spy.assert_not_called()


# ---------------------------------------------------------------------------
# Reject path — GpuBusyError BEFORE any wait, info finding emitted.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_raises_pre_wait_and_emits_finding():
    gpu = _quiet(GPUScheduler())
    gpu._assemble_admission_inputs = AsyncMock()  # inputs irrelevant — decide stubbed
    gpu._emit_admission_rejected_finding = MagicMock()
    with patch(
        "services.gpu_admission.decide",
        return_value=AdmissionDecision(
            action="reject", reason="eta_exceeds_budget", eta_seconds=412.0
        ),
    ):
        with patch(
            "services.gpu_scheduler._cfg_bool",
            _cfg_bool_map(gpu_sched_enabled=True),
        ):
            with pytest.raises(GpuBusyError) as exc_info:
                async with gpu.lock(
                    "ollama", model="m", phase="qa_vision", max_wait_s=60.0
                ):
                    pass  # pragma: no cover — must not enter

    assert exc_info.value.reason == "eta_exceeds_budget"
    assert exc_info.value.eta_seconds == 412.0
    # Rejected pre-wait: the in-process gate was never acquired.
    assert not gpu._lock.locked()
    kwargs = gpu._emit_admission_rejected_finding.call_args.kwargs
    assert kwargs["owner"] == "ollama"
    assert kwargs["phase"] == "qa_vision"
    assert kwargs["reason"] == "eta_exceeds_budget"


def test_finding_dedup_key_is_owner_phase_reason():
    gpu = GPUScheduler()
    captured = {}

    def _fake_emit(**kwargs):
        captured.update(kwargs)

    with patch("utils.findings.emit_finding", _fake_emit):
        gpu._emit_admission_rejected_finding(
            owner="ollama", phase="qa_vision", reason="no_fit",
            eta_seconds=None, max_wait_s=60.0,
        )
    assert captured["dedup_key"] == "gpu-admission:ollama:qa_vision:no_fit"
    assert captured["kind"] == "gpu_admission_rejected"
    assert captured["severity"] == "info"


# ---------------------------------------------------------------------------
# grant_after_unload — evicts via the existing helper AFTER the lock is held.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grant_after_unload_calls_eviction_helper():
    gpu = _quiet(GPUScheduler())
    gpu._assemble_admission_inputs = AsyncMock()
    with patch(
        "services.gpu_admission.decide",
        return_value=AdmissionDecision(action="grant_after_unload"),
    ):
        with patch(
            "services.gpu_scheduler._cfg_bool",
            _cfg_bool_map(gpu_sched_enabled=True),
        ):
            async with gpu.lock("ollama", model="big-writer", max_wait_s=30.0):
                gpu._unload_ollama_models.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_grant_does_not_evict_for_ollama_owner():
    gpu = _quiet(GPUScheduler())
    gpu._assemble_admission_inputs = AsyncMock()
    with patch(
        "services.gpu_admission.decide",
        return_value=AdmissionDecision(action="grant"),
    ):
        with patch(
            "services.gpu_scheduler._cfg_bool",
            _cfg_bool_map(gpu_sched_enabled=True),
        ):
            async with gpu.lock("ollama", model="m", max_wait_s=30.0):
                gpu._unload_ollama_models.assert_not_awaited()


# ---------------------------------------------------------------------------
# max_wait_s caps the actual wait (min with the operator ceiling).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_caps_wait_when_flag_on():
    """Holder present; admission grants (stubbed); a 0.1s budget must time the
    wait out far below the 900s operator ceiling."""
    from services.gpu_scheduler import GpuLockTimeoutError

    gpu = _quiet(GPUScheduler())
    gpu._assemble_admission_inputs = AsyncMock()
    gpu._emit_lock_timeout_finding = MagicMock()
    await gpu._lock.acquire()  # simulate a holder
    try:
        with patch(
            "services.gpu_admission.decide",
            return_value=AdmissionDecision(action="grant"),
        ):
            with patch(
                "services.gpu_scheduler._cfg_bool",
                _cfg_bool_map(gpu_sched_enabled=True),
            ):
                with pytest.raises(GpuLockTimeoutError):
                    await asyncio.wait_for(
                        gpu.lock("ollama", model="m", max_wait_s=0.1).__aenter__(),
                        timeout=5.0,
                    )
    finally:
        gpu._lock.release()


@pytest.mark.asyncio
async def test_budget_does_not_cap_wait_when_flag_off():
    """Flag off ⇒ fully legacy: the tiny budget is ignored and the waiter
    parks until the holder releases (no timeout at 0.3s)."""
    gpu = _quiet(GPUScheduler())
    await gpu._lock.acquire()
    with patch(
        "services.gpu_scheduler._cfg_bool",
        _cfg_bool_map(gpu_sched_enabled=False),
    ):
        async def _try():
            async with gpu.lock("ollama", max_wait_s=0.1):
                pass

        waiter = asyncio.ensure_future(_try())
        await asyncio.sleep(0.3)
        assert not waiter.done()  # still parked — budget had no effect
        gpu._lock.release()
        await asyncio.wait_for(waiter, timeout=5.0)


# ---------------------------------------------------------------------------
# Priority threads through to the queue mirror row.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_priority_reaches_queue_mirror():
    gpu = _quiet(GPUScheduler())
    await gpu._lock.acquire()
    enq = AsyncMock(return_value=None)
    with patch("services.gpu_queue_mirror.enqueue", enq):
        async def _try():
            async with gpu.lock("ollama", model="m", priority="background"):
                pass

        waiter = asyncio.ensure_future(_try())
        await asyncio.sleep(0.05)
        gpu._lock.release()
        await asyncio.wait_for(waiter, timeout=5.0)
    assert enq.await_args.kwargs.get("priority") == "background"


# ---------------------------------------------------------------------------
# Grep-proof of inertness (Task B6): no production call site passes the
# contract kwarg yet — P2 migrates callers group by group, each in its own PR.
# ---------------------------------------------------------------------------


def test_no_production_call_site_passes_max_wait_s_yet():
    root = Path(__file__).resolve().parents[3]  # src/cofounder_agent
    allowed_suffixes = ("services/gpu_scheduler.py", "services/gpu_admission.py")
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/") or rel.endswith(allowed_suffixes):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "max_wait_s=" in text:
            offenders.append(rel)
    assert offenders == [], (
        f"P1 ships with ZERO contract callers; these files pass max_wait_s= "
        f"outside the scheduler/admission modules: {offenders}. Caller "
        "migration is P2 — one caller group per PR, informed by the soak."
    )
