"""
Unit tests for agents/content_agent/utils/tools.py

Covers:
- WebSearchTool — warns when SERPER_API_KEY is absent
- CompetitorContentSearchTool — handles init failure gracefully
- DocumentAccessTool — read_research_file success/failure paths
- DirectoryAccessTool — default and custom directory init
- DataProcessingTool — process_data success/failure paths
- CrewAIToolsFactory — singleton caching, reset, and tool-list methods
"""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers — patch the entire crewai_tools import shim so tests run without
# the real crewai_tools package installed (which is not in test deps).
# The module falls back to crewai_tools_mock if ImportError is raised;
# we rely on that fallback being already active in the test environment.
# ---------------------------------------------------------------------------


def _fresh_factory():
    """Return a CrewAIToolsFactory with a clean instance cache."""
    from agents.content_agent.utils.tools import CrewAIToolsFactory
    CrewAIToolsFactory.reset_instances()
    return CrewAIToolsFactory


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------


class TestWebSearchTool:
    def test_instantiates_without_error(self):
        from agents.content_agent.utils.tools import WebSearchTool
        tool = WebSearchTool()
        assert tool is not None

    def test_warns_when_serper_api_key_missing(self):
        from agents.content_agent.utils.tools import WebSearchTool
        with patch.dict("os.environ", {}, clear=True):
            with patch("agents.content_agent.utils.tools.logger") as mock_logger:
                WebSearchTool()
                # logger.warning must be called about SERPER_API_KEY
                warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
                assert any("SERPER_API_KEY" in c for c in warning_calls)

    def test_no_warning_when_serper_api_key_present(self):
        from agents.content_agent.utils.tools import WebSearchTool
        with patch.dict("os.environ", {"SERPER_API_KEY": "fake-key"}):
            with patch("agents.content_agent.utils.tools.logger") as mock_logger:
                WebSearchTool()
                warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
                assert not any("SERPER_API_KEY" in c for c in warning_calls)


# ---------------------------------------------------------------------------
# CompetitorContentSearchTool
# ---------------------------------------------------------------------------


class TestCompetitorContentSearchTool:
    def test_instantiates_without_error_when_parent_succeeds(self):
        from agents.content_agent.utils.tools import CompetitorContentSearchTool
        # The mock WebsiteSearchTool.__init__ doesn't raise, so this should work fine
        tool = CompetitorContentSearchTool()
        assert tool is not None

    def test_handles_init_exception_gracefully(self):
        from agents.content_agent.utils.tools import CompetitorContentSearchTool
        # Patch the parent __init__ to raise an error
        with patch(
            "agents.content_agent.utils.tools.WebsiteSearchTool.__init__",
            side_effect=RuntimeError("missing CHROMA_OPENAI_API_KEY"),
        ):
            tool = CompetitorContentSearchTool()
            # Should not raise; error is stored on the instance
            assert hasattr(tool, "_initialization_error")
            assert "missing CHROMA_OPENAI_API_KEY" in tool._initialization_error
            assert tool._is_available is False

    def test_logs_warning_on_init_failure(self):
        from agents.content_agent.utils.tools import CompetitorContentSearchTool
        with patch(
            "agents.content_agent.utils.tools.WebsiteSearchTool.__init__",
            side_effect=RuntimeError("bad api key"),
        ):
            with patch("agents.content_agent.utils.tools.logger") as mock_logger:
                CompetitorContentSearchTool()
                assert mock_logger.warning.called


# ---------------------------------------------------------------------------
# DocumentAccessTool
# ---------------------------------------------------------------------------


