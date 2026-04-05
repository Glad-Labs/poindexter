"""
Migration 0038: Fix content_tasks.id — add NOT NULL + DEFAULT gen_random_uuid().

Issue #476: content_tasks.id is a UUID column declared as nullable with no
DEFAULT, meaning rows inserted without an explicit id value get NULL in the
primary-key column, breaking the uniqueness guarantee entirely.

Changes:
1. Ensure the pgcrypto extension is available (required for gen_random_uuid()).
2. Backfill any existing NULL id rows with a generated UUID so the NOT NULL
   constraint can be applied without failing.
3. ALTER the column to SET NOT NULL and SET DEFAULT gen_random_uuid().

Rollback: DROP the default and restore nullable (does not undo backfill).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Add NOT NULL + DEFAULT gen_random_uuid() to content_tasks.id."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'content_tasks')"
        )
        if not table_exists:
            logger.warning("Table 'content_tasks' does not exist — skipping migration 0038")
            return

        # Determine the column's data type — the migration was written assuming
        # UUID, but the column may actually be INTEGER (e.g. SERIAL / BIGSERIAL).
        # If it's an integer type the column already auto-increments via its
        # sequence default and gen_random_uuid() would be a type mismatch error.
        col_info = await conn.fetchrow(
            """
            SELECT data_type, column_default, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'content_tasks' AND column_name = 'id'
            """
        )
        if col_info is None:
            logger.warning("content_tasks.id column not found — skipping migration 0038")
            return

        data_type = col_info["data_type"]  # e.g. 'integer', 'bigint', 'uuid'
        existing_default = col_info["column_default"] or ""

        # If the column is an integer type, UUID backfill / default makes no
        # sense and would error.  The integer column already has a working
        # sequence default, so just ensure NOT NULL and move on.
        if data_type in ("integer", "bigint", "smallint"):
            logger.info(
                f"content_tasks.id is {data_type} — UUID default not applicable, "
                "skipping UUID-specific parts of migration 0038"
            )
            if col_info["is_nullable"] == "YES":
                await conn.execute(
                    "ALTER TABLE content_tasks ALTER COLUMN id SET NOT NULL"
                )
                logger.info("Set NOT NULL on integer content_tasks.id")
            return

        # --- UUID path (original logic) ---

        if "gen_random_uuid" in existing_default:
            logger.info("content_tasks.id already has gen_random_uuid() default — skipping")
            return

        # Ensure pgcrypto is available for gen_random_uuid()
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        logger.info("Ensured pgcrypto extension")

        # Backfill any rows where id IS NULL so we can add NOT NULL constraint safely
        backfilled = await conn.fetchval(
            """
            WITH updated AS (
                UPDATE content_tasks
                SET id = gen_random_uuid()
                WHERE id IS NULL
                RETURNING 1
            )
            SELECT COUNT(*) FROM updated
            """
        )
        if backfilled:
            logger.warning(
                f"Backfilled {backfilled} content_tasks rows that had NULL id values"
            )
        else:
            logger.info("No NULL id rows found in content_tasks — no backfill needed")

        # Add DEFAULT so future inserts without an explicit id get one
        await conn.execute(
            "ALTER TABLE content_tasks ALTER COLUMN id SET DEFAULT gen_random_uuid()"
        )
        logger.info("Set DEFAULT gen_random_uuid() on content_tasks.id")

        # Add NOT NULL constraint — safe now that we have backfilled
        await conn.execute(
            "ALTER TABLE content_tasks ALTER COLUMN id SET NOT NULL"
        )
        logger.info("Set NOT NULL on content_tasks.id")


async def down(pool) -> None:
    """Remove the DEFAULT and restore nullable on content_tasks.id."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE content_tasks ALTER COLUMN id DROP DEFAULT"
        )
        await conn.execute(
            "ALTER TABLE content_tasks ALTER COLUMN id DROP NOT NULL"
        )
        logger.info("Rolled back 0038: removed DEFAULT and NOT NULL from content_tasks.id")
