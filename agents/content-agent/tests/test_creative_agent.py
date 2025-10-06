import pytest
from unittest.mock import MagicMock
from agents.creative_agent import CreativeAgent

@pytest.fixture
def creative_agent():
    mock_llm_client = MagicMock()
    return CreativeAgent(llm_client=mock_llm_client)

def test_extract_asset_success(creative_agent):
    """Tests that the _extract_asset method correctly parses a value."""
    text = "Title: This is the Title\\nMetaDescription: This is the description."
    result = creative_agent._extract_asset(text, "Title")
    assert result == "This is the Title"

def test_extract_asset_not_found(creative_agent):
    """Tests that the _extract_asset method returns an empty string if the asset is not found."""
    text = "MetaDescription: This is the description."
    result = creative_agent._extract_asset(text, "Title")
    assert result == ""
