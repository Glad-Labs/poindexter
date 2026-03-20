import logging

from ..content_agent.agents.research_agent import ResearchAgent
from ..content_agent.services.llm_client import LLMClient
from ..content_agent.utils.tools import CrewAIToolsFactory


class MarketInsightAgent:
    """
    A specialized agent for analyzing market trends and suggesting content topics.
    """

    def __init__(self, llm_client: LLMClient):
        """Initializes the MarketInsightAgent with required clients."""
        self.llm_client = llm_client
        self.research_agent = ResearchAgent()
        self.tools = CrewAIToolsFactory.get_market_agent_tools()
        logging.info("Market Insight Agent initialized (REST API mode - no Firestore).")

    async def suggest_topics(self, base_query: str) -> str:
        """
        Suggests new content topics based on a base query.

        This method uses a web search tool to gather real-time data and then
        uses the LLM to generate suggestions based on the search results.

        Args:
            base_query (str): The base query to search for.

        Returns:
            str: A string containing the suggested topics.
        """
        try:
            # Get real-time data from the web
            search_results = await self.research_agent.run(base_query, [])

            # Generate suggestions based on the search results
            prompt = f"Based on the following search results, generate three blog post titles related to '{base_query}'. Return them as a numbered list.\n\n---SEARCH RESULTS---\n{search_results}\n---END SEARCH RESULTS---"
            suggestions = await self.llm_client.generate_text(prompt)
            return f"Here are some topic suggestions based on '{base_query}':\n{suggestions}"
        except Exception as e:
            logging.error(f"Error suggesting topics: {e}", exc_info=True)
            return "I'm sorry, I had trouble generating topic suggestions."

    async def create_tasks_from_trends(self, trend: str) -> str:
        """
        Analyzes a trend, generates topic ideas, and creates new tasks via REST API.
        """
        try:
            prompt = (
                f"Generate three blog post ideas based on the trend: '{trend}'. "
                "Return a JSON object with an 'ideas' array. Each idea must have: "
                "topic, primary_keyword, target_audience, category."
            )
            response = await self.llm_client.generate_json(prompt)
            suggestions = response.get("ideas", [])

            # Tasks would be created via REST API in production
            logging.info(
                f"Generated {len(suggestions)} task suggestions (REST API integration needed)"
            )

            return f"I've generated {len(suggestions)} potential task ideas based on the trend: '{trend}'."
        except Exception as e:
            logging.error(f"Error creating tasks from trends: {e}", exc_info=True)
            return "I'm sorry, I had trouble creating new tasks from the trend."
