import os
import logging
from typing import Optional

# Try to import crewai_tools, fall back to mock if not available
try:
    from crewai_tools import (
        SerperDevTool,
        WebsiteSearchTool,
        FileReadTool,
        DirectoryReadTool,
        CodeInterpreterTool,
    )
    CREWAI_TOOLS_AVAILABLE = True
except ImportError as e:
    logging.warning(f"crewai_tools not available ({e}), using mock implementations")
    from .crewai_tools_mock import (
        SerperDevTool,
        WebsiteSearchTool,
        FileReadTool,
        DirectoryReadTool,
        CodeInterpreterTool,
    )
    CREWAI_TOOLS_AVAILABLE = False

logger = logging.getLogger(__name__)


class WebSearchTool(SerperDevTool):
    """
    A tool for performing web searches to gather real-time information,
    find external links, and enrich content. This is a direct integration
    of the SerperDevTool for simplicity and robustness.
    
    **API Required:** SERPER_API_KEY
    **Use Cases:** Market research, trend discovery, competitor analysis
    """

    def __init__(self):
        super().__init__()
        if not os.getenv("SERPER_API_KEY"):
            logger.warning("SERPER_API_KEY not set - web search will be limited")


class CompetitorContentSearchTool(WebsiteSearchTool):
    """
    RAG-based search tool for analyzing competitor websites and content.
    Uses semantic search to find relevant information within web pages.
    
    **Use Cases:** Competitor analysis, content benchmarking, market positioning
    **Note:** Requires CHROMA_OPENAI_API_KEY environment variable to be set
    """

    def __init__(self):
        try:
            super().__init__()
            logger.info("CompetitorContentSearchTool initialized successfully")
        except Exception as e:
            # Handle missing CHROMA_OPENAI_API_KEY or other initialization errors gracefully
            logger.warning(f"CompetitorContentSearchTool initialization failed: {str(e)[:200]}")
            logger.warning("Competitor content search will be unavailable - continuing without this tool")
            # Store the error for later reference, but don't raise
            self._initialization_error = str(e)
            self._is_available = False


class DocumentAccessTool(FileReadTool):
    """
    Tool for reading and extracting content from various file formats.
    Supports: txt, md, json, csv, pdf, docx, and more.
    
    **Use Cases:** Research document analysis, configuration reading, data extraction
    **No API Required:** Local file access
    """

    def __init__(self):
        super().__init__()
        logger.info("DocumentAccessTool initialized")

    def read_research_file(self, file_path: str) -> str:
        """Read a research document with error handling"""
        try:
            content = self.run(file_path)
            logger.info(f"Successfully read file: {file_path}")
            return content
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            return None
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {str(e)}")
            return None


class DirectoryAccessTool(DirectoryReadTool):
    """
    Tool for navigating and analyzing directory structures.
    Useful for understanding content organization and finding related files.
    
    **Use Cases:** Project exploration, documentation navigation, file discovery
    **No API Required:** Local directory access
    """

    def __init__(self, directory: Optional[str] = None):
        if directory:
            super().__init__(directory=directory)
        else:
            super().__init__(directory="./")
        logger.info(f"DirectoryAccessTool initialized for: {directory or './'}")


class DataProcessingTool(CodeInterpreterTool):
    """
    Tool for executing Python code to process data, run calculations,
    and transform information. Useful for data cleaning and analysis.
    
    **Use Cases:** Data transformation, calculations, data analysis
    **No API Required:** Local Python execution
    **Security:** Be careful with untrusted input!
    """

    def __init__(self):
        super().__init__()
        logger.info("DataProcessingTool initialized")

    def process_data(self, code: str) -> str:
        """Execute data processing code with error handling"""
        try:
            result = self.run(code)
            logger.info("Data processing completed successfully")
            return result
        except Exception as e:
            logger.error(f"Error during data processing: {str(e)}")
            return None


class CrewAIToolsFactory:
    """
    Factory for creating and managing CrewAI tools.
    Provides a centralized interface for all available tools.
    
    **Phase 1 Tools (Available Now):**
    - WebSearchTool: Real-time web search
    - CompetitorContentSearchTool: Website content analysis
    - DocumentAccessTool: File reading
    - DirectoryAccessTool: Directory navigation
    - DataProcessingTool: Code execution
    
    **Example Usage:**
    ```python
    factory = CrewAIToolsFactory()
    tools = factory.get_content_agent_tools()  # Get all tools for content agent
    
    # Or get specific tools:
    web_search = factory.get_web_search_tool()
    doc_reader = factory.get_document_tool()
    ```
    """

    _instances = {}  # Singleton pattern to avoid recreating tools

    @classmethod
    def get_web_search_tool(cls) -> WebSearchTool:
        """Get or create web search tool"""
        if "web_search" not in cls._instances:
            cls._instances["web_search"] = WebSearchTool()
        return cls._instances["web_search"]

    @classmethod
    def get_competitor_search_tool(cls) -> CompetitorContentSearchTool:
        """Get or create competitor content search tool"""
        if "competitor_search" not in cls._instances:
            cls._instances["competitor_search"] = CompetitorContentSearchTool()
        return cls._instances["competitor_search"]

    @classmethod
    def get_document_tool(cls) -> DocumentAccessTool:
        """Get or create document access tool"""
        if "document_access" not in cls._instances:
            cls._instances["document_access"] = DocumentAccessTool()
        return cls._instances["document_access"]

    @classmethod
    def get_directory_tool(cls, directory: Optional[str] = None) -> DirectoryAccessTool:
        """Get or create directory access tool"""
        tool_key = f"directory_access_{directory or 'root'}"
        if tool_key not in cls._instances:
            cls._instances[tool_key] = DirectoryAccessTool(directory)
        return cls._instances[tool_key]

    @classmethod
    def get_data_processing_tool(cls) -> DataProcessingTool:
        """Get or create data processing tool"""
        if "data_processing" not in cls._instances:
            cls._instances["data_processing"] = DataProcessingTool()
        return cls._instances["data_processing"]

    @classmethod
    def get_content_agent_tools(cls) -> list:
        """Get all tools for content agent"""
        return [
            cls.get_web_search_tool(),
            cls.get_competitor_search_tool(),
            cls.get_document_tool(),
            cls.get_data_processing_tool(),
        ]

    @classmethod
    def get_research_agent_tools(cls) -> list:
        """Get all tools for research agent"""
        return [
            cls.get_web_search_tool(),
            cls.get_document_tool(),
            cls.get_directory_tool("./research_docs"),
            cls.get_data_processing_tool(),
        ]

    @classmethod
    def get_market_agent_tools(cls) -> list:
        """Get all tools for market analysis agent"""
        return [
            cls.get_web_search_tool(),
            cls.get_competitor_search_tool(),
            cls.get_data_processing_tool(),
        ]

    @classmethod
    def reset_instances(cls) -> None:
        """Reset all tool instances (useful for testing)"""
        cls._instances = {}
        logger.info("All tool instances reset")
