"""Tests for the embedding-call retry helper in services/rag_engine.py.

glad-labs-stack#876 — under concurrent pipeline load the Ollama *embedding*
endpoint intermittently refuses connections while the *chat* endpoint stays
healthy, which dropped the writer's RAG grounding to zero context and dragged
quality scores into refine loops. ``_aembed_query_with_retry`` adds bounded
exponential backoff + jitter so a transient refusal no longer zeroes grounding.

These exercise the module-level helper directly, so — unlike the rest of
test_rag_engine.py — they run without the optional llama-index SDK (importing
``services.rag_engine`` is light; LlamaIndex is lazy-imported inside factories).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.rag_engine import _aembed_query_with_retry


@pytest.mark.unit
class TestEmbedRetry:
    @pytest.mark.asyncio
    async def test_succeeds_first_attempt_does_not_sleep(self):
        embed = AsyncMock()
        embed.aget_query_embedding = AsyncMock(return_value=[0.1] * 768)

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            vec = await _aembed_query_with_retry(
                embed, "query text", attempts=3, base_delay=0.01
            )

        assert vec == [0.1] * 768
        assert embed.aget_query_embedding.await_count == 1
        sleep_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_transient_failure_is_retried_then_succeeds(self):
        embed = AsyncMock()
        embed.aget_query_embedding = AsyncMock(
            side_effect=[Exception("Failed to connect to Ollama"), [0.2] * 768]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            vec = await _aembed_query_with_retry(
                embed, "query text", attempts=3, base_delay=0.01
            )

        assert vec == [0.2] * 768
        assert embed.aget_query_embedding.await_count == 2
        sleep_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exhausts_budget_then_reraises(self):
        embed = AsyncMock()
        embed.aget_query_embedding = AsyncMock(side_effect=Exception("ollama down"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(Exception, match="ollama down"):
                await _aembed_query_with_retry(
                    embed, "query text", attempts=3, base_delay=0.01
                )

        # Re-raises only after the full budget is spent.
        assert embed.aget_query_embedding.await_count == 3

    @pytest.mark.asyncio
    async def test_attempts_one_means_no_retry(self):
        embed = AsyncMock()
        embed.aget_query_embedding = AsyncMock(side_effect=Exception("boom"))

        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            with pytest.raises(Exception, match="boom"):
                await _aembed_query_with_retry(
                    embed, "query text", attempts=1, base_delay=0.01
                )

        assert embed.aget_query_embedding.await_count == 1
        sleep_mock.assert_not_awaited()
