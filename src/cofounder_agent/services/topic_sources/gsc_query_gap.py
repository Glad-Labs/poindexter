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
- ``config.permutation_min_variants`` (default 5) -- how many reorderings of the
  same token bag mark a cluster as machine-generated (see below). Two phrasings
  of one idea are normal; keep this well above 2.

**An impression is only demand if a person made it.** The 2026-08-09 GSC audit
found that of the impressions Google will name, over half came from ONE cluster
of 103 reorderings of "cadquery official documentation parametric cad python" --
3,696 impressions, zero clicks -- and that this source's live output included
``site:www.gladlabs.io``, which would have become an article titled
"Site:Www.Gladlabs.Io". Candidates are therefore filtered through
``_filters.is_junk_search_query`` (search operators, URLs/repo paths, brand
navigation, letterless fragments) and ``_filters.permutation_clusters`` before
becoming topics. Note this source does NOT reuse ``is_news_or_junk``: that
rejects anything under four words, and short queries are the valuable ones
("5090 local llm" converts at 7.1% CTR, "fast api best practices" at 20%).

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
from services.topic_sources._filters import (
    brand_tokens_from_config,
    classify_category,
    is_junk_search_query,
    permutation_clusters,
)

logger = logging.getLogger(__name__)

# Candidates are filtered in Python (junk + permutation clusters), so the SQL
# has to return more than ``max_topics`` or a single machine-generated cluster
# could occupy every slot and leave the run empty after filtering.
_OVERFETCH = 5
_OVERFETCH_CAP = 200

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
        min_variants = int(config.get("permutation_min_variants", 5) or 5)

        fetch_limit = min(max_topics * _OVERFETCH, _OVERFETCH_CAP)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _GAP_QUERY_SQL, window_days, min_impressions, min_position, fetch_limit
            )

        brand = brand_tokens_from_config(config.get("_site_config"))
        candidates = [(r["query"] or "").strip() for r in rows]
        clustered = permutation_clusters(candidates, min_variants=min_variants)
        dropped_junk = 0
        dropped_cluster = 0

        topics: list[DiscoveredTopic] = []
        for r in rows:
            query = (r["query"] or "").strip()
            if not query:
                continue
            # An impression is only demand if a person made it. Operator/tool
            # queries and machine permutation clusters earn impressions and
            # never a click, so proposing them writes for nobody — the exact
            # shape of the zero-click posts the 2026-08-09 GSC audit found.
            if is_junk_search_query(query, brand_tokens=brand):
                dropped_junk += 1
                continue
            if query in clustered:
                dropped_cluster += 1
                continue
            if len(topics) >= max_topics:
                break
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
            "GscQueryGapSource: %d query-gap topics (window=%dd, min_impressions=%.0f, "
            "min_position=%.0f); dropped %d junk + %d permutation-cluster of %d candidates",
            len(topics), window_days, min_impressions, min_position,
            dropped_junk, dropped_cluster, len(rows),
        )
        return topics
