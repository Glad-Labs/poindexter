"""Unit tests for ``services/image_providers/pexels.py``.

Mocks ``httpx.AsyncClient`` + the container get_secret path. No real
Pexels API calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.image_provider import ImageProvider, ImageResult
from services.image_providers.pexels import PexelsProvider, build_semantic_pexels_query


def _make_pexels_client(photos: list[dict], status: int = 200):
    """Fake httpx.AsyncClient returning a Pexels ``/search`` payload."""
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"photos": photos})
    client.get = AsyncMock(return_value=resp)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


_SAMPLE_PHOTO = {
    "id": 12345,
    "src": {
        "large": "https://images.pexels.com/12345/large.jpg",
        "small": "https://images.pexels.com/12345/small.jpg",
        "medium": "https://images.pexels.com/12345/medium.jpg",
    },
    "photographer": "Jane Doe",
    "photographer_url": "https://pexels.com/@jane",
    "width": 1920,
    "height": 1080,
    "alt": "A sunset over mountains",
}


class TestFetch:
    @pytest.mark.asyncio
    async def test_returns_image_results(self):
        ctx, _ = _make_pexels_client([_SAMPLE_PHOTO])
        provider = PexelsProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            results = await provider.fetch(
                "mountains", config={"api_key": "test-key"},
            )
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, ImageResult)
        assert r.url == "https://images.pexels.com/12345/large.jpg"
        assert r.thumbnail == "https://images.pexels.com/12345/small.jpg"
        assert r.photographer == "Jane Doe"
        assert r.source == "pexels"
        assert r.search_query == "mountains"
        assert r.metadata["pexels_id"] == 12345

    @pytest.mark.asyncio
    async def test_empty_api_key_returns_empty_list(self):
        provider = PexelsProvider()
        # Container has no DatabaseService → load_api_key returns ""
        with patch("services.container.get_service", return_value=None):
            results = await provider.fetch("query", config={})
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        provider = PexelsProvider()
        results = await provider.fetch("", config={"api_key": "x"})
        assert results == []
        results = await provider.fetch("   ", config={"api_key": "x"})
        assert results == []

    @pytest.mark.asyncio
    async def test_falls_back_to_medium_when_large_missing(self):
        photo = {**_SAMPLE_PHOTO, "src": {"medium": "https://m.url"}}
        ctx, _ = _make_pexels_client([photo])
        provider = PexelsProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            results = await provider.fetch("q", config={"api_key": "k"})
        assert results[0].url == "https://m.url"

    @pytest.mark.asyncio
    async def test_missing_src_skipped(self):
        ctx, _ = _make_pexels_client([{"id": 1}])  # no src
        provider = PexelsProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            results = await provider.fetch("q", config={"api_key": "k"})
        assert results == []

    @pytest.mark.asyncio
    async def test_non_dict_response_handled(self):
        client = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        # Pexels rate-limit returns a string payload sometimes
        resp.json = MagicMock(return_value="rate limited")
        client.get = AsyncMock(return_value=resp)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        provider = PexelsProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            results = await provider.fetch("q", config={"api_key": "k"})
        assert results == []

    @pytest.mark.asyncio
    async def test_params_sent_correctly(self):
        captured: dict[str, Any] = {}

        async def capture_get(url: str, headers: Any = None, params: Any = None):
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={"photos": []})
            return resp

        client = AsyncMock()
        client.get = AsyncMock(side_effect=capture_get)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        provider = PexelsProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            await provider.fetch(
                "sunset",
                config={
                    "api_key": "secret-key",
                    "per_page": 10,
                    "orientation": "portrait",
                    "size": "large",
                    "page": 3,
                },
            )

        assert "pexels.com" in captured["url"]
        assert captured["headers"]["Authorization"] == "secret-key"
        assert captured["params"]["query"] == "sunset"
        assert captured["params"]["per_page"] == 10
        assert captured["params"]["orientation"] == "portrait"
        assert captured["params"]["page"] == 3

    @pytest.mark.asyncio
    async def test_per_page_capped_at_80(self):
        captured: dict[str, Any] = {}

        async def capture_get(url: str, headers: Any = None, params: Any = None):
            captured["params"] = params
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={"photos": []})
            return resp

        client = AsyncMock()
        client.get = AsyncMock(side_effect=capture_get)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        provider = PexelsProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            await provider.fetch("q", config={"api_key": "k", "per_page": 500})

        # Pexels caps at 80; our code should send the min.
        assert captured["params"]["per_page"] == 80


class TestImageResult:
    def test_thumbnail_falls_back_to_url(self):
        r = ImageResult(url="https://x/main.jpg")
        assert r.thumbnail == "https://x/main.jpg"

    def test_thumbnail_respected_when_provided(self):
        r = ImageResult(url="https://x/main.jpg", thumbnail="https://x/thumb.jpg")
        assert r.thumbnail == "https://x/thumb.jpg"

    def test_to_dict_includes_all_fields(self):
        r = ImageResult(
            url="https://x", thumbnail="https://y", photographer="A",
            source="pexels", metadata={"id": 1},
        )
        d = r.to_dict()
        assert d["url"] == "https://x"
        assert d["photographer"] == "A"
        assert d["source"] == "pexels"
        assert d["metadata"] == {"id": 1}


class TestContract:
    def test_conforms_to_image_provider_protocol(self):
        provider = PexelsProvider()
        assert isinstance(provider, ImageProvider)
        assert provider.name == "pexels"
        assert provider.kind == "search"

    def test_fetch_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(PexelsProvider.fetch)


_QUERY = "data analytics dashboard"


def _query_result(text: str) -> MagicMock:
    """A completion stub exposing ``.text`` (the field the helper reads)."""
    r = MagicMock()
    r.text = text
    return r


def _make_local_provider() -> MagicMock:
    provider = MagicMock()
    provider.name = "ollama_native"
    provider.complete = AsyncMock(return_value=_query_result(_QUERY))
    return provider


def _site_config(image_search_query_model: str, *, pool: object | None) -> MagicMock:
    sc = MagicMock()

    def _get(key: str, default: object = None) -> object:
        if key == "image_search_query_model":
            return image_search_query_model
        return default

    sc.get.side_effect = _get
    sc.get_int.return_value = 20  # timeouts + max_tokens; value is irrelevant here
    sc.get_float.return_value = 0.4
    sc._pool = pool
    return sc


class TestBuildSemanticPexelsQuery:
    """poindexter#109 — extracted from ``ImageService._llm_semantic_pexels_query``
    (still delegates through the ImageService method for back-compat; these
    tests exercise the moved implementation directly at its new home).
    """

    @pytest.mark.asyncio
    async def test_cloud_query_model_routes_through_dispatch_not_local_ollama(self):
        """A CLOUD ``image_search_query_model`` + a pool routes through
        ``dispatch_complete`` and MUST NOT hit the local ``ollama_native``
        provider (which 404s on a cloud model name)."""
        dispatch = AsyncMock(return_value=_query_result(_QUERY))
        local = _make_local_provider()
        sc = _site_config("anthropic/claude-sonnet-5", pool=object())

        with patch(
                 "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
             ), \
             patch(
                 "plugins.registry.get_all_llm_providers", return_value=[local],
             ):
            out = await build_semantic_pexels_query(
                "Postgres row-level security", site_config=sc,
            )

        assert out == _QUERY
        dispatch.assert_awaited_once()
        call = dispatch.await_args
        assert call.args[2] == "anthropic/claude-sonnet-5", (
            "cloud query model must be forwarded verbatim so LiteLLM routes it "
            "to Anthropic (removeprefix('ollama/') no-ops on 'anthropic/...')"
        )
        assert call.kwargs["tier"] == "free"
        assert call.kwargs["phase"] == "image_search_query"
        local.complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_local_query_model_with_pool_also_routes_through_dispatch(self):
        """A LOCAL ``image_search_query_model`` + a pool still routes through
        the dispatcher (the ``ollama/`` prefix is stripped so LiteLLM/Ollama
        see the bare tag), NOT the direct provider."""
        dispatch = AsyncMock(return_value=_query_result(_QUERY))
        local = _make_local_provider()
        sc = _site_config("ollama/gemma3:27b", pool=object())

        with patch(
                 "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
             ), \
             patch(
                 "plugins.registry.get_all_llm_providers", return_value=[local],
             ):
            out = await build_semantic_pexels_query(
                "Kubernetes pod lifecycle", site_config=sc,
            )

        assert out == _QUERY
        dispatch.assert_awaited_once()
        assert dispatch.await_args.args[2] == "gemma3:27b"  # ollama/ prefix stripped
        local.complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_pool_falls_back_to_local_ollama_provider(self):
        """No pool (tests / bootstrap) → the legacy local ``ollama_native``
        provider path is preserved and the dispatcher is NOT consulted."""
        dispatch = AsyncMock()  # must NOT be called
        local = _make_local_provider()
        sc = _site_config("ollama/gemma3:27b", pool=None)

        with patch(
                 "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
             ), \
             patch(
                 "plugins.registry.get_all_llm_providers", return_value=[local],
             ):
            out = await build_semantic_pexels_query(
                "Building a FastAPI queue", site_config=sc,
            )

        assert out == _QUERY
        local.complete.assert_awaited_once()
        assert local.complete.await_args.kwargs["model"] == "gemma3:27b"
        dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_model_returns_none(self):
        """An unset ``image_search_query_model`` skips the LLM call entirely
        and returns None so the caller falls back to the raw topic — this
        branch had no direct test coverage before the poindexter#109 move."""
        sc = _site_config("", pool=object())
        dispatch = AsyncMock()

        with patch(
                 "services.llm_providers.dispatcher.dispatch_complete", new=dispatch,
             ), \
             patch(
                 "services.integrations.operator_notify.notify_operator",
                 new=AsyncMock(),
             ):
            out = await build_semantic_pexels_query("Any topic", site_config=sc)

        assert out is None
        dispatch.assert_not_awaited()
