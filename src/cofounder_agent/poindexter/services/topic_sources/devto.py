"""DevtoSource — pull trending articles from the Dev.to / Forem API.

Free, no auth. Compatible with any Forem deployment — the API base
URL is configurable so self-hosters can point at their own instance
(or a future Dev.to API version) without a code change.

Config (``plugin.topic_source.devto`` in app_settings):

- ``enabled`` (default true)
- ``config.per_page`` (default 15)
- ``config.top_days`` (default 7) — rolling window
- ``config.min_reactions`` (default 20) — drop under-engaged articles
- ``config.tag`` (default ``""`` → all tags; e.g. ``"ai"``, ``"python"``)
- ``config.api_base`` (default ``https://dev.to/api``) — override for Forem
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import classify_category, rewrite_as_blog_topic

logger = logging.getLogger(__name__)


_DEFAULT_API_BASE = "https://dev.to/api"


class DevtoSource:
    """Pull trending dev articles from Dev.to (or any Forem instance)."""

    name = "devto"

    async def extract(
        self,
        pool: Any,  # unused — HTTP-only source
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        del pool

        per_page = int(config.get("per_page", 15) or 15)
        top_days = int(config.get("top_days", 7) or 7)
        min_reactions = int(config.get("min_reactions", 20) or 20)
        tag = str(config.get("tag", "") or "").strip()
        api_base = str(config.get("api_base", _DEFAULT_API_BASE) or _DEFAULT_API_BASE).rstrip("/")

        url_params = f"per_page={per_page}&top={top_days}"
        if tag:
            url_params += f"&tag={tag}"

        topics: list[DiscoveredTopic] = []
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0)
        ) as client:
            resp = await client.get(f"{api_base}/articles?{url_params}", timeout=10)
            resp.raise_for_status()
            articles = resp.json()

        if not isinstance(articles, list):
            logger.warning("DevtoSource: unexpected response shape %s", type(articles).__name__)
            return []

        for article in articles:
            if not isinstance(article, dict):
                continue
            title = article.get("title", "")
            url = article.get("url", "")
            reactions = int(article.get("positive_reactions_count", 0) or 0)

            if not title or reactions < min_reactions:
                continue

            rewritten = rewrite_as_blog_topic(title)
            if not rewritten:
                continue

            category = classify_category(rewritten)
            topics.append(
                DiscoveredTopic(
                    title=rewritten,
                    category=category,
                    source=self.name,
                    source_url=url,
                    # Reactions run 20-500+; normalize to 0-5.
                    relevance_score=min(reactions / 50, 5.0),
                )
            )

        logger.info(
            "DevtoSource: %d topics from %d articles (tag=%s, min_reactions=%d)",
            len(topics), len(articles), tag or "all", min_reactions,
        )
        return topics
