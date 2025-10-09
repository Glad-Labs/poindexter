import os
from crewai_tools import SerperDevTool


class WebSearchTool(SerperDevTool):
    """
    A tool for performing web searches to gather real-time information,
    find external links, and enrich content. This is a direct integration
    of the SerperDevTool for simplicity and robustness.
    """

    def __init__(self):
        super().__init__()
        if not os.getenv("SERPER_API_KEY"):
            raise ValueError("CRITICAL: SERPER_API_KEY is not set in the environment.")
