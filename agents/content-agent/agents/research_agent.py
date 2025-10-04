import logging
import requests
import json
from config import config

logger = logging.getLogger(__name__)

class ResearchAgent:
    """
    Performs initial research on a given topic to provide context
    for the creative agent by calling the Serper API directly.
    """
    def __init__(self):
        """
        Initializes the ResearchAgent.
        """
        logging.info("Initializing Research Agent...")
        if not config.SERPER_API_KEY:
            raise ValueError("SERPER_API_KEY is not set in the environment.")
        self.serper_api_key = config.SERPER_API_KEY

    def run(self, topic: str, keywords: list[str]) -> str:
        """
        Conducts a web search using a combination of the topic and keywords
        to get more targeted and relevant results.

        Args:
            topic (str): The core topic to research.
            keywords (list[str]): A list of supporting keywords to refine the search.

        Returns:
            A string containing the formatted search results, or an empty string on failure.
        """
        try:
            search_query = f"{topic} {' '.join(keywords)}"
            logger.info(f"ResearchAgent: Conducting research for query: '{search_query}'")
            
            url = "https://google.serper.dev/search"
            payload = json.dumps({"q": search_query})
            headers = {
                'X-API-KEY': self.serper_api_key,
                'Content-Type': 'application/json'
            }

            response = requests.post(url, headers=headers, data=payload)
            response.raise_for_status()
            
            search_results = response.json()
            
            # Format the results into a string for the LLM context
            context = ""
            if search_results.get("organic"):
                for result in search_results.get("organic", [])[:5]: # Get top 5 results
                    context += f"Title: {result.get('title', 'N/A')}\n"
                    context += f"Link: {result.get('link', 'N/A')}\n"
                    context += f"Snippet: {result.get('snippet', 'N/A')}\n---\n"
            
            logger.info(f"ResearchAgent: Found search results.")
            return context
        except requests.exceptions.RequestException as e:
            logger.error(f"An error occurred during research request: {e}")
            return ""
        except Exception as e:
            logger.error(f"An unexpected error occurred during research: {e}")
            return ""
