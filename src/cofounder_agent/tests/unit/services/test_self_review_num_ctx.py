"""``writer_self_review`` context window — end-to-end pin from phase to provider.

The self-review pass consumes the FULL draft as input, so it is the phase most
exposed to the context wall: with ``ollama_num_ctx=8192`` and a revise budget of
``content_router_contradiction_revise_max_tokens`` (8000), a ~5k-token draft
plus the revision cannot both fit, and the review output truncates. Measured on
prod 2026-07-28: 118 ``writer_self_review`` rows in ``cost_logs``, **zero** over
8192, three landing exactly on it.

The fix for that is operational (set ``writer_self_review_num_ctx``), not a code
change — but only because of a seam that is easy to misread, and has been
misread. ``services/self_review.py`` calls ``dispatch_complete`` WITHOUT a
``num_ctx`` kwarg, which looks like the per-phase override is unplumbed. It is
not: ``dispatch_complete`` back-fills ``num_ctx`` for any LOCAL dispatch that
did not thread one, via ``_resolve_default_num_ctx(phase, ...)`` ->
``resolve_num_ctx(phase)`` -> ``<phase>_num_ctx`` -> ``ollama_num_ctx`` -> 8192
(glad-labs-stack#2170). Because self-review already passes
``phase="writer_self_review"``, its override IS live.

Threading ``num_ctx`` explicitly at the call site — the "obvious" fix — would
REGRESS this: a caller-supplied ``num_ctx`` suppresses the back-fill, which is
the only thing that skips the param for a paid/cloud model. A cloud
``writer_self_review_model`` (the routing that ``test_self_review_cloud_writer``
exists to protect) would then drag an Ollama-only param and a wasted local
``/api/show`` from the VRAM clamp into every Anthropic call.

So these tests pin the chain rather than a call-site kwarg. They fail if:

- self-review stops passing ``phase`` (or the phase string drifts), so the
  override silently resolves against the wrong key;
- the ``dispatch_complete`` back-fill is removed, dropping every self-review
  call back to Ollama's Modelfile default (gemma-4-31B ships at 262144);
- someone threads ``num_ctx`` at the call site and breaks the cloud carve-out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.self_review import self_review_and_revise
from services.site_config import SiteConfig

# Draft must exceed the 500-char floor so the self-review path actually runs.
_DRAFT = "This draft has enough substance for a cross-section review. " * 15
# One numbered contradiction line so the detector fires the revise call too —
# both calls must carry the context, not just the first.
_CONTRADICTIONS = "1. SECTION A conflicts with SECTION B: the claims disagree."
# Revision must be >= 0.7 * len(draft) to be accepted (thinking-model guard).
_REVISED = "y" * len(_DRAFT)

_LOCAL_MODEL = "ollama/gemma-4-31B-it-qat:latest"
_CLOUD_MODEL = "anthropic/claude-sonnet-5"


def _result(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.prompt_tokens = 0
    r.completion_tokens = 0
    return r


def _site_config(model: str, **overrides: str) -> SiteConfig:
    """A real SiteConfig — this is what the container hands the back-fill."""
    return SiteConfig(
        initial_config={
            "enable_writer_self_review": "true",
            "writer_self_review_model": model,
            # The clamp would otherwise httpx the local /api/show; the budget
            # guard has its own tests and is not what this file pins.
            "vram_budget_guard_enabled": "false",
            **overrides,
        },
    )


def _run_through_real_dispatch(site_config: SiteConfig, is_paid: bool):
    """Patch the transport, keep ``dispatch_complete`` itself real.

    The whole point is to exercise the back-fill, so the dispatcher is NOT
    mocked — only the provider it resolves, the cost write, and the GPU lock.
    Returns the provider stub whose ``complete`` kwargs carry the assertion.
    """
    provider = MagicMock()
    provider.name = "litellm"
    provider.complete = AsyncMock(
        side_effect=[_result(_CONTRADICTIONS), _result(_REVISED)],
    )
    container = MagicMock()
    container.site_config = site_config

    return provider, [
        patch(
            "services.llm_providers.dispatcher.get_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "services.llm_providers.dispatcher.get_provider_config",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "services.llm_providers.dispatcher._record_dispatch_cost",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.llm_providers.dispatcher._enforce_budget_if_paid",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.llm_providers.dispatcher._is_paid_llm_call",
            lambda _model, _pc=None: is_paid,
        ),
        patch(
            "services.llm_providers.dispatcher._gpu_serialize_local_dispatch",
            lambda _model, _pc: False,
        ),
        patch("services.container_registry.get_container", lambda: container),
    ]


async def _self_review(site_config: SiteConfig, is_paid: bool) -> MagicMock:
    provider, patches = _run_through_real_dispatch(site_config, is_paid)
    import contextlib

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(
            patch("services.prompt_manager.get_prompt_manager"),
        ).return_value.get_prompt.return_value = "PROMPT"
        out, stats = await self_review_and_revise(
            _DRAFT, "A Title", "A Topic",
            pool=object(),  # non-None pool -> dispatcher path
            site_config=site_config,
        )

    assert out == _REVISED, "revise call should have been accepted"
    assert stats["revised"] is True
    return provider


@pytest.mark.asyncio
async def test_self_review_dispatch_uses_phase_override_num_ctx():
    """THE pin: ``writer_self_review_num_ctx`` reaches the provider.

    A per-phase override set to 16384 must land on BOTH the review and the
    revise dispatch — not the 8192 ``ollama_num_ctx`` global, and not absent.
    """
    provider = await _self_review(
        _site_config(
            _LOCAL_MODEL,
            ollama_num_ctx="8192",
            writer_self_review_num_ctx="16384",
        ),
        is_paid=False,
    )

    assert provider.complete.await_count == 2
    for call in provider.complete.await_args_list:
        assert call.kwargs["num_ctx"] == 16384, (
            "writer_self_review_num_ctx must reach the provider — self-review "
            "consumes the whole draft, so the phase override is what keeps its "
            "output off the context wall"
        )


@pytest.mark.asyncio
async def test_self_review_num_ctx_falls_back_to_global_when_phase_unset():
    """With no phase override, the dispatch inherits ``ollama_num_ctx``.

    This is the state prod was in on 2026-07-28 (the key was simply unset), and
    it is what makes the 8192 ceiling in ``cost_logs`` a config gap rather than
    a plumbing bug. Pinning the fallback keeps the distinction legible.
    """
    provider = await _self_review(
        _site_config(_LOCAL_MODEL, ollama_num_ctx="8192"),
        is_paid=False,
    )

    for call in provider.complete.await_args_list:
        assert call.kwargs["num_ctx"] == 8192


@pytest.mark.asyncio
async def test_self_review_sends_no_num_ctx_for_a_cloud_model():
    """The carve-out that an explicit call-site ``num_ctx`` would destroy.

    ``num_ctx`` is Ollama-only and meaningless to Anthropic; supplying it also
    drags the VRAM clamp's local ``/api/show`` into every cloud call. The
    back-fill skips paid models — keep it that way.
    """
    provider = await _self_review(
        _site_config(_CLOUD_MODEL, writer_self_review_num_ctx="16384"),
        is_paid=True,
    )

    assert provider.complete.await_count == 2
    for call in provider.complete.await_args_list:
        assert "num_ctx" not in call.kwargs, (
            "a paid/cloud self-review must not carry num_ctx — see "
            "dispatcher._resolve_default_num_ctx"
        )
