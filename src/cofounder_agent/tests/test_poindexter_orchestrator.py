"""
Unit and integration tests for Poindexter orchestrator logic.

Tests:
- PipelineState management (steps, plan, constraints)
- Tool executor coordination (execute_tool, batch_execute)
- Planning logic (create_plan, validate_plan)
- Self-critique loop (critique_generation, refine_content)
- Error handling and recovery

Target Coverage: >85% of orchestrator logic
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch, call
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.unit
class TestPipelineStateManagement:
    """Test suite for PipelineState dataclass and management."""

    @pytest.fixture
    def pipeline_state(self, sample_pipeline_state):
        """Initialize PipelineState with sample data."""
        try:
            from services.poindexter_orchestrator import PipelineState
            return sample_pipeline_state
        except ImportError:
            pytest.skip("poindexter_orchestrator module not available")

    def test_pipeline_state_creation(self, sample_pipeline_state):
        """PipelineState should be creatable with all fields."""
        assert sample_pipeline_state["request_id"] is not None
        assert sample_pipeline_state["current_step"] == 0
        assert isinstance(sample_pipeline_state["steps"], list)
        assert isinstance(sample_pipeline_state["constraints"], dict)

    def test_pipeline_state_step_tracking(self, pipeline_state):
        """PipelineState should track step progress."""
        assert pipeline_state["current_step"] == 0
        assert len(pipeline_state["steps"]) > 0
        
    def test_pipeline_state_constraint_validation(self, pipeline_state):
        """PipelineState should store validation constraints."""
        constraints = pipeline_state["constraints"]
        assert "quality_threshold" in constraints or "max_iterations" in constraints

    @pytest.mark.asyncio
    async def test_pipeline_state_progress_tracking(self, pipeline_state):
        """PipelineState should support progress updates."""
        initial_step = pipeline_state["current_step"]
        # Simulate step progress
        pipeline_state["current_step"] = initial_step + 1
        
        assert pipeline_state["current_step"] == initial_step + 1


@pytest.mark.unit
class TestOrchestratorPlanning:
    """Test suite for orchestrator planning logic."""

    @pytest.fixture
    def orchestrator_service(self, mock_tools_service):
        """Initialize orchestrator with mock tools."""
        try:
            from services.poindexter_orchestrator import PoindexterOrchestrator
            orchestrator = PoindexterOrchestrator()
            orchestrator.tools = mock_tools_service
            return orchestrator
        except ImportError:
            pytest.skip("PoindexterOrchestrator module not available")

    @pytest.mark.asyncio
    async def test_create_plan_simple_workflow(self, orchestrator_service):
        """Create plan should generate workflow steps."""
        plan = await orchestrator_service.create_plan(
            request_type="blog_post",
            parameters={"topic": "AI Trends", "length": "2000 words"}
        )

        assert plan is not None
        assert len(plan) > 0
        assert all("tool" in step for step in plan)

    @pytest.mark.asyncio
    async def test_create_plan_with_research(self, orchestrator_service):
        """Create plan should include research step."""
        plan = await orchestrator_service.create_plan(
            request_type="blog_post",
            parameters={"topic": "test", "require_research": True}
        )

        tool_names = [step["tool"] for step in plan]
        assert "research_tool" in tool_names

    @pytest.mark.asyncio
    async def test_create_plan_with_images(self, orchestrator_service):
        """Create plan should include image fetching."""
        plan = await orchestrator_service.create_plan(
            request_type="blog_post",
            parameters={"include_images": True}
        )

        tool_names = [step["tool"] for step in plan]
        assert "fetch_images_tool" in tool_names

    @pytest.mark.asyncio
    async def test_create_plan_publishing_step(self, orchestrator_service):
        """Create plan should include publishing step."""
        plan = await orchestrator_service.create_plan(
            request_type="blog_post",
            parameters={"auto_publish": True}
        )

        tool_names = [step["tool"] for step in plan]
        assert "publish_tool" in tool_names

    @pytest.mark.asyncio
    async def test_validate_plan_success(self, orchestrator_service):
        """Validate plan should accept valid plans."""
        plan = [
            {"tool": "research_tool", "params": {}},
            {"tool": "generate_content_tool", "params": {}},
            {"tool": "publish_tool", "params": {}}
        ]

        is_valid = await orchestrator_service.validate_plan(plan)
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_validate_plan_invalid_sequence(self, orchestrator_service):
        """Validate plan should reject invalid step sequences."""
        # Publishing before content generation
        plan = [
            {"tool": "publish_tool", "params": {}},
            {"tool": "generate_content_tool", "params": {}}
        ]

        is_valid = await orchestrator_service.validate_plan(plan)
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_validate_plan_missing_required_steps(self, orchestrator_service):
        """Validate plan should require key steps."""
        # Missing generate_content_tool
        plan = [
            {"tool": "research_tool", "params": {}},
            {"tool": "publish_tool", "params": {}}
        ]

        is_valid = await orchestrator_service.validate_plan(plan)
        assert is_valid is False


@pytest.mark.unit
class TestToolExecution:
    """Test suite for tool execution and coordination."""

    @pytest.fixture
    def orchestrator_service(self, mock_tools_service):
        """Initialize orchestrator with mock tools."""
        try:
            from services.poindexter_orchestrator import PoindexterOrchestrator
            orchestrator = PoindexterOrchestrator()
            orchestrator.tools = mock_tools_service
            return orchestrator
        except ImportError:
            pytest.skip("PoindexterOrchestrator module not available")

    @pytest.mark.asyncio
    async def test_execute_single_tool(self, orchestrator_service):
        """Execute single tool should call appropriate tool."""
        result = await orchestrator_service.execute_tool(
            tool_name="research_tool",
            parameters={"topic": "AI"}
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_tool_with_context(self, orchestrator_service):
        """Execute tool should pass context to tool."""
        context = {"previous_result": "research data"}
        
        result = await orchestrator_service.execute_tool(
            tool_name="generate_content_tool",
            parameters={"topic": "AI"},
            context=context
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_batch_execute_tools(self, orchestrator_service):
        """Batch execute should run parallel tools."""
        tools = [
            {"tool": "research_tool", "params": {"topic": "AI"}},
            {"tool": "fetch_images_tool", "params": {"topic": "AI", "count": 3}}
        ]

        results = await orchestrator_service.batch_execute_tools(tools)

        assert len(results) == 2
        assert all(r["success"] is True for r in results)

    @pytest.mark.asyncio
    async def test_batch_execute_dependency_tracking(self, orchestrator_service):
        """Batch execute should respect tool dependencies."""
        # Research must complete before content generation
        tools = [
            {"tool": "research_tool", "params": {}},
            {"tool": "generate_content_tool", "params": {}, "depends_on": ["research_tool"]}
        ]

        results = await orchestrator_service.batch_execute_tools(tools)
        
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_execute_tool_error_handling(self, orchestrator_service):
        """Execute tool should handle errors gracefully."""
        with patch.object(orchestrator_service.tools, 'research_tool', 
                         side_effect=Exception("API error")):
            result = await orchestrator_service.execute_tool(
                tool_name="research_tool",
                parameters={}
            )

            # Should fail gracefully
            assert result["success"] is False
            assert "error" in result


@pytest.mark.unit
class TestSelfCritiqueLoop:
    """Test suite for self-critique and refinement logic."""

    @pytest.fixture
    def orchestrator_service(self, mock_tools_service):
        """Initialize orchestrator with mock tools."""
        try:
            from services.poindexter_orchestrator import PoindexterOrchestrator
            orchestrator = PoindexterOrchestrator()
            orchestrator.tools = mock_tools_service
            return orchestrator
        except ImportError:
            pytest.skip("PoindexterOrchestrator module not available")

    @pytest.mark.asyncio
    async def test_critique_loop_evaluation(self, orchestrator_service):
        """Critique loop should evaluate content quality."""
        content = "Generated content"
        
        quality_score = await orchestrator_service.evaluate_content_quality(
            content=content,
            criteria=["clarity", "accuracy", "engagement"]
        )

        assert isinstance(quality_score, float)
        assert 0 <= quality_score <= 1.0

    @pytest.mark.asyncio
    async def test_critique_loop_needs_refinement(self, orchestrator_service):
        """Critique loop should identify refinement needs."""
        feedback = await orchestrator_service.generate_critique_feedback(
            content="short content",
            quality_score=0.65,
            target_score=0.85
        )

        assert feedback is not None
        assert isinstance(feedback, dict)
        assert "suggestions" in feedback or "areas_for_improvement" in feedback

    @pytest.mark.asyncio
    async def test_refine_content_with_feedback(self, orchestrator_service):
        """Refine content should improve based on feedback."""
        original = "Original content"
        feedback = "Make more engaging"
        
        refined = await orchestrator_service.refine_content_with_feedback(
            content=original,
            feedback=feedback
        )

        assert refined is not None
        assert refined != original  # Should be different

    @pytest.mark.asyncio
    async def test_critique_loop_max_iterations(self, orchestrator_service):
        """Critique loop should not exceed max iterations."""
        content = "Content"
        max_iterations = 3
        
        final_content, iterations = await orchestrator_service.critique_loop_with_limit(
            content=content,
            max_iterations=max_iterations,
            target_quality=0.95
        )

        assert iterations <= max_iterations

    @pytest.mark.asyncio
    async def test_critique_loop_early_exit_on_quality(self, orchestrator_service):
        """Critique loop should exit when quality target reached."""
        content = "Excellent content"
        
        final_content, iterations = await orchestrator_service.critique_loop_with_limit(
            content=content,
            max_iterations=5,
            target_quality=0.70  # Low threshold
        )

        assert iterations <= 2  # Should exit quickly


@pytest.mark.unit
class TestOrchestratorExecutionFlow:
    """Test suite for complete orchestrator execution flow."""

    @pytest.fixture
    def orchestrator_service(self, mock_tools_service):
        """Initialize orchestrator with mock tools."""
        try:
            from services.poindexter_orchestrator import PoindexterOrchestrator
            orchestrator = PoindexterOrchestrator()
            orchestrator.tools = mock_tools_service
            return orchestrator
        except ImportError:
            pytest.skip("PoindexterOrchestrator module not available")

    @pytest.mark.asyncio
    async def test_execute_workflow_blog_post(self, orchestrator_service):
        """Execute workflow should process complete blog post generation."""
        result = await orchestrator_service.execute_workflow(
            workflow_type="blog_post",
            parameters={
                "topic": "AI Trends",
                "length": "2000 words",
                "include_images": True,
                "auto_publish": True
            }
        )

        assert result["success"] is True
        assert "content" in result
        assert result["total_cost"] >= 0
        assert result["quality_score"] is not None

    @pytest.mark.asyncio
    async def test_execute_workflow_with_constraints(self, orchestrator_service):
        """Execute workflow should respect constraints."""
        result = await orchestrator_service.execute_workflow(
            workflow_type="blog_post",
            parameters={"topic": "test"},
            constraints={
                "max_cost": 1.0,
                "quality_threshold": 0.85,
                "max_iterations": 2
            }
        )

        assert result["total_cost"] <= 1.0

    @pytest.mark.asyncio
    async def test_execute_workflow_error_recovery(self, orchestrator_service):
        """Execute workflow should recover from tool errors."""
        # Mock a tool that fails then succeeds
        call_count = 0
        
        async def failing_tool(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Temporary error")
            return {"success": True, "data": {}}

        with patch.object(orchestrator_service.tools, 'research_tool', 
                         side_effect=failing_tool):
            result = await orchestrator_service.execute_workflow(
                workflow_type="blog_post",
                parameters={},
                allow_retries=True
            )

            assert call_count > 1  # Should retry

    @pytest.mark.asyncio
    async def test_execute_workflow_progress_tracking(self, orchestrator_service):
        """Execute workflow should track progress."""
        progress_updates = []
        
        async def progress_callback(step, total):
            progress_updates.append((step, total))

        result = await orchestrator_service.execute_workflow(
            workflow_type="blog_post",
            parameters={},
            progress_callback=progress_callback
        )

        assert len(progress_updates) > 0
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_workflow_cost_optimization(self, orchestrator_service):
        """Execute workflow should optimize for cost."""
        expensive_result = await orchestrator_service.execute_workflow(
            workflow_type="blog_post",
            parameters={},
            optimize_for="quality"
        )

        economical_result = await orchestrator_service.execute_workflow(
            workflow_type="blog_post",
            parameters={},
            optimize_for="cost"
        )

        # Economical should use cheaper models
        assert economical_result["total_cost"] <= expensive_result["total_cost"]


@pytest.mark.integration
class TestOrchestratorIntegration:
    """Integration tests for orchestrator with multiple components."""

    @pytest.mark.asyncio
    async def test_orchestrator_with_all_tools(self, mock_tools_service):
        """Orchestrator should coordinate all tools successfully."""
        try:
            from services.poindexter_orchestrator import PoindexterOrchestrator
            
            orchestrator = PoindexterOrchestrator()
            orchestrator.tools = mock_tools_service
            
            result = await orchestrator.execute_workflow(
                workflow_type="full_blog_post",
                parameters={
                    "topic": "AI Trends in 2025",
                    "require_research": True,
                    "include_images": True,
                    "auto_publish": True
                }
            )

            assert result["success"] is True
            assert result["steps_completed"] > 0
            assert result["quality_score"] > 0.70
        except ImportError:
            pytest.skip("PoindexterOrchestrator module not available")

    @pytest.mark.asyncio
    async def test_orchestrator_recovery_from_cascading_failures(self):
        """Orchestrator should recover from cascading failures."""
        try:
            from services.poindexter_orchestrator import PoindexterOrchestrator
            
            orchestrator = PoindexterOrchestrator()
            
            # Mock multiple failures
            with patch.object(orchestrator.tools, 'research_tool', 
                             side_effect=Exception("Failed")):
                result = await orchestrator.execute_workflow(
                    workflow_type="blog_post",
                    parameters={},
                    allow_fallback=True
                )

                # Should not crash
                assert "error" in result or result.get("fallback_executed")
        except ImportError:
            pytest.skip("PoindexterOrchestrator module not available")


@pytest.mark.unit
class TestOrchestratorMetrics:
    """Test suite for orchestrator metrics tracking."""

    @pytest.fixture
    def orchestrator_service(self, mock_tools_service):
        """Initialize orchestrator with mock tools."""
        try:
            from services.poindexter_orchestrator import PoindexterOrchestrator
            orchestrator = PoindexterOrchestrator()
            orchestrator.tools = mock_tools_service
            return orchestrator
        except ImportError:
            pytest.skip("PoindexterOrchestrator module not available")

    @pytest.mark.asyncio
    async def test_track_execution_metrics(self, orchestrator_service):
        """Orchestrator should track execution metrics."""
        result = await orchestrator_service.execute_workflow(
            workflow_type="blog_post",
            parameters={}
        )

        assert "total_cost" in result
        assert "execution_time" in result
        assert "tool_calls" in result
        assert "quality_score" in result

    @pytest.mark.asyncio
    async def test_aggregate_metrics(self, orchestrator_service):
        """Orchestrator should aggregate metrics across workflow."""
        results = []
        for i in range(3):
            result = await orchestrator_service.execute_workflow(
                workflow_type="blog_post",
                parameters={}
            )
            results.append(result)

        aggregated = await orchestrator_service.aggregate_metrics(results)
        
        assert "total_cost" in aggregated
        assert "average_quality" in aggregated
        assert "total_time" in aggregated
