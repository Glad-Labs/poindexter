"""
Unit tests for services/unified_orchestrator.py

Covers:
- Dataclass construction: Request, ExecutionContext, ExecutionResult, to_dict()
- UnifiedOrchestrator.__init__: attributes, agent registry, statistics
- UnifiedOrchestrator._get_system_info: success rate, agent list
- UnifiedOrchestrator._result_to_dict: field mapping
- UnifiedOrchestrator._extract_content_params: structured format parsing,
  unstructured fallback, style/tone detection, default values
- UnifiedOrchestrator._store_execution_result: no-op when no db, error swallowed
- ExecutionStatus and RequestType enum values
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from services.unified_orchestrator import (
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    Request,
    RequestType,
    UnifiedOrchestrator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orchestrator(**kwargs):
    return UnifiedOrchestrator(**kwargs)


def _make_result(
    request_id="req-001",
    request_type=RequestType.CONTENT_CREATION,
    status=ExecutionStatus.COMPLETED,
    output="Generated blog post",
    task_id="task-abc",
    quality_score=8.5,
    duration_ms=1200.0,
) -> ExecutionResult:
    return ExecutionResult(
        request_id=request_id,
        request_type=request_type,
        status=status,
        output=output,
        task_id=task_id,
        quality_score=quality_score,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnums:
    def test_request_type_values(self):
        assert RequestType.CONTENT_CREATION == "content_creation"
        assert RequestType.FINANCIAL_ANALYSIS == "financial_analysis"
        assert RequestType.TASK_MANAGEMENT == "task_management"
        assert RequestType.SYSTEM_OPERATION == "system_operation"

    def test_execution_status_values(self):
        assert ExecutionStatus.PENDING == "pending"
        assert ExecutionStatus.COMPLETED == "completed"
        assert ExecutionStatus.FAILED == "failed"
        assert ExecutionStatus.CANCELLED == "cancelled"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestDataclass:
    def test_construction(self):
        req = Request(
            request_id="req-1",
            original_text="Write a blog post about AI",
            request_type=RequestType.CONTENT_CREATION,
            extracted_intent="create_content",
        )
        assert req.request_id == "req-1"
        assert req.request_type == RequestType.CONTENT_CREATION
        assert req.parameters == {}
        assert req.context == {}
        assert req.user_id is None

    def test_user_id_defaults_none(self):
        req = Request(
            request_id="r",
            original_text="test",
            request_type=RequestType.SYSTEM_OPERATION,
            extracted_intent="status",
        )
        assert req.user_id is None

    def test_created_at_defaults_to_utc_now(self):
        req = Request(
            request_id="r",
            original_text="test",
            request_type=RequestType.SYSTEM_OPERATION,
            extracted_intent="status",
        )
        assert req.created_at.tzinfo is not None


@pytest.mark.unit
class TestExecutionResultDataclass:
    def test_construction_with_required_fields(self):
        result = _make_result()
        assert result.request_id == "req-001"
        assert result.status == ExecutionStatus.COMPLETED
        assert result.output == "Generated blog post"

    def test_to_dict_contains_all_fields(self):
        result = _make_result()
        d = result.to_dict()
        assert d["request_id"] == "req-001"
        assert d["request_type"] == "content_creation"
        assert d["status"] == "completed"
        assert d["output"] == "Generated blog post"
        assert d["task_id"] == "task-abc"
        assert d["quality_score"] == 8.5
        assert d["duration_ms"] == 1200.0
        assert "created_at" in d

    def test_to_dict_created_at_is_isoformat(self):
        result = _make_result()
        d = result.to_dict()
        # Should be parseable as ISO datetime
        dt = datetime.fromisoformat(d["created_at"])
        assert isinstance(dt, datetime)

    def test_defaults(self):
        result = ExecutionResult(
            request_id="r",
            request_type=RequestType.TASK_MANAGEMENT,
            status=ExecutionStatus.PENDING,
            output=None,
        )
        assert result.task_id is None
        assert result.quality_score is None
        assert result.duration_ms == 0
        assert result.cost_usd == 0.0
        assert result.refinement_attempts == 0
        assert result.metadata == {}


# ---------------------------------------------------------------------------
# UnifiedOrchestrator.__init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnifiedOrchestratorInit:
    def test_default_init(self):
        orch = _make_orchestrator()
        assert orch.database_service is None
        assert orch.model_router is None
        assert orch.quality_service is None
        assert orch.memory_system is None
        assert orch.total_requests == 0
        assert orch.successful_requests == 0
        assert orch.failed_requests == 0
        assert orch.agents == {}

    def test_init_with_services(self):
        db = MagicMock()
        router = MagicMock()
        orch = _make_orchestrator(database_service=db, model_router=router)
        assert orch.database_service is db
        assert orch.model_router is router

    def test_init_with_agents(self):
        agent_a = MagicMock()
        agent_b = MagicMock()
        orch = _make_orchestrator(content_agent=agent_a, compliance_agent=agent_b)
        assert "content_agent" in orch.agents
        assert "compliance_agent" in orch.agents


# ---------------------------------------------------------------------------
# UnifiedOrchestrator._get_system_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSystemInfo:
    def test_initial_state(self):
        orch = _make_orchestrator()
        info = orch._get_system_info()
        assert info["status"] == "operational"
        assert info["total_requests"] == 0
        assert info["successful_requests"] == 0
        assert info["failed_requests"] == 0
        assert info["success_rate"] == 0

    def test_success_rate_with_requests(self):
        orch = _make_orchestrator()
        orch.total_requests = 10
        orch.successful_requests = 8
        orch.failed_requests = 2
        info = orch._get_system_info()
        assert info["success_rate"] == 80.0

    def test_available_agents_listed(self):
        agent_a = MagicMock()
        orch = _make_orchestrator(content_agent=agent_a)
        info = orch._get_system_info()
        assert "content_agent" in info["available_agents"]

    def test_no_division_by_zero_when_no_requests(self):
        orch = _make_orchestrator()
        orch.total_requests = 0
        info = orch._get_system_info()
        assert info["success_rate"] == 0


# ---------------------------------------------------------------------------
# UnifiedOrchestrator._result_to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResultToDict:
    def test_basic_mapping(self):
        orch = _make_orchestrator()
        result = _make_result()
        d = orch._result_to_dict(result)
        assert d["request_id"] == "req-001"
        assert d["request_type"] == "content_creation"
        assert d["status"] == "completed"
        assert d["output"] == "Generated blog post"
        assert d["task_id"] == "task-abc"
        assert d["quality_score"] == 8.5
        assert d["duration_ms"] == 1200.0

    def test_none_fields_preserved(self):
        orch = _make_orchestrator()
        result = ExecutionResult(
            request_id="r",
            request_type=RequestType.SYSTEM_OPERATION,
            status=ExecutionStatus.FAILED,
            output=None,
        )
        d = orch._result_to_dict(result)
        assert d["output"] is None
        assert d["task_id"] is None
        assert d["quality_score"] is None


# ---------------------------------------------------------------------------
# UnifiedOrchestrator._extract_content_params
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractContentParams:
    """_extract_content_params parses structured and unstructured formats."""

    def test_parses_structured_format(self):
        orch = _make_orchestrator()
        text = (
            "Topic: Machine Learning in Healthcare\n"
            "Primary Keyword: ML healthcare\n"
            "Target Audience: medical professionals\n"
            "Category: technology\n"
            "Style: professional\n"
            "Tone: informative\n"
            "Target Length: 1500 words"
        )
        params = orch._extract_content_params(text)
        assert params["topic"] == "Machine Learning in Healthcare"
        assert params["primary_keyword"] == "ML healthcare"
        assert params["target_audience"] == "medical professionals"
        assert params["category"] == "technology"
        assert params["style"] == "professional"
        assert params["tone"] == "informative"
        assert params["target_length"] == 1500

    def test_structured_format_defaults_style_when_missing(self):
        orch = _make_orchestrator()
        text = "Topic: AI Revolution\n"
        params = orch._extract_content_params(text)
        assert params["topic"] == "AI Revolution"
        assert params["style"] == "professional"

    def test_structured_format_defaults_tone_when_missing(self):
        orch = _make_orchestrator()
        text = "Topic: Cloud Computing\n"
        params = orch._extract_content_params(text)
        assert params["tone"] == "informative"

    def test_unstructured_fallback_uses_full_text_as_topic(self):
        orch = _make_orchestrator()
        text = "Write a blog post about Python decorators"
        params = orch._extract_content_params(text)
        assert params["topic"] == text

    def test_unstructured_detects_professional_style(self):
        orch = _make_orchestrator()
        params = orch._extract_content_params("A professional guide to Docker")
        assert params["style"] == "professional"

    def test_unstructured_detects_casual_style(self):
        orch = _make_orchestrator()
        params = orch._extract_content_params("A casual introduction to Python")
        assert params["style"] == "casual"

    def test_unstructured_detects_technical_style(self):
        orch = _make_orchestrator()
        params = orch._extract_content_params("A technical deep-dive into async programming")
        assert params["style"] == "technical"

    def test_unstructured_defaults_professional_style(self):
        orch = _make_orchestrator()
        params = orch._extract_content_params("Write about machine learning")
        assert params["style"] == "professional"

    def test_unstructured_detects_educational_tone(self):
        orch = _make_orchestrator()
        params = orch._extract_content_params("An educational overview of ML algorithms")
        assert params["tone"] == "educational"

    def test_unstructured_detects_entertaining_tone(self):
        orch = _make_orchestrator()
        params = orch._extract_content_params("An entertaining look at programming history")
        assert params["tone"] == "entertaining"

    def test_unstructured_defaults_informative_tone(self):
        orch = _make_orchestrator()
        params = orch._extract_content_params("Overview of cloud databases")
        assert params["tone"] == "informative"

    def test_target_length_invalid_value_skipped(self):
        orch = _make_orchestrator()
        text = "Topic: AI\nTarget Length: not-a-number"
        params = orch._extract_content_params(text)
        assert "target_length" not in params

    def test_empty_string_returns_params(self):
        orch = _make_orchestrator()
        params = orch._extract_content_params("")
        assert isinstance(params, dict)


# ---------------------------------------------------------------------------
# UnifiedOrchestrator._store_execution_result
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStoreExecutionResult:
    @pytest.mark.asyncio
    async def test_noop_when_no_database_service(self):
        """Should return without error when database_service is None."""
        orch = _make_orchestrator()
        result = _make_result()
        ret = await orch._store_execution_result(result)
        assert ret is None

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self):
        """Exceptions from DB operations should be swallowed."""
        db = AsyncMock()
        db.store = AsyncMock(side_effect=Exception("DB error"))
        orch = _make_orchestrator(database_service=db)
        result = _make_result()
        ret = await orch._store_execution_result(result)
        assert ret is None


# ---------------------------------------------------------------------------
# UnifiedOrchestrator._handle_content_creation
# ---------------------------------------------------------------------------

# Module-level patch paths (where each symbol is used/imported inside the function)
_CONSTRAINT_UTILS = "utils.constraint_utils"
_WRITING_STYLE_INTEGRATION = "services.writing_style_integration"
_LLM_CLIENT_MOD = "agents.content_agent.services.llm_client"
_BLOG_POST_MOD = "agents.content_agent.utils.data_models"
_DB_SERVICE_MOD = "services.database_service"
_QUALITY_SVC_MOD = "services.quality_service"
_IMAGE_SVC_MOD = "services.image_service"
# emit_task_progress is imported at module level in unified_orchestrator, so patch it there
_EMIT_PROGRESS = "services.unified_orchestrator.emit_task_progress"


def _make_mock_compliance(within_tolerance: bool = True):
    """Return a MagicMock that looks like a ConstraintCompliance object."""
    c = MagicMock()
    c.word_count_actual = 1500
    c.word_count_target = 1500
    c.word_count_within_tolerance = within_tolerance
    c.word_count_percentage = 100.0
    c.writing_style_applied = "professional"
    c.strict_mode_enforced = False
    c.violation_message = None if within_tolerance else "Word count out of tolerance"
    return c


def _make_mock_quality_result(passing: bool = True, score: int = 85):
    """Return a MagicMock that looks like a QualityAssessment."""
    q = MagicMock()
    q.passing = passing
    q.overall_score = score
    q.feedback = "Looks good" if passing else "Needs improvement"
    return q


def _make_content_request(
    topic: str = "AI in Healthcare",
    request_id: str = "req-test-001",
    context: dict | None = None,
) -> Request:
    return Request(
        request_id=request_id,
        original_text=topic,
        request_type=RequestType.CONTENT_CREATION,
        extracted_intent="content_creation",
        parameters={"topic": topic, "style": "professional", "tone": "informative"},
        context=context or {},
    )


def _build_patch_context(
    constraint_compliance=None,
    merged_compliance=None,
    strict_valid: bool = True,
    quality_passing: bool = True,
    quality_score: int = 85,
    image_url: str | None = "https://example.com/image.jpg",
):
    """
    Build a dict of patch arguments so tests can selectively override defaults.
    All patches target the symbols as they are resolved at call time inside
    the deferred imports within _handle_content_creation.
    """
    compliance = constraint_compliance or _make_mock_compliance()
    merged = merged_compliance or _make_mock_compliance()
    return {
        "compliance": compliance,
        "merged": merged,
        "strict_valid": strict_valid,
        "quality_passing": quality_passing,
        "quality_score": quality_score,
        "image_url": image_url,
    }


@pytest.mark.unit
class TestHandleContentCreation:
    """
    Unit tests for UnifiedOrchestrator._handle_content_creation.

    Strategy: mock all 8 external dependencies via patch() so that no real
    LLM/DB/network calls are made. Each test exercises a specific behavioural
    variant (happy path, QA rejection+refinement, image failure, timeout, etc.).
    """

    def _patch_all(
        self,
        *,
        constraint_compliance=None,
        merged_compliance=None,
        strict_valid: bool = True,
        quality_passing: bool = True,
        quality_score: int = 85,
        image_url: str | None = "https://example.com/img.jpg",
        research_result: str = "Research data about AI",
        draft_body: str = "Draft blog post content about AI",
        formatted_content: str = "Formatted content",
        meta_description: str = "An article about AI",
    ):
        """
        Return a context-manager stack that patches all external dependencies.
        Returns a dict-like namespace via `patches` so test code can introspect
        specific mocks.
        """
        compliance = constraint_compliance or _make_mock_compliance()
        merged = merged_compliance or _make_mock_compliance()

        # Build mock objects
        mock_draft_post = MagicMock()
        mock_draft_post.body = draft_body
        mock_draft_post.raw_content = formatted_content
        mock_draft_post.meta_description = meta_description

        mock_research_agent = AsyncMock()
        mock_research_agent.run = AsyncMock(return_value=research_result)

        mock_creative_agent = AsyncMock()
        mock_creative_agent.run = AsyncMock(return_value=mock_draft_post)

        mock_publishing_agent = AsyncMock()
        mock_publishing_agent.run = AsyncMock(return_value=mock_draft_post)

        mock_quality_result = _make_mock_quality_result(quality_passing, quality_score)

        mock_quality_service = AsyncMock()
        mock_quality_service.evaluate = AsyncMock(return_value=mock_quality_result)

        mock_featured_image = MagicMock()
        mock_featured_image.url = image_url
        mock_image_service = AsyncMock()
        mock_image_service.search_featured_image = AsyncMock(
            return_value=mock_featured_image if image_url else None
        )

        def _get_agent_side_effect(agent_name, **kwargs):
            if agent_name == "research_agent":
                return mock_research_agent
            if agent_name == "creative_agent":
                return mock_creative_agent
            if agent_name == "publishing_agent":
                return mock_publishing_agent
            raise ValueError(f"Unknown agent: {agent_name}")

        return {
            "draft_post": mock_draft_post,
            "research_agent": mock_research_agent,
            "creative_agent": mock_creative_agent,
            "publishing_agent": mock_publishing_agent,
            "quality_service": mock_quality_service,
            "image_service": mock_image_service,
            "compliance": compliance,
            "merged": merged,
            "strict_valid": strict_valid,
            "_get_agent_side_effect": _get_agent_side_effect,
        }

    @pytest.mark.asyncio
    async def test_happy_path_returns_pending_approval(self):
        """Full pipeline completes and returns PENDING_APPROVAL status."""
        mocks = self._patch_all()
        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints") as MockConstraints,
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={"research": 300, "creative": 1200, "qa": 1200}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1500),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="content")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value="test-model"),
        ):
            mock_wsi_instance = AsyncMock()
            mock_wsi_instance.get_sample_for_content_generation = AsyncMock(return_value=None)
            MockWSI.return_value = mock_wsi_instance

            result = await orch._handle_content_creation(request)

        assert isinstance(result, ExecutionResult)
        assert result.status == ExecutionStatus.PENDING_APPROVAL
        assert result.task_id is not None
        assert result.task_id.startswith("task_")
        assert "awaiting_approval" in result.output["status"]

    @pytest.mark.asyncio
    async def test_result_output_contains_all_keys(self):
        """Output dict must contain all required keys for the approval workflow."""
        mocks = self._patch_all()
        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1000),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            result = await orch._handle_content_creation(request)

        output = result.output
        required_keys = [
            "task_id", "status", "approval_status", "content",
            "excerpt", "featured_image_url", "qa_feedback",
            "quality_score", "constraint_compliance", "message", "next_action",
        ]
        for key in required_keys:
            assert key in output, f"Missing key in output: {key}"

    @pytest.mark.asyncio
    async def test_research_timeout_continues_with_empty_research(self):
        """When research agent times out, pipeline continues with empty research data."""
        mocks = self._patch_all()
        # Override research agent to raise TimeoutError
        mocks["research_agent"].run = AsyncMock(side_effect=asyncio.TimeoutError())

        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=100),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            # Should NOT raise — pipeline degrades gracefully
            result = await orch._handle_content_creation(request)

        assert result.status == ExecutionStatus.PENDING_APPROVAL

    @pytest.mark.asyncio
    async def test_creative_agent_timeout_raises_and_returns_failed(self):
        """When creative agent times out (> 120s), function returns FAILED status."""
        mocks = self._patch_all()
        # Simulate asyncio.wait_for raising TimeoutError for the creative draft
        mocks["creative_agent"].run = AsyncMock(side_effect=asyncio.TimeoutError())

        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=100),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            result = await orch._handle_content_creation(request)

        # Pipeline wraps the timeout in RuntimeError, which the outer except catches
        assert result.status == ExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_image_failure_does_not_abort_pipeline(self):
        """Image service failure should be caught and pipeline completes with no image."""
        mocks = self._patch_all(image_url=None)
        mocks["image_service"].search_featured_image = AsyncMock(
            side_effect=Exception("Pexels API down")
        )

        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1000),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            result = await orch._handle_content_creation(request)

        assert result.status == ExecutionStatus.PENDING_APPROVAL
        assert result.output["featured_image_url"] is None

    @pytest.mark.asyncio
    async def test_qa_rejection_triggers_refinement(self):
        """When QA fails on first iteration, creative agent is called again for refinement."""
        # First QA call fails, second passes
        mock_qa_fail = _make_mock_quality_result(passing=False, score=50)
        mock_qa_pass = _make_mock_quality_result(passing=True, score=85)

        mocks = self._patch_all(quality_passing=False)
        mocks["quality_service"].evaluate = AsyncMock(
            side_effect=[mock_qa_fail, mock_qa_pass]
        )

        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={"creative": 1200}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1200),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            result = await orch._handle_content_creation(request)

        assert result.status == ExecutionStatus.PENDING_APPROVAL
        # creative_agent.run must have been called at least twice (initial + refinement)
        assert mocks["creative_agent"].run.call_count >= 2

    @pytest.mark.asyncio
    async def test_writing_style_failure_does_not_abort_pipeline(self):
        """A writing-style lookup exception should be logged and pipeline continues."""
        mocks = self._patch_all()

        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1000),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            # Writing-style lookup raises an exception
            mock_wsi_instance = AsyncMock()
            mock_wsi_instance.get_sample_for_content_generation = AsyncMock(
                side_effect=Exception("DB connection failed")
            )
            MockWSI.return_value = mock_wsi_instance
            result = await orch._handle_content_creation(request)

        assert result.status == ExecutionStatus.PENDING_APPROVAL

    @pytest.mark.asyncio
    async def test_strict_mode_violation_logged_but_pipeline_completes(self):
        """STRICT MODE violation is logged but does not abort the pipeline."""
        mocks = self._patch_all(strict_valid=False)

        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=800),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(False, "Word count exceeded")),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            result = await orch._handle_content_creation(request)

        # Pipeline still returns PENDING_APPROVAL despite strict mode violation
        assert result.status == ExecutionStatus.PENDING_APPROVAL

    @pytest.mark.asyncio
    async def test_task_id_format(self):
        """Generated task_id must start with 'task_' and contain a hex suffix."""
        import re

        mocks = self._patch_all()
        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1000),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            result = await orch._handle_content_creation(request)

        assert result.task_id is not None
        assert re.match(r"^task_\d+_[0-9a-f]{6}$", result.task_id)

    @pytest.mark.asyncio
    async def test_progress_events_emitted_for_all_five_stages(self):
        """emit_task_progress must be called at least 5 times (once per stage)."""
        mocks = self._patch_all()
        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1000),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock) as mock_emit,
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            await orch._handle_content_creation(request)

        # 5 stages × 1 emit each
        assert mock_emit.call_count >= 5

    @pytest.mark.asyncio
    async def test_emit_failure_does_not_abort_pipeline(self):
        """If emit_task_progress raises, the pipeline should continue."""
        mocks = self._patch_all()
        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1000),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock, side_effect=Exception("WebSocket down")),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value=None),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            result = await orch._handle_content_creation(request)

        assert result.status == ExecutionStatus.PENDING_APPROVAL

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_failed_status(self):
        """Any unhandled exception in the pipeline returns ExecutionStatus.FAILED."""
        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints", side_effect=RuntimeError("unexpected")),
            patch.object(orch, "_get_agent_instance"),
        ):
            result = await orch._handle_content_creation(request)

        assert result.status == ExecutionStatus.FAILED
        assert result.request_id == request.request_id

    @pytest.mark.asyncio
    async def test_context_model_selections_used(self):
        """Model selections from request context are forwarded to _get_model_for_phase."""
        mocks = self._patch_all()
        orch = _make_orchestrator()
        request = _make_content_request(
            context={
                "model_selections": '{"draft": "gpt-4", "refine": "gpt-3.5-turbo"}',
                "quality_preference": "quality",
            }
        )

        get_model_mock = MagicMock(return_value="gpt-4")

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1000),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="body")),
            patch(f"{_DB_SERVICE_MOD}.DatabaseService"),
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", get_model_mock),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            result = await orch._handle_content_creation(request)

        assert result.status == ExecutionStatus.PENDING_APPROVAL
        # _get_model_for_phase should have been called (at minimum for the draft phase)
        assert get_model_mock.call_count >= 1
        # Verify quality_preference was passed correctly
        call_args_list = get_model_mock.call_args_list
        quality_prefs = [call.args[2] if len(call.args) >= 3 else call.kwargs.get("quality_preference") for call in call_args_list]
        assert any(qp == "quality" for qp in quality_prefs)

    @pytest.mark.asyncio
    async def test_no_new_database_service_created_per_request(self):
        """
        Regression test for issue #783: _handle_content_creation must NOT instantiate
        a new DatabaseService() each call. It must reuse self.database_service so that
        the application-level connection pool is not exhausted under concurrent load.
        """
        mocks = self._patch_all()
        orch = _make_orchestrator()
        request = _make_content_request()

        with (
            patch(f"{_CONSTRAINT_UTILS}.ContentConstraints"),
            patch(f"{_CONSTRAINT_UTILS}.calculate_phase_targets", return_value={}),
            patch(f"{_CONSTRAINT_UTILS}.count_words_in_content", return_value=1000),
            patch(f"{_CONSTRAINT_UTILS}.validate_constraints", return_value=mocks["compliance"]),
            patch(f"{_CONSTRAINT_UTILS}.merge_compliance_reports", return_value=mocks["merged"]),
            patch(f"{_CONSTRAINT_UTILS}.apply_strict_mode", return_value=(True, None)),
            patch(f"{_WRITING_STYLE_INTEGRATION}.WritingStyleIntegrationService") as MockWSI,
            patch(f"{_LLM_CLIENT_MOD}.LLMClient"),
            patch(f"{_BLOG_POST_MOD}.BlogPost", return_value=MagicMock(body="content")),
            patch(
                f"{_DB_SERVICE_MOD}.DatabaseService"
            ) as MockDatabaseService,
            patch(f"{_QUALITY_SVC_MOD}.get_content_quality_service", return_value=mocks["quality_service"]),
            patch(f"{_IMAGE_SVC_MOD}.get_image_service", return_value=mocks["image_service"]),
            patch(_EMIT_PROGRESS, new_callable=AsyncMock),
            patch.object(orch, "_get_agent_instance", side_effect=mocks["_get_agent_side_effect"]),
            patch.object(orch, "_get_model_for_phase", return_value="test-model"),
        ):
            MockWSI.return_value.get_sample_for_content_generation = AsyncMock(return_value=None)
            await orch._handle_content_creation(request)

        # DatabaseService() constructor must NOT have been called — the orchestrator
        # must reuse self.database_service (injected at startup) rather than opening
        # a new connection pool on every content generation request.
        MockDatabaseService.assert_not_called()
