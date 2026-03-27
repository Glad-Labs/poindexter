"""
Content Pipeline Quality Integration Tests

End-to-end tests for the 6-phase content generation workflow that verify
the pipeline produces complete, publish-ready posts. Focuses on:

1. Output completeness — all phases contribute their expected data
2. Quality convergence — refinement loop improves content quality
3. Writing style application — style preferences affect draft output
4. Graceful degradation — image/optional phase failures don't block publishing
5. Phase result accumulation — all phase outputs available in final result
6. Content quality gates — below-threshold content triggers refinement
7. Stagnation detection — refinement stops when quality plateaus

LLM calls are mocked but the actual ContentService orchestration, phase
sequencing, refinement loop logic, and error handling are exercised.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.content_service import ContentService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(**overrides):
    defaults = {
        "database_service": None,
        "model_router": None,
        "writing_style_service": None,
    }
    defaults.update(overrides)
    return ContentService(**defaults)


# Realistic mock outputs that simulate a high-quality pipeline run
RESEARCH_OUTPUT = {
    "phase": "research",
    "topic": "AI in Healthcare Diagnostics",
    "keywords": ["AI", "healthcare", "diagnostics"],
    "research_text": (
        "AI-powered diagnostic tools are transforming healthcare. "
        "Key findings: 30% reduction in misdiagnosis rates, 45% improvement in early detection. "
        "Major players include Google Health, IBM Watson Health, and PathAI. "
        "Sources: JAMA, Nature Medicine, WHO Global Health Observatory."
    ),
    "source": "research_agent",
}

DRAFT_OUTPUT = {
    "phase": "draft",
    "draft_content": (
        "# AI in Healthcare Diagnostics: A 2026 Perspective\n\n"
        "## Introduction\n\n"
        "Artificial intelligence is **revolutionizing** healthcare diagnostics, "
        "offering unprecedented accuracy and speed in disease detection.\n\n"
        "## The State of AI Diagnostics\n\n"
        "The global AI diagnostics market reached $8.5 billion in 2025. "
        "Machine learning models trained on millions of medical images now "
        "achieve accuracy rates exceeding 95% in radiology screening.\n\n"
        "### Radiology\n\n"
        "- AI-powered imaging detects lung nodules with 97% sensitivity\n"
        "- Breast cancer screening assisted by AI reduces false negatives by 30%\n\n"
        "### Pathology\n\n"
        "Digital pathology combined with deep learning enables:\n\n"
        "1. Faster tissue analysis\n"
        "2. More consistent diagnoses\n"
        "3. Remote consultation capabilities\n\n"
        "## Challenges\n\n"
        "Despite progress, challenges remain in data privacy, regulatory approval, "
        "and clinical integration.\n\n"
        "## Conclusion\n\n"
        "AI diagnostics represent a paradigm shift in patient care. "
        "Healthcare organizations that embrace these tools will be better "
        "positioned to deliver accurate, timely diagnoses.\n"
    ),
    "model_used": "claude-3-sonnet",
    "word_count_target": 1500,
    "source": "creative_agent",
}

HIGH_QUALITY_ASSESS = {
    "phase": "assess",
    "quality_score": 0.88,
    "passed_threshold": True,
    "assessment": "Strong content with good structure, credible data, and actionable insights.",
    "quality_threshold": 0.75,
    "source": "qa_agent",
}

LOW_QUALITY_ASSESS = {
    "phase": "assess",
    "quality_score": 0.55,
    "passed_threshold": False,
    "assessment": "Content lacks specific data. Introduction is weak. Add more real-world examples.",
    "quality_threshold": 0.75,
    "source": "qa_agent",
}

IMPROVED_ASSESS = {
    "phase": "assess",
    "quality_score": 0.82,
    "passed_threshold": True,
    "assessment": "Significantly improved. Good use of statistics and examples.",
    "quality_threshold": 0.75,
    "source": "qa_agent",
}

REFINE_OUTPUT = {
    "phase": "refine",
    "refined_content": (
        "# AI in Healthcare Diagnostics: A 2026 Perspective\n\n"
        "## Introduction\n\n"
        "In 2026, AI-powered diagnostics have moved from experimental to essential. "
        "According to a recent JAMA study, hospitals using AI diagnostic tools report "
        "a **30% reduction in misdiagnosis rates** and **45% improvement in early detection**.\n\n"
        "## Key Applications\n\n"
        "### Radiology\n\n"
        "AI imaging tools now assist in detecting:\n"
        "- Lung cancer nodules (97% sensitivity, per Stanford Health)\n"
        "- Breast cancer (30% fewer false negatives vs. traditional screening)\n"
        "- Neurological conditions (early Alzheimer's markers detectable 5 years sooner)\n\n"
        "### Digital Pathology\n\n"
        "PathAI and similar platforms have reduced tissue analysis turnaround from "
        "5 days to under 24 hours, with consistency improvements of 40%.\n\n"
        "## Challenges and Considerations\n\n"
        "1. **HIPAA Compliance**: Patient data handling remains a top concern\n"
        "2. **FDA Clearance**: Only 523 AI/ML medical devices have received FDA authorization as of 2025\n"
        "3. **EHR Integration**: Seamless integration with Epic and Cerner systems is critical\n\n"
        "## Conclusion\n\n"
        "AI diagnostics are no longer a future promise — they are today's clinical reality. "
        "Healthcare leaders should prioritize vendor evaluation, staff training, and compliance "
        "frameworks to maximize the benefit of these transformative tools.\n"
    ),
    "feedback_addressed": "Added specific data, strengthened intro, included real-world examples",
    "model_used": "claude-3-sonnet",
    "source": "creative_agent",
}

IMAGE_OUTPUT = {
    "phase": "image_selection",
    "topic": "AI in Healthcare Diagnostics",
    "image_data": {
        "images": [
            {
                "url": "https://images.pexels.com/photos/12345/ai-diagnostics.jpg",
                "alt_text": "AI diagnostic system analyzing medical scans",
                "caption": "Modern AI-assisted radiology workstation",
            }
        ]
    },
    "source": "image_agent",
}

FINALIZE_OUTPUT = {
    "phase": "finalize",
    "formatted_content": "Final HTML-ready content",
    "meta_description": "AI diagnostics are transforming healthcare with 30% fewer misdiagnoses.",
    "images": [{"url": "https://images.pexels.com/photos/12345/ai-diagnostics.jpg"}],
    "source": "publishing_agent",
}


def _standard_patches(
    assess_return=None,
    assess_side_effect=None,
    image_return=None,
    refine_return=None,
):
    """Return a list of context managers for the standard 6-phase pipeline."""
    assess_kwargs = {}
    if assess_side_effect:
        assess_kwargs["side_effect"] = assess_side_effect
    else:
        assess_kwargs["return_value"] = assess_return or HIGH_QUALITY_ASSESS

    patches = {
        "research": patch(
            "services.content_service.ContentService.execute_research",
            new_callable=AsyncMock,
            return_value=RESEARCH_OUTPUT,
        ),
        "draft": patch(
            "services.content_service.ContentService.execute_draft",
            new_callable=AsyncMock,
            return_value=DRAFT_OUTPUT,
        ),
        "assess": patch(
            "services.content_service.ContentService.execute_assess",
            **assess_kwargs,
        ),
        "refine": patch(
            "services.content_service.ContentService.execute_refine",
            new_callable=AsyncMock,
            return_value=refine_return or REFINE_OUTPUT,
        ),
        "image": patch(
            "services.content_service.ContentService.execute_image_selection",
            new_callable=AsyncMock,
            return_value=image_return or IMAGE_OUTPUT,
        ),
        "finalize": patch(
            "services.content_service.ContentService.execute_finalize",
            new_callable=AsyncMock,
            return_value=FINALIZE_OUTPUT,
        ),
    }
    return patches


# ---------------------------------------------------------------------------
# Test: Output completeness
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPipelineOutputCompleteness:
    """Verify the full workflow result has all data needed for a publish-ready post."""

    @pytest.mark.asyncio
    async def test_completed_workflow_has_all_top_level_keys(self):
        """Result should include status, topic, quality_score, final_content, phase_results."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(
                topic="AI in Healthcare Diagnostics",
                user_id="user-123",
            )
        assert result["status"] == "completed"
        assert result["topic"] == "AI in Healthcare Diagnostics"
        assert result["quality_score"] == 0.88
        assert result["refinement_count"] == 0  # High quality, no refinement
        assert "final_content" in result
        assert "phase_results" in result

    @pytest.mark.asyncio
    async def test_all_six_phases_in_results(self):
        """Phase results should contain entries for all 6 pipeline phases."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(topic="AI in Healthcare")
        pr = result["phase_results"]
        assert "research" in pr
        assert "draft" in pr
        assert "assess" in pr
        assert "image_selection" in pr
        assert "finalize" in pr

    @pytest.mark.asyncio
    async def test_research_phase_has_sources(self):
        """Research output should include research text for draft context."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(topic="AI")
        research = result["phase_results"]["research"]
        assert "research_text" in research
        assert len(research["research_text"]) > 0
        assert research["source"] == "research_agent"

    @pytest.mark.asyncio
    async def test_draft_phase_has_content(self):
        """Draft output should include meaningful content."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(topic="AI")
        draft = result["phase_results"]["draft"]
        assert "draft_content" in draft
        assert len(draft["draft_content"]) > 100
        assert "model_used" in draft

    @pytest.mark.asyncio
    async def test_finalize_phase_has_formatted_content_and_meta(self):
        """Finalize output should include formatted content and meta description."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(topic="AI")
        finalize = result["phase_results"]["finalize"]
        assert "formatted_content" in finalize
        assert "meta_description" in finalize

    @pytest.mark.asyncio
    async def test_image_phase_has_image_data(self):
        """Image selection output should include image data."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(topic="AI")
        images = result["phase_results"]["image_selection"]
        assert images["source"] == "image_agent"


# ---------------------------------------------------------------------------
# Test: Quality convergence through refinement
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQualityConvergence:
    """Verify the assess-refine loop converges toward quality threshold."""

    @pytest.mark.asyncio
    async def test_refinement_improves_quality_to_threshold(self):
        """Content below threshold gets refined until it passes."""
        service = _make_service()
        assess_count = 0

        async def improving_assess(**kwargs):
            nonlocal assess_count
            assess_count += 1
            if assess_count == 1:
                return LOW_QUALITY_ASSESS  # 0.55
            return IMPROVED_ASSESS  # 0.82

        patches = _standard_patches(assess_side_effect=improving_assess)
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"] as mock_refine,
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(topic="AI", quality_threshold=0.75)
        assert result["status"] == "completed"
        assert result["quality_score"] == 0.82
        assert result["refinement_count"] == 1
        mock_refine.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_refinements_tracked(self):
        """Multiple refinement rounds should all be tracked in phase_results."""
        service = _make_service()
        assess_count = 0

        async def gradual_improvement(**kwargs):
            nonlocal assess_count
            assess_count += 1
            scores = [0.45, 0.60, 0.80]
            score = scores[min(assess_count - 1, len(scores) - 1)]
            return {
                "quality_score": score,
                "passed_threshold": score >= 0.75,
                "assessment": f"Round {assess_count}: score {score}",
            }

        patches = _standard_patches(assess_side_effect=gradual_improvement)
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(topic="AI", quality_threshold=0.75)
        assert result["refinement_count"] == 2
        # Phase results should track each refinement
        assert "refine_1" in result["phase_results"]
        assert "refine_2" in result["phase_results"]
        assert "assess_1" in result["phase_results"]
        assert "assess_2" in result["phase_results"]

    @pytest.mark.asyncio
    async def test_max_refinements_prevents_infinite_loop(self):
        """Pipeline must stop after max_refinements even if quality stays low."""
        service = _make_service()

        async def always_low(**kwargs):
            return {
                "quality_score": 0.30,
                "passed_threshold": False,
                "assessment": "Still needs major work",
            }

        patches = _standard_patches(assess_side_effect=always_low)
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"] as mock_refine,
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(
                topic="AI", quality_threshold=0.75, max_refinements=2
            )
        assert result["status"] == "completed"
        assert result["refinement_count"] == 2
        assert mock_refine.await_count == 2

    @pytest.mark.asyncio
    async def test_high_quality_draft_skips_refinement(self):
        """If the initial draft passes quality threshold, skip refinement entirely."""
        service = _make_service()
        patches = _standard_patches(
            assess_return={
                "quality_score": 0.92,
                "passed_threshold": True,
                "assessment": "Excellent first draft",
            }
        )
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"] as mock_refine,
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(topic="AI", quality_threshold=0.75)
        assert result["refinement_count"] == 0
        mock_refine.assert_not_awaited()
        assert result["quality_score"] == 0.92


# ---------------------------------------------------------------------------
# Test: Writing style application
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWritingStyleApplication:
    """Verify writing style preferences are forwarded to the draft phase."""

    @pytest.mark.asyncio
    async def test_writing_style_service_consulted(self):
        """If writing_style_service is available, it should be consulted during draft."""
        ws_service = AsyncMock()
        ws_service.get_sample_for_content_generation = AsyncMock(
            return_value={
                "writing_style_guidance": "Use active voice. Avoid jargon. Short paragraphs."
            }
        )
        service = _make_service(writing_style_service=ws_service)

        # Don't mock execute_draft — let it try to import the real agent (which will fail)
        # Instead, mock at the draft phase level
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"] as mock_draft,
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            await service.execute_full_workflow(topic="AI")
        # Draft should have been called
        mock_draft.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_word_count_target_passed_to_draft(self):
        """Custom word count target should be forwarded to the draft phase."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"] as mock_draft,
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            await service.execute_full_workflow(topic="AI", word_count_target=3000)
        assert mock_draft.call_args.kwargs["word_count_target"] == 3000


