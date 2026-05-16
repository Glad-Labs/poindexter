"""Lane B sweep — misc / leaf utilities migration tests (batch 2 sweep #4).

Pins the ``cost_tier`` resolution path (or the intent-based literal lift)
for the call sites migrated in batch 2 sweep #4:

- ``services.social_poster._resolve_social_model`` -> tier='standard'
- ``services.video_service._resolve_slideshow_prompt_model`` -> tier='standard'
- ``services.task_executor._auto_retry_failed_tasks`` -> intent-based,
  reads ``task_executor_first_retry_writer_model`` directly (NOT a tier
  migration target).
- ``services.ai_content_generator._resolve_rag_writer_model`` -> tier='standard'
- ``services.ragas_eval._resolve_judge_model`` -> tier='budget'

Per ``feedback_no_silent_defaults.md``, a missing tier mapping must
fail loudly (``notify_operator``) before falling back to the per-
call-site setting key. If both are missing, the call site bails (no
hardcoded literal default).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# social_poster._resolve_social_model
# ---------------------------------------------------------------------------


class TestSocialPosterResolveModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        from services.social_poster import _resolve_social_model

        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="")
        with patch(
            "services.social_poster.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b"),
        ), patch("services.social_poster.site_config", sc):
            model = await _resolve_social_model()
        assert model == "ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_falls_back_to_setting_when_tier_missing(self):
        from services.social_poster import _resolve_social_model

        notify = AsyncMock()
        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="ollama/llama3:latest")
        with patch(
            "services.social_poster.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.social_poster.notify_operator", notify), patch(
            "services.social_poster.site_config", sc,
        ):
            model = await _resolve_social_model()
        assert model == "ollama/llama3:latest"
        assert notify.await_count == 1

    @pytest.mark.asyncio
    async def test_raises_when_both_missing(self):
        from services.social_poster import _resolve_social_model

        notify = AsyncMock()
        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value=None)
        with patch(
            "services.social_poster.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.social_poster.notify_operator", notify), patch(
            "services.social_poster.site_config", sc,
        ):
            with pytest.raises(RuntimeError):
                await _resolve_social_model()
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_pool_none_skips_tier_lookup(self):
        from services.social_poster import _resolve_social_model

        notify = AsyncMock()
        sc = MagicMock()
        sc._pool = None
        sc.get = MagicMock(return_value="ollama/gemma3:27b-it-qat")
        with patch("services.social_poster.notify_operator", notify), patch(
            "services.social_poster.site_config", sc,
        ):
            model = await _resolve_social_model()
        assert model == "ollama/gemma3:27b-it-qat"
        assert notify.await_count == 1


# ---------------------------------------------------------------------------
# video_service._resolve_slideshow_prompt_model
# ---------------------------------------------------------------------------


class TestVideoServiceResolveModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        from services.video_service import _resolve_slideshow_prompt_model

        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="")
        with patch(
            "services.video_service.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b"),
        ), patch("services.video_service.site_config", sc):
            model = await _resolve_slideshow_prompt_model()
        assert model == "ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_falls_back_to_setting_when_tier_missing(self):
        from services.video_service import _resolve_slideshow_prompt_model

        notify = AsyncMock()
        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="ollama/llama3:latest")
        with patch(
            "services.video_service.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.video_service.notify_operator", notify), patch(
            "services.video_service.site_config", sc,
        ):
            model = await _resolve_slideshow_prompt_model()
        assert model == "ollama/llama3:latest"
        assert notify.await_count == 1

    @pytest.mark.asyncio
    async def test_raises_when_both_missing(self):
        from services.video_service import _resolve_slideshow_prompt_model

        notify = AsyncMock()
        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value=None)
        with patch(
            "services.video_service.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch("services.video_service.notify_operator", notify), patch(
            "services.video_service.site_config", sc,
        ):
            with pytest.raises(RuntimeError):
                await _resolve_slideshow_prompt_model()
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True


# ---------------------------------------------------------------------------
# task_executor first-retry writer — deleted with task_executor.py in
# Glad-Labs/poindexter#410 Stage 4 (2026-05-16). The ``task_retry_max_attempts``
# default of ``0`` has kept the auto-retry sweeper off in production
# since #370; operators retry via CLI/UI now. The
# ``task_executor_first_retry_writer_model`` app_settings row remains in
# the seed migrations for backwards compat but is no longer read by any
# production code path.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ai_content_generator._resolve_rag_writer_model
# ---------------------------------------------------------------------------


class TestAIContentGeneratorResolveRAGModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        from services.ai_content_generator import _resolve_rag_writer_model

        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="")
        with patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b"),
        ), patch("services.ai_content_generator.site_config", sc):
            model = await _resolve_rag_writer_model()
        # Bare model name (ollama/ stripped) per provider contract.
        assert model == "gemma3:27b"

    @pytest.mark.asyncio
    async def test_falls_back_to_pipeline_writer_model(self):
        from services.ai_content_generator import _resolve_rag_writer_model

        notify = AsyncMock()
        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="ollama/glm-4.7-5090:latest")
        with patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ), patch("services.ai_content_generator.site_config", sc):
            model = await _resolve_rag_writer_model()
        assert model == "glm-4.7-5090:latest"
        assert notify.await_count == 1

    @pytest.mark.asyncio
    async def test_raises_when_both_missing(self):
        from services.ai_content_generator import _resolve_rag_writer_model

        notify = AsyncMock()
        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="")
        with patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ), patch("services.ai_content_generator.site_config", sc):
            with pytest.raises(RuntimeError):
                await _resolve_rag_writer_model()
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True


# ---------------------------------------------------------------------------
# ragas_eval._resolve_judge_model — cost_tier='budget'
# ---------------------------------------------------------------------------


class TestRagasEvalResolveJudgeModel:
    @pytest.mark.asyncio
    async def test_returns_tier_model_on_success(self):
        from services.ragas_eval import _resolve_judge_model

        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="")
        with patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            AsyncMock(return_value="ollama/gemma3:27b-it-qat"),
        ):
            model = await _resolve_judge_model(sc)
        # ragas_eval keeps the ollama/ prefix; the langchain ChatOllama
        # wrapper handles that internally — only the bare-model
        # call sites strip it.
        assert model == "ollama/gemma3:27b-it-qat"

    @pytest.mark.asyncio
    async def test_falls_back_to_ragas_judge_model(self):
        from services.ragas_eval import _resolve_judge_model

        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="llama3:8b")
        notify = AsyncMock()
        with patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ):
            model = await _resolve_judge_model(sc)
        assert model == "llama3:8b"
        assert notify.await_count == 1

    @pytest.mark.asyncio
    async def test_raises_when_both_missing(self):
        from services.ragas_eval import _resolve_judge_model

        sc = MagicMock()
        sc._pool = MagicMock()
        sc.get = MagicMock(return_value="")
        notify = AsyncMock()
        with patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            AsyncMock(side_effect=RuntimeError("no model configured")),
        ), patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ):
            with pytest.raises(RuntimeError):
                await _resolve_judge_model(sc)
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_no_site_config_uses_legacy_path(self):
        """site_config=None -> tier skipped, no fallback available, raises."""
        from services.ragas_eval import _resolve_judge_model

        notify = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ):
            with pytest.raises(RuntimeError):
                await _resolve_judge_model(None)
        assert notify.await_count == 1
