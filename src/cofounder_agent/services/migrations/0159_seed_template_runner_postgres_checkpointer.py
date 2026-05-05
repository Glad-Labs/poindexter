"""Migration 0159: seed template_runner_use_postgres_checkpointer (default off).

Bumped from 0158 → 0159 to avoid a number collision with the two
sibling migrations that landed in the same overnight batch:
``0158_task_failure_alert_dedup`` (#370) and
``0158_seed_langfuse_tracing_setting`` (#373). The runner discovers
migrations by filename, so all three apply cleanly — but the prefix
should be unique.


Phase 1.5 of the dynamic-pipeline-composition spec
(``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``).
Adds the gating flag for the new Postgres-backed LangGraph checkpointer
wired into ``services/template_runner.py`` per Glad-Labs/poindexter#371.

Default is ``false`` so the cutover stays operator-controlled — flip
the flag to ``true`` only after running through the smoke + fallback
tests on the live worker. With the flag off, TemplateRunner continues
to use the in-memory ``MemorySaver`` (identical to pre-#371 behavior).

Per the DB-first-config principle (``feedback_db_first_config``): the
checkpointer choice is a tunable, not a hardcoded constant. SaaS /
A/B-testing readiness — operators can flip it per-environment via
``app_settings`` without redeploying.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS: list[tuple[str, str, str, str]] = [
    # (key, value, category, description)
    (
        "template_runner_use_postgres_checkpointer",
        "false",
        "pipeline",
        "When true, services/template_runner.py compiles each LangGraph "
        "with an AsyncPostgresSaver checkpointer (durable state across "
        "worker restarts; resumable by thread_id). When false (default), "
        "MemorySaver is used — checkpoints live only for the duration "
        "of the run. Phase 1.5 of Glad-Labs/poindexter#371; flip to "
        "true after operator review of the smoke + fallback tests.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description in _SEEDS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
            logger.info("Migration 0159: seeded %s = %s", key, value)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, _, _, _ in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", key,
            )
        logger.info(
            "Migration 0158 down: removed "
            "template_runner_use_postgres_checkpointer"
        )