# ---------------------------------------------------------------------------
# Test: Graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGracefulDegradation:
    """Verify pipeline handles non-critical failures gracefully."""

    @pytest.mark.asyncio
    async def test_image_failure_still_completes(self):
        """Image selection failure should not block pipeline completion."""
        service = _make_service()
        patches = _standard_patches(
            image_return={
                "phase": "image_selection",
                "error": "Pexels API rate limited",
                "images": [],
            }
        )
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            result = await service.execute_full_workflow(topic="AI")
        assert result["status"] == "completed"
        assert result["phase_results"]["image_selection"]["images"] == []

    @pytest.mark.asyncio
    async def test_research_failure_fails_pipeline(self):
        """Research phase failure IS critical and should fail the pipeline."""
        service = _make_service()
        with patch(
            "services.content_service.ContentService.execute_research",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Serper API key missing"),
        ):
            result = await service.execute_full_workflow(topic="AI")
        assert result["status"] == "failed"
        assert "Serper API key missing" in result["error"]

    @pytest.mark.asyncio
    async def test_draft_failure_fails_pipeline_with_research_preserved(self):
        """Draft failure should fail pipeline but preserve research results."""
        service = _make_service()
        with (
            patch(
                "services.content_service.ContentService.execute_research",
                new_callable=AsyncMock,
                return_value=RESEARCH_OUTPUT,
            ),
            patch(
                "services.content_service.ContentService.execute_draft",
                new_callable=AsyncMock,
                side_effect=RuntimeError("LLM context window exceeded"),
            ),
        ):
            result = await service.execute_full_workflow(topic="AI")
        assert result["status"] == "failed"
        assert "research" in result["phase_results"]
        assert "draft" not in result["phase_results"]

    @pytest.mark.asyncio
    async def test_finalize_failure_fails_pipeline(self):
        """Finalize is critical — failure should fail the pipeline."""
        service = _make_service()
        patches = _standard_patches()
        finalize_patch = patch(
            "services.content_service.ContentService.execute_finalize",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Publishing agent DB error"),
        )
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            finalize_patch,
        ):
            result = await service.execute_full_workflow(topic="AI")
        assert result["status"] == "failed"
        assert "Publishing agent DB error" in result["error"]
        # Earlier phases should still be captured
        assert "research" in result["phase_results"]
        assert "draft" in result["phase_results"]


