"""
Unit tests for agents/content_agent/utils/crewai_tools_mock.py

Tests for mock CrewAI tool implementations (BaseTool, SerperDevTool,
WebsiteSearchTool, FileReadTool, DirectoryReadTool, CodeInterpreterTool).
"""

import pytest
from unittest.mock import patch
from agents.content_agent.utils.crewai_tools_mock import (
    BaseTool,
    CodeInterpreterTool,
    DirectoryReadTool,
    FileReadTool,
    SerperDevTool,
    WebsiteSearchTool,
)


# ---------------------------------------------------------------------------
# BaseTool
# ---------------------------------------------------------------------------


class TestBaseTool:
    def test_init_stores_name_and_description(self):
        tool = BaseTool(name="TestTool", description="Does testing")
        assert tool.name == "TestTool"
        assert tool.description == "Does testing"

    def test_init_empty_defaults(self):
        tool = BaseTool()
        assert tool.name == ""
        assert tool.description == ""

    def test_call_returns_mock_string(self):
        tool = BaseTool(name="MyTool", description="")
        result = tool()
        assert "[MOCK]" in result
        assert "MyTool" in result

    def test_call_with_args_does_not_raise(self):
        tool = BaseTool(name="AnyTool")
        result = tool("arg1", key="val")
        assert result is not None

    def test_call_logs_warning(self):
        tool = BaseTool(name="LoggedTool")
        with patch("agents.content_agent.utils.crewai_tools_mock.logger") as mock_logger:
            tool()
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# SerperDevTool
# ---------------------------------------------------------------------------


class TestSerperDevTool:
    def test_init_sets_name(self):
        tool = SerperDevTool()
        assert tool.name == "SerperDevTool"

    def test_call_returns_mock_with_query(self):
        tool = SerperDevTool()
        result = tool(query="AI trends 2026")
        assert "[MOCK]" in result
        assert "AI trends 2026" in result

    def test_call_returns_string(self):
        tool = SerperDevTool()
        result = tool(query="test")
        assert isinstance(result, str)

    def test_description_set(self):
        tool = SerperDevTool()
        assert len(tool.description) > 0

    def test_call_logs_warning(self):
        tool = SerperDevTool()
        with patch("agents.content_agent.utils.crewai_tools_mock.logger") as mock_logger:
            tool(query="hello")
            mock_logger.warning.assert_called_once()
            # The query should appear in the warning
            args = mock_logger.warning.call_args
            assert "hello" in str(args)


# ---------------------------------------------------------------------------
# WebsiteSearchTool
# ---------------------------------------------------------------------------


class TestWebsiteSearchTool:
    def test_init_sets_name(self):
        tool = WebsiteSearchTool()
        assert tool.name == "WebsiteSearchTool"

    def test_call_returns_mock_with_site_and_query(self):
        tool = WebsiteSearchTool()
        result = tool(website="example.com", query="content strategy")
        assert "[MOCK]" in result
        assert "example.com" in result
        assert "content strategy" in result

    def test_call_returns_string(self):
        tool = WebsiteSearchTool()
        result = tool(website="site.com", query="q")
        assert isinstance(result, str)

    def test_call_logs_warning(self):
        tool = WebsiteSearchTool()
        with patch("agents.content_agent.utils.crewai_tools_mock.logger") as mock_logger:
            tool(website="foo.com", query="bar")
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# FileReadTool
# ---------------------------------------------------------------------------


class TestFileReadTool:
    def test_init_sets_name(self):
        tool = FileReadTool()
        assert tool.name == "FileReadTool"

    def test_call_returns_mock_with_path(self):
        tool = FileReadTool()
        result = tool(file_path="/data/report.txt")
        assert "[MOCK]" in result
        assert "/data/report.txt" in result

    def test_call_returns_string(self):
        tool = FileReadTool()
        result = tool(file_path="test.md")
        assert isinstance(result, str)

    def test_call_logs_warning(self):
        tool = FileReadTool()
        with patch("agents.content_agent.utils.crewai_tools_mock.logger") as mock_logger:
            tool(file_path="some/file.txt")
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# DirectoryReadTool
# ---------------------------------------------------------------------------


class TestDirectoryReadTool:
    def test_init_default_directory(self):
        tool = DirectoryReadTool()
        assert tool.directory == "./"

    def test_init_custom_directory(self):
        tool = DirectoryReadTool(directory="/tmp/docs")
        assert tool.directory == "/tmp/docs"

    def test_call_with_explicit_path(self):
        tool = DirectoryReadTool()
        result = tool(directory_path="/var/data")
        assert "[MOCK]" in result
        assert "/var/data" in result

    def test_call_uses_default_when_no_path_given(self):
        tool = DirectoryReadTool(directory="/home/user")
        result = tool()  # no directory_path arg → falls back to self.directory
        assert "/home/user" in result

    def test_call_returns_string(self):
        tool = DirectoryReadTool()
        result = tool()
        assert isinstance(result, str)

    def test_call_logs_warning(self):
        tool = DirectoryReadTool()
        with patch("agents.content_agent.utils.crewai_tools_mock.logger") as mock_logger:
            tool()
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# CodeInterpreterTool
# ---------------------------------------------------------------------------


class TestCodeInterpreterTool:
    def test_init_sets_name(self):
        tool = CodeInterpreterTool()
        assert tool.name == "CodeInterpreterTool"

    def test_call_returns_mock_string(self):
        tool = CodeInterpreterTool()
        result = tool(code="print('hello')")
        assert "[MOCK]" in result

    def test_call_returns_string(self):
        tool = CodeInterpreterTool()
        result = tool(code="x = 1 + 1")
        assert isinstance(result, str)

    def test_call_logs_warning(self):
        tool = CodeInterpreterTool()
        with patch("agents.content_agent.utils.crewai_tools_mock.logger") as mock_logger:
            tool(code="pass")
            mock_logger.warning.assert_called_once()

    def test_description_set(self):
        tool = CodeInterpreterTool()
        assert len(tool.description) > 0
