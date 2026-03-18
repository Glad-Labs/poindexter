"""
Integration tests for the content generation pipeline.

Tests the ContentService.execute_full_workflow() orchestration logic:
  Research -> Draft -> QA Assess -> Refine (loop) -> Image Selection -> Finalize

LLM calls are mocked but the actual pipeline orchestration, phase sequencing,
refinement loop logic, and error handling are exercised end-to-end.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.content_service import ContentService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(**overrides):
    """Build a ContentService with optional dependency overrides."""
    defaults = {
        "database_service": None,
        "model_router": None,
        "writing_style_service": None,
    }
    defaults.update(overrides)
    return ContentService(**defaults)


# ---------------------------------------------------------------------------
# Full workflow — happy path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContentPipelineFullWorkflow:
    """Test the complete 6-phase content generation pipeline."""

    @pytest.mark.asyncio
    async def test_full_workflow_completes_all_phases(self):
        """All 6 phases execute in order: research -> draft -> assess -> image -> finalize."""
        service = _make_service()

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value={
                    "phase": "research",
                    "topic": "AI Ethics",
                    "research_text": "Background research on AI ethics",
                    "source": "research_agent",
                },
            ) as mock_research,
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                return_value={
                    "phase": "draft",
                    "draft_content": "# AI Ethics\n\nDraft content here." * 20,
                    "model_used": "test-model",
                    "source": "creative_agent",
                },
            ) as mock_draft,
            patch(
                "services.content_service.ContentService.execute_assess",
                new_callable=AsyncMock,
                return_value={
                    "phase": "assess",
                    "quality_score": 0.9,
                    "passed_threshold": True,
                    "assessment": "Good quality",
                },
            ) as mock_assess,
            patch(
                "services.content_service.ContentService.execute_image_selection",
                new_callable=AsyncMock,
                return_value={
                    "phase": "image_selection",
                    "images": [{"url": "https://example.com/img.jpg"}],
                },
            ) as mock_image,
            patch(
                "services.content_service.ContentService.execute_finalize",
                new_callable=AsyncMock,
                return_value={
                    "phase": "finalize",
                    "formatted_content": "Final formatted content",
                    "meta_description": "AI Ethics",
                },
            ) as mock_finalize,
        ):
            result = await service.execute_full_workflow(
                topic="AI Ethics",
                user_id="user-123",
                quality_threshold=0.75,
                word_count_target=1500,
            )

        assert result["status"] == "completed"
        assert result["topic"] == "AI Ethics"
        assert result["quality_score"] == 0.9
        assert result["refinement_count"] == 0
        assert "final_content" in result

        # Verify phase execution order
        mock_research.assert_awaited_once()
        mock_draft.assert_awaited_once()
        mock_assess.assert_awaited_once()
        mock_image.assert_awaited_once()
        mock_finalize.assert_awaited_once()

        # Verify phase results are captured
        phase_results = result["phase_results"]
        assert "research" in phase_results
        assert "draft" in phase_results
        assert "assess" in phase_results
        assert "image_selection" in phase_results
        assert "finalize" in phase_results

    @pytest.mark.asyncio
    async def test_draft_receives_research_context(self):
        """Draft phase receives the research phase output as context."""
        service = _make_service()
        research_output = {
            "phase": "research",
            "topic": "AI",
            "research_text": "Deep research findings",
            "source": "research_agent",
        }

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value=research_output,
            ),
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                return_value={
                    "phase": "draft",
                    "draft_content": "Draft text",
                    "source": "creative_agent",
                },
            ) as mock_draft,
            patch(
                "services.content_service.ContentService.execute_assess",
                new_callable=AsyncMock,
                return_value={"quality_score": 0.9, "passed_threshold": True},
            ),
            patch(
                "services.content_service.ContentService.execute_image_selection",
                new_callable=AsyncMock,
                return_value={"images": []},
            ),
            patch(
                "services.content_service.ContentService.execute_finalize",
                new_callable=AsyncMock,
                return_value={"formatted_content": "Final"},
            ),
        ):
            await service.execute_full_workflow(topic="AI")

        # The draft phase should receive the full research output as context
        call_kwargs = mock_draft.call_args
        assert call_kwargs.kwargs["research_context"] == research_output

    @pytest.mark.asyncio
    async def test_model_selections_forwarded_to_phases(self):
        """Per-phase model selections are passed to each phase."""
        service = _make_service()
        model_selections = {
            "research": "gemini-pro",
            "draft": "claude-3-sonnet",
            "assess": "gpt-4",
            "image_selection": "gemini",
            "finalize": "claude-3-haiku",
        }

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value={"phase": "research", "research_text": "text"},
            ) as mock_research,
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                return_value={"phase": "draft", "draft_content": "content"},
            ) as mock_draft,
            patch(
                "services.content_service.ContentService.execute_assess",
                new_callable=AsyncMock,
                return_value={"quality_score": 0.9, "passed_threshold": True},
            ) as mock_assess,
            patch(
                "services.content_service.ContentService.execute_image_selection",
                new_callable=AsyncMock,
                return_value={"images": []},
            ) as mock_image,
            patch(
                "services.content_service.ContentService.execute_finalize",
                new_callable=AsyncMock,
                return_value={"formatted_content": "done"},
            ) as mock_finalize,
        ):
            await service.execute_full_workflow(
                topic="AI", model_selections=model_selections
            )

        assert mock_research.call_args.kwargs["model"] == "gemini-pro"
        assert mock_draft.call_args.kwargs["model"] == "claude-3-sonnet"
        assert mock_assess.call_args.kwargs["model"] == "gpt-4"
        assert mock_image.call_args.kwargs["model"] == "gemini"
        assert mock_finalize.call_args.kwargs["model"] == "claude-3-haiku"


# ---------------------------------------------------------------------------
# Refinement loop
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContentPipelineRefinementLoop:
    """Test the assess-refine loop within the pipeline."""

    @pytest.mark.asyncio
    async def test_refinement_triggered_when_below_threshold(self):
        """When quality is below threshold, refine and re-assess."""
        service = _make_service()
        assess_call_count = 0

        async def mock_assess(**kwargs):
            nonlocal assess_call_count
            assess_call_count += 1
            if assess_call_count == 1:
                return {"quality_score": 0.5, "passed_threshold": False, "assessment": "Needs work"}
            return {"quality_score": 0.9, "passed_threshold": True, "assessment": "Good now"}

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value={"phase": "research", "research_text": "text"},
            ),
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                return_value={"phase": "draft", "draft_content": "initial draft"},
            ),
            patch(
                "services.content_service.ContentService.execute_assess",
                side_effect=mock_assess,
            ),
            patch(
                "services.content_service.ContentService.execute_refine",
                new_callable=AsyncMock,
                return_value={
                    "phase": "refine",
                    "refined_content": "improved draft",
                    "feedback_addressed": "Needs work",
                },
            ) as mock_refine,
            patch(
                "services.content_service.ContentService.execute_image_selection",
                new_callable=AsyncMock,
                return_value={"images": []},
            ),
            patch(
                "services.content_service.ContentService.execute_finalize",
                new_callable=AsyncMock,
                return_value={"formatted_content": "done"},
            ),
        ):
            result = await service.execute_full_workflow(
                topic="AI", quality_threshold=0.75
            )

        assert result["status"] == "completed"
        assert result["refinement_count"] == 1
        mock_refine.assert_awaited_once()
        # Final content should be the refined version
        assert result["final_content"] == "improved draft"

    @pytest.mark.asyncio
    async def test_max_refinements_caps_loop(self):
        """Refinement loop stops at max_refinements even if quality stays low."""
        service = _make_service()

        async def always_fail_assess(**kwargs):
            return {"quality_score": 0.3, "passed_threshold": False, "assessment": "Still bad"}

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value={"phase": "research", "research_text": "text"},
            ),
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                return_value={"phase": "draft", "draft_content": "weak draft"},
            ),
            patch(
                "services.content_service.ContentService.execute_assess",
                side_effect=always_fail_assess,
            ),
            patch(
                "services.content_service.ContentService.execute_refine",
                new_callable=AsyncMock,
                return_value={
                    "phase": "refine",
                    "refined_content": "still weak",
                },
            ) as mock_refine,
            patch(
                "services.content_service.ContentService.execute_image_selection",
                new_callable=AsyncMock,
                return_value={"images": []},
            ),
            patch(
                "services.content_service.ContentService.execute_finalize",
                new_callable=AsyncMock,
                return_value={"formatted_content": "done"},
            ),
        ):
            result = await service.execute_full_workflow(
                topic="AI", quality_threshold=0.75, max_refinements=2
            )

        assert result["status"] == "completed"
        assert result["refinement_count"] == 2
        assert mock_refine.await_count == 2

    @pytest.mark.asyncio
    async def test_no_refinement_when_quality_passes(self):
        """No refinement loop when initial quality passes threshold."""
        service = _make_service()

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value={"phase": "research", "research_text": "text"},
            ),
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                return_value={"phase": "draft", "draft_content": "great draft"},
            ),
            patch(
                "services.content_service.ContentService.execute_assess",
                new_callable=AsyncMock,
                return_value={"quality_score": 0.95, "passed_threshold": True},
            ),
            patch(
                "services.content_service.ContentService.execute_refine",
                new_callable=AsyncMock,
            ) as mock_refine,
            patch(
                "services.content_service.ContentService.execute_image_selection",
                new_callable=AsyncMock,
                return_value={"images": []},
            ),
            patch(
                "services.content_service.ContentService.execute_finalize",
                new_callable=AsyncMock,
                return_value={"formatted_content": "done"},
            ),
        ):
            result = await service.execute_full_workflow(
                topic="AI", quality_threshold=0.75
            )

        assert result["refinement_count"] == 0
        mock_refine.assert_not_awaited()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContentPipelineErrorHandling:
    """Test pipeline behavior when individual phases fail."""

    @pytest.mark.asyncio
    async def test_research_failure_returns_failed_status(self):
        """If research phase raises, workflow returns failed status."""
        service = _make_service()

        with patch(
            "services.content_service.ContentService.execute_research",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM provider down"),
        ):
            result = await service.execute_full_workflow(topic="AI")

        assert result["status"] == "failed"
        assert "LLM provider down" in result["error"]
        assert result["phase_results"] == {}

    @pytest.mark.asyncio
    async def test_draft_failure_returns_failed_with_research_result(self):
        """If draft fails, result includes the research phase that succeeded."""
        service = _make_service()

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value={"phase": "research", "research_text": "data"},
            ),
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Draft generation failed"),
            ),
        ):
            result = await service.execute_full_workflow(topic="AI")

        assert result["status"] == "failed"
        assert "Draft generation failed" in result["error"]
        assert "research" in result["phase_results"]
        assert "draft" not in result["phase_results"]

    @pytest.mark.asyncio
    async def test_image_selection_failure_is_non_critical(self):
        """Image selection failure should not crash the pipeline."""
        service = _make_service()

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value={"phase": "research", "research_text": "text"},
            ),
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                return_value={"phase": "draft", "draft_content": "content"},
            ),
            patch(
                "services.content_service.ContentService.execute_assess",
                new_callable=AsyncMock,
                return_value={"quality_score": 0.9, "passed_threshold": True},
            ),
            patch(
                "services.content_service.ContentService.execute_image_selection",
                new_callable=AsyncMock,
                return_value={"phase": "image_selection", "error": "Provider down", "images": []},
            ),
            patch(
                "services.content_service.ContentService.execute_finalize",
                new_callable=AsyncMock,
                return_value={"formatted_content": "done", "images": []},
            ),
        ):
            result = await service.execute_full_workflow(topic="AI")

        assert result["status"] == "completed"
        assert result["phase_results"]["image_selection"]["images"] == []


# ---------------------------------------------------------------------------
# Phase data flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestContentPipelineDataFlow:
    """Test that data flows correctly between pipeline phases."""

    @pytest.mark.asyncio
    async def test_refined_content_used_for_finalize(self):
        """After refinement, the refined content (not original draft) goes to finalize."""
        service = _make_service()
        assess_count = 0

        async def two_pass_assess(**kwargs):
            nonlocal assess_count
            assess_count += 1
            if assess_count == 1:
                return {"quality_score": 0.4, "passed_threshold": False, "assessment": "weak"}
            return {"quality_score": 0.9, "passed_threshold": True}

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value={"phase": "research", "research_text": "research"},
            ),
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                return_value={"phase": "draft", "draft_content": "ORIGINAL_DRAFT"},
            ),
            patch(
                "services.content_service.ContentService.execute_assess",
                side_effect=two_pass_assess,
            ),
            patch(
                "services.content_service.ContentService.execute_refine",
                new_callable=AsyncMock,
                return_value={"phase": "refine", "refined_content": "REFINED_DRAFT"},
            ),
            patch(
                "services.content_service.ContentService.execute_image_selection",
                new_callable=AsyncMock,
                return_value={"images": []},
            ),
            patch(
                "services.content_service.ContentService.execute_finalize",
                new_callable=AsyncMock,
                return_value={"formatted_content": "final"},
            ) as mock_finalize,
        ):
            result = await service.execute_full_workflow(
                topic="AI", quality_threshold=0.75
            )

        assert mock_finalize.call_args.kwargs["content"] == "REFINED_DRAFT"
        assert result["final_content"] == "REFINED_DRAFT"

    @pytest.mark.asyncio
    async def test_word_count_target_flows_to_draft(self):
        """word_count_target parameter is passed to the draft phase."""
        service = _make_service()

        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value={"phase": "research", "research_text": "text"},
            ),
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                return_value={"phase": "draft", "draft_content": "content"},
            ) as mock_draft,
            patch(
                "services.content_service.ContentService.execute_assess",
                new_callable=AsyncMock,
                return_value={"quality_score": 0.9, "passed_threshold": True},
            ),
            patch(
                "services.content_service.ContentService.execute_image_selection",
                new_callable=AsyncMock,
                return_value={"images": []},
            ),
            patch(
                "services.content_service.ContentService.execute_finalize",
                new_callable=AsyncMock,
                return_value={"formatted_content": "done"},
            ),
        ):
            await service.execute_full_workflow(topic="AI", word_count_target=2500)

        assert mock_draft.call_args.kwargs["word_count_target"] == 2500
