"""Cloud-writer routing for ``generate_canonical_title`` (Sonnet-canary 404 fix).

Root cause (same class as the brain content_gen probe fix, glad-labs-stack
#2194): the title path hardcoded ``providers.get("ollama_native")`` and POSTed
the ``pipeline_writer_model`` to the LOCAL Ollama provider. Once the 2026-07-07
Sonnet canary set ``pipeline_writer_model=anthropic/claude-sonnet-5``,
``removeprefix("ollama/")`` left the value unchanged and it was POSTed to
``http://host.docker.internal:11434/api/chat`` → HTTP 404 on every post.

The fix (operator decision: "follow the writer"): when a ``pool`` is available
(the in-graph callers always have one), route the title call through
:func:`services.llm_providers.dispatcher.dispatch_complete` — the same path the
draft uses. A local writer stays local + free; a cloud writer goes to LiteLLM →
Anthropic and is cost-tracked + cost_guard-gated. When no pool is available
(tests / bootstrap) the legacy local ``ollama_native`` path is preserved.

These tests pin:

1. GUARD — a cloud ``pipeline_writer_model`` with a pool routes through the
   dispatcher and NEVER touches the local Ollama provider.
2. A local ``pipeline_writer_model`` with a pool also routes through the
   dispatcher (titles follow the writer uniformly; cost_logs get the row).
3. No pool → the legacy local ``ollama_native`` path still runs (the
   tests / bootstrap fallback), and the dispatcher is NOT used.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.title_generation import generate_canonical_title


def _result(text: str) -> MagicMock:
    """A provider/dispatch completion stub exposing ``.text`` (the field
    ``generate_canonical_title`` reads)."""
    r = MagicMock()
    r.text = text
    return r


def _make_local_provider(returns: str) -> MagicMock:
    provider = MagicMock()
    provider.name = "ollama_native"
    provider.complete = AsyncMock(return_value=_result(returns))
    return provider


def _sc(writer_model: str, title_model: str = "") -> MagicMock:
    sc = MagicMock()
    # generate_canonical_title consults ``pipeline_title_model`` first (empty =
    # follow the writer), then ``pipeline_writer_model`` — key-aware so the
    # existing writer-model tests exercise the empty-pin fallback for free.
    data = {
        "pipeline_title_model": title_model,
        "pipeline_writer_model": writer_model,
    }
    sc.get.side_effect = lambda key, default=None: data.get(key, default)
    sc.get_int.return_value = 4000
    return sc


@pytest.mark.asyncio
async def test_cloud_writer_routes_through_dispatch_not_local_ollama():
    """GUARD (the mandated one): a CLOUD ``pipeline_writer_model`` + a pool
    routes through ``dispatch_complete`` and MUST NOT hit the local
    ``ollama_native`` provider (which 404s on a cloud model name)."""
    dispatch = AsyncMock(return_value=_result('{"title": "A Sonnet-Written Title"}'))
    local = _make_local_provider('{"title": "SHOULD NOT BE USED"}')

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI trends",
            primary_keyword="AI",
            content_excerpt="Body excerpt",
            site_config=_sc("anthropic/claude-sonnet-5"),
            pool=object(),  # non-None pool → dispatcher path
        )

    assert out == "A Sonnet-Written Title"
    dispatch.assert_awaited_once()
    args, kwargs = dispatch.await_args.args, dispatch.await_args.kwargs
    # model is the 3rd positional arg: dispatch_complete(pool, messages, model, ...)
    assert args[2] == "anthropic/claude-sonnet-5", (
        "cloud writer must be forwarded verbatim so LiteLLM routes it to "
        "Anthropic (removeprefix('ollama/') is a no-op on 'anthropic/...')"
    )
    assert kwargs["tier"] == "standard"
    assert kwargs["phase"] == "title_generation"
    assert kwargs["think"] is False
    # THE load-bearing guard: the local Ollama provider is never called.
    local.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_writer_with_pool_also_routes_through_dispatch():
    """A LOCAL ``pipeline_writer_model`` + a pool still routes through the
    dispatcher (uniform 'follow the writer' path; the ``ollama/`` prefix is
    stripped so LiteLLM/Ollama see the bare tag), NOT the direct provider."""
    dispatch = AsyncMock(return_value=_result('{"title": "A Local Title"}'))
    local = _make_local_provider('{"title": "SHOULD NOT BE USED"}')

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI", primary_keyword="AI", content_excerpt="x",
            site_config=_sc("ollama/gemma3:27b"),
            pool=object(),
        )

    assert out == "A Local Title"
    dispatch.assert_awaited_once()
    assert dispatch.await_args.args[2] == "gemma3:27b"  # ollama/ prefix stripped
    assert dispatch.await_args.kwargs["think"] is False
    local.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_pool_falls_back_to_local_ollama_provider():
    """No pool (tests / bootstrap) → the legacy local ``ollama_native``
    provider path is preserved and the dispatcher is NOT consulted."""
    dispatch = AsyncMock()  # must NOT be called
    local = _make_local_provider('{"title": "A Local Fallback Title"}')

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI", primary_keyword="AI", content_excerpt="x",
            site_config=_sc("ollama/gemma3:27b"),
            # no pool
        )

    assert out == "A Local Fallback Title"
    local.complete.assert_awaited_once()
    lkwargs = local.complete.await_args.kwargs
    assert lkwargs["model"] == "gemma3:27b"
    assert lkwargs["think"] is False
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_title_pin_overrides_writer_model():
    """``pipeline_title_model`` (when set) wins over ``pipeline_writer_model``
    — the decouple-titles-from-a-writer-experiment knob. The ``ollama/``
    prefix is stripped exactly like the writer path."""
    dispatch = AsyncMock(return_value=_result('{"title": "Pinned Title"}'))
    local = _make_local_provider('{"title": "SHOULD NOT BE USED"}')

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ), \
         patch("services.prompt_manager.get_prompt_manager") as pm:
        pm.return_value.get_prompt.return_value = "PROMPT"
        out = await generate_canonical_title(
            topic="AI", primary_keyword="AI", content_excerpt="x",
            site_config=_sc(
                "anthropic/claude-sonnet-5", title_model="ollama/gemma3:27b",
            ),
            pool=object(),
        )

    assert out == "Pinned Title"
    dispatch.assert_awaited_once()
    assert dispatch.await_args.args[2] == "gemma3:27b", (
        "the title pin must be forwarded (ollama/ stripped), not the writer"
    )
    local.complete.assert_not_awaited()
