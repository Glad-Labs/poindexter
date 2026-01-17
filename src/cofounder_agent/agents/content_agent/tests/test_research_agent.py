"""
Tests for Research Agent
Tests web search via Serper API, research data collection, and source validation
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
import sys
import types
import httpx

# Mock Google Cloud modules
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
if "google.cloud" not in sys.modules:
    sys.modules["google.cloud"] = types.ModuleType("google.cloud")

from agents.research_agent import ResearchAgent


@pytest.fixture
def mock_serper_response():
    """Create mock Serper API response"""
    return {
        "organic": [
            {
                "title": "Test Result 1",
                "link": "https://example.com/1",
                "snippet": "This is a test snippet about the topic",
            },
            {
                "title": "Test Result 2",
                "link": "https://example.com/2",
                "snippet": "Another relevant piece of information",
            },
            {
                "title": "Test Result 3",
                "link": "https://example.com/3",
                "snippet": "Third research finding",
            },
        ]
    }


@pytest.fixture
def research_agent():
    """Create ResearchAgent with mocked API key"""
    with patch("agents.research_agent.config.SERPER_API_KEY", "test_api_key"):
        agent = ResearchAgent()
    return agent


@pytest.fixture
def sample_topic():
    """Sample research topic"""
    return "Artificial Intelligence in Healthcare"


@pytest.fixture
def sample_keywords():
    """Sample keywords list"""
    return ["machine learning", "diagnosis", "patient care"]


class TestResearchAgentInitialization:
    """Test ResearchAgent initialization"""

    @pytest.mark.asyncio
    async def test_agent_initializes_with_api_key(self):
        """Test that ResearchAgent initializes with Serper API key"""
        with patch("agents.research_agent.config.SERPER_API_KEY", "test_api_key"):
            agent = ResearchAgent()

        assert agent.serper_api_key == "test_api_key"

    @pytest.mark.asyncio
    async def test_agent_requires_api_key(self):
        """Test that agent raises error without API key"""
        with patch("agents.research_agent.config.SERPER_API_KEY", None):
            with pytest.raises(ValueError, match="SERPER_API_KEY"):
                ResearchAgent()

    @pytest.mark.asyncio
    async def test_agent_has_run_method(self, research_agent):
        """Test that agent has required run method"""
        assert hasattr(research_agent, "run")
        assert callable(research_agent.run)


class TestResearchRun:
    """Test research run functionality"""

    @pytest.mark.asyncio
    async def test_run_with_topic_and_keywords(
        self, research_agent, sample_topic, sample_keywords, mock_serper_response
    ):
        """Test that run method conducts research with topic and keywords"""
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_serper_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run(sample_topic, sample_keywords)

        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_run_formats_results_correctly(self, research_agent, mock_serper_response):
        """Test that results are formatted with title, link, snippet"""
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_serper_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run("Test Topic", ["keyword1"])

        assert "Title:" in result
        assert "Link:" in result
        assert "Snippet:" in result
        assert "---" in result  # Separator

    @pytest.mark.asyncio
    async def test_run_combines_topic_and_keywords(self, research_agent, mock_serper_response):
        """Test that run combines topic and keywords into query"""
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_serper_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            topic = "AI"
            keywords = ["healthcare", "diagnosis"]
            await research_agent.run(topic, keywords)

            # Check the request was made with combined query
            call_args = mock_client.post.call_args
            json_data = call_args[1]["json"]
            assert "AI" in json_data.get("q", "")
            assert "healthcare" in json_data.get("q", "") or "diagnosis" in json_data.get("q", "")

    @pytest.mark.asyncio
    async def test_run_limits_results_to_top_5(self, research_agent):
        """Test that only top 5 results are returned"""
        many_results = {
            "organic": [
                {
                    "title": f"Result {i}",
                    "link": f"https://example.com/{i}",
                    "snippet": f"Content {i}",
                }
                for i in range(20)
            ]
        }

        mock_response = AsyncMock()
        mock_response.json.return_value = many_results
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run("Topic", ["keyword"])

        # Should only include 5 results
        result_count = result.count("Title:")
        assert result_count == 5


class TestAPIIntegration:
    """Test Serper API integration"""

    @pytest.mark.asyncio
    async def test_makes_post_request_to_serper(self, research_agent, mock_serper_response):
        """Test that POST request is made to Serper API"""
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_serper_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            await research_agent.run("Test", ["keyword"])

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "google.serper.dev" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_includes_api_key_in_headers(self, research_agent, mock_serper_response):
        """Test that API key is included in request headers"""
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_serper_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            await research_agent.run("Test", ["keyword"])

            headers = mock_client.post.call_args[1]["headers"]
            assert "X-API-KEY" in headers
            assert headers["X-API-KEY"] == "test_api_key"

    @pytest.mark.asyncio
    async def test_sends_json_payload(self, research_agent, mock_serper_response):
        """Test that JSON payload is sent correctly"""
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_serper_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            await research_agent.run("AI Healthcare", ["ML"])

            headers = mock_client.post.call_args[1]["headers"]
            assert headers["Content-Type"] == "application/json"


class TestErrorHandling:
    """Test error handling and edge cases"""

    @pytest.mark.asyncio
    async def test_handles_request_exception(self, research_agent):
        """Test handling of request errors"""
        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.side_effect = httpx.HTTPError("API Error")
            mock_client_class.return_value = mock_client

            result = await research_agent.run("Test", ["keyword"])

        assert result == ""  # Returns empty string on error

    @pytest.mark.asyncio
    async def test_handles_http_error(self, research_agent):
        """Test handling of HTTP errors"""
        mock_response = AsyncMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPError("404")

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run("Test", ["keyword"])

        assert result == ""

    @pytest.mark.asyncio
    async def test_handles_empty_search_results(self, research_agent):
        """Test handling of empty search results"""
        empty_response = {"organic": []}
        mock_response = AsyncMock()
        mock_response.json.return_value = empty_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run("Obscure Topic", ["keyword"])

        # Should return empty string or minimal content
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_handles_missing_organic_key(self, research_agent):
        """Test handling of response without organic key"""
        malformed_response = {"knowledgeGraph": {}}
        mock_response = AsyncMock()
        mock_response.json.return_value = malformed_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run("Test", ["keyword"])

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_handles_empty_topic(self, research_agent, mock_serper_response):
        """Test handling of empty topic string"""
        mock_response = AsyncMock()
        mock_response.json.return_value = mock_serper_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run("", [])

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_handles_json_decode_error(self, research_agent):
        """Test handling of invalid JSON response"""
        mock_response = AsyncMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run("Test", ["keyword"])

        assert result == ""


class TestResultFormatting:
    """Test research result formatting"""

    @pytest.mark.asyncio
    async def test_formats_complete_results(self, research_agent):
        """Test formatting of complete search results"""
        complete_response = {
            "organic": [
                {
                    "title": "Complete Result",
                    "link": "https://example.com",
                    "snippet": "Full information here",
                }
            ]
        }

        mock_response = AsyncMock()
        mock_response.json.return_value = complete_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run("Test", ["keyword"])

        assert "Complete Result" in result
        assert "https://example.com" in result
        assert "Full information here" in result

    @pytest.mark.asyncio
    async def test_handles_missing_fields_in_results(self, research_agent):
        """Test handling of results with missing fields"""
        incomplete_response = {
            "organic": [
                {"title": "Only Title"},  # Missing link and snippet
                {"link": "https://example.com"},  # Missing title and snippet
            ]
        }

        mock_response = AsyncMock()
        mock_response.json.return_value = incomplete_response
        mock_response.raise_for_status = Mock()

        with patch("agents.research_agent.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await research_agent.run("Test", ["keyword"])

        # Should handle gracefully
        assert isinstance(result, str)


@pytest.mark.integration
class TestResearchAgentIntegration:
    """Integration tests for ResearchAgent (require actual API)"""

    @pytest.mark.skip(reason="Requires actual Serper API key")
    def test_real_search_integration(self):
        """Test actual Serper API integration"""
        agent = ResearchAgent()
        result = agent.run("Python programming", ["tutorial", "guide"])

        assert result is not None
        assert len(result) > 0
        assert "Title:" in result

    @pytest.mark.skip(reason="Requires actual Serper API key")
    def test_research_quality_check(self):
        """Test quality of actual research results"""
        agent = ResearchAgent()
        result = agent.run("Machine Learning", ["algorithms", "models"])

        # Results should be relevant and substantive
        assert result is not None
        assert len(result) > 100  # Should have meaningful content


@pytest.mark.performance
class TestResearchAgentPerformance:
    """Performance tests for ResearchAgent"""

    def test_research_performance(
        self, research_agent, sample_topic, sample_keywords, mock_serper_response
    ):
        """Test that research completes within acceptable time"""
        import time

        with patch("agents.research_agent.requests.post") as mock_post:
            mock_post.return_value.json.return_value = mock_serper_response
            mock_post.return_value.raise_for_status = Mock()

            start = time.time()
            result = research_agent.run(sample_topic, sample_keywords)
            duration = time.time() - start

        # Should complete quickly with mocked API
        assert duration < 1.0
        assert result is not None
