"""
Tests for Summarizer Agent
Tests text summarization functionality
"""

import sys
import types
from unittest.mock import Mock, patch

import pytest

# Mock Google Cloud modules
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
if "google.cloud" not in sys.modules:
    sys.modules["google.cloud"] = types.ModuleType("google.cloud")

from agents.summarizer_agent import SummarizerAgent


@pytest.fixture
def mock_llm_client():
    """Create mock LLM client"""
    mock_client = Mock()
    mock_client.generate_summary.return_value = "This is a concise summary of the text."
    return mock_client


@pytest.fixture
def summarizer_agent(mock_llm_client):
    """Create SummarizerAgent with mocked LLM client"""
    agent = SummarizerAgent(mock_llm_client)
    return agent


@pytest.fixture
def sample_long_text():
    """Sample long text for summarization"""
    return """
    Artificial intelligence is revolutionizing healthcare in numerous ways.
    From diagnostic imaging to personalized treatment plans, AI is making healthcare
    more efficient and effective. Machine learning algorithms can analyze medical images
    faster and more accurately than human radiologists in some cases. Natural language
    processing helps extract valuable information from clinical notes. Predictive
    analytics can identify patients at risk for certain conditions before symptoms appear.
    However, challenges remain including data privacy, algorithmic bias, and the need
    for regulatory oversight. Despite these challenges, the potential benefits of AI
    in healthcare are enormous and continue to grow as technology advances.
    """


@pytest.fixture
def sample_prompt_template():
    """Sample prompt template"""
    return "Please summarize the following text:\n\n{text}\n\nProvide a concise summary."


class TestSummarizerAgentInitialization:
    """Test SummarizerAgent initialization"""

    def test_agent_initializes_with_llm_client(self, mock_llm_client):
        """Test that agent initializes with LLM client"""
        agent = SummarizerAgent(mock_llm_client)

        assert agent.llm_client == mock_llm_client


class TestSummarization:
    """Test summarization functionality"""

    def test_run_returns_summary(
        self, summarizer_agent, sample_long_text, sample_prompt_template, mock_llm_client
    ):
        """Test that run returns a summary"""
        result = summarizer_agent.run(sample_long_text, sample_prompt_template)

        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_run_calls_llm_generate_summary(
        self, summarizer_agent, sample_long_text, sample_prompt_template, mock_llm_client
    ):
        """Test that run calls LLM generate_summary method"""
        summarizer_agent.run(sample_long_text, sample_prompt_template)

        mock_llm_client.generate_summary.assert_called_once()

    def test_run_formats_prompt_correctly(
        self, summarizer_agent, sample_long_text, sample_prompt_template, mock_llm_client
    ):
        """Test that prompt is formatted with text"""
        summarizer_agent.run(sample_long_text, sample_prompt_template)

        call_args = mock_llm_client.generate_summary.call_args[0][0]
        assert sample_long_text.strip() in call_args
        assert "summarize" in call_args.lower()

    def test_run_with_different_templates(
        self, summarizer_agent, sample_long_text, mock_llm_client
    ):
        """Test summarization with different prompt templates"""
        templates = ["Summarize: {text}", "Brief summary of: {text}", "Key points from: {text}"]

        for template in templates:
            result = summarizer_agent.run(sample_long_text, template)
            assert result is not None


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_handles_empty_text(self, summarizer_agent, sample_prompt_template, mock_llm_client):
        """Test handling of empty text"""
        result = summarizer_agent.run("", sample_prompt_template)

        assert result == ""
        mock_llm_client.generate_summary.assert_not_called()

    def test_handles_none_text(self, summarizer_agent, sample_prompt_template, mock_llm_client):
        """Test handling of None text"""
        result = summarizer_agent.run(None, sample_prompt_template)

        assert result == ""
        mock_llm_client.generate_summary.assert_not_called()

    def test_handles_whitespace_only(
        self, summarizer_agent, sample_prompt_template, mock_llm_client
    ):
        """Test handling of whitespace-only text"""
        result = summarizer_agent.run("   \n\t  ", sample_prompt_template)

        # May return empty or attempt to summarize
        assert isinstance(result, str)

    def test_handles_very_short_text(
        self, summarizer_agent, sample_prompt_template, mock_llm_client
    ):
        """Test handling of very short text"""
        short_text = "AI is useful."
        result = summarizer_agent.run(short_text, sample_prompt_template)

        assert result is not None


