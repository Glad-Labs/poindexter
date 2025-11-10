"""
Unit tests for Poindexter tool definitions and implementations.

Tests:
- research_tool() - ResearchAgent wrapper
- generate_content_tool() - ContentAgent with self-critique loop
- critique_content_tool() - QAAgent quality validation
- publish_tool() - PublishingAgent Strapi integration
- track_metrics_tool() - FinancialAgent metrics tracking
- fetch_images_tool() - ImageAgent image sourcing
- refine_tool() - ContentAgent refinement

Target Coverage: >90% of tool logic
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.unit
class TestPoindexterTools:
    """Test suite for PoindexterTools service."""

    @pytest.fixture
    def tools_service(self, mock_research_agent, mock_creative_agent, mock_qa_agent):
        """Initialize PoindexterTools with mock agents."""
        try:
            from services.poindexter_tools import PoindexterTools
            tools = PoindexterTools()
            tools.research_agent = mock_research_agent
            tools.creative_agent = mock_creative_agent
            tools.qa_agent = mock_qa_agent
            return tools
        except ImportError:
            pytest.skip("poindexter_tools module not available")

    # ========================================================================
    # RESEARCH TOOL TESTS
    # ========================================================================

    @pytest.mark.asyncio
    async def test_research_tool_success(self, tools_service):
        """Research tool should successfully gather information."""
        result = await tools_service.research_tool(
            topic="AI trends in 2025",
            depth="comprehensive",
            sources_limit=5
        )

        assert result["success"] is True
        assert "data" in result
        assert result["cost"] > 0
        assert result["cost"] <= 0.20  # Within estimated range

    @pytest.mark.asyncio
    async def test_research_tool_with_depth(self, tools_service):
        """Research tool should handle different depth levels."""
        depths = ["quick", "standard", "comprehensive"]
        for depth in depths:
            result = await tools_service.research_tool(
                topic="test",
                depth=depth,
                sources_limit=3
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_research_tool_cost_tracking(self, tools_service):
        """Research tool should track costs accurately."""
        result = await tools_service.research_tool(
            topic="machine learning",
            depth="comprehensive",
            sources_limit=10
        )
        assert "cost" in result
        assert isinstance(result["cost"], float)
        assert result["cost"] >= 0.05
        assert result["cost"] <= 0.20

    # ========================================================================
    # GENERATE CONTENT TOOL TESTS
    # ========================================================================

    @pytest.mark.asyncio
    async def test_generate_content_success(self, tools_service):
        """Generate content tool should produce content with self-critique."""
        result = await tools_service.generate_content_tool(
            topic="AI Co-Founders",
            style="professional",
            length="2000 words",
            research_data={"key_points": ["point1", "point2"]},
            constraints={"quality_threshold": 0.85}
        )

        assert result["success"] is True
        assert "data" in result
        assert "content" in result["data"]
        assert result["quality_score"] is not None

    @pytest.mark.asyncio
    async def test_generate_content_with_critique_loop(self, tools_service):
        """Generate content should support self-critique iterations."""
        result = await tools_service.generate_content_tool(
            topic="test topic",
            style="casual",
            length="500 words",
            research_data={},
            constraints={"quality_threshold": 0.90}  # High threshold triggers critique
        )

        assert result["success"] is True
        assert result["iterations"] >= 1  # At least initial generation
        assert result["quality_score"] is not None

    @pytest.mark.asyncio
    async def test_generate_content_max_iterations(self, tools_service):
        """Generate content should not exceed max critique iterations."""
        result = await tools_service.generate_content_tool(
            topic="test",
            style="professional",
            length="1000 words",
            research_data={},
            constraints={}
        )

        # Default max iterations is 3
        assert result["iterations"] <= 3

    @pytest.mark.asyncio
    async def test_generate_content_cost_tracking(self, tools_service):
        """Generate content should track total cost including critique."""
        result = await tools_service.generate_content_tool(
            topic="test",
            style="professional",
            length="2000 words",
            research_data={},
            constraints={}
        )

        assert "cost" in result
        assert result["cost"] >= 0.10  # Minimum for generation
        assert result["cost"] <= 0.50  # Max with critiques

    # ========================================================================
    # CRITIQUE CONTENT TOOL TESTS
    # ========================================================================

    @pytest.mark.asyncio
    async def test_critique_content_excellent_quality(self, tools_service):
        """Critique should evaluate high-quality content."""
        result = await tools_service.critique_content_tool(
            content="Excellent, well-researched article with clear structure.",
            criteria=["clarity", "accuracy", "engagement"],
            target_score=0.80
        )

        assert result["success"] is True
        assert result["quality_score"] is not None
        assert result["quality_score"] >= 0.80

    @pytest.mark.asyncio
    async def test_critique_content_needs_improvement(self, tools_service):
        """Critique should identify content needing improvement."""
        result = await tools_service.critique_content_tool(
            content="short",
            criteria=["completeness", "depth"],
            target_score=0.90
        )

        assert result["success"] is True
        if result["quality_score"] < 0.90:
            assert "feedback" in result["data"]

    @pytest.mark.asyncio
    async def test_critique_multiple_criteria(self, tools_service):
        """Critique should evaluate against multiple criteria."""
        criteria = ["clarity", "accuracy", "relevance", "engagement", "completeness"]
        result = await tools_service.critique_content_tool(
            content="Test article content",
            criteria=criteria,
            target_score=0.80
        )

        assert result["success"] is True
        assert result["quality_score"] is not None

    # ========================================================================
    # PUBLISH TOOL TESTS
    # ========================================================================

    @pytest.mark.asyncio
    async def test_publish_tool_success(self, tools_service):
        """Publish tool should format and publish to Strapi."""
        result = await tools_service.publish_tool(
            content="Article content here",
            platforms=["blog"],
            metadata={
                "title": "Test Article",
                "slug": "test-article",
                "category": "Tech"
            }
        )

        assert result["success"] is True
        assert "data" in result
        assert result["cost"] == 0.0  # Publishing has no LLM cost

    @pytest.mark.asyncio
    async def test_publish_tool_multiple_platforms(self, tools_service):
        """Publish tool should handle multiple target platforms."""
        result = await tools_service.publish_tool(
            content="Content",
            platforms=["blog", "email", "social"],
            metadata={"title": "Multi-platform"}
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_publish_tool_metadata_formatting(self, tools_service):
        """Publish tool should properly format metadata."""
        result = await tools_service.publish_tool(
            content="Article",
            platforms=["blog"],
            metadata={
                "title": "SEO-Optimized Title",
                "seo_description": "Meta description",
                "keywords": ["ai", "automation"]
            }
        )

        assert result["success"] is True

    # ========================================================================
    # TRACK METRICS TOOL TESTS
    # ========================================================================

    @pytest.mark.asyncio
    async def test_track_metrics_success(self, tools_service):
        """Track metrics tool should record workflow metrics."""
        result = await tools_service.track_metrics_tool(
            metric_type="workflow_cost",
            data={"total_cost": 2.50, "tools_used": 5},
            workflow_id="wf-001"
        )

        assert result["success"] is True
        assert result["cost"] == 0.0  # Metrics tracking has no cost

    @pytest.mark.asyncio
    async def test_track_metrics_quality_metrics(self, tools_service):
        """Track metrics should handle quality-related metrics."""
        result = await tools_service.track_metrics_tool(
            metric_type="content_quality",
            data={"quality_score": 0.92, "iterations": 2},
            workflow_id="wf-002"
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_track_metrics_performance_metrics(self, tools_service):
        """Track metrics should handle performance metrics."""
        result = await tools_service.track_metrics_tool(
            metric_type="performance",
            data={"total_time": 125, "model_calls": 8},
            workflow_id="wf-003"
        )

        assert result["success"] is True

    # ========================================================================
    # FETCH IMAGES TOOL TESTS
    # ========================================================================

    @pytest.mark.asyncio
    async def test_fetch_images_success(self, tools_service):
        """Fetch images tool should retrieve images for content."""
        result = await tools_service.fetch_images_tool(
            topic="AI and technology",
            count=3,
            style="professional"
        )

        assert result["success"] is True
        assert "data" in result
        assert result["cost"] >= 0.0
        assert result["cost"] <= 0.10

    @pytest.mark.asyncio
    async def test_fetch_images_multiple_count(self, tools_service):
        """Fetch images should handle variable counts."""
        for count in [1, 3, 5, 10]:
            result = await tools_service.fetch_images_tool(
                topic="test",
                count=count,
                style="professional"
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_fetch_images_different_styles(self, tools_service):
        """Fetch images should support different styles."""
        styles = ["professional", "casual", "artistic", "minimalist"]
        for style in styles:
            result = await tools_service.fetch_images_tool(
                topic="test",
                count=2,
                style=style
            )
            assert result["success"] is True

    # ========================================================================
    # REFINE TOOL TESTS
    # ========================================================================

    @pytest.mark.asyncio
    async def test_refine_content_success(self, tools_service):
        """Refine tool should improve content based on feedback."""
        result = await tools_service.refine_tool(
            content="Original content here",
            feedback="Make it more engaging and add examples",
            direction="improve"
        )

        assert result["success"] is True
        assert "data" in result
        assert result["cost"] >= 0.10  # Refinement has non-zero cost

    @pytest.mark.asyncio
    async def test_refine_content_shorten(self, tools_service):
        """Refine tool should shorten content when requested."""
        result = await tools_service.refine_tool(
            content="Long content here with lots of details",
            feedback="Condense to key points",
            direction="shorten"
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_refine_content_expand(self, tools_service):
        """Refine tool should expand content when requested."""
        result = await tools_service.refine_tool(
            content="Brief content",
            feedback="Add more details and examples",
            direction="expand"
        )

        assert result["success"] is True

    # ========================================================================
    # TOOL UTILITY TESTS
    # ========================================================================

    @pytest.mark.unit
    def test_get_all_tools(self, tools_service):
        """get_all_tools should return all available tools."""
        all_tools = tools_service.get_all_tools()
        
        assert len(all_tools) == 7
        tool_names = [t["name"] for t in all_tools]
        assert "research_tool" in tool_names
        assert "generate_content_tool" in tool_names
        assert "critique_content_tool" in tool_names
        assert "publish_tool" in tool_names
        assert "track_metrics_tool" in tool_names
        assert "fetch_images_tool" in tool_names
        assert "refine_tool" in tool_names

    @pytest.mark.unit
    def test_get_tool_descriptions(self, tools_service):
        """get_tool_descriptions should return tool descriptions."""
        descriptions = tools_service.get_tool_descriptions()
        
        assert isinstance(descriptions, str)
        assert "research_tool" in descriptions
        assert "generate_content_tool" in descriptions

    @pytest.mark.unit
    def test_estimate_tool_cost(self, tools_service):
        """estimate_tool_cost should return cost estimates."""
        cost = tools_service.estimate_tool_cost("generate_content_tool")
        
        assert isinstance(cost, tuple)  # (min, max)
        assert cost[0] > 0
        assert cost[1] > cost[0]

    @pytest.mark.unit
    def test_estimate_tool_time(self, tools_service):
        """estimate_tool_time should return time estimates."""
        time = tools_service.estimate_tool_time("research_tool")
        
        assert isinstance(time, tuple)  # (min, max)
        assert time[0] > 0
        assert time[1] > time[0]


@pytest.mark.unit
class TestToolQualityMetrics:
    """Test tool quality metric definitions."""

    def test_quality_metric_thresholds(self):
        """Quality metrics should have proper thresholds."""
        try:
            from services.poindexter_tools import QualityMetric
            
            assert QualityMetric.EXCELLENT == 0.95
            assert QualityMetric.GOOD == 0.85
            assert QualityMetric.ACCEPTABLE == 0.75
            assert QualityMetric.POOR == 0.65
        except ImportError:
            pytest.skip("QualityMetric not available")


@pytest.mark.unit
class TestToolResultDataclass:
    """Test ToolResult dataclass."""

    def test_tool_result_creation(self, sample_tool_result):
        """ToolResult should be creatable with all fields."""
        try:
            from services.poindexter_tools import ToolResult
            
            result = ToolResult(**sample_tool_result)
            assert result.success is True
            assert result.cost == 0.15
            assert result.quality_score == 0.92
        except ImportError:
            pytest.skip("ToolResult not available")

    def test_tool_result_success_variations(self):
        """ToolResult should handle success and failure states."""
        try:
            from services.poindexter_tools import ToolResult
            
            # Success state
            success_result = ToolResult(
                success=True,
                data={"key": "value"},
                cost=0.10,
                quality_score=0.90,
                critique_notes=None,
                iterations=1,
                error=None
            )
            assert success_result.success is True
            assert success_result.error is None
            
            # Failure state
            fail_result = ToolResult(
                success=False,
                data=None,
                cost=0.05,
                quality_score=None,
                critique_notes=None,
                iterations=0,
                error="API timeout"
            )
            assert fail_result.success is False
            assert fail_result.error == "API timeout"
        except ImportError:
            pytest.skip("ToolResult not available")
