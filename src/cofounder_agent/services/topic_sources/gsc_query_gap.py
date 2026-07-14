"""GscQueryGapSource -- surface high-impression, poorly-ranked search queries
as new topic candidates.

Part of Glad-Labs/poindexter#764. Reads the per-(page, query) rows landed by
the ``gsc_main`` external_taps row's ``performance_report_custom`` stream
(see ``scripts/enable_gsc_query_dimension.py``) -- a query that already earns
real search impressions but ranks poorly is a page nobody has written yet
(or an existing page only incidentally matches it), which is exactly the
kind of gap a new post can close. Distinct from the existing
``seo_refresh`` loop (Glad-Labs/poindexter#763), which re-optimizes the
metadata of *existing* posts -- this source proposes *new* topics through the
ordinary topic-proposal pipeline.

Config (``plugin.topic_source.gsc_query_gap`` in app_settings -- no seed
required, defaults are Python-side, matching every other TopicSource):

- ``config.min_impressions`` (default 50) -- floor on summed impressions over
  the window for a query to count as a real gap, not sampling noise.
- ``config.min_position`` (default 15) -- average position must be *worse*
  than this (higher number) to count as a gap; ranking well already means
  it's not a gap.
- ``config.window_days`` (default 28) -- lookback window over ``external_metrics.date``.
- ``config.max_topics`` (default 10) -- cap per run; the shared dedup/ranking
  pass downstream (``topic_sources/runner.py``) handles cross-source ranking,
  this cap just bounds one source's contribution.

Master switch: ``app_settings.seo.query_ingestion.enabled`` (default
``false``) -- read directly since it's a cross-cutting SEO-harvest setting,
not this source's own per-plugin config. Ships inert until the operator
verifies the ingested query data and flips it on.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

from plugins.topic_source import DiscoveredTopic
from services.topic_sources._filters import classify_category

logger = logging.getLogger(__name__)


_GAP_QUERY_SQL = """
SELECT
    dimensions->>'query' AS query,
    SUM(metric_value) FILTER (WHERE metric_name = 'impressions') AS impressions,
    AVG(metric_value) FILTER (
        WHERE metric_name = 'position' AND metric_value > 0
    ) AS avg_position
FROM external_metrics
WHERE source = 'google_search_console'
  AND dimensions ? 'query'
  AND dimensions->>'query' != ''
  AND date > (NOW() - ($1::int || ' days')::interval)::date
GROUP BY dimensions->>'query'
HAVING SUM(metric_value) FILTER (WHERE metric_name = 'impressions') >= $2
   AND AVG(metric_value) FILTER (
         WHERE metric_name = 'position' AND metric_value > 0
       ) > $3
ORDER BY SUM(metric_value) FILTER (WHERE metric_name = 'impressions') DESC
LIMIT $4
"""


class GscQueryGapSource:
    """Turn poorly-ranked, high-impression GSC queries into topic proposals."""

    name = "gsc_query_gap"

    async def extract(
        self,
        pool: Any,
        config: dict[str, Any],
    ) -> list[DiscoveredTopic]:
        async with pool.acquire() as conn:
            enabled_raw = await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1",
                "seo.query_ingestion.enabled",
            )
        if str(enabled_raw or "").strip().lower() != "true":
            return []

        min_impressions = float(config.get("min_impressions", 50) or 50)
        min_position = float(config.get("min_position", 15) or 15)
        window_days = int(config.get("window_days", 28) or 28)
        max_topics = int(config.get("max_topics", 10) or 10)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _GAP_QUERY_SQL, window_days, min_impressions, min_position, max_topics
            )

        topics: list[DiscoveredTopic] = []
        for r in rows:
            query = (r["query"] or "").strip()
            if not query:
                continue
            impressions = float(r["impressions"] or 0)
            avg_position = float(r["avg_position"] or 0)
            title = query.title()
            topics.append(
                DiscoveredTopic(
                    title=title,
                    category=classify_category(title),
                    source=self.name,
                    source_url=f"https://www.google.com/search?q={quote_plus(query)}",
                    # Real, calculated from impressions -- not fabricated.
                    # 500 impressions/window -> score 5.0; scales linearly below that.
                    relevance_score=min(impressions / 100.0, 5.0),
                    description=(
                        f"Ranks ~position {avg_position:.0f} for this query with "
                        f"{impressions:.0f} impressions in the last {window_days}d "
                        "but no page targets it directly."
                    ),
                    keywords=[query],
                )
            )

        logger.info(
            "GscQueryGapSource: %d query-gap topics (window=%dd, min_impressions=%.0f, min_position=%.0f)",
            len(topics), window_days, min_impressions, min_position,
        )
        return topics
