"""MemoryClient.embed routes through the LiteLLM dispatcher (poindexter#827).

In-repo consumers (worker / CLI / MCP server) have the ``services``
package importable, so ``embed()`` must go through ``dispatch_embed`` —
cost accounting, tracing, and per-model base-url overrides — and must
NOT hit the direct Ollama ``/api/embed`` path. The direct path survives
only for standalone ``pip install poindexter`` installs where
``services`` is absent (exercised implicitly by the ImportError guard).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from poindexter.memory.client import MemoryClient


def _client_with_fake_conn() -> tuple[MemoryClient, AsyncMock]:
    """MemoryClient with connect() pre-satisfied by mocks."""
    mc = MemoryClient(
        dsn="postgresql://unused/unused",
        ollama_url="http://unused:11434",
    )
    http_mock = AsyncMock()
    mc._pool = AsyncMock()
    mc._http = http_mock
    return mc, http_mock


@pytest.mark.unit
class TestEmbedDispatch:
    @pytest.mark.asyncio
    async def test_embed_routes_through_dispatcher(self):
        mc, http_mock = _client_with_fake_conn()
        vec = [0.1] * mc.embed_dim
        embed_mock = AsyncMock(return_value=vec)

        with patch(
            "services.llm_providers.dispatcher.dispatch_embed", embed_mock,
        ):
            out = await mc.embed("hello world")

        assert out == vec
        assert embed_mock.await_count == 1
        args = embed_mock.call_args.args
        assert args[0] is mc._pool
        assert args[1] == "hello world"
        assert args[2] == mc.embed_model
        # The direct Ollama /api/embed path must NOT fire.
        http_mock.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatcher_dim_mismatch_raises(self):
        mc, _http_mock = _client_with_fake_conn()

        with patch(
            "services.llm_providers.dispatcher.dispatch_embed",
            AsyncMock(return_value=[0.1] * 5),
        ):
            with pytest.raises(RuntimeError, match="Expected"):
                await mc.embed("hello")

    @pytest.mark.asyncio
    async def test_dispatcher_error_propagates(self):
        """No silent fallback to direct HTTP when the dispatcher errors —
        fail loud per feedback_no_silent_defaults; the direct path is only
        for installs where services isn't importable at all."""
        mc, http_mock = _client_with_fake_conn()

        with patch(
            "services.llm_providers.dispatcher.dispatch_embed",
            AsyncMock(side_effect=RuntimeError("provider down")),
        ):
            with pytest.raises(RuntimeError, match="provider down"):
                await mc.embed("hello")
        http_mock.post.assert_not_called()
