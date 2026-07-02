"""topic_pool — data access for the niche-tagged candidate pool.

The decoupling seam between ingestion (taps) and orchestration
(TopicBatchService). Taps insert here via insert_pooled_topics(); the
orchestrator reads pooled rows via read_pooled() (b2 cutover) and flips
the batch winners to 'batched' via mark_batched(). Unchosen rows stay
'pooled' for future sweeps until the topic_pool retention policy prunes
them.
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


# Best-scored rows per source, newest first on ties. PARTITION BY source is
# the read-side balance guard: the pool accumulates at very different rates
# per source (internal_rag deposits ~40x what devto does), so a plain
# ORDER BY .. LIMIT would hand the orchestrator an all-internal window.
_READ_POOLED_SQL = """
SELECT id, source, title, summary, url, category, score
  FROM (
    SELECT *, row_number() OVER (
        PARTITION BY source
        ORDER BY score DESC, ingested_at DESC
    ) AS rn
      FROM topic_pool
     WHERE niche_id = $1 AND status = 'pooled'
  ) ranked
 WHERE rn <= $2
 ORDER BY source, rn
"""


async def read_pooled(
    pool: Any,
    *,
    niche_id: Any,
    per_source_limit: int,
) -> list[dict[str, Any]]:
    """Read pooled candidates for a niche in the orchestrator's wire shape.

    Returns ``{"kind": "external"|"internal", "data": {...}}`` items —
    the exact shapes the deleted ``_discover_external`` /
    ``_discover_internal`` produced, so ``_embed_and_pre_rank`` consumes
    them unchanged. Rows whose ``source`` is ``internal_rag`` map to the
    internal-candidate dict (``distilled_topic`` / ``distilled_angle`` /
    ``source_kind`` / ``primary_ref``); everything else maps to the
    external dict. Both carry the pool row id (``data.id`` /
    ``data.primary_ref``) so the sweep can ``mark_batched`` the winners.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_READ_POOLED_SQL, niche_id, per_source_limit)

    items: list[dict[str, Any]] = []
    for r in rows:
        if r["source"] == "internal_rag":
            items.append({
                "kind": "internal",
                "data": {
                    "distilled_topic": r["title"],
                    "distilled_angle": r["summary"],
                    # b1's extract shim stores the source_kind in category.
                    "source_kind": r["category"] or "claude_session",
                    "primary_ref": str(r["id"]),
                },
            })
        else:
            items.append({
                "kind": "external",
                "data": {
                    "id": str(r["id"]),
                    "title": r["title"],
                    "summary": r["summary"],
                    "source_name": r["source"],
                    "source_ref": r["url"] or r["title"][:80],
                    "source_url": r["url"],
                    "category": r["category"],
                    "relevance_score": float(r["score"]),
                },
            })
    return items


async def claim_best_pooled_topic(
    pool: Any,
    *,
    niche_id: Any,
    site_config: Any = None,
) -> dict[str, Any] | None:
    """Claim the best sane pooled candidate for ``topic="auto"`` resolution.

    b3 of poindexter#812 — the niche-gated replacement for the retired
    ``TopicDiscovery.discover()`` inline call in the auto-topic path.
    Walks the niche's ``pooled`` rows best-score-first, skips any title
    the deterministic topic-sanity gate rejects, and flips the winner to
    ``batched`` so repeated auto-calls don't hand out the same topic.
    Returns ``{"id", "title", "summary", "source"}`` or ``None`` when the
    pool holds nothing sane (caller fails loud — the taps haven't
    deposited, which is an ingestion problem to surface, not paper over).
    """
    from services.topic_sanity import (
        evaluate_topic_sanity,
        resolve_min_alpha_words,
    )

    min_words = resolve_min_alpha_words(site_config)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, summary, source FROM topic_pool "
            "WHERE niche_id = $1 AND status = 'pooled' "
            "ORDER BY score DESC, ingested_at DESC",
            niche_id,
        )
        for r in rows:
            if not evaluate_topic_sanity(
                r["title"], min_alpha_words=min_words,
            ).ok:
                continue
            # mark_batched only flips still-'pooled' rows, so a concurrent
            # claimer racing us simply makes this return 0 → try the next.
            if await mark_batched(conn, [r["id"]]) == 1:
                return {
                    "id": str(r["id"]),
                    "title": r["title"],
                    "summary": r["summary"],
                    "source": r["source"],
                }
    return None


async def mark_batched(conn: Any, ids: list[Any]) -> int:
    """Flip the named pool rows to ``batched`` (stamping ``batched_at``).

    Only ``pooled`` rows flip — already-batched ids are skipped, so the
    call is idempotent. Returns the count actually flipped.
    """
    if not ids:
        return 0
    rows = await conn.fetch(
        "UPDATE topic_pool SET status = 'batched', batched_at = NOW() "
        "WHERE id = ANY($1::uuid[]) AND status = 'pooled' RETURNING id",
        [str(i) for i in ids],
    )
    return len(rows)
