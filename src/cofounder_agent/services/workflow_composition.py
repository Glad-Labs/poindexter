"""
Workflow Composition - Helper utilities for building workflows with dynamic agent selection

Provides:
- WorkflowBuilder: Fluent API for constructing workflows
- AgentPhase: Convenience wrapper for agent-based phases
- Content workflow builders: Pre-built workflows for common content types
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from agents.registry import get_agent_registry
from services.workflow_engine import WorkflowEngine, WorkflowPhase, WorkflowContext

logger = logging.getLogger(__name__)


async def create_agent_phase_handler(agent_name: str, phase_method: str = "run") -> Callable:
    """
    Create a phase handler that executes a registered agent.

    Args:
        agent_name: Name of the agent from AgentRegistry
        phase_method: Method name to call on the agent (default: "run")

    Returns:
        Async callable that can be used as a WorkflowPhase handler

    Example:
        ```python
        research_phase_handler = await create_agent_phase_handler("research_agent")
        research_phase = WorkflowPhase(
            name="research",
            handler=research_phase_handler,
            timeout_seconds=180
        )
        ```
    """

    async def phase_handler(context: WorkflowContext) -> Any:
        """Execute agent with input from workflow context"""
        try:
            registry = get_agent_registry()
            agent_class = registry.get_agent_class(agent_name)

            if not agent_class:
                raise ValueError(f"Agent '{agent_name}' not found in registry")

            # Instantiate agent
            agent = agent_class()

            # Get input from context (either accumulated_output or initial_input)
            phase_input = context.accumulated_output or context.initial_input

            # Call the phase method
            if hasattr(agent, phase_method):
                phase_method_callable = getattr(agent, phase_method)
                result = await phase_method_callable(phase_input)
                return result
            else:
                raise AttributeError(f"Agent '{agent_name}' has no method '{phase_method}'")

        except Exception as e:
            logger.error(f"Phase handler failed for agent '{agent_name}': {e}", exc_info=True)
            raise

    return phase_handler


class WorkflowBuilder:
    """
    Fluent builder for constructing workflows dynamically.

    Example:
        ```python
        builder = WorkflowBuilder()
        phases = (
            builder
            .add_agent_phase("research_agent", timeout=180, max_retries=2)
            .add_agent_phase("creative_agent", timeout=300, max_retries=3)
            .add_agent_phase("qa_agent", timeout=120, max_retries=2, skip_on_error=True)
            .build()
        )
        ```
    """

    def __init__(self):
        """Initialize workflow builder"""
        self.phases: List[WorkflowPhase] = []

    def add_phase(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        timeout_seconds: int = 300,
        max_retries: int = 3,
        skip_on_error: bool = False,
        required: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "WorkflowBuilder":
        """
        Add a phase to the workflow.

        Args:
            name: Phase name
            handler: Async callable that executes the phase
            description: Human-readable description
            timeout_seconds: Maximum execution time
            max_retries: Maximum retry attempts
            skip_on_error: Skip if previous phase failed
            required: Whether workflow fails if this phase fails
            metadata: Additional metadata

        Returns:
            Self for chaining
        """
        phase = WorkflowPhase(
            name=name,
            handler=handler,
            description=description,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            skip_on_error=skip_on_error,
            required=required,
            metadata=metadata or {},
        )
        self.phases.append(phase)
        return self

    def add_agent_phase(
        self,
        agent_name: str,
        phase_name: Optional[str] = None,
        timeout_seconds: int = 300,
        max_retries: int = 3,
        skip_on_error: bool = False,
        required: bool = True,
        agent_method: str = "run",
    ) -> "WorkflowBuilder":
        """
        Add a phase that executes a registered agent.

        Args:
            agent_name: Name of agent from AgentRegistry
            phase_name: Phase name (defaults to agent_name)
            timeout_seconds: Maximum execution time
            max_retries: Maximum retry attempts
            skip_on_error: Skip if previous phase failed
            required: Whether workflow fails if this phase fails
            agent_method: Method to call on agent

        Returns:
            Self for chaining
        """
        # Get agent metadata for description
        registry = get_agent_registry()
        agent_metadata = registry.get(agent_name)
        description = agent_metadata.get("description", "") if agent_metadata else ""

        # Create handler that will instantiate and call the agent
        async def agent_handler(context: WorkflowContext) -> Any:
            """Execute registered agent"""
            try:
                agent_class = registry.get_agent_class(agent_name)
                if not agent_class:
                    raise ValueError(f"Agent '{agent_name}' not found in registry")

                agent = agent_class()
                phase_input = context.accumulated_output or context.initial_input

                if hasattr(agent, agent_method):
                    method = getattr(agent, agent_method)
                    return await method(phase_input)
                else:
                    raise AttributeError(f"Agent has no method '{agent_method}'")
            except Exception as e:
                logger.error(f"Agent phase failed for '{agent_name}': {e}", exc_info=True)
                raise

        phase_name = phase_name or agent_name
        return self.add_phase(
            name=phase_name,
            handler=agent_handler,
            description=description,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            skip_on_error=skip_on_error,
            required=required,
        )

    def build(self) -> List[WorkflowPhase]:
        """Build and return the phase list"""
        if not self.phases:
            raise ValueError("Workflow must have at least one phase")
        logger.info(f"Built workflow with {len(self.phases)} phases: {[p.name for p in self.phases]}")
        return self.phases

    def clear(self) -> "WorkflowBuilder":
        """Clear all phases and start fresh"""
        self.phases = []
        return self


# ============================================================================
# PRE-BUILT WORKFLOW TEMPLATES
# ============================================================================


async def build_blog_post_workflow() -> List[WorkflowPhase]:
    """
    Build the blog post generation workflow.

    Phases:
    - research: Gather background information
    - draft: Create initial content
    - assess: Quality assessment
    - refine: Improve based on assessment
    - finalize: Format and publish-ready preparation
    - image_selection: Select or generate images
    - publish: Final publishing step
    """
    builder = WorkflowBuilder()

    builder.add_agent_phase(
        "research_agent",
        phase_name="research",
        timeout_seconds=180,
        max_retries=2,
        required=True,
    )

    builder.add_agent_phase(
        "creative_agent",
        phase_name="draft",
        timeout_seconds=300,
        max_retries=3,
        required=True,
    )

    builder.add_agent_phase(
        "qa_agent",
        phase_name="assess",
        timeout_seconds=120,
        max_retries=2,
        required=True,
    )

    # Refinement: use creative agent with is_refinement=True
    # For now, use creative_agent again (later can be customized)
    builder.add_agent_phase(
        "creative_agent",
        phase_name="refine",
        timeout_seconds=300,
        max_retries=3,
        required=False,
        skip_on_error=True,
    )

    builder.add_agent_phase(
        "publishing_agent",
        phase_name="finalize",
        timeout_seconds=120,
        max_retries=2,
        required=True,
    )

    builder.add_agent_phase(
        "image_agent",
        phase_name="image_selection",
        timeout_seconds=180,
        max_retries=2,
        required=False,
        skip_on_error=True,
    )

    # Publishing step (placeholder - can be custom Publishing agent)
    async def publish_handler(context: WorkflowContext) -> Any:
        """Publish workflow - currently a no-op"""
        logger.info("[%s] Publishing workflow result", context.workflow_id)
        return context.accumulated_output

    builder.add_phase(
        name="publish",
        handler=publish_handler,
        timeout_seconds=60,
        max_retries=1,
        required=False,
    )

    return builder.build()


async def build_social_media_workflow() -> List[WorkflowPhase]:
    """
    Build the social media content workflow.

    Phases:
    - research: Quick background research
    - draft: Create social media post
    - assess: Quality check
    - finalize: Format for platform
    - publish: Publish to platform
    """
    builder = WorkflowBuilder()

    builder.add_agent_phase(
        "research_agent",
        phase_name="research",
        timeout_seconds=120,
        max_retries=2,
    )

    builder.add_agent_phase(
        "creative_agent",
        phase_name="draft",
        timeout_seconds=180,
        max_retries=2,
    )

    builder.add_agent_phase(
        "qa_agent",
        phase_name="assess",
        timeout_seconds=90,
        max_retries=2,
        required=False,
    )

    builder.add_agent_phase(
        "publishing_agent",
        phase_name="finalize",
        timeout_seconds=60,
        max_retries=1,
    )

    # Publish step
    async def publish_handler(context: WorkflowContext) -> Any:
        """Publish to social media"""
        logger.info("[%s] Publishing to social media", context.workflow_id)
        return context.accumulated_output

    builder.add_phase(
        name="publish",
        handler=publish_handler,
        timeout_seconds=30,
        required=False,
    )

    return builder.build()


async def build_email_workflow() -> List[WorkflowPhase]:
    """
    Build the email content workflow.

    Phases:
    - draft: Create email content
    - assess: Quality assessment
    - finalize: Format for email
    - publish: Send email
    """
    builder = WorkflowBuilder()

    builder.add_agent_phase(
        "creative_agent",
        phase_name="draft",
        timeout_seconds=180,
        max_retries=2,
    )

    builder.add_agent_phase(
        "qa_agent",
        phase_name="assess",
        timeout_seconds=90,
        max_retries=2,
    )

    builder.add_agent_phase(
        "publishing_agent",
        phase_name="finalize",
        timeout_seconds=60,
        max_retries=1,
    )

    # Email send step
    async def send_handler(context: WorkflowContext) -> Any:
        """Send email"""
        logger.info("[%s] Sending email", context.workflow_id)
        return context.accumulated_output

    builder.add_phase(
        name="publish",
        handler=send_handler,
        timeout_seconds=30,
    )

    return builder.build()


# Workflow template registry
WORKFLOW_TEMPLATES = {
    "blog_post": build_blog_post_workflow,
    "social_media": build_social_media_workflow,
    "email": build_email_workflow,
}


async def get_workflow_phases(template_name: str) -> List[WorkflowPhase]:
    """
    Get phases for a workflow template.

    Args:
        template_name: Name of the template (blog_post, social_media, email)

    Returns:
        List of WorkflowPhase objects

    Raises:
        ValueError: If template not found
    """
    if template_name not in WORKFLOW_TEMPLATES:
        raise ValueError(
            f"Unknown workflow template: '{template_name}'. "
            f"Available: {list(WORKFLOW_TEMPLATES.keys())}"
        )

    builder = WORKFLOW_TEMPLATES[template_name]
    return await builder()
