"""
Integration tests for the content generation pipeline.

Tests ContentService.execute_full_workflow() orchestration logic:
Research -> Draft -> QA Assess -> Refine (loop) -> Image Selection -> Finalize

LLM calls are mocked; actual pipeline orchestration is exercised.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.content_service import ContentService


def _make_service(**overrides):
    defaults = {"database_service": None, "model_router": None, "writing_style_service": None}
    defaults.update(overrides)
    return ContentService(**defaults)


@pytest.mark.integration
class TestContentPipelineFullWorkflow:

    @pytest.mark.asyncio
    async def test_full_workflow_completes_all_phases(self):
        service = _make_service()
        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock,
                  return_value={"phase": "research", "topic": "AI", "research_text": "Background", "source": "research_agent"}) as mock_research,
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock,
                  return_value={"phase": "draft", "draft_content": "# AI" * 20, "model_used": "test", "source": "creative_agent"}) as mock_draft,
            patch("services.content_service.ContentService.execute_assess", new_callable=AsyncMock,
                  return_value={"phase": "assess", "quality_score": 0.9, "passed_threshold": True, "assessment": "Good"}) as mock_assess,
            patch("services.content_service.ContentService.execute_image_selection", new_callable=AsyncMock,
                  return_value={"phase": "image_selection", "images": [{"url": "https://example.com/img.jpg"}]}) as mock_image,
            patch("services.content_service.ContentService.execute_finalize", new_callable=AsyncMock,
                  return_value={"phase": "finalize", "formatted_content": "Final", "meta_description": "AI"}) as mock_finalize,
        ):
            result = await service.execute_full_workflow(topic="AI", user_id="user-123", quality_threshold=0.75, word_count_target=1500)

        assert result["status"] == "completed"
        assert result["topic"] == "AI"
        assert result["quality_score"] == 0.9
        assert result["refinement_count"] == 0
        assert "final_content" in result
        mock_research.assert_awaited_once()
        mock_draft.assert_awaited_once()
        mock_assess.assert_awaited_once()
        mock_image.assert_awaited_once()
        mock_finalize.assert_awaited_once()
        pr = result["phase_results"]
        assert all(k in pr for k in ["research", "draft", "assess", "image_selection", "finalize"])

    @pytest.mark.asyncio
    async def test_draft_receives_research_context(self):
        service = _make_service()
        research_out = {"phase": "research", "topic": "AI", "research_text": "Deep research", "source": "research_agent"}
        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, return_value=research_out),
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock,
                  return_value={"phase": "draft", "draft_content": "Draft", "source": "creative_agent"}) as mock_draft,
            patch("services.content_service.ContentService.execute_assess", new_callable=AsyncMock,
                  return_value={"quality_score": 0.9, "passed_threshold": True}),
            patch("services.content_service.ContentService.execute_image_selection", new_callable=AsyncMock, return_value={"images": []}),
            patch("services.content_service.ContentService.execute_finalize", new_callable=AsyncMock, return_value={"formatted_content": "Final"}),
        ):
            await service.execute_full_workflow(topic="AI")
        assert mock_draft.call_args.kwargs["research_context"] == research_out

    @pytest.mark.asyncio
    async def test_model_selections_forwarded_to_phases(self):
        service = _make_service()
        ms = {"research": "gemini-pro", "draft": "claude-3-sonnet", "assess": "gpt-4", "image_selection": "gemini", "finalize": "claude-3-haiku"}
        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, return_value={"phase": "research", "research_text": "t"}) as mr,
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock, return_value={"phase": "draft", "draft_content": "c"}) as md,
            patch("services.content_service.ContentService.execute_assess", new_callable=AsyncMock, return_value={"quality_score": 0.9, "passed_threshold": True}) as ma,
            patch("services.content_service.ContentService.execute_image_selection", new_callable=AsyncMock, return_value={"images": []}) as mi,
            patch("services.content_service.ContentService.execute_finalize", new_callable=AsyncMock, return_value={"formatted_content": "d"}) as mf,
        ):
            await service.execute_full_workflow(topic="AI", model_selections=ms)
        assert mr.call_args.kwargs["model"] == "gemini-pro"
        assert md.call_args.kwargs["model"] == "claude-3-sonnet"
        assert ma.call_args.kwargs["model"] == "gpt-4"
        assert mi.call_args.kwargs["model"] == "gemini"
        assert mf.call_args.kwargs["model"] == "claude-3-haiku"


@pytest.mark.integration
class TestContentPipelineRefinementLoop:

    @pytest.mark.asyncio
    async def test_refinement_triggered_when_below_threshold(self):
        service = _make_service()
        assess_call_count = 0

        async def mock_assess(**kwargs):
            nonlocal assess_call_count
            assess_call_count += 1
            if assess_call_count == 1:
                return {"quality_score": 0.5, "passed_threshold": False, "assessment": "Needs work"}
            return {"quality_score": 0.9, "passed_threshold": True, "assessment": "Good now"}

        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, return_value={"phase": "research", "research_text": "t"}),
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock, return_value={"phase": "draft", "draft_content": "initial"}),
            patch("services.content_service.ContentService.execute_assess", side_effect=mock_assess),
            patch("services.content_service.ContentService.execute_refine", new_callable=AsyncMock,
                  return_value={"phase": "refine", "refined_content": "improved", "feedback_addressed": "Needs work"}) as mock_refine,
            patch("services.content_service.ContentService.execute_image_selection", new_callable=AsyncMock, return_value={"images": []}),
            patch("services.content_service.ContentService.execute_finalize", new_callable=AsyncMock, return_value={"formatted_content": "done"}),
        ):
            result = await service.execute_full_workflow(topic="AI", quality_threshold=0.75)
        assert result["status"] == "completed"
        assert result["refinement_count"] == 1
        mock_refine.assert_awaited_once()
        assert result["final_content"] == "improved"

    @pytest.mark.asyncio
    async def test_max_refinements_caps_loop(self):
        service = _make_service()

        async def always_fail(**kwargs):
            return {"quality_score": 0.3, "passed_threshold": False, "assessment": "Bad"}

        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, return_value={"phase": "research", "research_text": "t"}),
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock, return_value={"phase": "draft", "draft_content": "weak"}),
            patch("services.content_service.ContentService.execute_assess", side_effect=always_fail),
            patch("services.content_service.ContentService.execute_refine", new_callable=AsyncMock,
                  return_value={"phase": "refine", "refined_content": "still weak"}) as mock_refine,
            patch("services.content_service.ContentService.execute_image_selection", new_callable=AsyncMock, return_value={"images": []}),
            patch("services.content_service.ContentService.execute_finalize", new_callable=AsyncMock, return_value={"formatted_content": "done"}),
        ):
            result = await service.execute_full_workflow(topic="AI", quality_threshold=0.75, max_refinements=2)
        assert result["status"] == "completed"
        assert result["refinement_count"] == 2
        assert mock_refine.await_count == 2

    @pytest.mark.asyncio
    async def test_no_refinement_when_quality_passes(self):
        service = _make_service()
        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, return_value={"phase": "research", "research_text": "t"}),
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock, return_value={"phase": "draft", "draft_content": "great"}),
            patch("services.content_service.ContentService.execute_assess", new_callable=AsyncMock, return_value={"quality_score": 0.95, "passed_threshold": True}),
            patch("services.content_service.ContentService.execute_refine", new_callable=AsyncMock) as mock_refine,
            patch("services.content_service.ContentService.execute_image_selection", new_callable=AsyncMock, return_value={"images": []}),
            patch("services.content_service.ContentService.execute_finalize", new_callable=AsyncMock, return_value={"formatted_content": "done"}),
        ):
            result = await service.execute_full_workflow(topic="AI", quality_threshold=0.75)
        assert result["refinement_count"] == 0
        mock_refine.assert_not_awaited()


@pytest.mark.integration
class TestContentPipelineErrorHandling:

    @pytest.mark.asyncio
    async def test_research_failure_returns_failed_status(self):
        service = _make_service()
        with patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, side_effect=RuntimeError("LLM down")):
            result = await service.execute_full_workflow(topic="AI")
        assert result["status"] == "failed"
        assert "LLM down" in result["error"]
        assert result["phase_results"] == {}

    @pytest.mark.asyncio
    async def test_draft_failure_preserves_research_result(self):
        service = _make_service()
        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, return_value={"phase": "research", "research_text": "data"}),
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock, side_effect=RuntimeError("Draft failed")),
        ):
            result = await service.execute_full_workflow(topic="AI")
        assert result["status"] == "failed"
        assert "Draft failed" in result["error"]
        assert "research" in result["phase_results"]
        assert "draft" not in result["phase_results"]

    @pytest.mark.asyncio
    async def test_image_failure_is_non_critical(self):
        service = _make_service()
        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, return_value={"phase": "research", "research_text": "t"}),
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock, return_value={"phase": "draft", "draft_content": "c"}),
            patch("services.content_service.ContentService.execute_assess", new_callable=AsyncMock, return_value={"quality_score": 0.9, "passed_threshold": True}),
            patch("services.content_service.ContentService.execute_image_selection", new_callable=AsyncMock,
                  return_value={"phase": "image_selection", "error": "Down", "images": []}),
            patch("services.content_service.ContentService.execute_finalize", new_callable=AsyncMock, return_value={"formatted_content": "done", "images": []}),
        ):
            result = await service.execute_full_workflow(topic="AI")
        assert result["status"] == "completed"
        assert result["phase_results"]["image_selection"]["images"] == []


@pytest.mark.integration
class TestContentPipelineDataFlow:

    @pytest.mark.asyncio
    async def test_refined_content_used_for_finalize(self):
        service = _make_service()
        assess_count = 0

        async def two_pass(**kwargs):
            nonlocal assess_count
            assess_count += 1
            if assess_count == 1:
                return {"quality_score": 0.4, "passed_threshold": False, "assessment": "weak"}
            return {"quality_score": 0.9, "passed_threshold": True}

        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, return_value={"phase": "research", "research_text": "r"}),
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock, return_value={"phase": "draft", "draft_content": "ORIGINAL"}),
            patch("services.content_service.ContentService.execute_assess", side_effect=two_pass),
            patch("services.content_service.ContentService.execute_refine", new_callable=AsyncMock, return_value={"phase": "refine", "refined_content": "REFINED"}),
            patch("services.content_service.ContentService.execute_image_selection", new_callable=AsyncMock, return_value={"images": []}),
            patch("services.content_service.ContentService.execute_finalize", new_callable=AsyncMock, return_value={"formatted_content": "final"}) as mf,
        ):
            result = await service.execute_full_workflow(topic="AI", quality_threshold=0.75)
        assert mf.call_args.kwargs["content"] == "REFINED"
        assert result["final_content"] == "REFINED"

    @pytest.mark.asyncio
    async def test_word_count_target_flows_to_draft(self):
        service = _make_service()
        with (
            patch("services.content_service.ContentService.execute_research", new_callable=AsyncMock, return_value={"phase": "research", "research_text": "t"}),
            patch("services.content_service.ContentService.execute_draft", new_callable=AsyncMock, return_value={"phase": "draft", "draft_content": "c"}) as md,
            patch("services.content_service.ContentService.execute_assess", new_callable=AsyncMock, return_value={"quality_score": 0.9, "passed_threshold": True}),
            patch("services.content_service.ContentService.execute_image_selection", new_callable=AsyncMock, return_value={"images": []}),
            patch("services.content_service.ContentService.execute_finalize", new_callable=AsyncMock, return_value={"formatted_content": "done"}),
        ):
            await service.execute_full_workflow(topic="AI", word_count_target=2500)
        assert md.call_args.kwargs["word_count_target"] == 2500
