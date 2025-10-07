import logging
from agents.content_agent.services.firestore_client import FirestoreClient
from agents.content_agent.services.llm_client import LLMClient
from agents.financial_agent.agent import FinancialAgent
from agents.market_insight_agent.agent import MarketInsightAgent
from agents.compliance_agent.agent import ComplianceAgent
# In the future, we will add a PubSubClient to delegate tasks
# from agents.content_agent.services.pubsub_client import PubSubClient

class Orchestrator:
    """
    The AI Business Assistant. It understands user commands and delegates tasks
    to specialized agents or interacts with data sources directly.
    """
    def __init__(self):
        self.firestore_client = FirestoreClient()
        self.llm_client = LLMClient()
        self.financial_agent = FinancialAgent()
        self.market_insight_agent = MarketInsightAgent()
        self.compliance_agent = ComplianceAgent(workspace_root=".") # Assuming the agent runs from the root
        # self.pubsub_client = PubSubClient(...)
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
            # This is a simple implementation. A more robust version would parse the query.
            base_query = command.replace("suggest topics about", "").strip()
            return self.market_insight_agent.suggest_topics(base_query)
        elif "security audit" in command or "compliance check" in command:
            return self.compliance_agent.run_security_audit()
        elif "run content agent" in command or "execute tasks" in command:
            return self.run_content_pipeline()
        else:
            return "I'm sorry, I don't understand that command yet. You can ask me to 'show the content calendar', 'create a new task', 'suggest topics', 'run a security audit', or 'run the content agent'."

    def get_content_calendar(self) -> str:
        """Fetches the content calendar from Firestore and formats it as a string."""
        try:
            tasks = self.firestore_client.get_content_queue()
            if not tasks:
                return "The content calendar is currently empty."
            
            response = "Here is the current content calendar:\\n"
            for task in tasks:
                response += f"- **{task['topic']}** (Status: {task['status']})\\n"
            return response
        except Exception as e:
            logging.error(f"Error fetching content calendar: {e}")
            return "I'm sorry, I had trouble fetching the content calendar from Firestore."

    def create_content_task(self, command: str) -> str:
        """
        Parses a user's command to create a new content task and saves it to Firestore.
        """
        try:
            # Use the LLM to parse the user's command into structured data
            # This is a simple implementation; a more robust version would use a dedicated prompt
            # and more sophisticated parsing logic.
            prompt = f"Parse the following command into a JSON object with keys 'topic', 'primary_keyword', 'target_audience', and 'category'. Command: '{command}'"
            
            # For now, we'll assume the LLM client can generate JSON directly.
            # This is a placeholder for a more robust implementation.
            # In a real-world scenario, you would want to use a more reliable method
            # for getting structured data from the LLM.
            task_data = self.llm_client.generate_json(prompt)

            if not all(k in task_data for k in ['topic', 'primary_keyword', 'target_audience', 'category']):
                return "I'm sorry, I couldn't understand all the details for the new task. Please be more specific."

            # Add the new task to Firestore
            self.firestore_client.add_task(task_data)
            return f"I have created a new task: '{task_data['topic']}'."

        except Exception as e:
            logging.error(f"Error creating content task: {e}")
            return "I'm sorry, I encountered an error while trying to create the new task."

    def run_content_pipeline(self) -> str:
        """
        Triggers the content agent pipeline to run for all 'Ready' tasks.
        This will be done via Pub/Sub in a future, more robust implementation.
        """
        try:
            # For now, we will directly call the content agent's orchestrator.
            # This is a placeholder for a Pub/Sub implementation.
            from agents.content_agent.orchestrator import Orchestrator as ContentOrchestrator
            
            content_orchestrator = ContentOrchestrator()
            content_orchestrator.run_batch_job()
            
            return "Content agent pipeline has been triggered for all 'Ready' tasks."
        except Exception as e:
            logging.error(f"Error running content pipeline: {e}")
            return "I'm sorry, I encountered an error while trying to run the content pipeline."

