import logging
from utils.tools import WebSearchTool

logger = logging.getLogger(__name__)

class ResearchAgent:
    def __init__(self):
        self.search_tool = WebSearchTool()

    def run(self, topic: str) -> str:
        """
        Conducts a web search to find relevant, credible external links for a given topic.

        Args:
            topic (str): The topic to research.

        Returns:
            str: A formatted string containing a list of relevant URLs.
        """
        logger.info(f"ResearchAgent: Conducting web search for '{topic}'.")
        
        # More specific query for better results
        query = f"latest trends and credible sources for {topic}"
        
        # The 'run' method of crewai tools expects keyword arguments 
        # that match the tool's argument schema. For Serper, this is 'search_query'.
        search_results = self.search_tool.run(search_query=query)
        
        # Basic processing to extract URLs (can be made more sophisticated)
        # For now, we'll just return the raw results to be used by the creative agent.
        logger.info(f"ResearchAgent: Found search results for '{topic}'.")
        return search_results
