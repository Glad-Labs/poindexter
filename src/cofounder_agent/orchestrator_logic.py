import logging
from typing import Dict, Any, List
import json

from agents.content_agent.services.firestore_client import FirestoreClient
from agents.content_agent.services.llm_client import LLMClient
from agents.financial_agent.financial_agent import FinancialAgent
from agents.market_insight_agent.market_insight_agent import MarketInsightAgent
from agents.content_agent.services.pubsub_client import PubSubClient
# from agents.compliance_agent.agent import ComplianceAgent

class Orchestrator:
    """
    The AI Business Assistant. It understands user commands and delegates tasks
    to specialized agents or interacts with data sources directly.
    """
    def __init__(self):
        """Initializes the Orchestrator and its clients."""
        self.firestore_client = FirestoreClient()
        self.llm_client = LLMClient()
        self.financial_agent = FinancialAgent()
        self.market_insight_agent = MarketInsightAgent()
        self.pubsub_client = PubSubClient()
        # self.compliance_agent = ComplianceAgent(workspace_root=".")
        logging.info("Orchestrator Agent logic initialized.")

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
        # elif "security audit" in command or "compliance check" in command:
        #     return self.compliance_agent.run_security_audit()
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
            # This is a simplified implementation. A more robust version would use a dedicated prompt
            # and more sophisticated parsing logic with validation.
            prompt = f"Parse the following command into a JSON object with keys 'topic', 'primary_keyword', 'target_audience', and 'category'. Command: '{command}'"
            parsed_data = self.llm_client.generate_text(prompt)
            
            # Basic validation
            task_data = json.loads(parsed_data)
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

