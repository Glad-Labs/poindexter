"""
Unit tests for services/content_service.py

Tests ContentService initialization, get_service_metadata, and each phase
method with agents mocked to avoid LLM calls. All agent imports are patched
to prevent pulling real agent/LLM dependencies.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.content_service import ContentService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(**kwargs) -> ContentService:
    """Return a ContentService with optional injected deps."""
    return ContentService(**kwargs)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestContentServiceInit:
    def test_creates_without_deps(self):
        svc = make_service()
        assert svc is not None

    def test_database_service_stored(self):
        mock_db = MagicMock()
        svc = make_service(database_service=mock_db)
        assert svc.database_service is mock_db

    def test_model_router_stored(self):
        mock_router = MagicMock()
        svc = make_service(model_router=mock_router)
        assert svc.model_router is mock_router

    def test_writing_style_service_stored(self):
        mock_ws = MagicMock()
        svc = make_service(writing_style_service=mock_ws)
        assert svc.writing_style_service is mock_ws

    def test_quality_service_stored(self):
        mock_qs = MagicMock()
        svc = make_service(quality_service=mock_qs)
        assert svc.quality_service is mock_qs

    def test_deps_default_to_none(self):
        svc = make_service()
        assert svc.database_service is None
        assert svc.model_router is None
        assert svc.writing_style_service is None
        assert svc.quality_service is None


# ---------------------------------------------------------------------------
# get_service_metadata
# ---------------------------------------------------------------------------


class TestGetServiceMetadata:
    def test_returns_dict(self):
        svc = make_service()
        meta = svc.get_service_metadata()
        assert isinstance(meta, dict)

    def test_name_is_content_service(self):
        svc = make_service()
        meta = svc.get_service_metadata()
        assert meta["name"] == "content_service"

    def test_phases_includes_all_six(self):
        svc = make_service()
        meta = svc.get_service_metadata()
        expected_phases = {"research", "draft", "assess", "refine", "image_selection", "finalize"}
        assert expected_phases.issubset(set(meta["phases"]))

    def test_capabilities_not_empty(self):
        svc = make_service()
        meta = svc.get_service_metadata()
        assert len(meta["capabilities"]) > 0

    def test_category_is_content(self):
        svc = make_service()
        meta = svc.get_service_metadata()
        assert meta["category"] == "content"


# ---------------------------------------------------------------------------
# execute_research
# ---------------------------------------------------------------------------


class TestExecuteResearch:
    @pytest.mark.asyncio
    async def test_returns_phase_research(self):
        svc = make_service()
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value="Research text about AI.")

        with patch("services.content_service.ResearchAgent", return_value=mock_agent, create=True):
            with patch(
                "agents.content_agent.agents.research_agent.ResearchAgent",
                return_value=mock_agent,
                create=True,
            ):
                # Patch at the import location inside execute_research
                with patch.dict("sys.modules", {
                    "agents.content_agent.agents.research_agent": MagicMock(
                        ResearchAgent=lambda: mock_agent
                    )
                }):
                    result = await svc.execute_research("AI in Healthcare")

        assert result["phase"] == "research"
        assert result["topic"] == "AI in Healthcare"

    @pytest.mark.asyncio
    async def test_propagates_exception(self):
        svc = make_service()
        with patch.dict("sys.modules", {
            "agents.content_agent.agents.research_agent": MagicMock(
                ResearchAgent=MagicMock(side_effect=RuntimeError("Agent down"))
            )
        }):
            with pytest.raises(RuntimeError):
                await svc.execute_research("AI")

    @pytest.mark.asyncio
    async def test_keywords_included_in_result(self):
        svc = make_service()
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value="Research results.")
        with patch.dict("sys.modules", {
            "agents.content_agent.agents.research_agent": MagicMock(
                ResearchAgent=lambda: mock_agent
            )
        }):
            result = await svc.execute_research("AI", keywords=["machine learning", "deep learning"])

        assert result["keywords"] == ["machine learning", "deep learning"]


# ---------------------------------------------------------------------------
# execute_assess
# ---------------------------------------------------------------------------


class TestExecuteAssess:
    @pytest.mark.asyncio
    async def test_returns_phase_assess(self):
        svc = make_service()
        mock_qa = MagicMock()
        mock_qa.run = AsyncMock(return_value="Assessment: content is good.")
        with patch.dict("sys.modules", {
            "agents.content_agent.agents.qa_agent": MagicMock(
                QAAgent=lambda: mock_qa
            )
        }):
            result = await svc.execute_assess("Some content here.", "AI")

        assert result["phase"] == "assess"

    @pytest.mark.asyncio
    async def test_quality_threshold_in_result(self):
        svc = make_service()
        mock_qa = MagicMock()
        mock_qa.run = AsyncMock(return_value="Assessment OK.")
        with patch.dict("sys.modules", {
            "agents.content_agent.agents.qa_agent": MagicMock(
                QAAgent=lambda: mock_qa
            )
        }):
            result = await svc.execute_assess("Content", "AI", quality_threshold=0.8)

        assert result["quality_threshold"] == 0.8

    @pytest.mark.asyncio
    async def test_source_is_qa_agent(self):
        svc = make_service()
        mock_qa = MagicMock()
        mock_qa.run = AsyncMock(return_value="OK")
        with patch.dict("sys.modules", {
            "agents.content_agent.agents.qa_agent": MagicMock(
                QAAgent=lambda: mock_qa
            )
        }):
            result = await svc.execute_assess("Content", "AI")
        assert result["source"] == "qa_agent"

    @pytest.mark.asyncio
    async def test_passed_threshold_true_when_above(self):
        svc = make_service()
        mock_qa = MagicMock()
        mock_qa.run = AsyncMock(return_value="OK")
        with patch.dict("sys.modules", {
            "agents.content_agent.agents.qa_agent": MagicMock(
                QAAgent=lambda: mock_qa
            )
        }):
            result = await svc.execute_assess("Content", "AI", quality_threshold=0.5)
        # Default quality score is 0.75
        assert result["passed_threshold"] is True

    @pytest.mark.asyncio
    async def test_passed_threshold_false_when_above_threshold(self):
        svc = make_service()
        mock_qa = MagicMock()
        mock_qa.run = AsyncMock(return_value="OK")
        with patch.dict("sys.modules", {
            "agents.content_agent.agents.qa_agent": MagicMock(
                QAAgent=lambda: mock_qa
            )
        }):
            result = await svc.execute_assess("Content", "AI", quality_threshold=0.9)
        # Default quality score is 0.75, below 0.9
        assert result["passed_threshold"] is False


# ---------------------------------------------------------------------------
# execute_draft
# ---------------------------------------------------------------------------


def _make_draft_modules(mock_agent=None, mock_llm_client=None, writing_style_integration=None):
    """Return a sys.modules patch dict for draft phase imports."""
    if mock_agent is None:
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value="Draft content here.")
    if mock_llm_client is None:
        mock_llm_client = MagicMock()
    if writing_style_integration is None:
        writing_style_integration = MagicMock()

    return {
        "agents.content_agent.agents.creative_agent": MagicMock(
            CreativeAgent=lambda llm_client=None: mock_agent
        ),
        "agents.content_agent.services.llm_client": MagicMock(
            LLMClient=lambda model_name=None: mock_llm_client
        ),
        "services.writing_style_integration": MagicMock(
            WritingStyleIntegrationService=writing_style_integration
        ),
    }


@pytest.mark.unit
class TestExecuteDraft:
    @pytest.mark.asyncio
    async def test_returns_phase_draft(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_draft_modules()):
            result = await svc.execute_draft(
                research_context={"research_text": "Research on AI."},
                topic="AI",
            )
        assert result["phase"] == "draft"

    @pytest.mark.asyncio
    async def test_draft_content_in_result(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value="My drafted content.")
        svc = make_service()
        with patch.dict("sys.modules", _make_draft_modules(mock_agent=mock_agent)):
            result = await svc.execute_draft(
                research_context={"research_text": "Research."},
                topic="AI",
            )
        assert result["draft_content"] == "My drafted content."

    @pytest.mark.asyncio
    async def test_source_is_creative_agent(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_draft_modules()):
            result = await svc.execute_draft(
                research_context={},
                topic="AI",
            )
        assert result["source"] == "creative_agent"

    @pytest.mark.asyncio
    async def test_word_count_target_in_result(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_draft_modules()):
            result = await svc.execute_draft(
                research_context={},
                topic="AI",
                word_count_target=2000,
            )
        assert result["word_count_target"] == 2000

    @pytest.mark.asyncio
    async def test_model_router_used_for_draft_model(self):
        mock_router = MagicMock()
        mock_router.select_model = MagicMock(return_value="claude-cheap")
        svc = make_service(model_router=mock_router)
        with patch.dict("sys.modules", _make_draft_modules()):
            result = await svc.execute_draft(
                research_context={},
                topic="AI",
            )
        mock_router.select_model.assert_called_once_with("draft")
        assert result["model_used"] == "claude-cheap"

    @pytest.mark.asyncio
    async def test_writing_style_service_called_when_present(self):
        mock_ws = MagicMock()
        mock_ws.get_sample_for_content_generation = AsyncMock(
            return_value={"writing_style_guidance": "Be concise."}
        )
        svc = make_service(writing_style_service=mock_ws)
        with patch.dict("sys.modules", _make_draft_modules()):
            await svc.execute_draft(
                research_context={},
                topic="AI",
            )
        mock_ws.get_sample_for_content_generation.assert_called_once()

    @pytest.mark.asyncio
    async def test_writing_style_error_does_not_propagate(self):
        mock_ws = MagicMock()
        mock_ws.get_sample_for_content_generation = AsyncMock(
            side_effect=RuntimeError("style service down")
        )
        svc = make_service(writing_style_service=mock_ws)
        with patch.dict("sys.modules", _make_draft_modules()):
            # Should not raise despite writing_style_service failure
            result = await svc.execute_draft(
                research_context={},
                topic="AI",
            )
        assert result["phase"] == "draft"

    @pytest.mark.asyncio
    async def test_agent_exception_propagates(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("Agent down"))
        svc = make_service()
        with patch.dict("sys.modules", _make_draft_modules(mock_agent=mock_agent)):
            with pytest.raises(RuntimeError):
                await svc.execute_draft(
                    research_context={},
                    topic="AI",
                )


# ---------------------------------------------------------------------------
# execute_refine
# ---------------------------------------------------------------------------


def _make_refine_modules(mock_agent=None):
    """Return a sys.modules patch dict for refine phase imports."""
    if mock_agent is None:
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value="Refined content.")
    return {
        "agents.content_agent.agents.creative_agent": MagicMock(
            CreativeAgent=lambda llm_client=None: mock_agent
        ),
        "agents.content_agent.services.llm_client": MagicMock(
            LLMClient=lambda model_name=None: MagicMock()
        ),
    }


@pytest.mark.unit
class TestExecuteRefine:
    @pytest.mark.asyncio
    async def test_returns_phase_refine(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_refine_modules()):
            result = await svc.execute_refine(
                content="Draft content.",
                feedback="Make it better.",
            )
        assert result["phase"] == "refine"

    @pytest.mark.asyncio
    async def test_refined_content_in_result(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value="Refined and improved content.")
        svc = make_service()
        with patch.dict("sys.modules", _make_refine_modules(mock_agent=mock_agent)):
            result = await svc.execute_refine(
                content="Draft.",
                feedback="Improve clarity.",
            )
        assert result["refined_content"] == "Refined and improved content."

    @pytest.mark.asyncio
    async def test_feedback_stored_in_result(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_refine_modules()):
            result = await svc.execute_refine(
                content="Draft.",
                feedback="Add more examples.",
            )
        assert result["feedback_addressed"] == "Add more examples."

    @pytest.mark.asyncio
    async def test_source_is_creative_agent(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_refine_modules()):
            result = await svc.execute_refine(content="Draft.", feedback="Improve.")
        assert result["source"] == "creative_agent"

    @pytest.mark.asyncio
    async def test_model_router_used_for_refine_model(self):
        mock_router = MagicMock()
        mock_router.select_model = MagicMock(return_value="claude-balanced")
        svc = make_service(model_router=mock_router)
        with patch.dict("sys.modules", _make_refine_modules()):
            result = await svc.execute_refine(content="Draft.", feedback="Fix.")
        mock_router.select_model.assert_called_once_with("refine")
        assert result["model_used"] == "claude-balanced"

    @pytest.mark.asyncio
    async def test_agent_exception_propagates(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("Refine failed"))
        svc = make_service()
        with patch.dict("sys.modules", _make_refine_modules(mock_agent=mock_agent)):
            with pytest.raises(RuntimeError):
                await svc.execute_refine(content="Draft.", feedback="Improve.")


# ---------------------------------------------------------------------------
# execute_image_selection
# ---------------------------------------------------------------------------


def _make_image_modules(mock_agent=None):
    if mock_agent is None:
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value={"url": "https://example.com/img.jpg"})
    return {
        "agents.content_agent.agents.postgres_image_agent": MagicMock(
            PostgreSQLImageAgent=lambda: mock_agent
        ),
    }


@pytest.mark.unit
class TestExecuteImageSelection:
    @pytest.mark.asyncio
    async def test_returns_phase_image_selection(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_image_modules()):
            result = await svc.execute_image_selection(topic="AI", content="Content here.")
        assert result["phase"] == "image_selection"

    @pytest.mark.asyncio
    async def test_topic_in_result(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_image_modules()):
            result = await svc.execute_image_selection(topic="Machine Learning", content="Content.")
        assert result["topic"] == "Machine Learning"

    @pytest.mark.asyncio
    async def test_image_data_in_result(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value={"url": "https://img.example.com/photo.jpg"})
        svc = make_service()
        with patch.dict("sys.modules", _make_image_modules(mock_agent=mock_agent)):
            result = await svc.execute_image_selection(topic="AI", content="Content.")
        assert result["image_data"] == {"url": "https://img.example.com/photo.jpg"}

    @pytest.mark.asyncio
    async def test_source_is_image_agent(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_image_modules()):
            result = await svc.execute_image_selection(topic="AI", content="Content.")
        assert result["source"] == "image_agent"

    @pytest.mark.asyncio
    async def test_agent_error_returns_empty_images(self):
        """Image selection errors are non-critical and return empty result."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("Image API down"))
        svc = make_service()
        with patch.dict("sys.modules", _make_image_modules(mock_agent=mock_agent)):
            result = await svc.execute_image_selection(topic="AI", content="Content.")
        # Should NOT raise — image selection is optional
        assert result["phase"] == "image_selection"
        assert result["images"] == []
        assert "error" in result

    @pytest.mark.asyncio
    async def test_agent_error_includes_error_message(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("Pexels API rate limited"))
        svc = make_service()
        with patch.dict("sys.modules", _make_image_modules(mock_agent=mock_agent)):
            result = await svc.execute_image_selection(topic="AI", content="Content.")
        assert "Pexels API rate limited" in result["error"]


