"""
This module contains the core logic for the Co-Founder Agent's orchestrator.
It receives commands and delegates them to the appropriate specialized agents.
"""
import logging
from typing import Dict, Any, List
import json
import os

from agents.content_agent.services.firestore_client import FirestoreClient
from agents.content_agent.services.llm_client import LLMClient
from agents.content_agent.content_agent import ContentAgent
from agents.financial_agent.financial_agent import FinancialAgent
from agents.market_insight_agent.market_insight_agent import MarketInsightAgent
from agents.content_agent.services.pubsub_client import PubSubClient
from agents.compliance_agent.agent import ComplianceAgent

class Orchestrator:
    """The main orchestrator for the AI Co-Founder."""

    def __init__(self):
        """Initializes the Orchestrator with all specialized agents."""
        # --- LLM Client Configuration ---
        # Allow flexible configuration of LLM providers via environment variables.
        # Defaults to 'ollama' for local development to minimize costs.
        parsing_llm_provider = os.getenv("PARSING_LLM_PROVIDER", "ollama")
        insights_llm_provider = os.getenv("INSIGHTS_LLM_PROVIDER", "ollama")
        # Default to Gemini for final content generation, but allow override.
        content_llm_provider = os.getenv("CONTENT_LLM_PROVIDER", "gemini")

        # --- Client Initialization ---
        # Create a dictionary of clients to avoid re-initializing the same client.
        self.llm_clients = {
            "ollama": LLMClient(provider="ollama"),
            "gemini": LLMClient(provider="gemini")
        }

        # Select the correct client based on configuration.
        self.parsing_llm_client = self.llm_clients.get(parsing_llm_provider)
        self.insights_llm_client = self.llm_clients.get(insights_llm_provider)
        self.content_llm_client = self.llm_clients.get(content_llm_provider)

        if not all([self.parsing_llm_client, self.insights_llm_client, self.content_llm_client]):
            raise ValueError("An invalid LLM provider was configured. Check your environment variables.")

        self.firestore_client = FirestoreClient()
        self.pubsub_client = PubSubClient()

        # --- Agent Initialization (with Dependency Injection) ---
        self.content_agent = ContentAgent(llm_client=self.content_llm_client, firestore_client=self.firestore_client)
        self.financial_agent = FinancialAgent()
        self.market_insight_agent = MarketInsightAgent(llm_client=self.insights_llm_client, firestore_client=self.firestore_client)
        logging.info("Orchestrator initialized with all agents.")

    def process_command(self, command: str) -> str:
        """
        The main entry point for processing a user's chat command.
        It uses simple intent recognition to route the command to the appropriate tool.
        """
        command = command.lower().strip()

        if "calendar" in command or "tasks" in command:
            return self.get_content_calendar()
        elif "create task" in command or "new post" in command:
            return self.create_content_task(command)
        elif "financial" in command or "balance" in command or "spend" in command:
            return self.financial_agent.get_financial_summary()
        elif "suggest topics" in command or "new ideas" in command:
            base_query = command.replace("suggest topics about", "").strip()
            return self.market_insight_agent.suggest_topics(base_query)
        elif "create tasks from trend" in command:
            trend = command.replace("create tasks from trend", "").strip()
            return self.market_insight_agent.create_tasks_from_trends(trend)
        elif "run content agent" in command or "execute tasks" in command:
            return self.run_content_pipeline()
        else:
            return "I'm sorry, I don't understand that command yet. You can ask me to 'show the content calendar', 'create a new task', or 'run the content agent'."

    def get_content_calendar(self) -> str:
        """Fetches the content calendar from Firestore and formats it as a string."""
        try:
            tasks: List[Dict[str, Any]] = self.firestore_client.get_content_queue()
            if not tasks:
                return "The content calendar is currently empty."
            
            response = "Here is the current content calendar:\n"
            for task in tasks:
                response += f"- {task.get('topic', 'No Topic')} (Status: {task.get('status', 'N/A')})\\n"
            return response
        except Exception as e:
            logging.error(f"Error fetching content calendar: {e}", exc_info=True)
            return "I'm sorry, I had trouble fetching the content calendar from Firestore."

    def create_content_task(self, command: str) -> str:
        """
        Parses a user's command to create a new content task and saves it to Firestore.
        """
        try:
            # Define the desired structure for the LLM to populate.
            # This is more efficient than asking the model to parse a long string.
            tool_schema = {
                "type": "function",
                "function": {
                    "name": "create_content_task",
                    "description": "Creates a new content task with a topic, keyword, audience, and category.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "description": "The main topic of the content."},
                            "primary_keyword": {"type": "string", "description": "The primary SEO keyword."},
                            "target_audience": {"type": "string", "description": "The intended audience for the content."},
                            "category": {"type": "string", "description": "A content category, like 'Technology' or 'Finance'."}
                        },
                        "required": ["topic", "primary_keyword", "target_audience", "category"]
                    }
                }
            }

            # Use the LLM's tool-calling feature to extract structured data.
            # This assumes your LLMClient has a method that supports tool/function calling.
            task_data = self.parsing_llm_client.generate_with_tools(command, tools=[tool_schema])

            if not all(k in task_data for k in ['topic', 'primary_keyword', 'target_audience', 'category']):
                return "I'm sorry, I couldn't understand all the details. Please provide a topic, primary keyword, target audience, and category."

            self.firestore_client.add_content_task(task_data)
            return f"I've created a new content task with the topic: '{task_data['topic']}'."
        except Exception as e:
            logging.error(f"Error creating content task: {e}", exc_info=True)
            return "I'm sorry, I had trouble creating the new content task."

    def run_content_pipeline(self) -> str:
        """
        Triggers the content agent pipeline for all 'Ready' tasks by publishing a message to Pub/Sub.
        """
        try:
            self.pubsub_client.publish_message("content-creation-topic", "run")
            return "I've sent a signal to the Content Agent to begin processing all 'Ready' tasks. You can monitor their progress in the Oversight Hub."
        except Exception as e:
            logging.error(f"Error running content pipeline: {e}", exc_info=True)
            return "I'm sorry, I encountered an error while trying to start the content pipeline."
