"""WebSearchSource — topic ideation via DuckDuckGo category searches.

Picks a random seed query from each enabled category's
``CATEGORY_SEARCHES`` list, runs it through ``WebResearcher.search_simple``
(which wraps DDG + fallbacks), and converts the top N results into
DiscoveredTopic candidates.

Config (``plugin.topic_source.web_search`` in app_settings):

- ``enabled`` (default true)
- ``config.categories`` — which categories to search this run.
  When omitted, uses every key in ``CATEGORY_SEARCHES``. Useful for
  operators who only publish in 1-2 categories to avoid paying search
  costs for irrelevant ones.
- ``config.max_categories_per_run`` (default 3) — cap on categories
  hit per invocation. Keeps run duration bounded when many categories
  are configured.
- ``config.results_per_query`` (default 3) — top N hits per category.
- ``config.relevance_score`` (default 2.0) — flat score assigned to
  every web-search candidate. Operators who trust DDG more can bump;
  those who prefer other signals drop it.

Returns ``source="ddg_search"`` (legacy-compatible label so downstream
dedup + display don't have to special-case the migration).
"""

from __future__ import annotations

import logging
import random
from typing import Any

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import rewrite_as_blog_topic

logger = logging.getLogger(__name__)


class WebSearchSource:
    """Category-seeded web search for topic ideation via WebResearcher."""

    name = "web_search"

    async def extract(
        self,
        pool: Any,  # unused — WebResearcher owns its own HTTP client
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        del pool

        # Lazy import so test environments that don't have the full
        # web_research dep chain installed can still import this module.
        from services.topic_discovery import CATEGORY_SEARCHES
        from services.web_research import WebResearcher

        raw_categories = config.get("categories")
        max_cats = int(config.get("max_categories_per_run", 3) or 3)
        results_per_query = int(config.get("results_per_query", 3) or 3)
        relevance_score = float(config.get("relevance_score", 2.0) or 2.0)

        target_categories = (
            list(raw_categories) if isinstance(raw_categories, list) and raw_categories
            else list(CATEGORY_SEARCHES.keys())
        )
        target_categories = target_categories[:max_cats]

        researcher = WebResearcher()

        topics: list[DiscoveredTopic] = []
        for cat in target_categories:
            queries = CATEGORY_SEARCHES.get(cat, [])
            if not queries:
                continue
            query = random.choice(queries)

            results = await researcher.search_simple(query, num_results=results_per_query)
            for r in results or []:
                title = r.get("title", "")
                if not title:
                    continue
                rewritten = rewrite_as_blog_topic(title)
                if not rewritten:
                    continue
                topics.append(
                    DiscoveredTopic(
                        title=rewritten,
                        category=cat,
                        source="ddg_search",
                        source_url=r.get("url", ""),
                        relevance_score=relevance_score,
                    )
                )

        logger.info(
            "WebSearchSource: %d topics across %d categories",
            len(topics), len(target_categories),
        )
        return topics
