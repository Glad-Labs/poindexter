"""Migration 0101: Seed retention TTLs for the embeddings table (#106).

The embeddings table grows unbounded today — claude_sessions alone
adds ~80k rows/month on Matt's instance, and old session embeddings
pollute semantic search drift for the "what have we been working on"
operator queries.

This migration seeds per-source-table TTL settings so the new
``prune_stale_embeddings`` job (lands alongside this migration) has
DB-tunable knobs from day one. No code-side defaults — every value
operators care about lives in app_settings, queryable + tunable
without a redeploy.

Default TTLs (Matt's call, 2026-04-27)
--------------------------------------

- ``claude_sessions``: 21 days  — middle ground between aggressive
  (14d, my recommendation for semantic-search recency) and
  conservative (30d, the issue's original suggestion). Operator can
  tune up or down without code changes.
- ``audit``: 90 days  — useful audit trail window without bloating
  the index.
- ``brain``: 365 days  — long memory; brain knowledge entries are
  designed to compound.
- ``issues``, ``memory``, ``posts``: NO TTL  — irreplaceable pipeline
  state, never auto-prune. Stored as ``""`` (empty string) which the
  job interprets as "skip this source_table."

Idempotent: ``ON CONFLICT (key) DO NOTHING`` so an operator who has
already pinned a custom TTL keeps it on a re-run.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


# (key, value, description). Empty value = "no TTL, never prune."
_SEEDS: list[tuple[str, str, str]] = [
    (
        "embedding_retention_days.claude_sessions",
        "21",
        "Days to keep Claude Code session embeddings before "
        "prune_stale_embeddings drops them. 21d balances semantic-search "
        "recency against keeping enough history for cross-session recall.",
    ),
    (
        "embedding_retention_days.audit",
        "90",
        "Days to keep audit_log embeddings before prune_stale_embeddings "
        "drops them. 90d covers the typical operator post-mortem window.",
    ),
    (
        "embedding_retention_days.brain",
        "365",
        "Days to keep brain_knowledge embeddings before prune. "
        "Brain memory is designed to compound; long horizon by design.",
    ),
    (
        "embedding_retention_days.issues",
        "",
        "Empty = no TTL. Issue embeddings are never auto-pruned — "
        "irreplaceable pipeline state.",
    ),
    (
        "embedding_retention_days.memory",
        "",
        "Empty = no TTL. Memory embeddings are never auto-pruned — "
        "operator's curated state.",
    ),
    (
        "embedding_retention_days.posts",
        "",
        "Empty = no TTL. Post embeddings are never auto-pruned — "
        "feed live RAG retrieval.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value, description, is_active)
                VALUES ($1, $2, $3, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "0101: seeded %d/%d embedding-retention TTL settings "
            "(remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, _value, _description in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
        logger.info("0101: removed %d embedding-retention TTL seeds", len(_SEEDS))
