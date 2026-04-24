"""Migration 0090: Seed ``external_taps`` rows for existing built-in
topic sources.

Phase C / GH-103. One row per registered topic_source plugin; all
``enabled=false`` so this migration is a no-op until operators flip
rows on. Each row is wired to ``tap.builtin_topic_source`` which
adapts the existing ``services/topic_sources/*.py`` scrapers into the
declarative model.

Singer-protocol taps (stripe, ga4, salesforce, etc.) have no seed
here — operators add those rows themselves once the
``tap.singer_subprocess`` handler is implemented.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


# (name, tap_type, schedule, description)
_BUILTIN_SEEDS: list[tuple[str, str, str, str]] = [
    (
        "hackernews",
        "hackernews",
        "every 1 hour",
        "Hacker News top stories — default source for tech content ideas",
    ),
    (
        "devto",
        "devto",
        "every 2 hours",
        "dev.to recent posts — practical dev-focused writing prompts",
    ),
    (
        "web_search",
        "web_search",
        "every 6 hours",
        "Web search on operator-defined seed queries — broad discovery",
    ),
    (
        "knowledge",
        "knowledge",
        "every 12 hours",
        "Operator knowledge base — curated topic candidates",
    ),
    (
        "codebase",
        "codebase",
        "every 1 day",
        "Source-code-derived topic ideas — docs, comments, open issues",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for name, tap_type, schedule, description in _BUILTIN_SEEDS:
            await conn.execute(
                """
                INSERT INTO external_taps
                    (name, handler_name, tap_type, target_table, schedule,
                     enabled, metadata)
                VALUES ($1, 'builtin_topic_source', $2, 'content_tasks', $3,
                        FALSE, jsonb_build_object('description', $4::text))
                ON CONFLICT (name) DO NOTHING
                """,
                name, tap_type, schedule, description,
            )
        logger.info(
            "0090: seeded %d external_taps rows (all disabled)", len(_BUILTIN_SEEDS),
        )


async def down(pool) -> None:
    names = [s[0] for s in _BUILTIN_SEEDS]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM external_taps WHERE name = ANY($1::text[])",
            names,
        )
        logger.info("0090: removed %d seeded external_taps rows", len(names))