class TestErrorHandling:
    """Test error handling"""

    def test_handles_llm_error(
        self, summarizer_agent, sample_long_text, sample_prompt_template, mock_llm_client
    ):
        """Test handling of LLM errors"""
        mock_llm_client.generate_summary.side_effect = Exception("LLM API Error")

        result = summarizer_agent.run(sample_long_text, sample_prompt_template)

        # Should return empty string on error
        assert result == ""

    def test_handles_invalid_prompt_template(
        self, summarizer_agent, sample_long_text, mock_llm_client
    ):
        """Test handling of invalid prompt template"""
        invalid_template = "Summary without placeholder"

        # May raise KeyError or handle gracefully
        try:
            result = summarizer_agent.run(sample_long_text, invalid_template)
            # If handled, should return something
            assert result == "" or isinstance(result, str)
        except KeyError:
            # Acceptable to raise error for invalid template
            pass

    def test_handles_network_timeout(
        self, summarizer_agent, sample_long_text, sample_prompt_template, mock_llm_client
    ):
        """Test handling of network timeouts"""
        import socket

        mock_llm_client.generate_summary.side_effect = socket.timeout("Timeout")

        result = summarizer_agent.run(sample_long_text, sample_prompt_template)

        assert result == ""


class TestSummaryQuality:
    """Test summary quality expectations"""

    def test_summary_shorter_than_input(
        self, summarizer_agent, sample_long_text, sample_prompt_template, mock_llm_client
    ):
        """Test that summary is shorter than input (when mocked realistically)"""
        mock_llm_client.generate_summary.return_value = (
            "AI is transforming healthcare with ML and NLP."
        )

        result = summarizer_agent.run(sample_long_text, sample_prompt_template)

        # In real usage, summary should be shorter
        assert len(result) < len(sample_long_text)

    def test_handles_very_long_text(
        self, summarizer_agent, sample_prompt_template, mock_llm_client
    ):
        """Test handling of very long text"""
        very_long_text = "This is a sentence. " * 1000  # 1000 sentences

        result = summarizer_agent.run(very_long_text, sample_prompt_template)

        assert result is not None
        mock_llm_client.generate_summary.assert_called_once()


@pytest.mark.integration
class TestSummarizerAgentIntegration:
    """Integration tests"""

    @pytest.mark.skip(reason="Requires actual LLM API")
    def test_real_summarization(self):
        """Test with real LLM client"""
        from services.llm_client import LLMClient

        llm_client = LLMClient()
        agent = SummarizerAgent(llm_client)

        text = """
        Machine learning is a subset of artificial intelligence that enables systems
        to learn and improve from experience without being explicitly programmed.
        It focuses on developing computer programs that can access data and use it
        to learn for themselves. The process of learning begins with observations
        or data, such as examples, direct experience, or instruction, in order to
        look for patterns in data and make better decisions in the future.
        """

        template = "Summarize this in one sentence: {text}"
        result = agent.run(text, template)

        assert result is not None
        assert len(result) > 0
        assert len(result) < len(text)


@pytest.mark.performance
class TestSummarizerAgentPerformance:
    """Performance tests"""

    def test_summarization_performance(
        self, summarizer_agent, sample_long_text, sample_prompt_template, mock_llm_client
    ):
        """Test that summarization completes quickly"""
        import time

        start = time.time()
        summarizer_agent.run(sample_long_text, sample_prompt_template)
        duration = time.time() - start

        # Should complete quickly with mocked client
        assert duration < 1.0

    def test_multiple_summarizations(
        self, summarizer_agent, sample_prompt_template, mock_llm_client
    ):
        """Test multiple summarizations in sequence"""
        texts = [f"Text number {i} to summarize." * 10 for i in range(10)]

        import time

        start = time.time()

        for text in texts:
            summarizer_agent.run(text, sample_prompt_template)

        duration = time.time() - start

        # All should complete quickly with mocked client
        assert duration < 2.0
        assert mock_llm_client.generate_summary.call_count == 10
