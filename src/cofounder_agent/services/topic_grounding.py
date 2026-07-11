"""Internal-corpus grounding for external topic candidates (poindexter#822).

An external topic (hackernews / devto / web_search) is a popularity signal.
Before it wins a batch slot, ask: does the operator's OWN corpus already have
material on this? If not, we'd be paraphrasing someone else's reporting — the
zero-new-value rewrite the system positions against. This module answers that
question with a single pgvector nearest-neighbor query against the
content-bearing slice of the embeddings table, reusing the same
``embedding <=> vec`` primitive internal_rag_source uses.

Fail-open by construction: any error, empty vector, or empty corpus returns
``grounded=True`` so infra trouble never penalizes a candidate and never sinks
the sweep (same posture as the dedup / empty-batch guards in run_sweep).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

# source_kind -> embeddings.source_table. Content-bearing kinds ONLY — the
# ops-noise kinds (audit_event->audit, brain_knowledge->brain) are
# deliberately absent so a status row can't manufacture grounding. Subset of
# internal_rag_source's table_map, kept local so the two concerns stay
# independent.
_KIND_TO_TABLE: dict[str, str] = {
    "post_history": "posts",
    "decision_log": "memory",
    "memory_file": "memory",
    "claude_session": "claude_sessions",
}

_PREVIEW_MAX_CHARS = 500


@dataclass
class GroundingMatch:
    source_table: str
    source_id: str
    preview: str
    similarity: float


@dataclass
class GroundingResult:
    similarity: float | None   # best cosine similarity; None on fail-open
    grounded: bool             # similarity >= threshold (True on fail-open)
    match: GroundingMatch | None


def _resolve_source_tables(site_config: SiteConfig) -> list[str]:
    raw = site_config.get(
        "niche_external_grounding_source_kinds",
        "post_history,decision_log,memory_file,claude_session",
    ) or ""
    tables: list[str] = []
    for kind in (k.strip() for k in raw.split(",")):
        if not kind:
            continue
        table = _KIND_TO_TABLE.get(kind)
        if table is None:
            logger.warning(
                "[topic_grounding] unsupported source_kind %r — skipping "
                "(content-bearing kinds only)", kind,
            )
            continue
        if table not in tables:
            tables.append(table)
    return tables


async def internal_grounding(
    pool, candidate_vec: list[float], *, site_config: SiteConfig,
) -> GroundingResult:
    """Return the best internal-corpus match for ``candidate_vec`` and whether
    it clears ``niche_external_grounding_threshold``.

    ``candidate_vec`` is the embedding already computed for goal pre-ranking —
    no second embed call.
    """
    threshold = site_config.get_float("niche_external_grounding_threshold", 0.55)

    if not candidate_vec:
        return GroundingResult(similarity=None, grounded=True, match=None)

    tables = _resolve_source_tables(site_config)
    if not tables:
        return GroundingResult(similarity=None, grounded=True, match=None)

    try:
        # pgvector has no asyncpg codec — pass the vector in its text form
        # (pattern: internal_rag_source._fetch_recent_snippets / embeddings_db).
        vec_str = "[" + ",".join(str(v) for v in candidate_vec) + "]"
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT source_table, source_id, text_preview,
                       1 - (embedding <=> $1::vector) AS similarity
                  FROM embeddings
                 WHERE source_table = ANY($2::text[])
                 ORDER BY embedding <=> $1::vector
                 LIMIT 1
                """,
                vec_str, tables,
            )
    except Exception:
        logger.warning(
            "[topic_grounding] grounding query failed — fail-open (grounded)",
            exc_info=True,
        )
        return GroundingResult(similarity=None, grounded=True, match=None)

    if row is None:
        # Empty corpus (fresh install): nothing to ground against. Fail-open
        # so a brand-new operator isn't penalized into an empty batch.
        return GroundingResult(similarity=None, grounded=True, match=None)

    sim = float(row["similarity"])
    match = GroundingMatch(
        source_table=row["source_table"],
        source_id=str(row["source_id"]),
        preview=(row["text_preview"] or "")[:_PREVIEW_MAX_CHARS],
        similarity=sim,
    )
    return GroundingResult(similarity=sim, grounded=sim >= threshold, match=match)
