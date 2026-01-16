"""
Mock CrewAI Tools - Provides stub implementations when crewai_tools is not available.
This allows the content agent pipeline to run without explicit CrewAI tool dependencies
while crewai_tools is being installed or available.
"""

import logging
from typing import Optional, List

logger = logging.getLogger(__name__)


class BaseTool:
    """Base class for mock tools."""

    def __init__(self, name: str = "", description: str = ""):
        self.name = name
        self.description = description

    def __call__(self, *args, **kwargs):
        return f"Tool '{self.name}' would execute with args: {args}, kwargs: {kwargs}"


class SerperDevTool(BaseTool):
    """Mock SerperDevTool for web search."""

    def __init__(self):
        super().__init__(
            name="SerperDevTool",
            description="Search the web for real-time information using Serper API",
        )

    def __call__(self, query: str) -> str:
        logger.info(f"SerperDevTool mocked search: {query}")
        return f"Search results for: {query}"


class WebsiteSearchTool(BaseTool):
    """Mock WebsiteSearchTool for searching specific websites."""

    def __init__(self):
        super().__init__(
            name="WebsiteSearchTool", description="Search content within specific websites"
        )

    def __call__(self, website: str, query: str) -> str:
        logger.info(f"WebsiteSearchTool mocked search on {website}: {query}")
        return f"Results from {website} for: {query}"


class FileReadTool(BaseTool):
    """Mock FileReadTool for reading files."""

    def __init__(self):
        super().__init__(name="FileReadTool", description="Read and process file contents")

    def __call__(self, file_path: str) -> str:
        logger.info(f"FileReadTool mocked read: {file_path}")
        return f"File contents of: {file_path}"


class DirectoryReadTool(BaseTool):
    """Mock DirectoryReadTool for reading directories."""

    def __init__(self, directory: str = "./"):
        super().__init__(name="DirectoryReadTool", description="List and read directory contents")
        self.directory = directory

    def __call__(self, directory_path: Optional[str] = None) -> str:
        path = directory_path or self.directory
        logging.getLogger(__name__).info(f"DirectoryReadTool mocked read: {path}")
        return f"Directory contents of: {path}"


class CodeInterpreterTool(BaseTool):
    """Mock CodeInterpreterTool for executing code."""

    def __init__(self):
        super().__init__(
            name="CodeInterpreterTool", description="Execute and interpret code snippets"
        )

    def __call__(self, code: str) -> str:
        logging.getLogger(__name__).info(f"CodeInterpreterTool mocked execution: {code[:100]}")
        return f"Code execution result: {code[:50]}..."
