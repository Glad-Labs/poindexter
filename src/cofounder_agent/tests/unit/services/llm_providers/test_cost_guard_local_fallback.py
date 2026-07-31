"""Budget exhaustion downgrades to a local model instead of failing the run.

A spend cap is a ceiling on *money*, not a kill switch on *content*. With
``pipeline_writer_model`` pinned to a cloud model on prod, propagating
``CostGuardExhausted`` turned "we've spent enough this month" into "the pipeline
stops" — the writer dispatch raised and the task failed rather than producing a
cheaper article.

``CostGuardExhausted``'s own docstring prescribes this path: *"fall back to a
free local provider explicitly via the router policy, or surface the error to
the operator."* What it forbids — silently retrying against a **different paid
provider** — is pinned here too.

The downgrade is deliberately loud (``feedback_flag_quality_downgrades``): a
cheaper model is an acceptable outcome, a silent one is not. Both a WARNING log
and a ``warn``-severity finding fire.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.llm_providers.dispatcher as d
from services.cost_guard import CostGuardExhausted
from services.site_config import SiteConfig

_PAID = "anthropic/claude-sonnet-5"
_LOCAL = "ollama/gemma-4-31B-it-qat:latest"


def _exhausted(scope: str = "monthly") -> CostGuardExhausted:
    return CostGuardExhausted(
        "monthly total $15.10 >= $15.00",
        scope=scope,
        spent_usd=15.10,
        limit_usd=15.00,
        provider="litellm",
        model=_PAID,
    )


def _container(**settings: str):
    """Register a container whose SiteConfig carries ``settings``."""
    sc = SiteConfig(initial_config=dict(settings))
    container = MagicMock()
    container.site_config = sc
    return patch("services.container_registry.get_container", lambda: container)


def _resolve(exc, *, model=_PAID, provider_config=None, paid=lambda m, pc: "anthropic" in m):
    span = MagicMock()
    with patch.object(d, "_is_paid_llm_call", paid):
        return d._local_fallback_or_reraise(
            exc, model, provider_config, phase="draft_generation", span=span,
        ), span


def test_downgrades_to_the_configured_local_model():
    with _container(cost_guard_local_fallback_model=_LOCAL), \
         patch("utils.findings.emit_finding") as finding:
        out, span = _resolve(_exhausted())

    assert out == _LOCAL
    span.set_attribute.assert_any_call("llm.cost_guard.downgraded", True)
    span.set_attribute.assert_any_call("llm.cost_guard.original_model", _PAID)
    finding.assert_called_once()
    assert finding.call_args.kwargs["severity"] == "warn", (
        "a quality downgrade must be announced, not absorbed"
    )
    assert finding.call_args.kwargs["kind"] == "paid_call_downgraded_to_local"


def test_falls_back_to_pipeline_local_writer_model_when_unset():
    """No dedicated key configured -> reuse the existing guaranteed-local pin
    rather than inventing a model name."""
    with _container(pipeline_local_writer_model=_LOCAL), \
         patch("utils.findings.emit_finding"):
        out, _ = _resolve(_exhausted())
    assert out == _LOCAL


def test_reraises_when_no_local_model_is_resolvable():
    """No silent invention of a model name (feedback_no_silent_defaults)."""
    exc = _exhausted()
    with _container(), patch("utils.findings.emit_finding"):
        with pytest.raises(CostGuardExhausted) as caught:
            _resolve(exc)
    assert caught.value is exc, "must propagate the ORIGINAL error unchanged"


def test_reraises_when_the_configured_fallback_is_itself_paid():
    """THE guard CostGuardExhausted's contract demands: never swap one paid
    provider for another. A misconfigured fallback must not bill a second API."""
    exc = _exhausted()
    with _container(cost_guard_local_fallback_model="openai/gpt-5"), \
         patch("utils.findings.emit_finding") as finding:
        with pytest.raises(CostGuardExhausted):
            _resolve(exc, paid=lambda m, pc: True)
    finding.assert_not_called()


def test_disabled_flag_restores_hard_fail():
    exc = _exhausted()
    with _container(
        cost_guard_local_fallback_enabled="false",
        cost_guard_local_fallback_model=_LOCAL,
    ):
        with pytest.raises(CostGuardExhausted):
            _resolve(exc)


def test_reraises_when_no_container_is_registered():
    """Early-boot / bootstrap paths have no SiteConfig — fail as before rather
    than guess."""
    exc = _exhausted()
    with patch("services.container_registry.get_container", lambda: None):
        with pytest.raises(CostGuardExhausted):
            _resolve(exc)


@pytest.mark.asyncio
async def test_dispatch_completes_locally_after_the_cap_is_hit():
    """End-to-end at the dispatch seam: the paid call is refused, the LOCAL
    model reaches the provider, and the run does NOT raise.

    Also pins that the swap happens BEFORE the num_ctx backfill — the whole
    point of placing it where it is, so the downgraded call gets its per-phase
    context like any native-local dispatch.
    """
    provider = MagicMock()
    provider.name = "litellm"
    result = MagicMock()
    result.text = "ok"
    result.prompt_tokens = 1
    result.completion_tokens = 1
    provider.complete = AsyncMock(return_value=result)

    with _container(cost_guard_local_fallback_model=_LOCAL), \
         patch.object(d, "get_provider", new=AsyncMock(return_value=provider)), \
         patch.object(d, "get_provider_config", new=AsyncMock(return_value={})), \
         patch.object(d, "_record_dispatch_cost", new=AsyncMock(return_value=None)), \
         patch.object(d, "_enforce_budget_if_paid", new=AsyncMock(side_effect=_exhausted())), \
         patch.object(d, "_is_paid_llm_call", lambda m, pc=None: "anthropic" in m), \
         patch.object(d, "_gpu_serialize_local_dispatch", lambda m, pc: False), \
         patch.object(d, "_vram_guard_enabled", lambda: False), \
         patch("services.ollama_client.resolve_num_ctx", lambda *a, **k: 16384), \
         patch("utils.findings.emit_finding"):
        out = await d.dispatch_complete(
            MagicMock(), [{"role": "user", "content": "hi"}], _PAID,
            tier="standard", phase="draft_generation",
        )

    assert out is result, "dispatch must succeed, not raise, after the downgrade"
    provider.complete.assert_awaited_once()
    assert provider.complete.await_args.kwargs["model"] == _LOCAL
    assert provider.complete.await_args.kwargs["num_ctx"] == 16384, (
        "the downgraded local call must still get its per-phase num_ctx — the "
        "swap has to happen before the backfill"
    )
