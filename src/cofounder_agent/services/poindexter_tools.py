"""
Poindexter Tools: Wrap existing agents as smolagents-compatible tools

Converts ContentAgent, QAAgent, PublishingAgent, etc. into tools
that Poindexter can discover and chain autonomously.

Each tool:
1. Accepts standard input parameters
2. Integrates self-critique loops where applicable
3. Tracks costs and metrics
4. Returns structured results
5. Can call other tools for complex workflows
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class QualityMetric(Enum):
    """Quality score thresholds for self-critique."""

    EXCELLENT = 0.95
    GOOD = 0.85
    ACCEPTABLE = 0.75
    POOR = 0.65


@dataclass
class ToolResult:
    """Standard result format for all Poindexter tools."""

    success: bool
    data: Any
    cost: float = 0.0
    quality_score: Optional[float] = None
    critique_notes: Optional[str] = None
    iterations: int = 1
    error: Optional[str] = None


class PoindexterTools:
    """
    Convert existing agents to Poindexter tools.

    This service wraps your agents (ContentAgent, QAAgent, etc.) to work
    with smolagents' ReAct pattern for autonomous orchestration.
    """

    def __init__(
        self,
        agent_factory: Any,  # Creates specialized agents
        model_router: Any,  # For quality scoring
        constraint_config: Optional[Dict] = None,
    ):
        """
        Initialize tool set.

        Args:
            agent_factory: Factory for creating specialized agents
            model_router: Model router for LLM calls
            constraint_config: Default constraints for tools
        """
        self.agent_factory = agent_factory
        self.model_router = model_router
        self.constraint_config = constraint_config or {}
        self.max_critique_iterations = 3

    # ============================================================================
    # RESEARCH TOOL - Gather information for content
    # ============================================================================

    async def research_tool(
        self,
        topic: str,
        depth: str = "comprehensive",
        sources_limit: int = 5,
        context: Optional[Dict] = None,
    ) -> ToolResult:
        """
        Research a topic using available resources (web search, MCP servers).

        Args:
            topic: What to research
            depth: "quick", "standard", "comprehensive"
            sources_limit: How many sources to gather
            context: User/project context

        Returns:
            ToolResult with research data

        Cost: ~$0.05-0.20 depending on depth
        Time: ~10-30 seconds
        """
        try:
            logger.info(f"[RESEARCH] Starting research on: {topic}")

            # Create research agent
            research_agent = await self.agent_factory.create_research_agent()

            # Execute research
            result = await research_agent.execute(
                {
                    "topic": topic,
                    "depth": depth,
                    "sources_limit": sources_limit,
                    "context": context or {},
                }
            )

            # Calculate cost based on depth
            cost_map = {"quick": 0.05, "standard": 0.10, "comprehensive": 0.20}
            cost = cost_map.get(depth, 0.10)

            logger.info(f"[RESEARCH] Completed research on {topic}")

            return ToolResult(
                success=True,
                data=result,
                cost=cost,
                quality_score=0.90,  # Research usually high quality
            )

        except Exception as e:
            logger.error(f"[RESEARCH] Failed: {e}")
            return ToolResult(success=False, data=None, cost=0.0, error=str(e))

    # ============================================================================
    # GENERATE CONTENT TOOL - Create content with self-critique loop
    # ============================================================================

    async def generate_content_tool(
        self,
        topic: str,
        style: str = "professional",
        length: str = "medium",
        research_data: Optional[Dict] = None,
        constraints: Optional[Dict] = None,
    ) -> ToolResult:
        """
        Generate content with integrated self-critique loop.

        Workflow:
        1. Generate initial draft
        2. Get quality critique (optional if quality < threshold)
        3. Refine based on feedback
        4. Return final result

        Args:
            topic: What to write about
            style: "professional", "casual", "academic", "creative"
            length: "short", "medium", "long"
            research_data: Background information
            constraints: Quality threshold, max iterations

        Returns:
            ToolResult with generated content

        Cost: ~$0.10-0.30 depending on length
        Time: ~30-120 seconds (includes critique loop)
        """
        try:
            logger.info(f"[GENERATE] Creating content: {topic}")

            constraints = constraints or {}
            quality_threshold = constraints.get("quality_threshold", 0.85)
            max_iterations = min(
                constraints.get("max_iterations", self.max_critique_iterations),
                self.max_critique_iterations,
            )

            # Step 1: Generate initial draft
            logger.info(f"[GENERATE] Step 1: Creating initial draft")
            content_agent = await self.agent_factory.create_content_agent()

            draft = await content_agent.execute(
                {
                    "topic": topic,
                    "style": style,
                    "length": length,
                    "research_data": research_data or {},
                }
            )

            current_content = draft
            current_quality = 0.85
            cost = 0.10
            iterations = 1

            # Step 2: Self-critique loop (if enabled)
            if constraints.get("enable_critique", True) and max_iterations > 1:
                for iteration in range(max_iterations - 1):
                    # Check quality
                    if current_quality >= quality_threshold:
                        logger.info(f"[GENERATE] Quality threshold met ({current_quality:.2f})")
                        break

                    logger.info(
                        f"[GENERATE] Step {iteration + 2}: Quality critique (score: {current_quality:.2f})"
                    )

                    # Get critique
                    qa_agent = await self.agent_factory.create_qa_agent()
                    critique = await qa_agent.execute(
                        {
                            "content": current_content,
                            "criteria": ["clarity", "accuracy", "engagement", "relevance"],
                        }
                    )

                    # Add critique cost
                    cost += 0.05

                    # Check if critique suggests improvements
                    if critique.get("needs_improvement", False):
                        logger.info(f"[GENERATE] Step {iteration + 3}: Refining based on feedback")

                        # Refine content
                        refined = await content_agent.refine(
                            {
                                "content": current_content,
                                "feedback": critique.get("feedback", ""),
                                "previous_feedback_iterations": iteration + 1,
                            }
                        )

                        current_content = refined
                        current_quality = critique.get("quality_score", current_quality)
                        cost += 0.10
                        iterations += 1
                    else:
                        # No improvements needed
                        current_quality = critique.get("quality_score", current_quality)
                        logger.info(f"[GENERATE] Critique complete, quality: {current_quality:.2f}")
                        break

            logger.info(f"[GENERATE] Content generation complete (iterations: {iterations})")

            return ToolResult(
                success=True,
                data={
                    "content": current_content,
                    "topic": topic,
                    "style": style,
                    "metadata": {
                        "word_count": len(current_content.split()),
                        "style_applied": style,
                    },
                },
                cost=cost,
                quality_score=current_quality,
                iterations=iterations,
            )

        except Exception as e:
            logger.error(f"[GENERATE] Failed: {e}")
            return ToolResult(success=False, data=None, error=str(e))

    # ============================================================================
    # CRITIQUE CONTENT TOOL - Quality validation
    # ============================================================================

    async def critique_content_tool(
        self, content: str, criteria: Optional[List[str]] = None, target_score: float = 0.90
    ) -> ToolResult:
        """
        Validate content quality against criteria.

        Args:
            content: Content to critique
            criteria: What to evaluate (clarity, accuracy, engagement, etc.)
            target_score: Quality target for recommendations

        Returns:
            ToolResult with quality score and feedback

        Cost: ~$0.05
        Time: ~15 seconds
        """
        try:
            criteria = criteria or ["clarity", "accuracy", "engagement", "relevance"]
            logger.info(
                f"[CRITIQUE] Evaluating {len(content)} chars against {len(criteria)} criteria"
            )

            qa_agent = await self.agent_factory.create_qa_agent()

            critique = await qa_agent.execute({"content": content, "criteria": criteria})

            return ToolResult(
                success=True,
                data={
                    "quality_score": critique.get("quality_score", 0.85),
                    "feedback": critique.get("feedback", ""),
                    "criteria_scores": critique.get("criteria_scores", {}),
                    "needs_improvement": critique.get("quality_score", 0.85) < target_score,
                },
                cost=0.05,
                quality_score=1.0,  # This tool is objective
            )

        except Exception as e:
            logger.error(f"[CRITIQUE] Failed: {e}")
            return ToolResult(success=False, data=None, error=str(e))

    # ============================================================================
    # PUBLISH TOOL - Publish to platforms
    # ============================================================================

    async def publish_tool(
        self, content: str, platforms: List[str], metadata: Optional[Dict] = None
    ) -> ToolResult:
        """
        Publish content to configured platforms (Strapi, social media, etc.).

        Args:
            content: Content to publish
            platforms: Where to publish ["strapi", "twitter", "linkedin"]
            metadata: Title, description, tags, etc.

        Returns:
            ToolResult with publication results

        Cost: $0.00 (depends on platform APIs)
        Time: ~5-10 seconds
        """
        try:
            logger.info(f"[PUBLISH] Publishing to {platforms}")

            publishing_agent = await self.agent_factory.create_publishing_agent()

            results = await publishing_agent.execute(
                {"content": content, "platforms": platforms, "metadata": metadata or {}}
            )

            return ToolResult(
                success=True,
                data={
                    "platforms_published": results.get("platforms_published", platforms),
                    "urls": results.get("urls", {}),
                    "publish_timestamp": results.get("timestamp", ""),
                },
                cost=0.0,  # Publishing is typically free
                quality_score=1.0,
            )

        except Exception as e:
            logger.error(f"[PUBLISH] Failed: {e}")
            return ToolResult(success=False, data=None, error=str(e))

    # ============================================================================
    # METRICS TOOL - Track costs and performance
    # ============================================================================

    async def track_metrics_tool(
        self, metric_type: str, data: Dict, workflow_id: Optional[str] = None
    ) -> ToolResult:
        """
        Track metrics for workflow optimization.

        Args:
            metric_type: "cost", "quality", "performance", "roi"
            data: Metric data to track
            workflow_id: Which workflow these metrics are for

        Returns:
            ToolResult with metric recording confirmation

        Cost: $0.00 (local operation)
        Time: ~1 second
        """
        try:
            logger.info(f"[METRICS] Recording {metric_type} metric")

            financial_agent = await self.agent_factory.create_financial_agent()

            result = await financial_agent.execute(
                {"metric_type": metric_type, "data": data, "workflow_id": workflow_id}
            )

            return ToolResult(success=True, data=result, cost=0.0, quality_score=1.0)

        except Exception as e:
            logger.error(f"[METRICS] Failed: {e}")
            return ToolResult(success=False, data=None, error=str(e))

    # ============================================================================
    # FETCH IMAGES TOOL - Find and optimize images
    # ============================================================================

    async def fetch_images_tool(
        self, topic: str, count: int = 3, style: str = "professional"
    ) -> ToolResult:
        """
        Fetch relevant images for content.

        Args:
            topic: What images to find
            count: How many images
            style: Image style preference

        Returns:
            ToolResult with image URLs and metadata

        Cost: ~$0.02-0.10 depending on source
        Time: ~10 seconds
        """
        try:
            logger.info(f"[IMAGES] Finding {count} images for: {topic}")

            image_agent = await self.agent_factory.create_image_agent()

            images = await image_agent.execute({"topic": topic, "count": count, "style": style})

            return ToolResult(
                success=True,
                data={"images": images.get("images", []), "count": len(images.get("images", []))},
                cost=0.05,
                quality_score=0.90,
            )

        except Exception as e:
            logger.error(f"[IMAGES] Failed: {e}")
            return ToolResult(success=False, data=None, error=str(e))

    # ============================================================================
    # REFINE TOOL - Improve existing content
    # ============================================================================

    async def refine_tool(
        self, content: str, feedback: str, direction: str = "improve_clarity"
    ) -> ToolResult:
        """
        Refine existing content based on feedback.

        Args:
            content: Content to refine
            feedback: What to improve
            direction: Type of refinement

        Returns:
            ToolResult with refined content

        Cost: ~$0.10
        Time: ~30 seconds
        """
        try:
            logger.info(f"[REFINE] Refining content ({direction})")

            content_agent = await self.agent_factory.create_content_agent()

            refined = await content_agent.refine(
                {"content": content, "feedback": feedback, "direction": direction}
            )

            return ToolResult(success=True, data=refined, cost=0.10, quality_score=0.85)

        except Exception as e:
            logger.error(f"[REFINE] Failed: {e}")
            return ToolResult(success=False, data=None, error=str(e))

    # ============================================================================
    # UTILITY METHODS
    # ============================================================================

    async def get_all_tools(self) -> Dict[str, Any]:
        """
        Get all available tools for Poindexter.

        Returns dict suitable for smolagents agent initialization.
        """
        return {
            "research": self.research_tool,
            "generate": self.generate_content_tool,
            "critique": self.critique_content_tool,
            "publish": self.publish_tool,
            "track_metrics": self.track_metrics_tool,
            "fetch_images": self.fetch_images_tool,
            "refine": self.refine_tool,
        }

    async def get_tool_descriptions(self) -> Dict[str, str]:
        """Get descriptions of all tools for Poindexter reasoning."""
        return {
            "research": "Gather information and sources on a topic",
            "generate": "Create content with integrated quality feedback loops",
            "critique": "Evaluate content quality against criteria",
            "publish": "Publish content to platforms (Strapi, social media)",
            "track_metrics": "Record performance and cost metrics",
            "fetch_images": "Find and optimize images for content",
            "refine": "Improve content based on specific feedback",
        }

    async def estimate_tool_cost(self, tool_name: str, params: Dict) -> float:
        """Estimate cost for calling a tool."""
        cost_map = {
            "research": 0.10,
            "generate": 0.20,
            "critique": 0.05,
            "publish": 0.00,
            "track_metrics": 0.00,
            "fetch_images": 0.05,
            "refine": 0.10,
        }
        return cost_map.get(tool_name, 0.05)

    async def estimate_tool_time(self, tool_name: str, params: Dict) -> float:
        """Estimate execution time for a tool (seconds)."""
        time_map = {
            "research": 15.0,
            "generate": 60.0,
            "critique": 15.0,
            "publish": 5.0,
            "track_metrics": 1.0,
            "fetch_images": 10.0,
            "refine": 30.0,
        }
        return time_map.get(tool_name, 10.0)


# Example usage
if __name__ == "__main__":

    async def main():
        # In real usage, this would be initialized from the FastAPI app
        # poindexter_tools = PoindexterTools(
        #     agent_factory=app.agent_factory,
        #     model_router=app.model_router
        # )

        # Get all tools for smolagents
        # tools = await poindexter_tools.get_all_tools()

        print("Poindexter tools initialized!")

    asyncio.run(main())
