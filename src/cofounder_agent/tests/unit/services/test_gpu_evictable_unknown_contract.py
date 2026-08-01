"""Eviction credit distinguishes "nothing evictable" from "I don't know yet".

`evictable_ollama_gb` returned `0.0` for every failure mode, and `0.0` is also
the legitimate "nothing is loaded" answer — so admission could not tell them
apart and treated ignorance as absence.

Measured on prod 2026-07-31 (poindexter#914): Prometheus was missing a live
`llama-server` holding ~21 GB on gpu0 that `nvidia-smi` saw at the same instant.
Pure scrape lag — 10s exporter refresh + 30s Prometheus interval ≈ 40s worst
case, against a 45s QA-rail budget. Admission read the `0.0` as "nothing to
evict", answered `no_fit`, and the rail degraded and **passed open**, so the
judgement silently did not happen. One 66-minute window: 105 rejections, and a
0% success rate on `qa_deepeval_judge`.

The asymmetry that decides the fail-open direction: a rejected rail means NO QA
at all, whereas an over-granted one merely thrashes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.gpu_admission import AdmissionInputs, decide
from services.gpu_registry import GPURegistry
from services.site_config import SiteConfig


def _registry(**settings: str) -> GPURegistry:
    return GPURegistry(site_config=SiteConfig(initial_config=dict(settings)))


def _row(gpu: str, process: str, mib: float, pid: str = "1") -> dict:
    return {"metric": {"gpu": gpu, "process": process, "pid": pid},
            "value": [0, str(mib)]}


# ---------------------------------------------------------------------------
# GPURegistry.evictable_ollama_gb — known vs unknown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_lag_returns_unknown_not_zero():
    """THE prod bug: a 21GB model is resident but not yet in the process list.

    `used` says the card is nearly full; the per-process rows account for only a
    fraction. That gap means the list is stale, so the honest answer is None.
    """
    reg = _registry()
    rows = [  # everything EXCEPT the just-loaded llama-server
        _row("0", "python", 7636), _row("0", "chrome", 85), _row("0", "cosmic", 42),
        _row("1", "llama-server", 21326),
    ]
    with patch.object(reg, "_instant_query", new=AsyncMock(return_value=rows)), \
         patch.object(reg, "_instant_scalar", new=AsyncMock(return_value=29500.0)):
        out = await reg.evictable_ollama_gb(0)

    assert out is None, (
        "a large unattributed remainder means the process list has not caught "
        "up with a load that already happened — that is unknown, not zero"
    )


@pytest.mark.asyncio
async def test_genuinely_empty_card_reports_zero_not_unknown():
    """The other side: telemetry is fresh and really nothing evictable is up.

    This must stay a hard 0.0 or the fit gate loses its teeth entirely.
    """
    reg = _registry()
    rows = [_row("0", "chrome", 85), _row("0", "cosmic", 42)]
    with patch.object(reg, "_instant_query", new=AsyncMock(return_value=rows)), \
         patch.object(reg, "_instant_scalar", new=AsyncMock(return_value=200.0)):
        out = await reg.evictable_ollama_gb(0)

    assert out == 0.0


@pytest.mark.asyncio
async def test_resident_runner_is_credited_when_telemetry_is_fresh():
    reg = _registry()
    rows = [_row("0", "llama-server", 20480), _row("0", "chrome", 85)]
    with patch.object(reg, "_instant_query", new=AsyncMock(return_value=rows)), \
         patch.object(reg, "_instant_scalar", new=AsyncMock(return_value=20700.0)):
        out = await reg.evictable_ollama_gb(0)

    assert out == pytest.approx(20.0, abs=0.05)


@pytest.mark.asyncio
async def test_only_this_card_is_counted():
    """Per-card mandate: the GPU1-pinned instance must not credit GPU0."""
    reg = _registry()
    rows = [_row("1", "llama-server", 21326), _row("0", "chrome", 85)]
    with patch.object(reg, "_instant_query", new=AsyncMock(return_value=rows)), \
         patch.object(reg, "_instant_scalar", new=AsyncMock(return_value=100.0)):
        out = await reg.evictable_ollama_gb(0)

    assert out == 0.0, "gpu1's runner is not evictable credit for gpu0"


@pytest.mark.asyncio
async def test_absent_metric_is_unknown():
    reg = _registry()
    with patch.object(reg, "_instant_query", new=AsyncMock(return_value=[])):
        assert await reg.evictable_ollama_gb(0) is None


@pytest.mark.asyncio
async def test_no_rows_for_this_card_is_unknown():
    """The exporter may not have enumerated the card yet."""
    reg = _registry()
    with patch.object(reg, "_instant_query", new=AsyncMock(return_value=[_row("1", "x", 5)])):
        assert await reg.evictable_ollama_gb(0) is None


@pytest.mark.asyncio
async def test_uncorroboratable_used_is_unknown():
    """Without `used` there is no way to tell a fresh list from a stale one."""
    reg = _registry()
    rows = [_row("0", "chrome", 85)]
    with patch.object(reg, "_instant_query", new=AsyncMock(return_value=rows)), \
         patch.object(reg, "_instant_scalar", new=AsyncMock(return_value=None)):
        assert await reg.evictable_ollama_gb(0) is None


@pytest.mark.asyncio
async def test_cleared_pattern_is_a_real_zero():
    """An operator who blanks the pattern has DECLARED nothing evictable."""
    reg = _registry(gpu_evictable_process_pattern=" ")
    assert await reg.evictable_ollama_gb(0) == 0.0


# ---------------------------------------------------------------------------
# decide() — unknown must fail open
# ---------------------------------------------------------------------------


def _inputs(**kw) -> AdmissionInputs:
    # max_wait_s must be set: `None` is the legacy "admission does not apply"
    # path and returns grant before the fit gate is reached. 45.0 is the live
    # `gpu_sched_qa_rail_max_wait_s`. No holder_key, so the ETA gate is skipped
    # and each test isolates the fit gate.
    base = dict(max_wait_s=45.0, free_gpu0_gb=9.6, headroom_gb=6.0,
                model_estimate_gb=9.93)
    base.update(kw)
    return AdmissionInputs(**base)


def test_unknown_eviction_credit_fails_open():
    """THE guard. Real prod numbers: phi4 at 9.93GB, 9.6GB free, 6GB headroom.

    Budget is 3.6GB so the model does not fit free space — the only remaining
    question is whether something evictable holds the rest. Answering that with
    a rejection on unknown is what silently skipped QA.
    """
    out = decide(_inputs(evictable_gpu0_gb=None))
    assert out.action == "grant_after_unload"
    assert out.reason is None


def test_known_zero_still_rejects():
    """Fail-open on UNKNOWN must not become fail-open on everything — a card
    with genuinely nothing to evict and no room must still reject."""
    out = decide(_inputs(evictable_gpu0_gb=0.0))
    assert out.action == "reject"
    assert out.reason == "no_fit"


def test_known_credit_grants_after_unload():
    out = decide(_inputs(evictable_gpu0_gb=21.6))
    assert out.action == "grant_after_unload"


def test_fits_free_space_outright():
    out = decide(_inputs(free_gpu0_gb=30.0, evictable_gpu0_gb=None))
    assert out.action == "grant"


def test_default_is_unknown_not_zero():
    """A caller that never supplies the field must not silently mean 'zero'."""
    assert AdmissionInputs(max_wait_s=None).evictable_gpu0_gb is None
