import pytest
from unittest.mock import MagicMock
from src.cofounder_agent.orchestrator_logic import Orchestrator

@pytest.fixture
def orchestrator():
    """Fixture to create an Orchestrator with mocked clients."""
    orchestrator = Orchestrator()
    orchestrator.firestore_client = MagicMock()
    orchestrator.llm_client = MagicMock()
    orchestrator.financial_agent = MagicMock()
    orchestrator.market_insight_agent = MagicMock()
    orchestrator.pubsub_client = MagicMock()
    return orchestrator

def test_process_command_calendar(orchestrator):
    """Test that 'calendar' commands are correctly routed."""
    orchestrator.process_command("show me the content calendar")
    orchestrator.firestore_client.get_content_queue.assert_called_once()

def test_process_command_create_task(orchestrator):
    """Test that 'create task' commands are correctly routed."""
    orchestrator.process_command("create a new post about AI")
    orchestrator.llm_client.generate_text.assert_called_once()

def test_process_command_financial(orchestrator):
    """Test that 'financial' commands are correctly routed."""
    orchestrator.process_command("what is our current balance?")
    orchestrator.financial_agent.get_financial_summary.assert_called_once()

def test_process_command_suggest_topics(orchestrator):
    """Test that 'suggest topics' commands are correctly routed."""
    orchestrator.process_command("suggest topics about serverless")
    orchestrator.market_insight_agent.suggest_topics.assert_called_once_with("serverless")

def test_process_command_run_pipeline(orchestrator):
    """Test that 'run content agent' commands are correctly routed."""
    orchestrator.process_command("run content agent")
    orchestrator.pubsub_client.publish_message.assert_called_once_with("content-creation-topic", "run")

def test_process_command_unknown(orchestrator):
    """Test that unknown commands return a helpful message."""
    response = orchestrator.process_command("this is an unknown command")
    assert "I'm sorry, I don't understand" in response
