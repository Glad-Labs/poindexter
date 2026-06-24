"""Model-pin resolution tests — misc / leaf utilities.

Pins the per-step ``*_model`` resolution contract for the leaf call sites.
The ``cost_tier`` indirection was removed; each resolver reads its dedicated
pin and fails loud (``notify_operator(critical=True)`` + raise) when unset,
per ``feedback_no_silent_defaults.md``:

- ``services.social_poster._resolve_social_model`` → ``social_poster_fallback_model``
- ``modules.content.ai_content_generator._resolve_rag_writer_model`` → ``pipeline_writer_model``
- ``services.ragas_eval._resolve_judge_model`` → ``ragas_judge_model``

(``video_service._resolve_slideshow_prompt_model`` is covered by
``test_video_service.py::TestResolveSlideshowPromptModel``.)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# social_poster._resolve_social_model
# ---------------------------------------------------------------------------


class TestSocialPosterResolveModel:
    @pytest.mark.asyncio
    async def test_returns_pin(self):
        from services.social_poster import _resolve_social_model

        sc = MagicMock()
        sc.get = MagicMock(return_value="ollama/llama3:latest")
        model = await _resolve_social_model(site_config=sc)
        assert model == "ollama/llama3:latest"

    @pytest.mark.asyncio
    async def test_raises_when_pin_unset(self):
        from services.social_poster import _resolve_social_model

        notify = AsyncMock()
        sc = MagicMock()
        sc.get = MagicMock(return_value=None)
        with patch("services.social_poster.notify_operator", notify):
            with pytest.raises(RuntimeError):
                await _resolve_social_model(site_config=sc)
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True


# ---------------------------------------------------------------------------
# ai_content_generator._resolve_rag_writer_model
# ---------------------------------------------------------------------------


class TestAIContentGeneratorResolveRAGModel:
    @pytest.mark.asyncio
    async def test_returns_pipeline_writer_model_stripped(self):
        from modules.content.ai_content_generator import _resolve_rag_writer_model

        sc = MagicMock()
        sc.get = MagicMock(return_value="ollama/glm-4.7-5090:latest")
        model = await _resolve_rag_writer_model(site_config=sc)
        # Bare model name (ollama/ stripped) per provider contract.
        assert model == "glm-4.7-5090:latest"

    @pytest.mark.asyncio
    async def test_raises_when_pin_unset(self):
        from modules.content.ai_content_generator import _resolve_rag_writer_model

        notify = AsyncMock()
        sc = MagicMock()
        sc.get = MagicMock(return_value="")
        with patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ):
            with pytest.raises(RuntimeError):
                await _resolve_rag_writer_model(site_config=sc)
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True


# ---------------------------------------------------------------------------
# ragas_eval._resolve_judge_model
# ---------------------------------------------------------------------------


class TestRagasEvalResolveJudgeModel:
    @pytest.mark.asyncio
    async def test_returns_pin_keeps_prefix(self):
        from services.ragas_eval import _resolve_judge_model

        sc = MagicMock()
        sc.get = MagicMock(return_value="ollama/gemma3:27b-it-qat")
        model = await _resolve_judge_model(sc)
        # ragas keeps the ollama/ prefix — the langchain ChatOllama wrapper
        # handles it internally (only bare-model call sites strip it).
        assert model == "ollama/gemma3:27b-it-qat"

    @pytest.mark.asyncio
    async def test_raises_when_pin_unset(self):
        from services.ragas_eval import _resolve_judge_model

        sc = MagicMock()
        sc.get = MagicMock(return_value="")
        notify = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ):
            with pytest.raises(RuntimeError):
                await _resolve_judge_model(sc)
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_no_site_config_raises(self):
        from services.ragas_eval import _resolve_judge_model

        notify = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ):
            with pytest.raises(RuntimeError):
                await _resolve_judge_model(None)
        assert notify.await_count == 1
