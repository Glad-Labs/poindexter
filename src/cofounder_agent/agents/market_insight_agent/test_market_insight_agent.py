import pytest
from unittest.mock import MagicMock
from src.agents.market_insight_agent.market_insight_agent import MarketInsightAgent


@pytest.fixture
def market_agent():
    """Fixture to create a MarketInsightAgent with mocked clients."""
    mock_llm_client = MagicMock()
    mock_firestore_client = MagicMock()
    agent = MarketInsightAgent(mock_llm_client, mock_firestore_client)
    # Patch research_agent.run to return empty string for predictable prompt
    agent.research_agent.run = MagicMock(return_value="")
    return agent


def test_suggest_topics(market_agent):
    """Test that the suggest_topics method calls the LLM with the correct prompt."""
    market_agent.llm_client.generate_text.return_value = "1. Topic A\n2. Topic B"
    market_agent.research_agent.run.return_value = ""
    market_agent.suggest_topics("AI in gaming")
    expected_prompt = "Based on the following search results, generate three blog post titles related to 'AI in gaming'. Return them as a numbered list.\n\n---SEARCH RESULTS---\n\n---END SEARCH RESULTS---"
    market_agent.llm_client.generate_text.assert_called_once_with(expected_prompt)


def test_create_tasks_from_trends(market_agent):
    """Test that create_tasks_from_trends calls the LLM and Firestore client correctly."""
    mock_response = {
        "ideas": [
            {
                "topic": "AI in Game Development",
                "primary_keyword": "AI gaming",
                "target_audience": "Developers",
                "category": "Technology",
            }
        ]
    }
    market_agent.llm_client.generate_with_tools = MagicMock(return_value=mock_response)
    market_agent.firestore_client.add_content_task = MagicMock()
    result = market_agent.create_tasks_from_trends("AI trends")
    market_agent.llm_client.generate_with_tools.assert_called_once()
    market_agent.firestore_client.add_content_task.assert_called_once_with(
        mock_response["ideas"][0]
    )
    assert "I've created 1 new tasks" in result
