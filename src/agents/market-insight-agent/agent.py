import logging
from pytrends.request import TrendReq

class MarketInsightAgent:
    """
    An agent responsible for monitoring market trends and generating
    relevant, popular topic options for content creation using Google Trends.
    """
    def __init__(self):
        self.pytrends = TrendReq(hl='en-US', tz=360)
        logging.info("Market Insight Agent initialized with Pytrends.")

    def suggest_topics(self, base_query: str) -> str:
        """
        Generates a list of suggested blog post topics based on a query
        using related and rising queries from Google Trends.
        """
        try:
            logging.info(f"Generating topic suggestions for query: '{base_query}'")
            
            # Build the payload
            self.pytrends.build_payload(kw_list=[base_query])

            # Get related queries (these are often long-tail keywords)
            related_queries = self.pytrends.related_queries().get(base_query, {}).get('top', [])
            
            # Get rising queries (these indicate growing interest)
            rising_queries = self.pytrends.related_queries().get(base_query, {}).get('rising', [])

            if not related_queries and not rising_queries:
                return f"I couldn't find any significant trends related to '{base_query}'. You might want to try a broader topic."

            response = f"Here are some topic suggestions for '{base_query}' based on Google Trends:\\n"
            
            if related_queries:
                response += "\\n**Top Related Topics:**\\n"
                for item in related_queries.head(5).to_dict('records'):
                    response += f"- {item['query']}\\n"

            if rising_queries:
                response += "\\n**Rising Topics (Growing Interest):**\\n"
                for item in rising_queries.head(5).to_dict('records'):
                    response += f"- {item['query']} (Trending up by {item['value']}%)\\n"
            
            return response
            
        except Exception as e:
            logging.error(f"Error generating topic suggestions from Google Trends: {e}")
            return "I'm sorry, I had trouble fetching data from Google Trends."
