"""QA-rail GPU wait budgets — poindexter#914 P2 caller migration.

QA rails are the first caller group migrated onto admission, because they are
the cheapest work to skip and the most expensive to block. The 2026-07-26..29
soak measured it directly: ``image_gen`` holds the GPU at ~229s p90
(featured_image) and ~223s (inline_image_batch), while the ``qa_ragas_judge``
rail's OWN p90 is 18.4s over 336 samples. Unmigrated, a rail queued behind a
render could burn the full 900s lock ceiling waiting to do 18s of work.

Pinned here:

1. Rails pass a budget + ``background`` priority through ``dispatch_complete``
   (the single seam wrapping every local LLM call in ``gpu.lock``).
2. ``GpuBusyError`` fails SOFT — sentinel scores / advisory pass, never a
   fabricated success and never a raised exception into the pipeline.
3. A contention skip is reported as its OWN finding kind. A skip and a broken
   rail produce identical sentinel scores, so without the split a burst of GPU
   pressure reads as the QA stack degrading and sends someone debugging a
   healthy rail.
4. The budget is operator-tunable and ``0`` restores the legacy unbounded
   contract — the escape hatch if skipping proves too aggressive.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gpu_admission import GpuBusyError
from services.site_config import SiteConfig

# ---------------------------------------------------------------------------
# Budget resolution
# ---------------------------------------------------------------------------


def _with_settings(**cfg):
    """Register a SiteConfig on the process container gpu_scheduler reads."""
    return patch(
        "services.gpu_scheduler._sc",
        return_value=SiteConfig(initial_config=cfg),
    )


@pytest.mark.unit
def test_budget_defaults_between_llm_and_render_holds():
    """45s must sit ABOVE ordinary LLM holds and BELOW a render hold, or the
    rail either skips constantly or never skips at all.

    Soak p90s that bracket it: writer_self_review 35.1s, title_generation
    34.5s (must be waitable) vs featured_image 228.7s, inline_image_batch
    222.9s (must be skippable)."""
    from services.gpu_scheduler import qa_rail_wait_budget_s

    with _with_settings():
        budget = qa_rail_wait_budget_s()

    assert budget == 45.0
    assert budget > 35.1, "must outwait writer_self_review p90"
    assert budget < 222.9, "must not outwait an image-render hold"


@pytest.mark.unit
def test_budget_zero_restores_legacy_unbounded_contract():
    """The escape hatch: 0 means None, which is the legacy no-admission path."""
    from services.gpu_scheduler import qa_rail_wait_budget_s

    with _with_settings(gpu_sched_qa_rail_max_wait_s="0"):
        assert qa_rail_wait_budget_s() is None


@pytest.mark.unit
def test_budget_is_operator_tunable():
    from services.gpu_scheduler import qa_rail_wait_budget_s

    with _with_settings(gpu_sched_qa_rail_max_wait_s="120"):
        assert qa_rail_wait_budget_s() == 120.0


# ---------------------------------------------------------------------------
# dispatch_complete threads the contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_complete_forwards_budget_to_the_lock():
    """The budget must reach gpu.lock — otherwise the whole migration is a
    no-op that still waits 900s while looking migrated."""
    from services.llm_providers import dispatcher

    seen = {}

    class _Lock:
        async def __aenter__(self_inner):
            return None

        async def __aexit__(self_inner, *a):
            return False

    def _fake_lock(owner, **kw):
        seen.update(kw)
        seen["owner"] = owner
        return _Lock()

    provider = MagicMock()
    provider.complete = AsyncMock(return_value=MagicMock(text="ok"))

    with patch.object(dispatcher.gpu, "lock", _fake_lock), \
         patch.object(dispatcher, "_gpu_serialize_local_dispatch", return_value=True), \
         patch.object(dispatcher, "get_provider", AsyncMock(return_value=provider)), \
         patch.object(dispatcher, "get_provider_config", AsyncMock(return_value={})):
        await dispatcher.dispatch_complete(
            pool=MagicMock(), messages=[{"role": "user", "content": "x"}],
            model="phi4:14b", phase="qa_ragas_judge",
            max_wait_s=45.0, priority="background",
        )

    assert seen.get("max_wait_s") == 45.0
    assert seen.get("priority") == "background"
    assert seen.get("phase") == "qa_ragas_judge"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_complete_default_stays_legacy():
    """Unmigrated callers must be bit-identical — no budget, pipeline priority."""
    from services.llm_providers import dispatcher

    seen = {}

    class _Lock:
        async def __aenter__(self_inner):
            return None

        async def __aexit__(self_inner, *a):
            return False

    def _fake_lock(owner, **kw):
        seen.update(kw)
        return _Lock()

    provider = MagicMock()
    provider.complete = AsyncMock(return_value=MagicMock(text="ok"))

    with patch.object(dispatcher.gpu, "lock", _fake_lock), \
         patch.object(dispatcher, "_gpu_serialize_local_dispatch", return_value=True), \
         patch.object(dispatcher, "get_provider", AsyncMock(return_value=provider)), \
         patch.object(dispatcher, "get_provider_config", AsyncMock(return_value={})):
        await dispatcher.dispatch_complete(
            pool=MagicMock(), messages=[{"role": "user", "content": "x"}],
            model="phi4:14b",
        )

    assert seen.get("max_wait_s") is None
    assert seen.get("priority") == "pipeline"


# ---------------------------------------------------------------------------
# Fail-soft on GpuBusyError, with a DISTINCT signal
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_gpu_busy_skip_uses_its_own_finding_kind_not_degraded():
    """The core triage guard: contention must not look like a broken rail."""
    from services import ragas_eval

    emit = MagicMock()
    with patch("utils.findings.emit_finding", emit):
        ragas_eval._surface_gpu_busy_skip(
            "ragas", GpuBusyError("eta_exceeds_budget", 210.0), task_id="t-1",
        )

    kwargs = emit.call_args.kwargs
    assert kwargs["kind"] == "qa_rail_gpu_busy_skip"
    assert kwargs["kind"] != "qa_rail_degraded"
    # info, not warn: a bounded skip under load is the design working.
    assert kwargs["severity"] == "info"
    assert kwargs["extra"]["reason"] == "eta_exceeds_budget"
    assert kwargs["extra"]["eta_seconds"] == 210.0


@pytest.mark.unit
def test_deepeval_gpu_busy_uses_the_same_shared_kind():
    """Both rails must report contention identically, or a dashboard filter
    catches one and silently misses the other."""
    from services import deepeval_rails

    emit = MagicMock()
    with patch("utils.findings.emit_finding", emit):
        deepeval_rails._surface_deepeval_gpu_busy(
            GpuBusyError("no_fit", None), rail="brand_fabrication",
        )

    kwargs = emit.call_args.kwargs
    assert kwargs["kind"] == "qa_rail_gpu_busy_skip"
    assert kwargs["severity"] == "info"
    assert kwargs["extra"]["reason"] == "no_fit"


@pytest.mark.unit
def test_gpu_busy_finding_emit_never_raises_into_the_rail():
    """Reporting a skip must not become a new failure mode."""
    from services import deepeval_rails, ragas_eval

    with patch("utils.findings.emit_finding", side_effect=RuntimeError("boom")):
        ragas_eval._surface_gpu_busy_skip(
            "ragas", GpuBusyError("no_fit", None), task_id=None,
        )
        deepeval_rails._surface_deepeval_gpu_busy(
            GpuBusyError("no_fit", None), rail="brand_fabrication",
        )
