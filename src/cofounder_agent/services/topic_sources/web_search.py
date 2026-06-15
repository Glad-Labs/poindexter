"""WebSearchSource — niche-aware topic ideation via DuckDuckGo search.

Resolves a set of search queries from the niche context the tap handler
passes in, runs each through ``WebResearcher.search_simple`` (DDG +
fallbacks), and converts the top N results into DiscoveredTopic candidates.

Query resolution (first match wins, §2b of the taps-ingest design):

1. ``config.seed_queries`` — explicit operator-pinned queries.
2. ``config.categories`` — one random seed query per category from the
   ``CATEGORY_SEARCHES`` bank. The niche-bound tap's migration seeds these
   for niches the bank covers (gaming, pc-hardware).
3. niche ``target_audience_tags`` — ``"{niche_name} {tag}"`` per tag, for
   niches the bank doesn't cover (AI/ML). Passed in by the tap handler.
4. none of the above — raise ``ValueError`` (the silent "search every global
   category" fallback is retired; feedback_no_silent_defaults).

Config (``plugin.topic_source.web_search`` in app_settings, layered with the
tap row's config + niche context):

- ``enabled`` (default true)
- ``seed_queries`` — explicit query list (highest priority).
- ``categories`` — which bank categories to search this run.
- ``max_categories_per_run`` (default 3) — cap on queries hit per run.
- ``results_per_query`` (default 3) — top N hits per query.
- ``relevance_score`` (default 2.0) — flat score on every candidate.

Returns ``source="ddg_search"`` (legacy-compatible label so downstream dedup
+ display don't have to special-case the migration).
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import rewrite_as_blog_topic

logger = logging.getLogger(__name__)


class WebSearchSource:
    """Niche-aware category/tag-seeded web search via WebResearcher."""

    name = "web_search"

    async def extract(
        self,
        pool: Any,  # unused — WebResearcher owns its own HTTP client
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        del pool

        # Lazy import so test environments without the full web_research dep
        # chain can still import this module.
        from services.site_config import SiteConfig
        from services.web_research import WebResearcher

        results_per_query = int(config.get("results_per_query", 3) or 3)
        relevance_score = float(config.get("relevance_score", 2.0) or 2.0)
        max_queries = int(config.get("max_categories_per_run", 3) or 3)

        plan = self._resolve_queries(config)[:max_queries]
        if not plan:
            # Niche-aware resolution found nothing AND no explicit config was
            # given. Fail loud rather than silently searching every global
            # category (the retired pre-niche behaviour). feedback_no_silent_defaults.
            raise ValueError(
                "web_search: no seed_queries, no categories, and no niche "
                "target_audience_tags to derive queries from — refusing to "
                "fall back to a global all-category search"
            )

        researcher = WebResearcher(
            site_config=config.get("_site_config") or SiteConfig()
        )

        topics: list[DiscoveredTopic] = []
        for query, category_label in plan:
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
                        category=category_label,
                        source="ddg_search",
                        source_url=r.get("url", ""),
                        relevance_score=relevance_score,
                    )
                )

        logger.info(
            "WebSearchSource: %d topics across %d queries", len(topics), len(plan),
        )
        return topics

    @staticmethod
    def _resolve_queries(config: dict[str, Any]) -> list[tuple[str, str]]:
        """Resolve (query, category_label) pairs, first match wins (§2b).

        1. explicit config.seed_queries  -> pinned, label 'custom'
        2. explicit config.categories    -> bank queries, label = category
        3. niche target_audience_tags    -> '{niche_name} {tag}', label = tag
        4. nothing                        -> [] (caller fails loud)
        """
        seed_queries = config.get("seed_queries")
        if isinstance(seed_queries, list) and seed_queries:
            return [(str(q), "custom") for q in seed_queries]

        categories = config.get("categories")
        if isinstance(categories, list) and categories:
            import random

            from services.topic_sources._filters import CATEGORY_SEARCHES

            plan: list[tuple[str, str]] = []
            for cat in categories:
                queries = CATEGORY_SEARCHES.get(cat, [])
                if queries:
                    plan.append((random.choice(queries), cat))
            return plan

        tags = config.get("target_audience_tags")
        niche_name = (config.get("niche_name") or "").strip()
        if isinstance(tags, list) and tags and niche_name:
            return [(f"{niche_name} {tag}", str(tag)) for tag in tags]

        return []
