"""Unit tests for ``routes.triage_routes._DefaultModelRouter``.

Pins the 2026-05-26 fix that stopped the brain's alert triage from
silently consuming every token on ``<think>`` blocks:

1. When ``ops_triage_writer_model`` is set on the SiteConfig, the
   router uses that model directly — does NOT fall through to
   ``resolve_local_model()``.
2. When ``ops_triage_writer_model`` is empty, the router falls back
   to ``resolve_local_model()`` (which reads ``pipeline_writer_model``)
   — preserves back-compat for installs that never seed the new key.
3. The response is always run through ``strip_think_blocks()`` so a
   thinking model accidentally configured as the triage model still
   produces clean operator prose.

External I/O (``ollama_chat_text``) is mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def fake_site_config():
    """Minimal SiteConfig stub — supports just ``.get(key, default)``."""

    class _StubSC:
        def __init__(self, values: dict[str, str]) -> None:
            self._values = values

        def get(self, key: str, default: Any = "") -> Any:
            return self._values.get(key, default)

    return _StubSC


@pytest.mark.asyncio
async def test_uses_ops_triage_writer_model_when_set(fake_site_config):
    """Triage-specific override path. The router must NOT call
    resolve_local_model() — the override is the whole point."""
    from routes.triage_routes import _DefaultModelRouter

    sc = fake_site_config({
        "ops_triage_writer_model": "gemma3:27b",
    })
    router = _DefaultModelRouter(sc)

    with (
        patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value="clean diagnosis text"),
        ) as mock_chat,
        patch(
            "services.llm_text.resolve_local_model",
        ) as mock_resolve,
    ):
        result = await router.invoke(
            model_class="ops_triage", system="sys", user="user",
        )

    mock_resolve.assert_not_called()  # the override wins
    mock_chat.assert_awaited_once()
    assert mock_chat.await_args.kwargs["model"] == "gemma3:27b"
    assert result["model"] == "ollama/gemma3:27b"
    assert result["text"] == "clean diagnosis text"


@pytest.mark.asyncio
async def test_strips_ollama_prefix_from_override(fake_site_config):
    """Operators sometimes set ``ollama/<name>`` (LiteLLM convention)
    in app_settings; the router strips the prefix because the local
    chat helper takes the bare model name."""
    from routes.triage_routes import _DefaultModelRouter

    sc = fake_site_config({"ops_triage_writer_model": "ollama/gemma3:27b"})
    router = _DefaultModelRouter(sc)

    with (
        patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value="ok"),
        ) as mock_chat,
        patch("services.llm_text.resolve_local_model"),
    ):
        await router.invoke(model_class="ops_triage", system="s", user="u")

    assert mock_chat.await_args.kwargs["model"] == "gemma3:27b"


@pytest.mark.asyncio
async def test_falls_back_to_resolve_local_model_when_override_empty(fake_site_config):
    """Back-compat: installs that never seed the triage-specific key
    must still resolve a model via the writer-model chain. Pins the
    fallback so a future refactor doesn't drop it silently."""
    from routes.triage_routes import _DefaultModelRouter

    sc = fake_site_config({})  # no override
    router = _DefaultModelRouter(sc)

    with (
        patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value="ok"),
        ) as mock_chat,
        patch(
            "services.llm_text.resolve_local_model",
            return_value="some-fallback-model:latest",
        ) as mock_resolve,
    ):
        result = await router.invoke(
            model_class="ops_triage", system="s", user="u",
        )

    mock_resolve.assert_called_once()
    assert mock_chat.await_args.kwargs["model"] == "some-fallback-model:latest"
    assert result["model"] == "ollama/some-fallback-model:latest"


@pytest.mark.asyncio
async def test_response_is_think_block_stripped(fake_site_config):
    """The defensive layer: even if a thinking model IS configured, the
    response gets cleaned before reaching the alert pipeline."""
    from routes.triage_routes import _DefaultModelRouter

    sc = fake_site_config({"ops_triage_writer_model": "glm-4.7-5090:latest"})
    router = _DefaultModelRouter(sc)

    raw_with_think = (
        "<think>analysing the alert and considering possible causes</think>"
        "Pipeline stalled due to a stuck Prefect flow run."
    )
    with (
        patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value=raw_with_think),
        ),
        patch("services.llm_text.resolve_local_model"),
    ):
        result = await router.invoke(model_class="ops_triage", system="s", user="u")

    assert "<think>" not in result["text"]
    assert result["text"] == "Pipeline stalled due to a stuck Prefect flow run."


@pytest.mark.asyncio
async def test_router_threads_pool_into_ollama_chat_text(fake_site_config):
    """Regression for mid-render GPU oversubscription (#1766/#1794 follow-up):
    the triage router MUST pass its asyncpg ``pool`` to ``ollama_chat_text``
    so the call routes through ``dispatch_complete`` — which holds
    ``gpu.lock("ollama")`` (#1794) and therefore blocks while a video render
    holds ``gpu.lock("video")``. The pool-less path takes the direct-httpx
    fallback that BYPASSES the GPU lock, reloading the 19GB writer model
    mid-render and CUDA-OOMing the SDXL server (validation 2026-06-21).
    """
    from routes.triage_routes import _DefaultModelRouter

    sc = fake_site_config({"ops_triage_writer_model": "gemma3:27b"})
    sentinel_pool = object()
    router = _DefaultModelRouter(sc, pool=sentinel_pool)

    with (
        patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value="ok"),
        ) as mock_chat,
        patch("services.llm_text.resolve_local_model"),
    ):
        await router.invoke(model_class="ops_triage", system="s", user="u")

    assert mock_chat.await_args.kwargs.get("pool") is sentinel_pool


@pytest.mark.asyncio
async def test_build_router_threads_pool_to_default_router(fake_site_config):
    """``_build_router`` must forward the route's pool to the default
    router so production triage routes through the gated dispatcher.
    The test-injection factory seam stays pool-agnostic (``(site_config)``)."""
    from routes.triage_routes import _build_router, set_model_router_for_tests

    set_model_router_for_tests(None)  # ensure the default router path
    sc = fake_site_config({"ops_triage_writer_model": "gemma3:27b"})
    sentinel_pool = object()

    router = _build_router(sc, sentinel_pool)

    with (
        patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value="ok"),
        ) as mock_chat,
        patch("services.llm_text.resolve_local_model"),
    ):
        await router.invoke(model_class="ops_triage", system="s", user="u")

    assert mock_chat.await_args.kwargs.get("pool") is sentinel_pool


@pytest.mark.asyncio
async def test_site_config_get_raising_uses_fallback(fake_site_config):
    """A misbehaving SiteConfig that raises on ``.get()`` must NOT
    crash the triage path — it falls back to the writer-model chain."""
    from routes.triage_routes import _DefaultModelRouter

    class _BadSC:
        def get(self, key: str, default: Any = "") -> Any:
            raise RuntimeError("simulated SiteConfig failure")

    router = _DefaultModelRouter(_BadSC())  # type: ignore[arg-type]

    with (
        patch(
            "services.llm_text.ollama_chat_text",
            new=AsyncMock(return_value="ok"),
        ) as mock_chat,
        patch(
            "services.llm_text.resolve_local_model",
            return_value="writer-model:latest",
        ) as mock_resolve,
    ):
        await router.invoke(model_class="ops_triage", system="s", user="u")

    mock_resolve.assert_called_once()
    assert mock_chat.await_args.kwargs["model"] == "writer-model:latest"
