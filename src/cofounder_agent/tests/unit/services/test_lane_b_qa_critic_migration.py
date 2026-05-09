"""Lane B sweep — QA / critic surface migration tests.

Pins the ``cost_tier="standard"`` resolution path for the four call
sites migrated in the QA / critic batch:

- ``services.multi_model_qa.MultiModelQA._resolve_critic_model``
  (used by ``_review_with_ollama`` + ``_run_gate_prompt`` +
  ``_review_with_cloud_model`` fallback)
- ``services.self_review._resolve_self_review_model`` +
  ``self_review_and_revise``
- ``services.stages.cross_model_qa._resolve_writer_model``

Per ``feedback_no_silent_defaults.md``, a missing tier mapping must
fail loudly (``notify_operator``) before falling back to the per-
call-site setting key. If both are missing, the call site bails.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.multi_model_qa import MultiModelQA


# ---------------------------------------------------------------------------
# multi_model_qa._resolve_critic_model
# ---------------------------------------------------------------------------


class TestMultiModelQAResolveCriticModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        qa = MultiModelQA(pool=MagicMock(), settings_service=AsyncMock())
        with patch(
            "services.multi_model_qa.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b"),
        ):
            model = await qa._resolve_critic_model(
                setting_key="qa_fallback_critic_model", site="critic",
            )
        assert model == "ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_falls_back_to_setting_when_tier_missing(self):
        settings = AsyncMock()
        settings.get = AsyncMock(return_value="ollama/gemma3:27b-it-qat")
        qa = MultiModelQA(pool=MagicMock(), settings_service=settings)
        notify = AsyncMock()
        with patch(
            "services.multi_model_qa.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.multi_model_qa.notify_operator", notify):
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
        qa = MultiModelQA(pool=MagicMock(), settings_service=settings)
        notify = AsyncMock()
        with patch(
            "services.multi_model_qa.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.multi_model_qa.notify_operator", notify):
            with pytest.raises(RuntimeError, match="no model configured"):
                await qa._resolve_critic_model(
                    setting_key="qa_fallback_critic_model", site="critic",
                )
        # Critical operator notification fired before raising.
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_raises_when_no_settings_service_and_tier_missing(self):
        qa = MultiModelQA(pool=MagicMock(), settings_service=None)
        notify = AsyncMock()
        with patch(
            "services.multi_model_qa.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.multi_model_qa.notify_operator", notify):
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
            model = await _resolve_self_review_model(MagicMock())
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
        ), patch("services.self_review.notify_operator", notify), patch(
            "services.self_review.site_config", sc,
        ):
            model = await _resolve_self_review_model(MagicMock())
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
        ), patch("services.self_review.notify_operator", notify), patch(
            "services.self_review.site_config", sc,
        ):
            with pytest.raises(RuntimeError):
                await _resolve_self_review_model(MagicMock())
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
        with patch("services.self_review.notify_operator", notify), patch(
            "services.self_review.site_config", sc,
        ):
            model = await _resolve_self_review_model(None)
        assert model == "gemma3:27b"
        assert notify.await_count == 1


# ---------------------------------------------------------------------------
# stages.cross_model_qa._resolve_writer_model
# ---------------------------------------------------------------------------


class TestCrossModelQAResolveWriterModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        from services.stages.cross_model_qa import _resolve_writer_model

        with patch(
            "services.stages.cross_model_qa.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b"),
        ):
            model = await _resolve_writer_model(
                pool=MagicMock(),
                settings_service=AsyncMock(),
                setting_key="pipeline_writer_model",
                site="primary",
            )
        assert model == "ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_falls_back_to_setting_when_tier_missing(self):
        from services.stages.cross_model_qa import _resolve_writer_model

        settings = AsyncMock()
        settings.get = AsyncMock(return_value="ollama/gemma3:27b")
        notify = AsyncMock()
        with patch(
            "services.stages.cross_model_qa.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.stages.cross_model_qa.notify_operator", notify):
            model = await _resolve_writer_model(
                pool=MagicMock(),
                settings_service=settings,
                setting_key="pipeline_writer_model",
                site="primary",
            )
        assert model == "ollama/gemma3:27b"
        assert notify.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_both_missing(self):
        from services.stages.cross_model_qa import _resolve_writer_model

        settings = AsyncMock()
        settings.get = AsyncMock(return_value=None)
        notify = AsyncMock()
        with patch(
            "services.stages.cross_model_qa.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.stages.cross_model_qa.notify_operator", notify):
            result = await _resolve_writer_model(
                pool=MagicMock(),
                settings_service=settings,
                setting_key="qa_fallback_writer_model",
                site="fallback",
            )
        # Per feedback_no_silent_defaults.md the call site does not silently
        # land on a hardcoded literal. The caller is responsible for
        # turning None into a graceful skip.
        assert result is None
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_pool_none_skips_tier_and_uses_setting(self):
        from services.stages.cross_model_qa import _resolve_writer_model

        settings = AsyncMock()
        settings.get = AsyncMock(return_value="ollama/gemma3:27b")
        notify = AsyncMock()
        with patch("services.stages.cross_model_qa.notify_operator", notify):
            model = await _resolve_writer_model(
                pool=None,
                settings_service=settings,
                setting_key="pipeline_writer_model",
                site="primary",
            )
        assert model == "ollama/gemma3:27b"
        assert notify.await_count == 1
