"""Migration 0075: summarize+collapse support for the embeddings table (GH-81).

The ``embeddings`` table is growing unboundedly after the Claude Code
session tap (#80) landed — ~10k rows today, dominated by
``claude_sessions`` (6k+) and ``brain`` (1.5k+). Rather than a plain
time-based prune (which loses semantic signal), GH-81 introduces a
background job that clusters old rows per source table, writes one
summary row per cluster, and deletes the originals inside a transaction.

This migration prepares the schema for that job:

1. ``is_summary BOOLEAN NOT NULL DEFAULT FALSE`` — flags rows that are
   centroid summaries of collapsed source_ids. The collapse job
   filters on ``is_summary = FALSE`` when selecting candidates so
   re-runs over already-collapsed content are no-ops (idempotent).
2. Partial index on ``(source_table, created_at) WHERE NOT is_summary``
   so the "find old raw rows per source" scan is cheap even as the
   table grows. Summary rows are a fraction of total volume and don't
   need to be in the hot path for the candidate query.
3. Four ``app_settings`` rows that control the job, all disabled by
   default so existing deployments see a no-op until the operator
   opts in:

   - ``embedding_collapse_enabled`` (bool, default ``false``)
   - ``embedding_collapse_age_days`` (default ``90``)
   - ``embedding_collapse_cluster_size`` (default ``8``)
   - ``embedding_collapse_source_tables`` (comma list, default
     ``"claude_sessions,brain,audit"`` — the tables explicitly
     declared safe to collapse in the issue body. ``posts``,
     ``issues``, ``memory`` are NOT in this list and must never
     be collapsed by default.)

Idempotent: ``ADD COLUMN IF NOT EXISTS``, ``CREATE INDEX IF NOT
EXISTS``, ``INSERT ... ON CONFLICT DO NOTHING``. Safe to re-run.

Down migration drops the seeded settings + the partial index. The
``is_summary`` column is left in place because collapsed rows may
have been written by the job already — dropping the flag would make
those rows indistinguishable from raw data.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_SEED_ROWS = (
    (
        "embedding_collapse_enabled",
        "false",
        "memory",
        "GH-81: master switch for the embeddings collapse job. When true, "
        "the scheduled job clusters old rows per source_table, writes "
        "one summary row per cluster, and deletes the originals. "
        "Defaults to false so existing deployments are unaffected "
        "until an operator opts in.",
    ),
    (
        "embedding_collapse_age_days",
        "90",
        "memory",
        "GH-81: embeddings older than this many days are eligible for "
        "the collapse job. Tune down to compress sooner, up to retain "
        "raw rows longer. The job always filters on is_summary=false so "
        "existing summaries are never recollapsed.",
    ),
    (
        "embedding_collapse_cluster_size",
        "8",
        "memory",
        "GH-81: target cluster count per (source_table, age-group) when "
        "the collapse job runs k-means over candidate embeddings. The "
        "effective k is min(cluster_size, candidate_count // 2) so small "
        "groups fall back to fewer clusters. 8 gives ~8x reduction on "
        "a typical ~64-row group.",
    ),
    (
        "embedding_collapse_source_tables",
        "claude_sessions,brain,audit",
        "memory",
        "GH-81: comma-separated list of source_table values the collapse "
        "job is allowed to touch. posts/issues/memory are deliberately "
        "NOT in this list — they're queried by the live pipeline and "
        "must retain raw embeddings. Operators may add/remove entries "
        "but should never include an authoritative source here.",
    ),
)


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "embeddings"):
            logger.warning(
                "Table 'embeddings' missing — skipping migration 0075"
            )
            return

        await conn.execute(
            "ALTER TABLE embeddings "
            "ADD COLUMN IF NOT EXISTS is_summary BOOLEAN NOT NULL DEFAULT FALSE"
        )

        # Partial index — candidate scan is "old raw rows for a given
        # source_table". Summaries are excluded from the scan by design
        # so keeping them out of the index keeps it compact.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_collapse_candidates "
            "ON embeddings (source_table, created_at) "
            "WHERE is_summary = FALSE"
        )

        if not await _table_exists(conn, "app_settings"):
            logger.warning(
                "Table 'app_settings' missing — seeding skipped "
                "(column + index already applied)"
            )
            return

        for key, value, category, description in _SEED_ROWS:
            await conn.execute(
                """
                INSERT INTO app_settings (
                    key, value, category, description, is_secret
                )
                VALUES ($1, $2, $3, $4, false)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
        logger.info(
            "Migration 0075: embeddings.is_summary column + %d collapse "
            "settings seeded (job remains disabled by default)",
            len(_SEED_ROWS),
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if await _table_exists(conn, "app_settings"):
            keys = [r[0] for r in _SEED_ROWS]
            await conn.execute(
                "DELETE FROM app_settings WHERE key = ANY($1::text[])",
                keys,
            )

        if await _table_exists(conn, "embeddings"):
            await conn.execute(
                "DROP INDEX IF EXISTS idx_embeddings_collapse_candidates"
            )
        logger.info(
            "Migration 0075 rolled back: removed collapse settings "
            "+ candidate index. is_summary column preserved — collapsed "
            "rows may already exist and their flag must be retained."
        )
