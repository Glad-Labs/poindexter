"""Migration 0083: Declarative integrations framework — scaffolding seed.

Phase 0 of the Declarative Data Plane RFC
(docs/architecture/declarative-data-plane-rfc-2026-04-24.md).

No new tables yet — every surface (webhooks, retention, external taps,
...) owns its own table, each landing in its own migration. What this
migration does:

1. Ensure ``pgcrypto`` is installed (required for every surface that
   references an encrypted secret via the shared secret_resolver).
2. Seed a ``integrations_framework_version`` app_setting so the runner
   can detect whether the framework scaffolding has landed before
   dispatching any handlers.

Idempotent.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret)
            VALUES (
                'integrations_framework_version',
                '1',
                'integrations',
                'Declarative integrations framework version. Bumped when '
                'the handler registry or secret_resolver contract changes '
                'in a way handlers must adapt to.',
                FALSE
            )
            ON CONFLICT (key) DO NOTHING
            """,
        )
        logger.info(
            "0083: integrations framework scaffolding seeded (pgcrypto ready, "
            "version flag set)"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'integrations_framework_version'"
        )
        logger.info("0083: removed integrations_framework_version flag")
