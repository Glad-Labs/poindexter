"""
CI-runnable integration tests for blog workflow infrastructure.

These tests verify the blog workflow's phase definitions, executor wiring,
and workflow-schema validation WITHOUT requiring a live server or database.

Ported from test_blog_workflow.py (which requires INTEGRATION_TESTS=1 / live
server) to use pure-Python instantiation and AsyncMock patching.

See also: test_task_lifecycle.py for the full TestClient pattern.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BLOG_PHASES = [
    "blog_generate_content",
    "blog_quality_evaluation",
    "blog_search_image",
    "blog_create_post",
]

BLOG_AGENT_TYPES = [
    "blog_content_generator_agent",
    "blog_quality_agent",
    "blog_image_agent",
    "blog_publisher_agent",
]


# ---------------------------------------------------------------------------
# Phase registry tests (no server required)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBlogPhaseDefinitions:
    """All blog phases must be registered in PhaseRegistry."""

    def test_all_blog_phases_registered(self):
        """PhaseRegistry.get_instance() returns definitions for all 4 blog phases."""
        from services.phase_registry import PhaseRegistry

        registry = PhaseRegistry.get_instance()
        for phase_name in BLOG_PHASES:
            phase_def = registry.get_phase(phase_name)
            assert phase_def is not None, f"Phase '{phase_name}' not found in PhaseRegistry"

    def test_blog_phases_have_required_fields(self):
        """Each phase definition has agent_type, input_schema, output_schema, tags."""
        from services.phase_registry import PhaseRegistry

        registry = PhaseRegistry.get_instance()
        for phase_name in BLOG_PHASES:
            phase_def = registry.get_phase(phase_name)
            assert phase_def is not None
            assert hasattr(phase_def, "agent_type"), f"{phase_name} missing agent_type"
            assert hasattr(phase_def, "input_schema"), f"{phase_name} missing input_schema"
            assert hasattr(phase_def, "output_schema"), f"{phase_name} missing output_schema"
            assert hasattr(phase_def, "tags"), f"{phase_name} missing tags"

    def test_generate_content_phase_has_topic_input(self):
        """blog_generate_content phase has 'topic' in its input_schema."""
        from services.phase_registry import PhaseRegistry

        registry = PhaseRegistry.get_instance()
        phase_def = registry.get_phase("blog_generate_content")
        assert phase_def is not None
        assert "topic" in phase_def.input_schema, (
            "blog_generate_content.input_schema must include 'topic'"
        )

    def test_blog_phase_tags_contain_blog_keyword(self):
        """All blog phases have 'blog' in their tags list."""
        from services.phase_registry import PhaseRegistry

        registry = PhaseRegistry.get_instance()
        for phase_name in BLOG_PHASES:
            phase_def = registry.get_phase(phase_name)
            assert phase_def is not None
            tags = [t.lower() for t in (phase_def.tags or [])]
            assert any("blog" in t for t in tags), (
                f"Phase '{phase_name}' missing 'blog' tag (got: {phase_def.tags})"
            )


# ---------------------------------------------------------------------------
# WorkflowExecutor agent-loading tests (imports only, no LLM calls)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkflowExecutorAgentLoading:
    """WorkflowExecutor._get_agent() must resolve all 4 blog agent types."""

    def _make_mock_agent(self, name: str) -> MagicMock:
        agent = MagicMock()
        agent.__class__.__name__ = name
        agent.run = MagicMock()
        return agent

    def test_all_blog_agents_loadable(self):
        """_get_agent() returns a non-None object for each blog agent type."""
        from services.workflow_executor import WorkflowExecutor

        executor = WorkflowExecutor()
        for agent_type in BLOG_AGENT_TYPES:
            agent = executor._get_agent(agent_type)
            assert agent is not None, f"_get_agent('{agent_type}') returned None"

    def test_blog_agents_have_run_method(self):
        """Each resolved blog agent has a 'run' method."""
        from services.workflow_executor import WorkflowExecutor

        executor = WorkflowExecutor()
        for agent_type in BLOG_AGENT_TYPES:
            agent = executor._get_agent(agent_type)
            assert agent is not None
            assert hasattr(agent, "run"), (
                f"Agent '{agent_type}' (class {type(agent).__name__}) has no run() method"
            )

    def test_unknown_agent_type_returns_none(self):
        """_get_agent() with an unrecognised type returns None (does not raise)."""
        from services.workflow_executor import WorkflowExecutor

        executor = WorkflowExecutor()
        result = executor._get_agent("nonexistent_agent_xyz")
        assert result is None


# ---------------------------------------------------------------------------
# Workflow schema validation (pure Python)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBlogWorkflowSchema:
    """CustomWorkflow + WorkflowPhase schema accepts valid blog workflow definitions."""

    def test_workflow_schema_accepts_four_blog_phases(self):
        """A 4-phase blog workflow can be constructed without validation errors."""
        from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase

        workflow = CustomWorkflow(  # type: ignore[call-arg]
            name="Blog Post Generation",
            description="Complete blog post generation workflow",
            phases=[
                WorkflowPhase(  # type: ignore[call-arg]
                    index=0,
                    name="blog_generate_content",
                    user_inputs={
                        "topic": "Artificial Intelligence in Healthcare",
                        "style": "balanced",
                    },
                ),
                WorkflowPhase(  # type: ignore[call-arg]
                    index=1,
                    name="blog_quality_evaluation",
                    user_inputs={"evaluation_method": "pattern-based"},
                ),
                WorkflowPhase(  # type: ignore[call-arg]
                    index=2,
                    name="blog_search_image",
                    user_inputs={"image_count": 1},
                ),
                WorkflowPhase(  # type: ignore[call-arg]
                    index=3,
                    name="blog_create_post",
                    user_inputs={"publish": True},
                ),
            ],
        )

        assert workflow.name == "Blog Post Generation"
        assert len(workflow.phases) == 4
        assert [p.name for p in workflow.phases] == BLOG_PHASES

    def test_all_phases_in_registry(self):
        """All phases in the test workflow are registered in PhaseRegistry."""
        from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase
        from services.phase_registry import PhaseRegistry

        workflow = CustomWorkflow(  # type: ignore[call-arg]
            name="Blog Post Generation",
            description="Blog workflow",
            phases=[
                WorkflowPhase(index=i, name=name, user_inputs={})  # type: ignore[call-arg]
                for i, name in enumerate(BLOG_PHASES)
            ],
        )

        registry = PhaseRegistry.get_instance()
        for phase in workflow.phases:
            assert registry.get_phase(phase.name) is not None, (
                f"Phase '{phase.name}' not in registry"
            )
