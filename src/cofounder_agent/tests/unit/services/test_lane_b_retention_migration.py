"""Model-pin resolution tests — retention / housekeeping summary surfaces.

Pins the per-step ``*_model`` resolution contract for the cold-data summary
call sites. The ``cost_tier.budget`` indirection was removed; each resolver
now reads its dedicated pin directly via ``_get_setting`` and fails loud
(``notify_operator(critical=True)`` + raise) when unset, per
``feedback_no_silent_defaults.md``. The caller then downgrades the run to
``joined_preview`` rather than landing on a hardcoded literal.

- ``services.jobs.collapse_old_embeddings._resolve_summary_model`` →
  ``embedding_collapse_summary_model``
- ``services.integrations.handlers.retention_summarize_to_table._resolve_summary_model`` →
  ``memory_compression_summary_model``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# collapse_old_embeddings._resolve_summary_model
# ---------------------------------------------------------------------------


class TestCollapseResolveSummaryModel:
    @pytest.mark.asyncio
    async def test_returns_pin_strips_prefix(self):
        from services.jobs import collapse_old_embeddings as mod

        with patch(
            "services.jobs.collapse_old_embeddings._get_setting",
            AsyncMock(return_value="ollama/gemma3:27b-it-qat"),
        ):
            model = await mod._resolve_summary_model(MagicMock())
        # ``ollama/`` prefix is stripped — OllamaClient consumes the bare name.
        assert model == "gemma3:27b-it-qat"

    @pytest.mark.asyncio
    async def test_raises_and_notifies_when_pin_unset(self):
        from services.jobs import collapse_old_embeddings as mod

        notify = AsyncMock()
        with patch(
            "services.jobs.collapse_old_embeddings._get_setting",
            AsyncMock(return_value=""),
        ), patch(
            "services.jobs.collapse_old_embeddings.notify_operator", notify,
        ):
            with pytest.raises(RuntimeError, match="no summary model"):
                await mod._resolve_summary_model(MagicMock())
        # Critical operator notification fired before raising.
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True


# ---------------------------------------------------------------------------
# retention_summarize_to_table._resolve_summary_model
# ---------------------------------------------------------------------------


class TestRetentionResolveSummaryModel:
    @pytest.mark.asyncio
    async def test_returns_pin_strips_prefix(self):
        from services.integrations.handlers import retention_summarize_to_table as mod

        with patch(
            "services.integrations.handlers.retention_summarize_to_table._get_setting",
            AsyncMock(return_value="ollama/gemma3:27b-it-qat"),
        ):
            model = await mod._resolve_summary_model(MagicMock())
        assert model == "gemma3:27b-it-qat"

    @pytest.mark.asyncio
    async def test_raises_and_notifies_when_pin_unset(self):
        from services.integrations.handlers import retention_summarize_to_table as mod

        notify = AsyncMock()
        with patch(
            "services.integrations.handlers.retention_summarize_to_table._get_setting",
            AsyncMock(return_value=""),
        ), patch(
            "services.integrations.handlers.retention_summarize_to_table.notify_operator",
            notify,
        ):
            with pytest.raises(RuntimeError, match="no summary model"):
                await mod._resolve_summary_model(MagicMock())
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True
