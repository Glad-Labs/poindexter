"""Lane B sweep — retention / housekeeping surface migration tests.

Pins the ``cost_tier="budget"`` resolution path for the two call sites
migrated in batch 2 sweep #3:

- ``services.jobs.collapse_old_embeddings._resolve_summary_model``
  (consumed by ``CollapseOldEmbeddingsJob.run`` when
  ``embedding_collapse_summary_provider == "ollama"``)
- ``services.integrations.handlers.retention_summarize_to_table._resolve_summary_model``
  (consumed by ``summarize_to_table`` for per-day LLM summaries)

Per ``feedback_no_silent_defaults.md``, a missing tier mapping must
fail loudly (``notify_operator``) before falling back to the per-call-
site setting key. If both are missing, the helper raises and the
caller downgrades the run to ``joined_preview`` rather than landing on
a hardcoded literal.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# collapse_old_embeddings._resolve_summary_model
# ---------------------------------------------------------------------------


class TestCollapseResolveSummaryModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        from services.jobs.collapse_old_embeddings import _resolve_summary_model

        with patch(
            "services.jobs.collapse_old_embeddings.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b-it-qat"),
        ):
            model = await _resolve_summary_model(MagicMock())
        # ``ollama/`` prefix is stripped — OllamaClient consumes the bare name.
        assert model == "gemma3:27b-it-qat"

    @pytest.mark.asyncio
    async def test_falls_back_to_setting_when_tier_missing(self):
        from services.jobs import collapse_old_embeddings as mod

        notify = AsyncMock()
        # Stub _get_setting to return the per-call-site fallback key.
        with patch(
            "services.jobs.collapse_old_embeddings.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch(
            "services.jobs.collapse_old_embeddings._get_setting",
            AsyncMock(return_value="ollama/legacy-fallback:7b"),
        ), patch(
            "services.jobs.collapse_old_embeddings.notify_operator", notify,
        ):
            model = await mod._resolve_summary_model(MagicMock())

        assert model == "legacy-fallback:7b"
        # Operator must be notified about the tier miss before the
        # fallback fires (no silent defaults).
        assert notify.await_count == 1
        # Non-critical because the fallback succeeded.
        assert notify.await_args.kwargs.get("critical") is False

    @pytest.mark.asyncio
    async def test_raises_when_tier_and_setting_both_missing(self):
        from services.jobs import collapse_old_embeddings as mod

        notify = AsyncMock()
        with patch(
            "services.jobs.collapse_old_embeddings.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch(
            "services.jobs.collapse_old_embeddings._get_setting",
            AsyncMock(return_value=""),
        ), patch(
            "services.jobs.collapse_old_embeddings.notify_operator", notify,
        ):
            with pytest.raises(RuntimeError, match="no summary model resolvable"):
                await mod._resolve_summary_model(MagicMock())

        # Critical operator notification fired before raising.
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True


# ---------------------------------------------------------------------------
# retention_summarize_to_table._resolve_summary_model
# ---------------------------------------------------------------------------


class TestRetentionResolveSummaryModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        from services.integrations.handlers.retention_summarize_to_table import (
            _resolve_summary_model,
        )

        with patch(
            "services.integrations.handlers.retention_summarize_to_table.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b-it-qat"),
        ):
            model = await _resolve_summary_model(MagicMock())
        assert model == "gemma3:27b-it-qat"

    @pytest.mark.asyncio
    async def test_falls_back_to_setting_when_tier_missing(self):
        from services.integrations.handlers import retention_summarize_to_table as mod

        notify = AsyncMock()
        with patch(
            "services.integrations.handlers.retention_summarize_to_table.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch(
            "services.integrations.handlers.retention_summarize_to_table._get_setting",
            AsyncMock(return_value="ollama/legacy-fallback:7b"),
        ), patch(
            "services.integrations.handlers.retention_summarize_to_table.notify_operator",
            notify,
        ):
            model = await mod._resolve_summary_model(MagicMock())

        assert model == "legacy-fallback:7b"
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is False

    @pytest.mark.asyncio
    async def test_raises_when_tier_and_setting_both_missing(self):
        from services.integrations.handlers import retention_summarize_to_table as mod

        notify = AsyncMock()
        with patch(
            "services.integrations.handlers.retention_summarize_to_table.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch(
            "services.integrations.handlers.retention_summarize_to_table._get_setting",
            AsyncMock(return_value=""),
        ), patch(
            "services.integrations.handlers.retention_summarize_to_table.notify_operator",
            notify,
        ):
            with pytest.raises(RuntimeError, match="no summary model resolvable"):
                await mod._resolve_summary_model(MagicMock())

        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_value_error_on_bad_tier_also_falls_back(self):
        """``resolve_tier_model`` raises ValueError on unknown tiers; the
        helper must catch that path the same way it catches RuntimeError."""
        from services.integrations.handlers import retention_summarize_to_table as mod

        notify = AsyncMock()
        with patch(
            "services.integrations.handlers.retention_summarize_to_table.resolve_tier_model",
            AsyncMock(side_effect=ValueError("unknown tier 'budget'")),
        ), patch(
            "services.integrations.handlers.retention_summarize_to_table._get_setting",
            AsyncMock(return_value="fallback-model:7b"),
        ), patch(
            "services.integrations.handlers.retention_summarize_to_table.notify_operator",
            notify,
        ):
            model = await mod._resolve_summary_model(MagicMock())

        assert model == "fallback-model:7b"
        assert notify.await_count == 1
