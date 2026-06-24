"""Model-pin resolution tests — QA critic + self-review surfaces.

Pins the per-step ``*_model`` resolution contract for the QA / critic call
sites. The ``cost_tier.<tier>.model`` indirection these used to resolve
through was removed; each resolver now reads its dedicated pin and fails
loud (``notify_operator``) when unset, per ``feedback_no_silent_defaults.md``:

- ``modules.content.multi_model_qa.MultiModelQA._resolve_critic_model`` —
  ``pipeline_critic_model`` → per-call-site ``setting_key`` → raise.
- ``services.self_review._resolve_self_review_model`` —
  ``writer_self_review_model`` → raise.
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
    async def test_returns_pipeline_critic_model_pin(self):
        settings = AsyncMock()
        settings.get = AsyncMock(side_effect=lambda k: {
            "pipeline_critic_model": "ollama/glm-4.7-5090:latest",
        }.get(k))
        qa = MultiModelQA(pool=MagicMock(), settings_service=settings, site_config=SiteConfig())
        model = await qa._resolve_critic_model(
            setting_key="qa_fallback_critic_model", site="critic",
        )
        assert model == "ollama/glm-4.7-5090:latest"

    @pytest.mark.asyncio
    async def test_falls_back_to_setting_key(self):
        # pipeline_critic_model unset → per-call-site setting_key is used.
        settings = AsyncMock()
        settings.get = AsyncMock(side_effect=lambda k: {
            "qa_fallback_critic_model": "ollama/gemma3:27b-it-qat",
        }.get(k))
        qa = MultiModelQA(pool=MagicMock(), settings_service=settings, site_config=SiteConfig())
        model = await qa._resolve_critic_model(
            setting_key="qa_fallback_critic_model", site="critic",
        )
        assert model == "ollama/gemma3:27b-it-qat"

    @pytest.mark.asyncio
    async def test_raises_when_pin_and_setting_both_missing(self):
        settings = AsyncMock()
        settings.get = AsyncMock(return_value=None)
        qa = MultiModelQA(pool=MagicMock(), settings_service=settings, site_config=SiteConfig())
        notify = AsyncMock()
        with patch("modules.content.multi_model_qa.notify_operator", notify):
            with pytest.raises(RuntimeError):
                await qa._resolve_critic_model(
                    setting_key="qa_fallback_critic_model", site="critic",
                )
        # Critical operator notification fired before raising.
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_raises_when_no_settings_service(self):
        qa = MultiModelQA(pool=MagicMock(), settings_service=None, site_config=SiteConfig())
        notify = AsyncMock()
        with patch("modules.content.multi_model_qa.notify_operator", notify):
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
    async def test_returns_pin(self):
        from services.self_review import _resolve_self_review_model

        sc = MagicMock()
        sc.get = MagicMock(return_value="ollama/gemma3:27b")
        model = await _resolve_self_review_model(MagicMock(), site_config=sc)
        assert model == "ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_pool_none_still_reads_pin(self):
        # ``pool`` is unused — resolution reads writer_self_review_model
        # regardless of whether a pool is supplied.
        from services.self_review import _resolve_self_review_model

        sc = MagicMock()
        sc.get = MagicMock(return_value="gemma3:27b")
        model = await _resolve_self_review_model(None, site_config=sc)
        assert model == "gemma3:27b"

    @pytest.mark.asyncio
    async def test_raises_and_notifies_when_pin_unset(self):
        from services.self_review import _resolve_self_review_model

        notify = AsyncMock()
        sc = MagicMock()
        sc.get = MagicMock(return_value=None)
        with patch("services.self_review.notify_operator", notify):
            with pytest.raises(RuntimeError, match="no model resolvable"):
                await _resolve_self_review_model(MagicMock(), site_config=sc)
        assert notify.await_count == 1
