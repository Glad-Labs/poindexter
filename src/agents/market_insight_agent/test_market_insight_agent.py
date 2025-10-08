import pytest
from unittest.mock import MagicMock
from src.agents.market_insight_agent.market_insight_agent import MarketInsightAgent

@pytest.fixture
def market_agent():
    """Fixture to create a MarketInsightAgent with a mocked LLM client."""
    agent = MarketInsightAgent()
    agent.llm_client = MagicMock()
    agent.firestore_client = MagicMock()
    return agent

def test_suggest_topics(market_agent):
    """Test that the suggest_topics method calls the LLM with the correct prompt."""
    market_agent.llm_client.generate_text.return_value = "1. Topic A\\n2. Topic B"
    market_agent.suggest_topics("AI in gaming")
    market_agent.llm_client.generate_text.assert_called_once_with(
        "Generate three blog post titles based on the topic: 'AI in gaming'. Return them as a numbered list."
    )

def test_create_tasks_from_trends(market_agent):
    """Test that create_tasks_from_trends calls the LLM and Firestore client correctly."""
    mock_json_response = '[{"topic": "AI in Game Development", "primary_keyword": "AI gaming", "target_audience": "Developers", "category": "Technology"}]'
    market_agent.llm_client.generate_text.return_value = mock_json_response
    
    market_agent.create_tasks_from_trends("AI trends")
    
    market_agent.llm_client.generate_text.assert_called_once()
    market_agent.firestore_client.add_content_task.assert_called_once()
