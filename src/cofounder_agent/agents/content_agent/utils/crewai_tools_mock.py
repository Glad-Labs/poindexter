"""
Mock CrewAI Tools - Provides stub implementations when crewai_tools is not installed.

WARNING: These stubs return placeholder strings, NOT real data. Any pipeline stage
that relies on web search, file reading, or code execution will produce degraded
output when running against these mocks. Install crewai-tools for real functionality.
"""

from services.logger_config import get_logger
from typing import Optional

logger = get_logger(__name__)
class BaseTool:
    """Base class for mock tools. Logs a warning on every call."""

    def __init__(self, name: str = "", description: str = ""):
        self.name = name
        self.description = description

    def __call__(self, *args, **kwargs):
        logger.warning(
            "[MOCK] %s called — returning placeholder. "
            "Install crewai-tools for real results.",
            self.name,
        )
        return f"[MOCK] Tool '{self.name}' is not available (crewai_tools not installed)"


class SerperDevTool(BaseTool):
    """Mock SerperDevTool for web search."""

    def __init__(self):
        super().__init__(
            name="SerperDevTool",
            description="Search the web for real-time information using Serper API",
        )

    def __call__(self, query: str) -> str:
        logger.warning("[MOCK] SerperDevTool search called for: %s — returning placeholder", query)
        return f"[MOCK] No real search results available (crewai_tools not installed). Query: {query}"


class WebsiteSearchTool(BaseTool):
    """Mock WebsiteSearchTool for searching specific websites."""

    def __init__(self):
        super().__init__(
            name="WebsiteSearchTool", description="Search content within specific websites"
        )

    def __call__(self, website: str, query: str) -> str:
        logger.warning("[MOCK] WebsiteSearchTool called for %s: %s — returning placeholder", website, query)
        return f"[MOCK] No real website search results available (crewai_tools not installed). Site: {website}, Query: {query}"


class FileReadTool(BaseTool):
    """Mock FileReadTool for reading files."""

    def __init__(self):
        super().__init__(name="FileReadTool", description="Read and process file contents")

    def __call__(self, file_path: str) -> str:
        logger.warning("[MOCK] FileReadTool called for: %s — returning placeholder", file_path)
        return f"[MOCK] File read not available (crewai_tools not installed). Path: {file_path}"


class DirectoryReadTool(BaseTool):
    """Mock DirectoryReadTool for reading directories."""

    def __init__(self, directory: str = "./"):
        super().__init__(name="DirectoryReadTool", description="List and read directory contents")
        self.directory = directory

    def __call__(self, directory_path: Optional[str] = None) -> str:
        path = directory_path or self.directory
        logger.warning("[MOCK] DirectoryReadTool called for: %s — returning placeholder", path)
        return f"[MOCK] Directory read not available (crewai_tools not installed). Path: {path}"


class CodeInterpreterTool(BaseTool):
    """Mock CodeInterpreterTool for executing code."""

    def __init__(self):
        super().__init__(
            name="CodeInterpreterTool", description="Execute and interpret code snippets"
        )

    def __call__(self, code: str) -> str:
        logger.warning("[MOCK] CodeInterpreterTool called — returning placeholder")
        return f"[MOCK] Code execution not available (crewai_tools not installed)"
