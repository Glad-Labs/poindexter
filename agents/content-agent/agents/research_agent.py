import logging
from utils.tools import WebSearchTool

logger = logging.getLogger(__name__)

class ResearchAgent:
    """
    Performs initial research on a given topic to provide context
    for the creative agent.
    """
    def __init__(self):
        """
        Initializes the ResearchAgent with a web search tool.
        """
        logging.info("Initializing Research Agent...")
        self.search_tool = WebSearchTool()

    def run(self, topic: str) -> str:
        """
        Conducts a web search for the given topic.

        Args:
            topic: The topic to research.

        Returns:
            A string containing the search results, or an empty string on failure.
        """
        try:
            logger.info(f"ResearchAgent: Conducting research for topic: '{topic}'")
            # The SerperDevTool's run method can be finicky.
            # The most reliable way is to call it with a simple string.
            search_query = f"latest trends and credible sources for {topic}"
            search_results = self.search_tool.run(search_query)
            logger.info(f"ResearchAgent: Found search results.")
            return str(search_results) # Convert results to string for the prompt
        except Exception as e:
            logger.error(f"An error occurred during research: {e}")
            return ""
