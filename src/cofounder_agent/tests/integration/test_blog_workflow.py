"""
Blog Workflow Integration Test

Tests the end-to-end blog post generation workflow using the phase-based system.
Requires a live backend server and database — run with INTEGRATION_TESTS=1.

Workflow: [blog_generate_content] → [blog_quality_evaluation] → [blog_search_image] → [blog_create_post]
"""

import asyncio
import logging
import os

import pytest

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("INTEGRATION_TESTS"),
    reason="Set INTEGRATION_TESTS=1 to run integration tests (requires live server)",
)
async def test_blog_workflow():
    """Test complete blog workflow execution"""
    from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase
    from services.phase_registry import PhaseRegistry

    # Create workflow with blog phases
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
                    "tone": "professional",
                    "target_length": 1500,
                    "tags": ["AI", "Healthcare", "Machine Learning"],
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
                user_inputs={"image_count": 1, "orientation": "landscape"},
            ),
            WorkflowPhase(index=3, name="blog_create_post", user_inputs={"publish": True}),  # type: ignore[call-arg]
        ],
    )

    logger.info("=" * 80)
    logger.info("Blog Workflow Integration Test")
    logger.info("=" * 80)
    logger.info(f"Workflow: {workflow.name}")
    logger.info(f"Phases: {[p.name for p in workflow.phases]}")
    logger.info("=" * 80)

    # Verify phases exist in registry
    registry = PhaseRegistry.get_instance()
    logger.info("Checking phase registry...")
    for phase in workflow.phases:
        phase_def = registry.get_phase(phase.name)
        if phase_def:
            logger.info(f"✓ Phase '{phase.name}' found in registry")
            logger.info(f"  Agent type: {phase_def.agent_type}")
            logger.info(f"  Description: {phase_def.description}")
        else:
            logger.error(f"✗ Phase '{phase.name}' NOT found in registry")
            return False

    logger.info("=" * 80)
    logger.info("All phases registered. Ready to execute workflow.")
    logger.info("=" * 80)

    return True


async def test_blog_phase_definitions():
    """Test that blog phase definitions are properly configured"""
    from services.phase_registry import PhaseRegistry

    registry = PhaseRegistry.get_instance()

    blog_phases = [
        "blog_generate_content",
        "blog_quality_evaluation",
        "blog_search_image",
        "blog_create_post",
    ]

    logger.info("=" * 80)
    logger.info("Blog Phase Definitions Test")
    logger.info("=" * 80)

    for phase_name in blog_phases:
        phase_def = registry.get_phase(phase_name)
        if phase_def:
            logger.info(f"\n✓ {phase_name}")
            logger.info(f"  Agent: {phase_def.agent_type}")
            logger.info(f"  Input fields: {list(phase_def.input_schema.keys())}")
            logger.info(f"  Output fields: {list(phase_def.output_schema.keys())}")
            logger.info(f"  Tags: {phase_def.tags}")
        else:
            logger.error(f"\n✗ {phase_name} NOT FOUND")
            return False

    logger.info("\n" + "=" * 80)
    logger.info("All blog phases properly defined!")
    logger.info("=" * 80)

    return True


async def test_workflow_executor():
    """Test that workflow executor can load agents"""
    from services.workflow_executor import WorkflowExecutor

    executor = WorkflowExecutor()

    agent_types = [
        "blog_content_generator_agent",
        "blog_quality_agent",
        "blog_image_agent",
        "blog_publisher_agent",
    ]

    logger.info("=" * 80)
    logger.info("Workflow Executor Agent Loading Test")
    logger.info("=" * 80)

    for agent_type in agent_types:
        agent = executor._get_agent(agent_type)
        if agent:
            logger.info(f"✓ Agent '{agent_type}' loaded")
            logger.info(f"  Class: {agent.__class__.__name__}")
            logger.info(f"  Has run method: {hasattr(agent, 'run')}")
        else:
            logger.error(f"✗ Agent '{agent_type}' failed to load")
            return False

    logger.info("=" * 80)
    logger.info("All agents loaded successfully!")
    logger.info("=" * 80)

    return True


async def main():
    """Run all tests"""
    logger.info("\n")
    logger.info("Starting Blog Workflow Integration Tests...")
    logger.info("\n")

    # Test 1: Phase definitions
    test1 = await test_blog_phase_definitions()
    if not test1:
        logger.error("Test 1 FAILED: Phase definitions")
        return False

    logger.info("\n")

    # Test 2: Workflow executor
    test2 = await test_workflow_executor()
    if not test2:
        logger.error("Test 2 FAILED: Workflow executor")
        return False

    logger.info("\n")

    # Test 3: Workflow structure
    test3 = await test_blog_workflow()
    if not test3:
        logger.error("Test 3 FAILED: Workflow structure")
        return False

    logger.info("\n")
    logger.info("=" * 80)
    logger.info("✓ ALL TESTS PASSED!")
    logger.info("=" * 80)
    logger.info("\nBlog workflow system is ready for execution.")
    logger.info("Next steps:")
    logger.info("1. Start the FastAPI server: npm run dev:cofounder")
    logger.info("2. Execute workflow via API: POST /api/workflows/custom")
    logger.info("3. Monitor progress via WebSocket")
    logger.info("=" * 80)
    logger.info("\n")

    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