class TestDocumentAccessTool:
    def test_instantiates_and_logs_init(self):
        from agents.content_agent.utils.tools import DocumentAccessTool
        with patch("agents.content_agent.utils.tools.logger") as mock_logger:
            tool = DocumentAccessTool()
            assert tool is not None
            mock_logger.info.assert_called()

    def test_read_research_file_returns_content_on_success(self):
        from agents.content_agent.utils.tools import DocumentAccessTool
        tool = DocumentAccessTool()
        tool.run = MagicMock(return_value="file contents here")
        result = tool.read_research_file("/path/to/doc.md")
        assert result == "file contents here"

    def test_read_research_file_returns_none_on_file_not_found(self):
        from agents.content_agent.utils.tools import DocumentAccessTool
        tool = DocumentAccessTool()
        tool.run = MagicMock(side_effect=FileNotFoundError("no such file"))
        result = tool.read_research_file("/missing/file.txt")
        assert result is None

    def test_read_research_file_returns_none_on_generic_exception(self):
        from agents.content_agent.utils.tools import DocumentAccessTool
        tool = DocumentAccessTool()
        tool.run = MagicMock(side_effect=OSError("permission denied"))
        result = tool.read_research_file("/restricted/file.txt")
        assert result is None

    def test_read_research_file_logs_error_on_file_not_found(self):
        from agents.content_agent.utils.tools import DocumentAccessTool
        tool = DocumentAccessTool()
        tool.run = MagicMock(side_effect=FileNotFoundError("no file"))
        with patch("agents.content_agent.utils.tools.logger") as mock_logger:
            tool.read_research_file("/missing.txt")
            mock_logger.error.assert_called()

    def test_read_research_file_logs_error_on_generic_exception(self):
        from agents.content_agent.utils.tools import DocumentAccessTool
        tool = DocumentAccessTool()
        tool.run = MagicMock(side_effect=ValueError("parse error"))
        with patch("agents.content_agent.utils.tools.logger") as mock_logger:
            tool.read_research_file("/bad.txt")
            mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# DirectoryAccessTool
# ---------------------------------------------------------------------------


class TestDirectoryAccessTool:
    def test_instantiates_with_default_directory(self):
        from agents.content_agent.utils.tools import DirectoryAccessTool
        tool = DirectoryAccessTool()
        assert tool is not None

    def test_instantiates_with_custom_directory(self):
        from agents.content_agent.utils.tools import DirectoryAccessTool
        tool = DirectoryAccessTool(directory="/tmp/docs")
        assert tool is not None

    def test_logs_init_message(self):
        from agents.content_agent.utils.tools import DirectoryAccessTool
        with patch("agents.content_agent.utils.tools.logger") as mock_logger:
            DirectoryAccessTool(directory="/some/path")
            mock_logger.info.assert_called()
            call_str = str(mock_logger.info.call_args_list)
            assert "/some/path" in call_str

    def test_logs_default_directory_in_init(self):
        from agents.content_agent.utils.tools import DirectoryAccessTool
        with patch("agents.content_agent.utils.tools.logger") as mock_logger:
            DirectoryAccessTool()
            call_str = str(mock_logger.info.call_args_list)
            assert "./" in call_str


# ---------------------------------------------------------------------------
# DataProcessingTool
# ---------------------------------------------------------------------------


class TestDataProcessingTool:
    def test_instantiates_and_logs(self):
        from agents.content_agent.utils.tools import DataProcessingTool
        with patch("agents.content_agent.utils.tools.logger") as mock_logger:
            tool = DataProcessingTool()
            assert tool is not None
            mock_logger.info.assert_called()

    def test_process_data_returns_result_on_success(self):
        from agents.content_agent.utils.tools import DataProcessingTool
        tool = DataProcessingTool()
        tool.run = MagicMock(return_value="42")
        result = tool.process_data("x = 6 * 7; print(x)")
        assert result == "42"

    def test_process_data_returns_none_on_exception(self):
        from agents.content_agent.utils.tools import DataProcessingTool
        tool = DataProcessingTool()
        tool.run = MagicMock(side_effect=RuntimeError("execution error"))
        result = tool.process_data("raise RuntimeError()")
        assert result is None

    def test_process_data_logs_error_on_exception(self):
        from agents.content_agent.utils.tools import DataProcessingTool
        tool = DataProcessingTool()
        tool.run = MagicMock(side_effect=RuntimeError("oops"))
        with patch("agents.content_agent.utils.tools.logger") as mock_logger:
            tool.process_data("bad code")
            mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# CrewAIToolsFactory — singleton caching and reset
