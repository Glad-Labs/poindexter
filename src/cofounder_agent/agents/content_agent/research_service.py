"""
Research Service using SearXNG for content agent pipeline.

SearXNG provides privacy-respecting, aggregated search results from 247+ engines.
This service integrates SearXNG for the research stage of content generation.
"""

import asyncio
import json
from datetime import datetime
from typing import Optional

import httpx

from services.logger_config import get_logger

# Optional dependencies for enhanced functionality
try:
    import feedparser  # type: ignore

    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False

try:
    from bs4 import BeautifulSoup  # type: ignore

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

logger = get_logger(__name__)


class SearXNGResearchService:
    """Privacy-respecting research service using SearXNG metasearch."""

    def __init__(
        self,
        searxng_instance: str = "https://searx.be/",
        timeout: int = 30,
        max_results: int = 10,
    ):
        """
        Initialize SearXNG research service.

        Args:
            searxng_instance: Base URL of SearXNG instance (default: public instance)
            timeout: Request timeout in seconds
            max_results: Maximum results to fetch per search
        """
        self.searxng_instance = searxng_instance.rstrip("/")
        self.timeout = timeout
        self.max_results = max_results
        # Initialise the client eagerly so it is shared across all method calls
        # and connection pooling is effective. Closed in __aexit__.
        self.client = httpx.AsyncClient(timeout=self.timeout)

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit — closes the shared client."""
        if self.client is not None:
            await self.client.aclose()

    async def search(self, query: str, category: str = "general") -> dict:
        """
        Perform privacy-respecting search using SearXNG.

        Args:
            query: Search query string
            category: Search category (general, images, videos, music, etc.)

        Returns:
            Dictionary with search results and metadata
        """
        try:
            url = f"{self.searxng_instance}/search"
            params = {
                "q": query,
                "category": category,
                "format": "json",
                "results": self.max_results,
            }

            response = await self.client.get(url, params=params)
            response.raise_for_status()

            data = response.json()

            return {
                "query": query,
                "category": category,
                "timestamp": datetime.now().isoformat(),
                "results": self._parse_results(data.get("results", [])),
                "count": len(data.get("results", [])),
                "source": "SearXNG",
            }

        except Exception as e:
            logger.error(f"SearXNG search failed: {e}", exc_info=True)
            return {
                "query": query,
                "error": str(e),
                "results": [],
                "count": 0,
            }

    async def search_news(self, query: str) -> dict:
        """
        Search for recent news articles using SearXNG news category.

        Args:
            query: Search query for news

        Returns:
            Dictionary with news results
        """
        return await self.search(query, category="news")

    async def research_topic(self, topic: str, depth: str = "standard") -> dict:
        """
        Comprehensive research on a topic with multiple search angles.

        Args:
            topic: Main topic to research
            depth: Research depth (quick, standard, comprehensive)

        Returns:
            Dictionary with comprehensive research data
        """
        searches = {
            "overview": topic,
            "latest": f"{topic} 2024 2025",
            "trends": f"{topic} trends insights",
        }

        if depth in ("standard", "comprehensive"):
            searches.update(
                {
                    "market": f"{topic} market analysis",
                    "challenges": f"{topic} challenges solutions",
                }
            )

        if depth == "comprehensive":
            searches.update(
                {
                    "research": f"{topic} research studies",
                    "expert": f"{topic} expert analysis",
                    "case_studies": f"{topic} case studies examples",
                }
            )

        results = {}
        tasks = [self.search(query, category="general") for query in searches.values()]

        search_results = await asyncio.gather(*tasks)

        for (category, _), result in zip(searches.items(), search_results):
            results[category] = result

        return {
            "topic": topic,
            "depth": depth,
            "timestamp": datetime.now().isoformat(),
            "research": results,
            "total_results": sum(r.get("count", 0) for r in results.values()),
        }

    async def fetch_article_content(self, url: str) -> Optional[str]:
        """
        Fetch and extract main content from a URL.

        Args:
            url: URL to fetch

        Returns:
            Extracted text content or None if failed
        """
        if not BS4_AVAILABLE:
            logger.debug("BeautifulSoup4 not available, skipping article content extraction")
            return None

        try:
            response = await self.client.get(url, follow_redirects=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")  # type: ignore

            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()

            # Get text
            text = soup.get_text()

            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = " ".join(chunk for chunk in chunks if chunk)

            return text[:5000]  # Return first 5000 chars

        except Exception as e:
            logger.warning(
                f"[fetch_article] Failed to fetch article from {url}: {e}", exc_info=True
            )
            return None

    async def get_news_feeds(self, keywords: list[str], limit: int = 5) -> dict:
        """
        Aggregate RSS feeds for keywords using SearXNG.

        Args:
            keywords: List of keywords to monitor
            limit: Maximum feeds to return per keyword

        Returns:
            Dictionary with aggregated feed entries
        """
        if not FEEDPARSER_AVAILABLE:
            logger.debug("feedparser not available, skipping RSS feed aggregation")
            return {
                "keywords": keywords,
                "timestamp": datetime.now().isoformat(),
                "feeds": {},
                "note": "feedparser not available",
            }

        feeds_data = {}

        for keyword in keywords:
            try:
                # Search for feeds related to keyword
                url = f"{self.searxng_instance}/search"
                params = {
                    "q": f"{keyword} feed rss",
                    "category": "general",
                    "format": "json",
                    "results": 3,
                }

                response = await self.client.get(url, params=params)
                response.raise_for_status()

                results = response.json().get("results", [])
                entries = []

                for result in results[:limit]:
                    try:
                        feed = feedparser.parse(result.get("url", ""))  # type: ignore
                        if feed.entries:
                            entries.extend(
                                {
                                    "title": entry.get("title", ""),
                                    "link": entry.get("link", ""),
                                    "summary": entry.get("summary", ""),
                                    "published": entry.get("published", ""),
                                }
                                for entry in feed.entries[:2]
                            )
                    except Exception as e:
                        logger.debug(f"Feed parsing error: {e}")
                        continue

                feeds_data[keyword] = {
                    "entries": entries,
                    "count": len(entries),
                }

            except Exception as e:
                logger.error(f"Feed aggregation error for {keyword}: {e}", exc_info=True)
                feeds_data[keyword] = {"error": str(e), "entries": []}

        return {
            "keywords": keywords,
            "timestamp": datetime.now().isoformat(),
            "feeds": feeds_data,
        }

    def _parse_results(self, raw_results: list) -> list:
        """Parse SearXNG raw results into standardized format."""
        parsed = []

        for result in raw_results:
            parsed.append(
                {
                    "title": result.get("title", ""),
                    "url": result.get("url", ""),
                    "content": result.get("content", ""),
                    "engine": result.get("engine", []),
                    "score": result.get("score", 0),
                }
            )

        return parsed


# Research Agent Integration
async def research_content_topic(
    topic: str,
    depth: str = "standard",
    fetch_articles: bool = False,
    searxng_instance: str = "https://searx.be/",
) -> dict:
    """
    Main function for research agent to use SearXNG.

    Args:
        topic: Topic to research
        depth: Research depth (quick, standard, comprehensive)
        fetch_articles: Whether to fetch full article content
        searxng_instance: SearXNG instance URL

    Returns:
        Comprehensive research data for content creation
    """
    async with SearXNGResearchService(searxng_instance=searxng_instance) as research:

        # Get comprehensive research
        research_data = await research.research_topic(topic, depth=depth)

        # Optionally fetch full article content for top results
        if fetch_articles:
            top_results = []
            for category_data in research_data["research"].values():
                top_results.extend(category_data.get("results", [])[:2])

            article_contents = []
            for result in top_results:
                content = await research.fetch_article_content(result["url"])
                if content:
                    article_contents.append(
                        {
                            "title": result["title"],
                            "url": result["url"],
                            "content": content,
                        }
                    )

            research_data["article_contents"] = article_contents

        return research_data


if __name__ == "__main__":
    # Example usage
    async def main():
        data = await research_content_topic(
            topic="AI in marketing 2025",
            depth="comprehensive",
            fetch_articles=True,
        )
        logger.info(json.dumps(data, indent=2, default=str))

    asyncio.run(main())
