"""Migration 0115: seed Glad Labs as the first configured niche.

Uses TWO_PASS writer mode (per spec — Glad Labs is the primary-source niche
where we lean hardest on internal RAG context). Goals are the operator's
opening guesses; tune later via the CLI.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 17)
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM niches WHERE slug = 'glad-labs'")
        if existing is not None:
            logger.info("Glad Labs niche already exists (id=%s) — skipping seed", existing)
            return

        niche_id = await conn.fetchval(
            """
            INSERT INTO niches (slug, name, target_audience_tags,
                                writer_rag_mode, batch_size,
                                discovery_cadence_minute_floor)
            VALUES ('glad-labs', 'Glad Labs',
                    ARRAY['indie-devs','ai-curious','prospects','future-matt'],
                    'TWO_PASS', 5, 60)
            RETURNING id
            """,
        )

        # Goals (sum to 100)
        goals = [
            ("AUTHORITY", 35),  # show what we actually know
            ("EDUCATION", 25),  # teach the reader
            ("BRAND",     20),  # reinforce the Glad Labs voice
            ("TRAFFIC",   15),  # not nothing, but not the driver
            ("REVENUE",    5),  # eventually
        ]
        for goal_type, weight in goals:
            await conn.execute(
                "INSERT INTO niche_goals (niche_id, goal_type, weight_pct) VALUES ($1, $2, $3)",
                niche_id, goal_type, weight,
            )

        # Sources — internal_rag is the lead, plus the existing external feeds
        sources = [
            ("internal_rag", True, 50),
            ("hackernews",   True, 20),
            ("devto",        True, 15),
            ("web_search",   True, 10),
            ("knowledge",    True,  5),
        ]
        for name, enabled, weight in sources:
            await conn.execute(
                "INSERT INTO niche_sources (niche_id, source_name, enabled, weight_pct) VALUES ($1, $2, $3, $4)",
                niche_id, name, enabled, weight,
            )
        logger.info("Seeded Glad Labs niche (id=%s)", niche_id)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM niches WHERE slug = 'glad-labs'")
        logger.info("Removed Glad Labs niche seed (0115 down)")
