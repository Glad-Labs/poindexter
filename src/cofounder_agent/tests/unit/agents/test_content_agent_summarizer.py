"""
Unit tests for agents/content_agent/agents/summarizer_agent.py — SummarizerAgent

Tests focus on:
- run(): empty input guard
- run(): successful summarization
- run(): LLM exception fallback (returns "")
"""

import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-placeholder")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_summarizer():
    """Build a SummarizerAgent with mocked LLM and tools."""
    with patch("agents.content_agent.agents.summarizer_agent.CrewAIToolsFactory") as mock_tools:
        mock_tools.get_content_agent_tools.return_value = []
        llm_client = MagicMock()

        from agents.content_agent.agents.summarizer_agent import SummarizerAgent

        agent = SummarizerAgent(llm_client=llm_client)
        agent.llm_client = llm_client
    return agent, llm_client


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


class TestSummarizerAgentRun:
    def test_returns_empty_string_for_empty_input(self):
        agent, llm_client = make_summarizer()
        result = agent.run("", "Summarize: {text}")
        assert result == ""
        llm_client.generate_summary.assert_not_called()

    def test_returns_empty_string_for_none_input(self):
        agent, llm_client = make_summarizer()
        result = agent.run(None, "Summarize: {text}")  # type: ignore[arg-type]
        assert result == ""
        llm_client.generate_summary.assert_not_called()

    def test_calls_llm_with_formatted_prompt(self):
        agent, llm_client = make_summarizer()
        llm_client.generate_summary.return_value = "Concise summary."
        text = "Long article about machine learning and its applications."
        template = "Please summarize: {text}"

        result = agent.run(text, template)

        assert result == "Concise summary."
        llm_client.generate_summary.assert_called_once_with(
            f"Please summarize: {text}"
        )

    def test_returns_empty_string_on_llm_exception(self):
        agent, llm_client = make_summarizer()
        llm_client.generate_summary.side_effect = RuntimeError("LLM failure")

        result = agent.run("Some text to summarize.", "Summarize: {text}")

        assert result == ""

    def test_passes_formatted_template_not_raw_text(self):
        agent, llm_client = make_summarizer()
        llm_client.generate_summary.return_value = "Summary result"

        agent.run("the article content", "Context: Summarize {text} in 3 sentences.")

        llm_client.generate_summary.assert_called_once_with(
            "Context: Summarize the article content in 3 sentences."
        )

    def test_returns_llm_response_as_is(self):
        agent, llm_client = make_summarizer()
        llm_client.generate_summary.return_value = "Multi\nline\nsummary."

        result = agent.run("Article text", "Summarize: {text}")

        assert result == "Multi\nline\nsummary."

    def test_handles_template_format_error(self):
        agent, llm_client = make_summarizer()
        # Template with wrong placeholder raises KeyError when formatted
        result = agent.run("Some text", "Summarize: {wrong_key}")

        # Should catch the KeyError and return ""
        assert result == ""
        llm_client.generate_summary.assert_not_called()
