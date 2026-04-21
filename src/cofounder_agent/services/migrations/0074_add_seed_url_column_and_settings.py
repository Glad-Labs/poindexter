"""Migration 0074: URL-based topic seeding column + settings (GH-42).

GitHub issue Glad-Labs/poindexter#42 — users want to drop a URL into
``POST /api/tasks`` and have the pipeline fetch it, extract a topic,
and seed the writer. This migration does the DB side of that feature:

1. Adds ``pipeline_tasks.seed_url TEXT`` (nullable) so operators can
   query "what's the origin URL for this task?" without digging into
   the JSONB task_metadata. Idempotent — ``ADD COLUMN IF NOT EXISTS``.
   The application already stores the URL in ``task_metadata.seed_url``
   today; the column exists so a future cutover (populated by the
   insert redirect or the application layer) can promote it.

2. Seeds three app_settings keys that the new
   :mod:`services.seed_url_fetcher` reads:

   - ``seed_url_fetch_timeout_seconds`` (10) — total request timeout.
   - ``seed_url_user_agent`` — Chrome-ish UA so news sites don't 403 a
     requests-default agent. Operators can override to their brand UA
     once they have an outbound scraper allowlist.
   - ``seed_url_max_bytes`` (1 MiB) — safety cap on decoded response
     body. Stops a pathological page from OOM-ing the API worker
     before we even try to extract a title.

Why 0074 and not 0072? GH-28 (DB-driven Grafana alerts) took 0072 and
GH-17 (docs) is staging 0073 in a parallel agent. 0074 is our slot to
avoid the merge-time renumber dance.

Idempotent end-to-end: the ALTER uses IF NOT EXISTS, the INSERTs use
ON CONFLICT DO NOTHING, so reruns are safe.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SEEDS = [
    (
        "seed_url_fetch_timeout_seconds",
        "10",
        "pipeline",
        "URL-based topic seeding: total HTTP timeout (seconds) for "
        "the seed_url fetch on POST /api/tasks. Short by design — if "
        "a source page takes >10s to respond we'd rather reject the "
        "task with a clear 400 than tie up an API worker. Ref: GH-42.",
    ),
    (
        "seed_url_user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36",
        "pipeline",
        "URL-based topic seeding: User-Agent header for the seed_url "
        "fetch. Chrome-ish by default because many news/publisher "
        "sites 403 requests-default or urllib UAs. Operators can swap "
        "in a branded UA once they've set up outbound allowlists. "
        "Ref: GH-42.",
    ),
    (
        "seed_url_max_bytes",
        "1048576",  # 1 MiB
        "pipeline",
        "URL-based topic seeding: hard cap (bytes) on the decoded "
        "response body. Guards against pathological pages that would "
        "OOM the API worker before title extraction runs. 1 MiB is "
        "plenty for any real article's <title>/<h1>/<meta>. Ref: GH-42.",
    ),
]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def _is_real_table(conn, name: str) -> bool:
    """Distinguish real tables from views — only ALTER real tables."""
    row = await conn.fetchrow(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_name = $1",
        name,
    )
    return bool(row) and row["table_type"] == "BASE TABLE"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # 1. Add seed_url column to pipeline_tasks (hybrid architecture
        # target — migration 0066 onwards). If pipeline_tasks doesn't
        # exist yet (older DB checkpoints), skip silently; a later
        # schema reconciliation migration will pick it up. If the old
        # ``content_tasks`` is still a real table on this DB (pre-0066),
        # add the column there too so the feature works before cutover.
        if await _is_real_table(conn, "pipeline_tasks"):
            await conn.execute(
                "ALTER TABLE pipeline_tasks "
                "ADD COLUMN IF NOT EXISTS seed_url TEXT"
            )
            logger.info(
                "Migration 0074: added pipeline_tasks.seed_url column (GH-42)"
            )
        else:
            logger.info(
                "Migration 0074: pipeline_tasks not a base table — "
                "skipping seed_url column add (will be applied in a "
                "later reconciliation)"
            )

        if await _is_real_table(conn, "content_tasks"):
            # Pre-0066 schemas where content_tasks is still a real
            # table — add the column here so the application's
            # task_metadata.seed_url round-trip has a canonical home.
            await conn.execute(
                "ALTER TABLE content_tasks "
                "ADD COLUMN IF NOT EXISTS seed_url TEXT"
            )
            logger.info(
                "Migration 0074: also added content_tasks.seed_url "
                "(pre-0066 schema detected)"
            )

        # 2. Seed fetcher settings. Only seed if app_settings exists —
        # test schemas sometimes skip it and we don't want to blow up
        # the migration runner over a missing config table.
        if not await _table_exists(conn, "app_settings"):
            logger.warning(
                "Migration 0074: app_settings table missing — "
                "skipping seed_url_* settings seed"
            )
            return

        for key, value, category, description in _SEEDS:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret)
                VALUES ($1, $2, $3, $4, false)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
        logger.info(
            "Migration 0074: seeded %d seed_url_* settings "
            "(seed_url_fetch_timeout_seconds, seed_url_user_agent, "
            "seed_url_max_bytes)",
            len(_SEEDS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if await _is_real_table(conn, "pipeline_tasks"):
            await conn.execute(
                "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS seed_url"
            )
        if await _is_real_table(conn, "content_tasks"):
            await conn.execute(
                "ALTER TABLE content_tasks DROP COLUMN IF EXISTS seed_url"
            )
        if await _table_exists(conn, "app_settings"):
            keys = [k for k, *_ in _SEEDS]
            await conn.execute(
                "DELETE FROM app_settings WHERE key = ANY($1::text[])",
                keys,
            )
        logger.info("Migration 0074 rolled back: removed seed_url column + settings")
