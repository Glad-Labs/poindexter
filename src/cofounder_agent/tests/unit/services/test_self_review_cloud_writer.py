"""Cloud-writer routing for ``self_review_and_revise`` (Sonnet-canary 404 class).

Same latent trap as glad-labs-stack #2199 (title generation): the self-review
pass hardcoded ``providers.get("ollama_native")`` and POSTed the
``writer_self_review_model`` to the LOCAL Ollama provider. It does not fire
today because the 2026-07-07 Sonnet canary only set ``pipeline_writer_model`` —
but the moment ``writer_self_review_model`` is pointed at a cloud model
(e.g. ``anthropic/claude-sonnet-5``), ``removeprefix("ollama/")`` leaves the
value unchanged and it is POSTed to ``http://host.docker.internal:11434/api/chat``
→ HTTP 404 on every self-review call.

The fix (mirror #2199 "follow the writer"): when a ``pool`` is available (both
in-graph callers thread one via ``getattr(database_service, "pool", None)``),
route BOTH the review and the revise call through
:func:`services.llm_providers.dispatcher.dispatch_complete` — the same path the
draft uses. A local writer stays local + free; a cloud writer goes to LiteLLM →
Anthropic and is cost-tracked + cost_guard-gated. When no pool is available
(tests / bootstrap) the legacy local ``ollama_native`` path is preserved.

These tests pin:

1. GUARD — a cloud ``writer_self_review_model`` with a pool routes through the
   dispatcher and NEVER touches the local Ollama provider (which 404s on a
   cloud model name).
2. A local ``writer_self_review_model`` with a pool also routes through the
   dispatcher (self-review follows the writer uniformly; the ``ollama/`` prefix
   is stripped so LiteLLM/Ollama see the bare tag).
3. No pool → the legacy local ``ollama_native`` path still runs (the
   tests / bootstrap fallback), and the dispatcher is NOT used.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.self_review import self_review_and_revise

# Draft must exceed the 500-char floor so the self-review path actually runs.
_DRAFT = "This draft has enough substance for a cross-section review. " * 15
# One numbered contradiction line so the detector fires the revise call.
_CONTRADICTIONS = "1. SECTION A conflicts with SECTION B: the claims disagree."
# Revision must be >= 0.7 * len(draft) to be accepted (thinking-model guard).
_REVISED = "y" * len(_DRAFT)


def _result(text: str) -> MagicMock:
    """A provider/dispatch completion stub exposing ``.text`` (the field
    ``self_review_and_revise`` reads)."""
    r = MagicMock()
    r.text = text
    return r


def _make_local_provider() -> MagicMock:
    provider = MagicMock()
    provider.name = "ollama_native"
    # side_effect covers both the review and the revise call if the (buggy)
    # local path is taken — the guard asserts it is NOT.
    provider.complete = AsyncMock(
        side_effect=[_result(_CONTRADICTIONS), _result(_REVISED)],
    )
    return provider


def _sc(writer_self_review_model: str) -> MagicMock:
    sc = MagicMock()

    def _get(key: str, default: object = None) -> object:
        if key == "enable_writer_self_review":
            return "true"
        if key == "writer_self_review_model":
            return writer_self_review_model
        return default

    sc.get.side_effect = _get
    sc.get_int.return_value = 1500  # timeouts + max_tokens; value is irrelevant here
    # Minimal-edit bounds (poindexter#1000) — real floats, not a MagicMock, or
    # the ratio comparison raises and the stage degrades to "keep original".
    sc.get_float.side_effect = lambda key, default=0.0: default
    return sc


@pytest.mark.asyncio
async def test_cloud_self_review_model_routes_through_dispatch_not_local_ollama():
    """GUARD (the mandated one): a CLOUD ``writer_self_review_model`` + a pool
    routes through ``dispatch_complete`` and MUST NOT hit the local
    ``ollama_native`` provider (which 404s on a cloud model name)."""
    dispatch = AsyncMock(side_effect=[_result(_CONTRADICTIONS), _result(_REVISED)])
    local = _make_local_provider()

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out, stats = await self_review_and_revise(
            _DRAFT,
            "A Title",
            "A Topic",
            pool=object(),  # non-None pool → dispatcher path
            site_config=_sc("anthropic/claude-sonnet-5"),
        )

    assert out == _REVISED
    assert stats["revised"] is True
    # Both the review and the revise call route through the dispatcher.
    assert dispatch.await_count == 2
    for call in dispatch.await_args_list:
        # model is the 3rd positional arg: dispatch_complete(pool, messages, model, ...)
        assert call.args[2] == "anthropic/claude-sonnet-5", (
            "cloud self-review model must be forwarded verbatim so LiteLLM "
            "routes it to Anthropic (removeprefix('ollama/') no-ops on 'anthropic/...')"
        )
    review_call = dispatch.await_args_list[0]
    assert review_call.kwargs["tier"] == "standard"
    assert review_call.kwargs["phase"] == "writer_self_review"
    # THE load-bearing guard: the local Ollama provider is never called.
    local.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_self_review_model_with_pool_also_routes_through_dispatch():
    """A LOCAL ``writer_self_review_model`` + a pool still routes through the
    dispatcher (uniform 'follow the writer' path; the ``ollama/`` prefix is
    stripped so LiteLLM/Ollama see the bare tag), NOT the direct provider."""
    dispatch = AsyncMock(side_effect=[_result(_CONTRADICTIONS), _result(_REVISED)])
    local = _make_local_provider()

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out, stats = await self_review_and_revise(
            _DRAFT, "T", "Topic",
            pool=object(),
            site_config=_sc("ollama/gemma3:27b"),
        )

    assert out == _REVISED
    assert dispatch.await_count == 2
    for call in dispatch.await_args_list:
        assert call.args[2] == "gemma3:27b"  # ollama/ prefix stripped
    local.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_pool_falls_back_to_local_ollama_provider():
    """No pool (tests / bootstrap) → the legacy local ``ollama_native``
    provider path is preserved and the dispatcher is NOT consulted."""
    dispatch = AsyncMock()  # must NOT be called
    local = _make_local_provider()

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out, stats = await self_review_and_revise(
            _DRAFT, "T", "Topic",
            site_config=_sc("ollama/gemma3:27b"),
            # no pool
        )

    assert out == _REVISED
    assert local.complete.await_count == 2
    for call in local.complete.await_args_list:
        assert call.kwargs["model"] == "gemma3:27b"
    dispatch.assert_not_awaited()
