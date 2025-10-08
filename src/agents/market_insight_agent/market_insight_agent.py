import logging
import json
from agents.content_agent.services.llm_client import LLMClient
from agents.content_agent.services.firestore_client import FirestoreClient

class MarketInsightAgent:
    """
    A specialized agent for analyzing market trends and suggesting content topics.
    """
    def __init__(self):
        """Initializes the MarketInsightAgent."""
        self.llm_client = LLMClient()
        self.firestore_client = FirestoreClient()
        logging.info("Market Insight Agent initialized.")

    def suggest_topics(self, base_query: str) -> str:
        """
        Suggests new content topics based on a base query.

        In the future, this will use a web search tool to gather real-time data.
        For now, it uses the LLM to generate suggestions based on the query.
        """
        try:
            prompt = f"Generate three blog post titles based on the topic: '{base_query}'. Return them as a numbered list."
            suggestions = self.llm_client.generate_text(prompt)
            return f"Here are some topic suggestions based on '{base_query}':\\n{suggestions}"
        except Exception as e:
            logging.error(f"Error suggesting topics: {e}", exc_info=True)
            return "I'm sorry, I had trouble generating topic suggestions."

    def create_tasks_from_trends(self, trend: str) -> str:
        """
        Analyzes a trend, generates topic ideas, and creates new tasks in Firestore.
        """
        try:
            prompt = f"Generate three blog post ideas based on the trend: '{trend}'. For each idea, provide a 'topic', 'primary_keyword', 'target_audience', and 'category'. Return them as a JSON array of objects."
            suggestions_json = self.llm_client.generate_text(prompt)
            suggestions = json.loads(suggestions_json)

            for task_data in suggestions:
                self.firestore_client.add_content_task(task_data)

            return f"I've created {len(suggestions)} new tasks based on the trend: '{trend}'. You can see them in the content calendar."
        except Exception as e:
            logging.error(f"Error creating tasks from trends: {e}", exc_info=True)
            return "I'm sorry, I had trouble creating new tasks from the trend."