# ---------------------------------------------------------------------------
# execute_finalize
# ---------------------------------------------------------------------------


def _make_finalize_modules(mock_agent=None):
    if mock_agent is None:
        mock_result = MagicMock()
        mock_result.raw_content = "Finalized content."
        mock_result.meta_description = "AI article summary."
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
    return {
        "agents.content_agent.agents.postgres_publishing_agent": MagicMock(
            PostgreSQLPublishingAgent=lambda: mock_agent
        ),
    }


@pytest.mark.unit
class TestExecuteFinalize:
    @pytest.mark.asyncio
    async def test_returns_phase_finalize(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_finalize_modules()):
            result = await svc.execute_finalize(content="Draft content.", topic="AI")
        assert result["phase"] == "finalize"

    @pytest.mark.asyncio
    async def test_formatted_content_in_result(self):
        mock_result = MagicMock()
        mock_result.raw_content = "Final polished article."
        mock_result.meta_description = "Summary."
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        svc = make_service()
        with patch.dict("sys.modules", _make_finalize_modules(mock_agent=mock_agent)):
            result = await svc.execute_finalize(content="Draft.", topic="AI")
        assert result["formatted_content"] == "Final polished article."

    @pytest.mark.asyncio
    async def test_meta_description_in_result(self):
        mock_result = MagicMock()
        mock_result.raw_content = "Content."
        mock_result.meta_description = "AI in healthcare: what you need to know."
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        svc = make_service()
        with patch.dict("sys.modules", _make_finalize_modules(mock_agent=mock_agent)):
            result = await svc.execute_finalize(content="Draft.", topic="Healthcare AI")
        assert result["meta_description"] == "AI in healthcare: what you need to know."

    @pytest.mark.asyncio
    async def test_images_passed_through(self):
        svc = make_service()
        images = [{"url": "https://example.com/img1.jpg"}, {"url": "https://example.com/img2.jpg"}]
        with patch.dict("sys.modules", _make_finalize_modules()):
            result = await svc.execute_finalize(content="Draft.", topic="AI", images=images)
        assert result["images"] == images

    @pytest.mark.asyncio
    async def test_empty_images_when_none_passed(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_finalize_modules()):
            result = await svc.execute_finalize(content="Draft.", topic="AI")
        assert result["images"] == []

    @pytest.mark.asyncio
    async def test_source_is_publishing_agent(self):
        svc = make_service()
        with patch.dict("sys.modules", _make_finalize_modules()):
            result = await svc.execute_finalize(content="Draft.", topic="AI")
        assert result["source"] == "publishing_agent"

    @pytest.mark.asyncio
    async def test_agent_exception_propagates(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("DB write failed"))
        svc = make_service()
        with patch.dict("sys.modules", _make_finalize_modules(mock_agent=mock_agent)):
            with pytest.raises(RuntimeError):
                await svc.execute_finalize(content="Draft.", topic="AI")

    @pytest.mark.asyncio
    async def test_fallback_for_missing_raw_content_attr(self):
        """If result has no raw_content, falls back to str(content)."""
        mock_result = MagicMock(spec=[])  # No attributes
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        svc = make_service()
        with patch.dict("sys.modules", _make_finalize_modules(mock_agent=mock_agent)):
            result = await svc.execute_finalize(content="My draft content.", topic="AI")
        # Falls back to str(content) — the original content string
        assert "My draft content." in result["formatted_content"]


