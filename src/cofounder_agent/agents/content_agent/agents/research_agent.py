from typing import Any, Dict

import httpx

from services.logger_config import get_logger
from services.research_quality_service import ResearchQualityService

from ..config import config
from ..utils.tools import CrewAIToolsFactory

logger = get_logger(__name__)


class ResearchAgent:
    """
    Performs initial research on a given topic to provide context
    for the creative agent by calling the Serper API directly.

    ASYNC-FIRST: All HTTP operations use httpx (no blocking I/O)
    """

    def __init__(self):
        """
        Initializes the ResearchAgent.
        """
        logger.info("Initializing Research Agent...")
        if not config.SERPER_API_KEY:
            raise ValueError("SERPER_API_KEY is not set in the environment.")
        self.serper_api_key = config.SERPER_API_KEY
        self.research_quality_service = ResearchQualityService()
        try:
            self.tools = CrewAIToolsFactory.get_research_agent_tools()
            logger.info("ResearchAgent: Initialized with all research agent tools")
        except Exception as e:
            logger.warning(f"ResearchAgent: Failed to initialize tools: {e}", exc_info=True)
            logger.warning("ResearchAgent will continue without some tools", exc_info=True)
            self.tools = []

    async def run(self, topic: str, keywords: list[str]) -> str:
        """
        Conducts a web search using a combination of the topic and keywords
        to get more targeted and relevant results. Results are filtered and
        scored for quality using ResearchQualityService.

        Args:
            topic (str): The core topic to research.
            keywords (list[str]): A list of supporting keywords to refine the search.

        Returns:
            A string containing the formatted research results, or an empty string on failure.
        """
        try:
            search_query = f"{topic} {' '.join(keywords)}"
            logger.info(f"ResearchAgent: Conducting research for query: '{search_query}'")

            url = "https://google.serper.dev/search"
            payload = {"q": search_query}
            headers = {
                "X-API-KEY": self.serper_api_key,
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()

            search_results = response.json()

            # Filter, deduplicate, and score results using ResearchQualityService
            raw_results = search_results.get("organic", [])[:7]  # Get top 7 for filtering
            scored_results = self.research_quality_service.filter_and_score(
                raw_results, query=search_query
            )

            # Format results using improved service formatter (top 5 after filtering)
            context = self.research_quality_service.format_context(scored_results[:5])

            logger.info(
                f"ResearchAgent: Found and filtered {len(scored_results)} "
                f"high-quality sources from {len(raw_results)} results"
            )
            return context
        except httpx.HTTPError as e:
            logger.error(f"An error occurred during research request: {e}", exc_info=True)
            return ""
        except Exception as e:
            logger.error(f"An unexpected error occurred during research: {e}", exc_info=True)
            return ""


class _WorkflowResearchAgentAdapter:
    """Adapter to make ResearchAgent compatible with workflow_executor `run(inputs)` contract."""

    def __init__(self):
        self._agent = None
        self._init_error = None
        try:
            self._agent = ResearchAgent()
        except Exception as e:
            self._init_error = str(e)

    async def run(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        topic = inputs.get("topic") or inputs.get("prompt") or "general topic"
        keywords = inputs.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = [str(keywords)]

        if self._agent is None:
            return {
                "status": "success",
                "research_data": "",
                "notes": f"Research agent unavailable: {self._init_error}",
                "topic": topic,
                "keywords": keywords,
            }

        context = await self._agent.run(topic=topic, keywords=keywords)
        return {
            "status": "success",
            "research_data": context,
            "topic": topic,
            "keywords": keywords,
        }


def get_research_agent():
    """Factory used by workflow_executor dynamic agent loading."""
    return _WorkflowResearchAgentAdapter()
