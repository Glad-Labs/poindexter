"""Lane B sweep — QA / critic surface migration tests.

Pins the ``cost_tier="standard"`` resolution path for the four call
sites migrated in the QA / critic batch:

- ``modules.content.multi_model_qa.MultiModelQA._resolve_critic_model``
  (used by ``_review_with_ollama`` + ``_run_gate_prompt`` +
  ``_review_with_cloud_model`` fallback)
- ``services.self_review._resolve_self_review_model`` +
  ``self_review_and_revise``
- ``modules.content.stages.cross_model_qa._resolve_writer_model``

Per ``feedback_no_silent_defaults.md``, a missing tier mapping must
fail loudly (``notify_operator``) before falling back to the per-
call-site setting key. If both are missing, the call site bails.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.multi_model_qa import MultiModelQA
from services.site_config import SiteConfig

# ---------------------------------------------------------------------------
# multi_model_qa._resolve_critic_model
# ---------------------------------------------------------------------------


class TestMultiModelQAResolveCriticModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        # No dedicated pipeline_critic_model → resolution falls through to
        # the standard cost-tier (the Lane B path this test pins). The
        # step-0 dedicated-model precedence is covered by
        # test_multi_model_qa.py::TestCriticModelDistinctFromWriter.
        settings = AsyncMock()
        settings.get = AsyncMock(return_value=None)
        qa = MultiModelQA(pool=MagicMock(), settings_service=settings, site_config=SiteConfig())
        with patch(
            "modules.content.multi_model_qa.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b"),
        ):
            model = await qa._resolve_critic_model(
                setting_key="qa_fallback_critic_model", site="critic",
            )
        assert model == "ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_falls_back_to_setting_when_tier_missing(self):
        settings = AsyncMock()

        async def _get(key):
            # No dedicated pipeline_critic_model → fall through to the tier
            # path; qa_fallback_critic_model is the per-call-site backstop.
            return {"qa_fallback_critic_model": "ollama/gemma3:27b-it-qat"}.get(key)

        settings.get = AsyncMock(side_effect=_get)
        qa = MultiModelQA(pool=MagicMock(), settings_service=settings, site_config=SiteConfig())
        notify = AsyncMock()
        with patch(
            "modules.content.multi_model_qa.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("modules.content.multi_model_qa.notify_operator", notify):
            model = await qa._resolve_critic_model(
                setting_key="qa_fallback_critic_model", site="critic",
            )
        assert model == "ollama/gemma3:27b-it-qat"
        # Operator must be notified about the tier miss before the
        # fallback fires (no silent defaults).
        assert notify.await_count == 1

    @pytest.mark.asyncio
    async def test_raises_when_tier_and_setting_both_missing(self):
        settings = AsyncMock()
        settings.get = AsyncMock(return_value=None)
        qa = MultiModelQA(pool=MagicMock(), settings_service=settings, site_config=SiteConfig())
        notify = AsyncMock()
        with patch(
            "modules.content.multi_model_qa.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("modules.content.multi_model_qa.notify_operator", notify):
            with pytest.raises(RuntimeError, match="no model configured"):
                await qa._resolve_critic_model(
                    setting_key="qa_fallback_critic_model", site="critic",
                )
        # Critical operator notification fired before raising.
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_raises_when_no_settings_service_and_tier_missing(self):
        qa = MultiModelQA(pool=MagicMock(), settings_service=None, site_config=SiteConfig())
        notify = AsyncMock()
        with patch(
            "modules.content.multi_model_qa.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("modules.content.multi_model_qa.notify_operator", notify):
            with pytest.raises(RuntimeError):
                await qa._resolve_critic_model(
                    setting_key="qa_fallback_critic_model", site="critic",
                )
        assert notify.await_count == 1


# ---------------------------------------------------------------------------
# self_review._resolve_self_review_model
# ---------------------------------------------------------------------------


class TestSelfReviewResolveModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        from services.self_review import _resolve_self_review_model

        with patch(
            "services.self_review.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b"),
        ):
            model = await _resolve_self_review_model(
                MagicMock(), site_config=MagicMock(),
            )
        assert model == "ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_falls_back_to_site_config_when_tier_missing(self):
        from services.self_review import _resolve_self_review_model

        notify = AsyncMock()
        sc = MagicMock()
        sc.get = MagicMock(return_value="gemma3:27b-it-qat")

        with patch(
            "services.self_review.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.self_review.notify_operator", notify):
            model = await _resolve_self_review_model(MagicMock(), site_config=sc)
        assert model == "gemma3:27b-it-qat"
        assert notify.await_count == 1

    @pytest.mark.asyncio
    async def test_raises_when_both_missing(self):
        from services.self_review import _resolve_self_review_model

        notify = AsyncMock()
        sc = MagicMock()
        sc.get = MagicMock(return_value=None)
        with patch(
            "services.self_review.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.self_review.notify_operator", notify):
            with pytest.raises(RuntimeError):
                await _resolve_self_review_model(MagicMock(), site_config=sc)
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_pool_none_skips_tier_lookup(self):
        """When no pool is supplied, the tier lookup is skipped and the
        per-call-site setting is used directly (with notify_operator)."""
        from services.self_review import _resolve_self_review_model

        notify = AsyncMock()
        sc = MagicMock()
        sc.get = MagicMock(return_value="gemma3:27b")
        with patch("services.self_review.notify_operator", notify):
            model = await _resolve_self_review_model(None, site_config=sc)
        assert model == "gemma3:27b"
        assert notify.await_count == 1


# Note: stages.cross_model_qa._resolve_writer_model tests removed 2026-06-01
# (atom-cutover Plan 5, #355) — cross_model_qa.py deleted, superseded by
# the qa.* atom graph_def path. The _resolve_writer_model helper is gone.
