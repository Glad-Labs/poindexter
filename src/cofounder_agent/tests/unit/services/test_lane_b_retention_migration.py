"""Model-pin resolution tests — retention / housekeeping summary surfaces.

Pins the ``cost_tier="budget"`` resolution path for:

- ``services.integrations.handlers.retention_summarize_to_table._resolve_summary_model``
  (consumed by ``summarize_to_table`` for per-day LLM summaries)

Note: ``services.jobs.collapse_old_embeddings._resolve_summary_model`` was
also tested here, but the job was retired 2026-06-24 (folded into the
``embeddings_collapse`` retention handler). The collapse handler resolves
the budget model inline without a separate helper.

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
