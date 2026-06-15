"""topic_pool — data access for the niche-tagged candidate pool.

The decoupling seam between ingestion (taps) and orchestration
(TopicBatchService). Taps insert here via insert_pooled_topics(); b2's
orchestrator reads pooled rows and flips them to 'batched'.
"""

from __future__ import annotations

from typing import Any

from plugins.topic_source import DiscoveredTopic

# Tap rows name their destination in external_taps.target_table; we read it
# from the (trusted) row but allowlist it before interpolation — asyncpg can't
# parametrize a table name, and defense-in-depth beats trusting the column.
_ALLOWED_TABLES = frozenset({"topic_pool"})


def dedup_key(title: str) -> str:
    """Canonical per-niche dedup key: lowercased, whitespace-collapsed title.

    Backs the UNIQUE(niche_id, dedup_key) constraint so trivial title
    variants ("Local  LLM " vs "local llm") collapse to one pool row. The
    fuzzy/semantic pass in the tap handler catches near-dupes this exact key
    misses.
    """
    return " ".join((title or "").lower().split())


async def insert_pooled_topics(
    conn: Any,
    *,
    niche_id: Any,
    source: str,
    topics: list[DiscoveredTopic],
    table: str = "topic_pool",
) -> int:
    """Insert candidates into the pool, skipping per-niche dedup_key dupes.

    Returns the count of rows actually inserted (RETURNING id is NULL on an
    ON CONFLICT no-op). ``table`` is read from the tap's target_table and
    must be in the allowlist.
    """
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"insert_pooled_topics: refusing unknown table {table!r}")

    sql = (
        f"INSERT INTO {table} "  # noqa: S608 — table is allowlisted above
        "(niche_id, source, title, summary, url, category, score, dedup_key, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pooled') "
        "ON CONFLICT (niche_id, dedup_key) DO NOTHING "
        "RETURNING id"
    )
    inserted = 0
    for t in topics:
        new_id = await conn.fetchval(
            sql,
            niche_id,
            source,
            t.title,
            getattr(t, "description", "") or "",
            getattr(t, "source_url", "") or "",
            getattr(t, "category", "") or "",
            float(getattr(t, "relevance_score", 0.0) or 0.0),
            dedup_key(t.title),
        )
        if new_id is not None:
            inserted += 1
    return inserted
