"""Cloud-model routing for ``ImageService._llm_semantic_pexels_query``.

Same latent trap as glad-labs-stack #2199 (title generation): the Pexels
semantic-query helper hardcoded ``providers.get("ollama_native")`` and POSTed
the ``image_search_query_model`` to the LOCAL Ollama provider. It does not fire
today because the 2026-07-07 Sonnet canary only set ``pipeline_writer_model`` —
but the moment ``image_search_query_model`` is pointed at a cloud model
(e.g. ``anthropic/claude-sonnet-5``), ``removeprefix("ollama/")`` leaves the
value unchanged and it is POSTed to ``http://host.docker.internal:11434/api/chat``
→ HTTP 404 on every featured-image search.

The fix (mirror #2199): a pool IS reachable here — ``ImageService`` already
reaches the DB pool via ``getattr(self._site_config, "_pool", None)`` (see
``_ensure_pexels_key``). When it is present, route through
:func:`services.llm_providers.dispatcher.dispatch_complete` on the ``free`` tier
(the tier the original migration comment named — ``plugin.llm_provider.primary
.free``). A local model stays local + free; a cloud model goes to LiteLLM. When
no pool is available (tests / bootstrap) the legacy local ``ollama_native`` path
is preserved.

These tests pin:

1. GUARD — a cloud ``image_search_query_model`` with a pool routes through the
   dispatcher and NEVER touches the local Ollama provider.
2. A local ``image_search_query_model`` with a pool also routes through the
   dispatcher (the ``ollama/`` prefix is stripped so LiteLLM/Ollama see the
   bare tag).
3. No pool → the legacy local ``ollama_native`` path still runs, and the
   dispatcher is NOT used.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.image_service import ImageService

_QUERY = "data analytics dashboard"


def _result(text: str) -> MagicMock:
    """A completion stub exposing ``.text`` (the field the helper reads)."""
    r = MagicMock()
    r.text = text
    return r


def _make_local_provider() -> MagicMock:
    provider = MagicMock()
    provider.name = "ollama_native"
    provider.complete = AsyncMock(return_value=_result(_QUERY))
    return provider


def _sc(image_search_query_model: str, *, pool: object | None) -> MagicMock:
    sc = MagicMock()

    def _get(key: str, default: object = None) -> object:
        if key == "image_search_query_model":
            return image_search_query_model
        if key == "pexels_api_base":
            return "https://api.pexels.com/v1"
        return default

    sc.get.side_effect = _get
    sc.get_int.return_value = 20  # timeouts + max_tokens; value is irrelevant here
    sc.get_float.return_value = 0.4
    # ImageService reaches the DB pool via getattr(self._site_config, "_pool", None).
    sc._pool = pool
    return sc


@pytest.mark.asyncio
async def test_cloud_query_model_routes_through_dispatch_not_local_ollama():
    """GUARD (the mandated one): a CLOUD ``image_search_query_model`` + a pool
    routes through ``dispatch_complete`` and MUST NOT hit the local
    ``ollama_native`` provider (which 404s on a cloud model name)."""
    dispatch = AsyncMock(return_value=_result(_QUERY))
    local = _make_local_provider()
    svc = ImageService(site_config=_sc("anthropic/claude-sonnet-5", pool=object()))

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ):
        out = await svc._llm_semantic_pexels_query("Postgres row-level security")

    assert out == _QUERY
    dispatch.assert_awaited_once()
    call = dispatch.await_args
    # model is the 3rd positional arg: dispatch_complete(pool, messages, model, ...)
    assert call.args[2] == "anthropic/claude-sonnet-5", (
        "cloud query model must be forwarded verbatim so LiteLLM routes it to "
        "Anthropic (removeprefix('ollama/') no-ops on 'anthropic/...')"
    )
    assert call.kwargs["tier"] == "free"
    assert call.kwargs["phase"] == "image_search_query"
    # THE load-bearing guard: the local Ollama provider is never called.
    local.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_query_model_with_pool_also_routes_through_dispatch():
    """A LOCAL ``image_search_query_model`` + a pool still routes through the
    dispatcher (the ``ollama/`` prefix is stripped so LiteLLM/Ollama see the
    bare tag), NOT the direct provider."""
    dispatch = AsyncMock(return_value=_result(_QUERY))
    local = _make_local_provider()
    svc = ImageService(site_config=_sc("ollama/gemma3:27b", pool=object()))

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ):
        out = await svc._llm_semantic_pexels_query("Kubernetes pod lifecycle")

    assert out == _QUERY
    dispatch.assert_awaited_once()
    assert dispatch.await_args.args[2] == "gemma3:27b"  # ollama/ prefix stripped
    local.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_pool_falls_back_to_local_ollama_provider():
    """No pool (tests / bootstrap) → the legacy local ``ollama_native``
    provider path is preserved and the dispatcher is NOT consulted."""
    dispatch = AsyncMock()  # must NOT be called
    local = _make_local_provider()
    svc = ImageService(site_config=_sc("ollama/gemma3:27b", pool=None))

    with patch(
             "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
         ), \
         patch(
             "plugins.registry.get_all_llm_providers", return_value=[local],
         ):
        out = await svc._llm_semantic_pexels_query("Building a FastAPI queue")

    assert out == _QUERY
    local.complete.assert_awaited_once()
    assert local.complete.await_args.kwargs["model"] == "gemma3:27b"
    dispatch.assert_not_awaited()
