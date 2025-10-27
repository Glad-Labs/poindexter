import logging
import json
from src.agents.content_agent.services.llm_client import LLMClient
from src.agents.content_agent.agents.research_agent import ResearchAgent

class MarketInsightAgent:
    """
    A specialized agent for analyzing market trends and suggesting content topics.
    """
    def __init__(self, llm_client: LLMClient):
        """Initializes the MarketInsightAgent with required clients."""
        self.llm_client = llm_client
        self.research_agent = ResearchAgent()
        logging.info("Market Insight Agent initialized (REST API mode - no Firestore).")

    def suggest_topics(self, base_query: str) -> str:
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
            search_results = self.research_agent.run(base_query, [])

            # Generate suggestions based on the search results
            prompt = f"Based on the following search results, generate three blog post titles related to '{base_query}'. Return them as a numbered list.\n\n---SEARCH RESULTS---\n{search_results}\n---END SEARCH RESULTS---"
            suggestions = self.llm_client.generate_text(prompt)
            return f"Here are some topic suggestions based on '{base_query}':\n{suggestions}"
        except Exception as e:
            logging.error(f"Error suggesting topics: {e}", exc_info=True)
            return "I'm sorry, I had trouble generating topic suggestions."

    def create_tasks_from_trends(self, trend: str) -> str:
        """
        Analyzes a trend, generates topic ideas, and creates new tasks in Firestore.
        """
        try:
            # Define a tool for the LLM to generate structured task ideas.
            tool_schema = {
                "type": "function",
                "function": {
                    "name": "create_blog_post_ideas",
                    "description": "Generates a list of blog post ideas based on a trend.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ideas": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "topic": {"type": "string"},
                                        "primary_keyword": {"type": "string"},
                                        "target_audience": {"type": "string"},
                                        "category": {"type": "string"}
                                    },
                                    "required": ["topic", "primary_keyword", "target_audience", "category"]
                                }
                            }
                        }
                    }
                }
            }
            prompt = f"Generate three blog post ideas based on the trend: '{trend}'."
            response = self.llm_client.generate_with_tools(prompt, tools=[tool_schema])
            suggestions = response.get("ideas", [])

            # Tasks would be created via REST API in production
            logging.info(f"Generated {len(suggestions)} task suggestions (REST API integration needed)")

            return f"I've generated {len(suggestions)} potential task ideas based on the trend: '{trend}'."
        except Exception as e:
            logging.error(f"Error creating tasks from trends: {e}", exc_info=True)
            return "I'm sorry, I had trouble creating new tasks from the trend."