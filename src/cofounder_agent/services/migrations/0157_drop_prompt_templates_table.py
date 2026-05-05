"""Migration 0157: drop prompt_templates table — phase 2 of poindexter#47.

Phase 1 (commit 5a9f548f) put Langfuse in front of the resolution
chain; phase 1.5 (commit 75973748) actually wired the credentials
through the DI seam so the lookup hit Langfuse instead of empty
strings. With Langfuse as the live edit surface and YAML as the OSS
distribution default, the parallel ``prompt_templates`` DB store
added no value — operators can't tell which copy is live, and the
load_from_db query was a flat-file replay of YAML anyway (the local
DB had 30 default rows + 0 premium rows when this migration was
written).

The companion ``prompt_manager.py`` change gutted ``load_from_db``
to a no-op + restartworker. After both ship, the table has no
writers and the loader doesn't query it; this migration removes the
storage.

Pre-flight: phase-1.5 worker shipped 2026-05-04 with the gutted
loader. If a stale process from before that point is still running,
it'll error on load — that's a louder signal than silent
fallback-to-YAML.

Down-path re-applies 0094 (table create) + 0141 (premium gating
column) so a rollback boots an empty table the operator can re-seed
via 0152's logic. Doesn't restore data — the migration history
record from 0152 stays applied, so a true rollback also needs a
manual ``DELETE FROM schema_migrations WHERE name LIKE '0157_%'``
followed by re-running 0152's up().
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS prompt_templates CASCADE")
        logger.info(
            "Migration 0157: dropped prompt_templates table — "
            "Langfuse is now the live edit surface, YAML is the OSS default"
        )


async def down(pool) -> None:
    """Recreate the table shell. Data is NOT restored.

    Replays the original CREATE from migration 0094 plus the premium
    gating column from 0141. Operators wanting to roll back fully
    must also re-seed the table — easiest via re-running the YAML
    import path (services/migrations/0152_seed_atom_prompts_into_prompt_templates.py
    has the seeding logic for the atom-prompt subset).
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_templates (
                id SERIAL PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                template TEXT NOT NULL,
                category TEXT,
                version TEXT,
                description TEXT,
                output_format TEXT,
                example_output TEXT,
                notes TEXT,
                is_active BOOLEAN NOT NULL DEFAULT true,
                source TEXT NOT NULL DEFAULT 'default'
                    CHECK (source IN ('default', 'premium')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prompt_templates_active "
            "ON prompt_templates (is_active) WHERE is_active = true"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prompt_templates_source "
            "ON prompt_templates (source)"
        )
        logger.info(
            "Migration 0157 down: recreated prompt_templates shell — "
            "data NOT restored, re-run 0152 to reseed atom prompts"
        )
