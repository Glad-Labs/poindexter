"""
Integration of SearXNG research service into content agent pipeline.

This module shows how to use the research service in the 6-stage self-critiquing
content generation pipeline.
"""

from typing import Optional
import logging
from datetime import datetime

from research_service import research_content_topic

logger = logging.getLogger(__name__)


class ResearchStage:
    """
    Stage 1: Research Agent - Gathers background using SearXNG privacy-respecting search.

    This stage uses SearXNG to conduct comprehensive research on a topic before
    passing findings to the Creative Agent for content generation.
    """

    def __init__(
        self,
        searxng_instance: Optional[str] = None,
        fetch_full_articles: bool = False,
    ):
        """
        Initialize research stage.

        Args:
            searxng_instance: Optional custom SearXNG instance URL
            fetch_full_articles: Whether to extract full article content
        """
        self.searxng_instance = searxng_instance or "https://searx.be/"
        self.fetch_full_articles = fetch_full_articles

    async def execute(
        self,
        topic: str,
        depth: str = "standard",
        keywords: Optional[list[str]] = None,
    ) -> dict:
        """
        Execute research stage.

        Args:
            topic: Content topic to research
            depth: Research depth (quick, standard, comprehensive)
            keywords: Optional additional keywords for news feeds

        Returns:
            Research data dict with:
                - topic: Original topic
                - search_results: Aggregated web search results
                - news: Recent news articles
                - article_contents: Full article texts (if enabled)
                - market_insights: Trend and market analysis
                - timestamp: When research was conducted
        """
        logger.info(f"[RESEARCH] Starting research for topic: {topic}")

        try:
            # Conduct comprehensive research
            research_data = await research_content_topic(
                topic=topic,
                depth=depth,
                fetch_articles=self.fetch_full_articles,
                searxng_instance=self.searxng_instance,
            )

            result = {
                "stage": "research",
                "topic": topic,
                "timestamp": datetime.now().isoformat(),
                "research_data": research_data,
                "status": "success",
            }

            # Log summary
            logger.info(
                f"[RESEARCH] Completed - Found {research_data.get('total_results', 0)} results"
            )

            return result

        except Exception as e:
            logger.error(f"[RESEARCH] Failed: {e}")
            return {
                "stage": "research",
                "topic": topic,
                "status": "error",
                "error": str(e),
            }


class ResearchPipelineIntegration:
    """
    Full pipeline integration showing how research feeds into content generation.

    Usage with content agent:
    ```python
    integration = ResearchPipelineIntegration()
    
    # Stage 1: Research
    research = await integration.research_stage.execute(
        topic="AI in marketing",
        depth="comprehensive"
    )
    
    # Stage 2: Creative (takes research data as context)
    # Stage 3: QA/Critique
    # ... etc
    ```
    """

    def __init__(self, searxng_instance: Optional[str] = None):
        """Initialize pipeline with research stage."""
        self.research_stage = ResearchStage(
            searxng_instance=searxng_instance,
            fetch_full_articles=True,
        )

    async def execute_research_phase(
        self,
        topic: str,
        brand_voice: Optional[str] = None,
        target_audience: Optional[str] = None,
    ) -> dict:
        """
        Execute research phase with context for content generation.

        Args:
            topic: Content topic
            brand_voice: Brand tone and voice guidelines
            target_audience: Target audience description

        Returns:
            Research context prepared for creative stage
        """
        research_result = await self.research_stage.execute(
            topic=topic,
            depth="comprehensive",
        )

        if research_result["status"] == "success":
            # Prepare context for creative stage
            research_data = research_result["research_data"]

            context = {
                "research_phase_complete": True,
                "topic": topic,
                "brand_voice": brand_voice,
                "target_audience": target_audience,
                "key_findings": self._extract_key_findings(research_data),
                "sources": self._extract_sources(research_data),
                "trends": self._extract_trends(research_data),
                "full_research": research_data,
            }

            return context

        else:
            return {
                "research_phase_complete": False,
                "error": research_result.get("error"),
            }

    def _extract_key_findings(self, research_data: dict) -> list[str]:
        """Extract key findings from research data."""
        findings = []

        for category, data in research_data.get("research", {}).items():
            results = data.get("results", [])
            for result in results[:3]:  # Top 3 per category
                if result.get("content"):
                    findings.append(result["content"][:200])

        return findings

    def _extract_sources(self, research_data: dict) -> list[dict]:
        """Extract and deduplicate sources from research."""
        sources = {}

        for category, data in research_data.get("research", {}).items():
            for result in data.get("results", []):
                url = result.get("url")
                if url and url not in sources:
                    sources[url] = {
                        "title": result.get("title"),
                        "url": url,
                        "category": category,
                    }

        return list(sources.values())

    def _extract_trends(self, research_data: dict) -> list[str]:
        """Extract trend indicators from research."""
        trends = []

        # Look for trend indicators in content
        for data in research_data.get("research", {}).values():
            for result in data.get("results", []):
                content = result.get("content", "").lower()
                if any(
                    word in content
                    for word in ["trend", "growth", "emerging", "future", "latest"]
                ):
                    trends.append(result.get("title", ""))

        return trends[:5]  # Top 5 trends


# Usage example in content agent
async def example_research_for_content():
    """
    Example showing how to use research in content generation pipeline.
    """
    integration = ResearchPipelineIntegration()

    # Execute research phase
    context = await integration.execute_research_phase(
        topic="Sustainable fashion in 2025",
        brand_voice="Professional, forward-thinking, data-driven",
        target_audience="Fashion industry professionals, sustainability advocates",
    )

    if context["research_phase_complete"]:
        print(f"✓ Research complete for: {context['topic']}")
        print(f"  - Key findings: {len(context['key_findings'])} extracted")
        print(f"  - Sources: {len(context['sources'])} identified")
        print(f"  - Trends: {context['trends']}")

        # This context is now ready to pass to Creative Agent
        # creative_result = await creative_agent.execute(
        #     topic=context['topic'],
        #     research_context=context,
        #     brand_voice=context['brand_voice'],
        # )

        return context

    else:
        print(f"✗ Research failed: {context.get('error')}")
        return None


if __name__ == "__main__":
    import asyncio

    asyncio.run(example_research_for_content())
