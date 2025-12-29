"""
Serper API Client for Web Search and SEO Research

Provides web search capabilities for content research, fact-checking, and trend analysis.
Free tier: 100 searches/month

Cost: $0/month (vs spending on expensive searches)

ASYNC-FIRST: All operations use httpx async client (no blocking I/O)
"""

import os
import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


class SerperClient:
    """
    Serper API client for web search, news search, and shopping search.

    Features:
    - Google-like search results
    - News and shopping search options
    - Free tier with 100 searches/month
    - Used for content research and fact-checking
    """

    BASE_URL = "https://google.serper.dev"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Serper client.

        Args:
            api_key: Serper API key (defaults to SERPER_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("SERPER_API_KEY")
        if not self.api_key:
            logger.warning("Serper API key not configured - web search will be unavailable")

        self.headers = {
            "X-API-KEY": self.api_key if self.api_key else "",
            "Content-Type": "application/json",
        }
        self.monthly_usage = 0  # Track free tier usage

    async def search(
        self, query: str, num: int = 10, search_type: str = "search"
    ) -> Dict[str, Any]:
        """
        Perform web search via Serper API (ASYNC).

        Args:
            query: Search query
            num: Number of results (default 10)
            search_type: Type of search - "search", "news", "shopping"

        Returns:
            Search results dictionary
        """
        if not self.api_key:
            logger.warning("Serper API key not configured")
            return {}

        try:
            payload = {"q": query, "num": min(num, 30), "type": search_type}

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{self.BASE_URL}/{search_type}", json=payload, headers=self.headers
                )
                response.raise_for_status()

            self.monthly_usage += 1
            if self.monthly_usage % 10 == 0:
                logger.info(f"Serper API usage: {self.monthly_usage}/100 (free tier)")

            data = response.json()
            logger.info(f"Serper search '{query}' returned {len(data.get('organic', []))} results")
            return data

        except httpx.HTTPError as e:
            logger.error(f"Serper API request failed: {e}")
            return {}
        except Exception as e:
            logger.error(f"Serper search error: {e}")
            return {}

    async def news_search(self, query: str, num: int = 10) -> Dict[str, Any]:
        """
        Search for news articles (ASYNC).

        Args:
            query: News search query
            num: Number of results

        Returns:
            News search results
        """
        return await self.search(query, num, search_type="news")

    async def shopping_search(self, query: str, num: int = 10) -> Dict[str, Any]:
        """
        Search for shopping results (ASYNC).

        Args:
            query: Product search query
            num: Number of results

        Returns:
            Shopping search results
        """
        return await self.search(query, num, search_type="shopping")

    async def get_search_results_summary(self, query: str, max_results: int = 5) -> Dict[str, Any]:
        """
        Get summarized search results for content research (ASYNC).

        Args:
            query: Search query
            max_results: Maximum results to return

        Returns:
            Structured search summary
        """
        try:
            results = self.search(query, num=max_results)

            return {
                "query": query,
                "search_date": datetime.now().isoformat(),
                "results_found": len(results.get("organic", [])),
                "sources": [
                    {
                        "title": item.get("title"),
                        "url": item.get("link"),
                        "snippet": item.get("snippet"),
                        "position": idx + 1,
                    }
                    for idx, item in enumerate(results.get("organic", [])[:max_results])
                ],
                "knowledge_panel": results.get("knowledgePanel", {}),
            }
        except Exception as e:
            logger.error(f"Error getting search summary: {e}")
            return {}

    async def fact_check_claims(self, claims: List[str]) -> Dict[str, Any]:
        """
        Search for fact-checking information on claims (ASYNC).

        Args:
            claims: List of claims to fact-check

        Returns:
            Fact-checking results for each claim
        """
        results = {}

        for claim in claims[:3]:  # Limit to 3 to conserve API quota
            try:
                search_results = await self.search(f'fact check: "{claim}"', num=3)

                results[claim] = {
                    "claim": claim,
                    "sources_found": len(search_results.get("organic", [])),
                    "top_sources": [
                        {
                            "title": item.get("title"),
                            "url": item.get("link"),
                            "snippet": item.get("snippet"),
                        }
                        for item in search_results.get("organic", [])[:2]
                    ],
                }
            except Exception as e:
                logger.error(f"Error fact-checking '{claim}': {e}")
                results[claim] = {"error": str(e)}

        return results

    async def get_trending_topics(self, category: str = "general") -> List[Dict[str, str]]:
        """
        Get trending topics for content ideas (ASYNC).

        Args:
            category: Trend category - "general", "technology", "business", "health"

        Returns:
            List of trending topics
        """
        try:
            query = f"trending {category} 2025"
            results = await self.search(query, num=5)

            topics = []
            for idx, item in enumerate(results.get("organic", [])[:5]):
                topics.append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "trend_rank": idx + 1,
                    }
                )

            logger.info(f"Found {len(topics)} trending {category} topics")
            return topics

        except Exception as e:
            logger.error(f"Error getting trending topics: {e}")
            return []

    async def research_topic(
        self, topic: str, aspects: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Comprehensive research on a topic with multiple aspects (ASYNC).

        Args:
            topic: Main topic to research
            aspects: Specific aspects to research (e.g., ["history", "benefits", "risks"])

        Returns:
            Comprehensive research results
        """
        aspects = aspects or ["overview", "current trends", "expert opinion"]
        research = {"topic": topic, "research_date": datetime.now().isoformat(), "aspects": {}}

        try:
            # Search for main topic
            main_results = await self.search(topic, num=3)
            research["main_sources"] = [
                {
                    "title": item.get("title"),
                    "url": item.get("link"),
                    "snippet": item.get("snippet"),
                }
                for item in main_results.get("organic", [])
            ]

            # Search for each aspect
            for aspect in aspects[:2]:  # Limit searches to conserve quota
                aspect_query = f"{topic} {aspect}"
                aspect_results = await self.search(aspect_query, num=2)

                research["aspects"][aspect] = [
                    {
                        "title": item.get("title"),
                        "url": item.get("link"),
                        "snippet": item.get("snippet"),
                    }
                    for item in aspect_results.get("organic", [])
                ]

            logger.info(f"Completed research on '{topic}'")
            return research

        except Exception as e:
            logger.error(f"Error researching topic '{topic}': {e}")
            return research

    async def get_author_information(self, author_name: str) -> Dict[str, Any]:
        """
        Get information about an author or expert (ASYNC).

        Args:
            author_name: Name of author/expert

        Returns:
            Author information and notable works
        """
        try:
            results = await self.search(author_name, num=5)

            return {
                "author": author_name,
                "search_date": datetime.now().isoformat(),
                "results": [
                    {
                        "title": item.get("title"),
                        "url": item.get("link"),
                        "snippet": item.get("snippet"),
                    }
                    for item in results.get("organic", [])
                ],
                "knowledge_panel": results.get("knowledgePanel", {}),
            }
        except Exception as e:
            logger.error(f"Error getting author info: {e}")
            return {}

    def check_api_quota(self) -> Dict[str, Any]:
        """
        Check API quota usage (note: can't check exactly from client, but tracks locally).

        Returns:
            Quota information
        """
        return {
            "service": "Serper (Free Tier)",
            "monthly_limit": 100,
            "local_usage_tracked": self.monthly_usage,
            "warning": "This tracks calls made in current session, actual quota resets monthly",
            "recommendation": "Monitor at https://serper.dev/dashboard if usage > 80",
        }


# Initialize client with API key from environment
def get_serper_client() -> SerperClient:
    """Factory function to create Serper client."""
    return SerperClient(os.getenv("SERPER_API_KEY"))
