"""
Unit tests for command_schemas.py and subtask_schemas.py

Tests field validation and model behaviour for command queue and subtask schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.command_schemas import (
    CommandErrorRequest,
    CommandListResponse,
    CommandRequest,
    CommandResponse,
    CommandResultRequest,
)
from schemas.subtask_schemas import (
    CreativeSubtaskRequest,
    FormatSubtaskRequest,
    ImageSubtaskRequest,
    QASubtaskRequest,
    ResearchSubtaskRequest,
    SubtaskResponse,
)

# ---------------------------------------------------------------------------
# CommandRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCommandRequest:
    def test_valid(self):
        req = CommandRequest(agent_type="content_agent", action="generate")
        assert req.payload is None

    def test_with_payload(self):
        req = CommandRequest(
            agent_type="content_agent",
            action="generate",
            payload={"topic": "AI trends"},
        )
        assert req.payload == {"topic": "AI trends"}

    def test_missing_agent_type_raises(self):
        with pytest.raises(ValidationError):
            CommandRequest(action="generate")  # type: ignore[call-arg]

    def test_missing_action_raises(self):
        with pytest.raises(ValidationError):
            CommandRequest(agent_type="content_agent")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CommandResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCommandResponse:
    def _valid(self, **kwargs):
        defaults = {
            "id": "cmd-123",
            "agent_type": "content_agent",
            "action": "generate",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        defaults.update(kwargs)
        return CommandResponse(**defaults)  # type: ignore[arg-type]

    def test_valid_minimal(self):
        cmd = self._valid()
        assert cmd.result is None
        assert cmd.error is None
        assert cmd.started_at is None
        assert cmd.completed_at is None

    def test_with_result(self):
        cmd = self._valid(
            status="completed",
            result={"content": "generated text"},
            completed_at="2026-01-01T00:05:00Z",
        )
        assert cmd.result == {"content": "generated text"}
        assert cmd.completed_at == "2026-01-01T00:05:00Z"

    def test_with_error(self):
        cmd = self._valid(status="failed", error="Connection timeout")
        assert cmd.error == "Connection timeout"


# ---------------------------------------------------------------------------
# CommandListResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCommandListResponse:
    def _make_cmd(self, cmd_id="cmd-1"):
        return CommandResponse(
            id=cmd_id,
            agent_type="content_agent",
            action="generate",
            status="pending",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )

    def test_valid(self):
        resp = CommandListResponse(commands=[self._make_cmd()], total=1)
        assert resp.total == 1
        assert resp.status_filter is None

    def test_with_filter(self):
        resp = CommandListResponse(
            commands=[self._make_cmd()],
            total=1,
            status_filter="pending",
        )
        assert resp.status_filter == "pending"

    def test_empty(self):
        resp = CommandListResponse(commands=[], total=0)
        assert resp.total == 0


# ---------------------------------------------------------------------------
# CommandResultRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCommandResultRequest:
    def test_valid(self):
        req = CommandResultRequest(result={"output": "generated content"})
        assert req.result == {"output": "generated content"}

    def test_missing_result_raises(self):
        with pytest.raises(ValidationError):
            CommandResultRequest()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CommandErrorRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCommandErrorRequest:
    def test_valid_defaults(self):
        req = CommandErrorRequest(error="Something went wrong")
        assert req.retry is True

    def test_no_retry(self):
        req = CommandErrorRequest(error="Fatal error", retry=False)
        assert req.retry is False

    def test_missing_error_raises(self):
        with pytest.raises(ValidationError):
            CommandErrorRequest()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ResearchSubtaskRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResearchSubtaskRequest:
    def test_valid_minimal(self):
        req = ResearchSubtaskRequest(topic="Machine learning healthcare")  # type: ignore[call-arg]
        assert req.keywords == []
        assert req.parent_task_id is None

    def test_with_keywords(self):
        req = ResearchSubtaskRequest(  # type: ignore[call-arg]
            topic="Machine learning healthcare",
            keywords=["AI", "ML", "healthcare"],
        )
        assert req.keywords == ["AI", "ML", "healthcare"]

    def test_with_parent_task_id(self):
        req = ResearchSubtaskRequest(
            topic="AI trends",
            parent_task_id="task-parent-123",
        )
        assert req.parent_task_id == "task-parent-123"

    def test_missing_topic_raises(self):
        with pytest.raises(ValidationError):
            ResearchSubtaskRequest()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CreativeSubtaskRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreativeSubtaskRequest:
    def test_valid_minimal(self):
        req = CreativeSubtaskRequest(topic="AI in Finance")  # type: ignore[call-arg]
        assert req.style == "professional"
        assert req.tone == "informative"
        assert req.target_length == 2000
        assert req.research_output is None

    def test_with_research_output(self):
        req = CreativeSubtaskRequest(  # type: ignore[call-arg]
            topic="AI in Finance",
            research_output="Key findings: ...",
            style="technical",
            tone="professional",
        )
        assert req.research_output == "Key findings: ..."
        assert req.style == "technical"


# ---------------------------------------------------------------------------
# QASubtaskRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQASubtaskRequest:
    def test_valid_minimal(self):
        req = QASubtaskRequest(  # type: ignore[call-arg]
            topic="AI trends",
            creative_output="This is the generated blog post content...",
        )
        assert req.max_iterations == 2
        assert req.research_output is None

    def test_max_iterations_bounds(self):
        req = QASubtaskRequest(  # type: ignore[call-arg]
            topic="AI",
            creative_output="Content here",
            max_iterations=1,
        )
        assert req.max_iterations == 1

        req = QASubtaskRequest(  # type: ignore[call-arg]
            topic="AI",
            creative_output="Content here",
            max_iterations=5,
        )
        assert req.max_iterations == 5

    def test_max_iterations_too_low_raises(self):
        with pytest.raises(ValidationError):
            QASubtaskRequest(  # type: ignore[call-arg]
                topic="AI",
                creative_output="Content",
                max_iterations=0,
            )

    def test_max_iterations_too_high_raises(self):
        with pytest.raises(ValidationError):
            QASubtaskRequest(  # type: ignore[call-arg]
                topic="AI",
                creative_output="Content",
                max_iterations=6,
            )


# ---------------------------------------------------------------------------
# ImageSubtaskRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImageSubtaskRequest:
    def test_valid_minimal(self):
        req = ImageSubtaskRequest(topic="AI technology")  # type: ignore[call-arg]
        assert req.number_of_images == 1
        assert req.content is None

    def test_multiple_images(self):
        req = ImageSubtaskRequest(topic="AI", number_of_images=3)  # type: ignore[call-arg]
        assert req.number_of_images == 3

    def test_number_too_low_raises(self):
        with pytest.raises(ValidationError):
            ImageSubtaskRequest(topic="AI", number_of_images=0)  # type: ignore[call-arg]

    def test_number_too_high_raises(self):
        with pytest.raises(ValidationError):
            ImageSubtaskRequest(topic="AI", number_of_images=6)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# FormatSubtaskRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatSubtaskRequest:
    def test_valid_minimal(self):
        req = FormatSubtaskRequest(  # type: ignore[call-arg]
            topic="AI in Healthcare",
            content="# My Blog Post\n\nContent here...",
        )
        assert req.tags == []
        assert req.featured_image_url is None
        assert req.category is None

    def test_with_all_fields(self):
        req = FormatSubtaskRequest(  # type: ignore[call-arg]
            topic="AI",
            content="Content...",
            featured_image_url="https://images.pexels.com/photo.jpg",
            tags=["AI", "Tech"],
            category="technology",
        )
        assert len(req.tags) == 2
        assert req.category == "technology"


# ---------------------------------------------------------------------------
# SubtaskResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubtaskResponse:
    def test_valid(self):
        resp = SubtaskResponse(
            subtask_id="subtask-123",
            stage="research",
            parent_task_id="task-456",
            status="completed",
            result={"findings": "AI is growing"},
            metadata={"duration_ms": 1500, "tokens_used": 300},
        )
        assert resp.stage == "research"
        assert resp.status == "completed"

    def test_no_parent_task_id(self):
        resp = SubtaskResponse(
            subtask_id="subtask-789",
            stage="creative",
            parent_task_id=None,
            status="completed",
            result={"content": "blog post content"},
            metadata={},
        )
        assert resp.parent_task_id is None

    def test_failed_status(self):
        resp = SubtaskResponse(
            subtask_id="subtask-999",
            stage="qa",
            parent_task_id="task-001",
            status="failed",
            result={},
            metadata={"error": "LLM timeout"},
        )
        assert resp.status == "failed"