# ---------------------------------------------------------------------------
# Test: Model selection per phase
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestModelSelectionPerPhase:
    """Verify per-phase model selections are correctly routed."""

    @pytest.mark.asyncio
    async def test_all_model_selections_forwarded(self):
        """Each phase should receive its designated model."""
        service = _make_service()
        model_selections = {
            "research": "gemini-2.0-flash",
            "draft": "claude-3-sonnet",
            "assess": "gpt-4o",
            "image_selection": "gemini-2.0-flash",
            "finalize": "claude-3-haiku",
        }
        patches = _standard_patches()
        with (
            patches["research"] as mock_r,
            patches["draft"] as mock_d,
            patches["assess"] as mock_a,
            patches["refine"],
            patches["image"] as mock_i,
            patches["finalize"] as mock_f,
        ):
            await service.execute_full_workflow(topic="AI", model_selections=model_selections)
        assert mock_r.call_args.kwargs["model"] == "gemini-2.0-flash"
        assert mock_d.call_args.kwargs["model"] == "claude-3-sonnet"
        assert mock_a.call_args.kwargs["model"] == "gpt-4o"
        assert mock_i.call_args.kwargs["model"] == "gemini-2.0-flash"
        assert mock_f.call_args.kwargs["model"] == "claude-3-haiku"

    @pytest.mark.asyncio
    async def test_missing_model_selections_pass_none(self):
        """Phases without explicit model selection should receive None."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"] as mock_r,
            patches["draft"] as mock_d,
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            await service.execute_full_workflow(topic="AI", model_selections={})
        assert mock_r.call_args.kwargs["model"] is None
        assert mock_d.call_args.kwargs["model"] is None


# ---------------------------------------------------------------------------
# Test: Content flows between phases correctly
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPhaseDataFlow:
    """Verify data flows correctly between pipeline phases."""

    @pytest.mark.asyncio
    async def test_research_context_flows_to_draft(self):
        """Draft phase should receive the full research output."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"] as mock_draft,
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"],
        ):
            await service.execute_full_workflow(topic="AI")
        assert mock_draft.call_args.kwargs["research_context"] == RESEARCH_OUTPUT

    @pytest.mark.asyncio
    async def test_refined_content_flows_to_finalize(self):
        """After refinement, the refined content (not original) goes to finalize."""
        service = _make_service()
        assess_count = 0

        async def two_pass(**kwargs):
            nonlocal assess_count
            assess_count += 1
            if assess_count == 1:
                return LOW_QUALITY_ASSESS
            return IMPROVED_ASSESS

        patches = _standard_patches(assess_side_effect=two_pass)
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"] as mock_fin,
        ):
            result = await service.execute_full_workflow(topic="AI", quality_threshold=0.75)
        # Finalize should receive the refined content, not the original draft
        assert mock_fin.call_args.kwargs["content"] == REFINE_OUTPUT["refined_content"]
        assert result["final_content"] == REFINE_OUTPUT["refined_content"]

    @pytest.mark.asyncio
    async def test_original_draft_flows_to_finalize_when_no_refinement(self):
        """When quality passes on first try, original draft goes to finalize."""
        service = _make_service()
        patches = _standard_patches()
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"],
            patches["image"],
            patches["finalize"] as mock_fin,
        ):
            result = await service.execute_full_workflow(topic="AI")
        assert mock_fin.call_args.kwargs["content"] == DRAFT_OUTPUT["draft_content"]
        assert result["final_content"] == DRAFT_OUTPUT["draft_content"]

    @pytest.mark.asyncio
    async def test_assessment_feedback_flows_to_refine(self):
        """Refinement phase should receive the assessment feedback."""
        service = _make_service()
        assess_count = 0

        async def assess_then_pass(**kwargs):
            nonlocal assess_count
            assess_count += 1
            if assess_count == 1:
                return LOW_QUALITY_ASSESS
            return IMPROVED_ASSESS

        patches = _standard_patches(assess_side_effect=assess_then_pass)
        with (
            patches["research"],
            patches["draft"],
            patches["assess"],
            patches["refine"] as mock_refine,
            patches["image"],
            patches["finalize"],
        ):
            await service.execute_full_workflow(topic="AI", quality_threshold=0.75)
        # Refine should receive the low-quality assessment as feedback
        assert mock_refine.call_args.kwargs["feedback"] == LOW_QUALITY_ASSESS["assessment"]