# ---------------------------------------------------------------------------


class TestCrewAIToolsFactory:
    def setup_method(self):
        """Reset factory singleton state before each test."""
        from agents.content_agent.utils.tools import CrewAIToolsFactory
        CrewAIToolsFactory.reset_instances()

    def test_get_web_search_tool_returns_instance(self):
        factory = _fresh_factory()
        tool = factory.get_web_search_tool()
        from agents.content_agent.utils.tools import WebSearchTool
        assert isinstance(tool, WebSearchTool)

    def test_get_web_search_tool_returns_same_instance_on_second_call(self):
        factory = _fresh_factory()
        t1 = factory.get_web_search_tool()
        t2 = factory.get_web_search_tool()
        assert t1 is t2

    def test_get_competitor_search_tool_returns_instance(self):
        factory = _fresh_factory()
        tool = factory.get_competitor_search_tool()
        from agents.content_agent.utils.tools import CompetitorContentSearchTool
        assert isinstance(tool, CompetitorContentSearchTool)

    def test_get_competitor_search_tool_singleton(self):
        factory = _fresh_factory()
        t1 = factory.get_competitor_search_tool()
        t2 = factory.get_competitor_search_tool()
        assert t1 is t2

    def test_get_document_tool_returns_instance(self):
        factory = _fresh_factory()
        tool = factory.get_document_tool()
        from agents.content_agent.utils.tools import DocumentAccessTool
        assert isinstance(tool, DocumentAccessTool)

    def test_get_document_tool_singleton(self):
        factory = _fresh_factory()
        t1 = factory.get_document_tool()
        t2 = factory.get_document_tool()
        assert t1 is t2

    def test_get_directory_tool_returns_instance(self):
        factory = _fresh_factory()
        tool = factory.get_directory_tool()
        from agents.content_agent.utils.tools import DirectoryAccessTool
        assert isinstance(tool, DirectoryAccessTool)

    def test_get_directory_tool_uses_provided_directory(self):
        factory = _fresh_factory()
        tool = factory.get_directory_tool("/my/docs")
        assert tool is not None

    def test_get_directory_tool_different_dirs_are_different_instances(self):
        factory = _fresh_factory()
        t1 = factory.get_directory_tool("/dir/a")
        t2 = factory.get_directory_tool("/dir/b")
        assert t1 is not t2

    def test_get_data_processing_tool_returns_instance(self):
        factory = _fresh_factory()
        tool = factory.get_data_processing_tool()
        from agents.content_agent.utils.tools import DataProcessingTool
        assert isinstance(tool, DataProcessingTool)

    def test_get_data_processing_tool_singleton(self):
        factory = _fresh_factory()
        t1 = factory.get_data_processing_tool()
        t2 = factory.get_data_processing_tool()
        assert t1 is t2

    def test_reset_instances_clears_cache(self):
        factory = _fresh_factory()
        t1 = factory.get_web_search_tool()
        factory.reset_instances()
        t2 = factory.get_web_search_tool()
        # After reset, a new instance should be created
        assert t1 is not t2

    def test_get_content_agent_tools_returns_list(self):
        factory = _fresh_factory()
        tools = factory.get_content_agent_tools()
        assert isinstance(tools, list)
        assert len(tools) == 4

    def test_get_research_agent_tools_returns_list(self):
        factory = _fresh_factory()
        tools = factory.get_research_agent_tools()
        assert isinstance(tools, list)
        assert len(tools) == 4

    def test_get_market_agent_tools_returns_list(self):
        factory = _fresh_factory()
        tools = factory.get_market_agent_tools()
        assert isinstance(tools, list)
        assert len(tools) == 3

    def test_content_agent_tools_contains_no_duplicates(self):
        factory = _fresh_factory()
        tools = factory.get_content_agent_tools()
        # Each tool should appear at most once
        ids = [id(t) for t in tools]
        assert len(ids) == len(set(ids))