# ---------------------------------------------------------------------------
# execute_full_workflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteFullWorkflow:
    def _mock_svc(self, passes_threshold=True):
        """
        Return a ContentService where all phase methods are mocked.
        If passes_threshold=True, assess returns passed_threshold=True.
        """
        svc = make_service()
        svc.execute_research = AsyncMock(
            return_value={"phase": "research", "research_text": "Research.", "topic": "AI"}
        )
        svc.execute_draft = AsyncMock(
            return_value={"phase": "draft", "draft_content": "Draft content.", "source": "creative_agent"}
        )
        svc.execute_assess = AsyncMock(
            return_value={
                "phase": "assess",
                "quality_score": 0.85 if passes_threshold else 0.60,
                "passed_threshold": passes_threshold,
                "assessment": "Looks good.",
                "quality_threshold": 0.75,
            }
        )
        svc.execute_refine = AsyncMock(
            return_value={
                "phase": "refine",
                "refined_content": "Refined content.",
                "source": "creative_agent",
            }
        )
        svc.execute_image_selection = AsyncMock(
            return_value={"phase": "image_selection", "images": [], "image_data": {}}
        )
        svc.execute_finalize = AsyncMock(
            return_value={
                "phase": "finalize",
                "formatted_content": "Final content.",
                "source": "publishing_agent",
            }
        )
        return svc

    @pytest.mark.asyncio
    async def test_returns_completed_status_on_success(self):
        svc = self._mock_svc(passes_threshold=True)
        result = await svc.execute_full_workflow("AI in Healthcare")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_topic_in_result(self):
        svc = self._mock_svc(passes_threshold=True)
        result = await svc.execute_full_workflow("AI in Healthcare")
        assert result["topic"] == "AI in Healthcare"

    @pytest.mark.asyncio
    async def test_zero_refinements_when_threshold_passes(self):
        svc = self._mock_svc(passes_threshold=True)
        result = await svc.execute_full_workflow("AI")
        assert result["refinement_count"] == 0
        svc.execute_refine.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_phase_results_contains_all_phases(self):
        svc = self._mock_svc(passes_threshold=True)
        result = await svc.execute_full_workflow("AI")
        phases = result["phase_results"]
        assert "research" in phases
        assert "draft" in phases
        assert "assess" in phases
        assert "image_selection" in phases
        assert "finalize" in phases

    @pytest.mark.asyncio
    async def test_quality_score_in_result(self):
        svc = self._mock_svc(passes_threshold=True)
        result = await svc.execute_full_workflow("AI")
        assert result["quality_score"] == 0.85

    @pytest.mark.asyncio
    async def test_refinement_triggered_when_below_threshold(self):
        """When quality is below threshold, refine is called once."""
        svc = make_service()
        svc.execute_research = AsyncMock(
            return_value={"phase": "research", "research_text": "Research.", "topic": "AI"}
        )
        svc.execute_draft = AsyncMock(
            return_value={"phase": "draft", "draft_content": "Draft.", "source": "creative_agent"}
        )
        # First assess fails, second passes
        svc.execute_assess = AsyncMock(
            side_effect=[
                {"phase": "assess", "quality_score": 0.60, "passed_threshold": False,
                 "assessment": "Needs work.", "quality_threshold": 0.75},
                {"phase": "assess", "quality_score": 0.85, "passed_threshold": True,
                 "assessment": "Good now.", "quality_threshold": 0.75},
            ]
        )
        svc.execute_refine = AsyncMock(
            return_value={"phase": "refine", "refined_content": "Refined.", "source": "creative_agent"}
        )
        svc.execute_image_selection = AsyncMock(
            return_value={"phase": "image_selection", "images": [], "image_data": {}}
        )
        svc.execute_finalize = AsyncMock(
            return_value={"phase": "finalize", "formatted_content": "Final.", "source": "publishing_agent"}
        )
        result = await svc.execute_full_workflow("AI", quality_threshold=0.75)
        assert result["refinement_count"] == 1
        svc.execute_refine.assert_called_once()  # type: ignore[attr-defined]
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_max_refinements_respected(self):
        """Refinement loop stops at max_refinements even if quality never passes."""
        svc = make_service()
        svc.execute_research = AsyncMock(
            return_value={"phase": "research", "research_text": "Research.", "topic": "AI"}
        )
        svc.execute_draft = AsyncMock(
            return_value={"phase": "draft", "draft_content": "Draft.", "source": "creative_agent"}
        )
        # Always fails threshold
        svc.execute_assess = AsyncMock(
            return_value={"phase": "assess", "quality_score": 0.50, "passed_threshold": False,
                          "assessment": "Needs work.", "quality_threshold": 0.75}
        )
        svc.execute_refine = AsyncMock(
            return_value={"phase": "refine", "refined_content": "Refined.", "source": "creative_agent"}
        )
        svc.execute_image_selection = AsyncMock(
            return_value={"phase": "image_selection", "images": [], "image_data": {}}
        )
        svc.execute_finalize = AsyncMock(
            return_value={"phase": "finalize", "formatted_content": "Final.", "source": "publishing_agent"}
        )
        result = await svc.execute_full_workflow("AI", max_refinements=2)
        # execute_refine called exactly 2 times (max_refinements)
        assert svc.execute_refine.call_count == 2  # type: ignore[attr-defined]
        # Workflow still completes (not failed) — it continues past max_refinements
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_failed_status_when_research_raises(self):
        svc = make_service()
        svc.execute_research = AsyncMock(side_effect=RuntimeError("Research service down"))
        result = await svc.execute_full_workflow("AI")
        assert result["status"] == "failed"
        assert result["topic"] == "AI"

    @pytest.mark.asyncio
    async def test_failed_status_includes_error_message(self):
        svc = make_service()
        svc.execute_research = AsyncMock(side_effect=RuntimeError("Research service down"))
        result = await svc.execute_full_workflow("AI")
        assert "Research service down" in result["error"]

    @pytest.mark.asyncio
    async def test_failed_includes_partial_phase_results(self):
        """Even on failure, partial results from completed phases are returned."""
        svc = make_service()
        svc.execute_research = AsyncMock(
            return_value={"phase": "research", "research_text": "Research.", "topic": "AI"}
        )
        svc.execute_draft = AsyncMock(side_effect=RuntimeError("Draft failed"))
        result = await svc.execute_full_workflow("AI")
        assert result["status"] == "failed"
        # Research phase completed before failure
        assert "research" in result["phase_results"]

    @pytest.mark.asyncio
    async def test_model_selections_passed_to_phases(self):
        svc = self._mock_svc(passes_threshold=True)
        model_selections = {"research": "gpt-4", "draft": "claude-3"}
        await svc.execute_full_workflow("AI", model_selections=model_selections)
        svc.execute_research.assert_called_once_with(  # type: ignore[attr-defined]
            topic="AI", model="gpt-4"
        )
        svc.execute_draft.assert_called_once_with(  # type: ignore[attr-defined]
            research_context=svc.execute_research.return_value,  # type: ignore[attr-defined]
            topic="AI",
            model="claude-3",
            word_count_target=1500,
        )

    @pytest.mark.asyncio
    async def test_final_content_is_last_refined_content(self):
        """After refinement, final_content should be the refined content."""
        svc = make_service()
        svc.execute_research = AsyncMock(
            return_value={"phase": "research", "research_text": "Research.", "topic": "AI"}
        )
        svc.execute_draft = AsyncMock(
            return_value={"phase": "draft", "draft_content": "Original draft.", "source": "creative_agent"}
        )
        svc.execute_assess = AsyncMock(
            side_effect=[
                {"phase": "assess", "quality_score": 0.60, "passed_threshold": False,
                 "assessment": "Needs work.", "quality_threshold": 0.75},
                {"phase": "assess", "quality_score": 0.90, "passed_threshold": True,
                 "assessment": "Excellent.", "quality_threshold": 0.75},
            ]
        )
        svc.execute_refine = AsyncMock(
            return_value={"phase": "refine", "refined_content": "Refined final content.", "source": "creative_agent"}
        )
        svc.execute_image_selection = AsyncMock(
            return_value={"phase": "image_selection", "images": [], "image_data": {}}
        )
        svc.execute_finalize = AsyncMock(
            return_value={"phase": "finalize", "formatted_content": "Final.", "source": "publishing_agent"}
        )
        result = await svc.execute_full_workflow("AI")
        assert result["final_content"] == "Refined final content."
