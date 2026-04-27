"""Migration 0099: Seed reject-status for the topic_decision gate (#146).

The generic approval-service falls back to ``"rejected"`` when no
per-gate ``approval_gate_<name>_reject_status`` setting is present.
For the ``topic_decision`` gate we want ``"dismissed"`` instead — a
rejected topic shouldn't surface in the same dashboard panels as a
rejected draft, and ``dismissed`` keeps the dispositions distinct
(matches the design called out in #146).

This migration is idempotent: ``ON CONFLICT (key) DO NOTHING`` so an
operator who's already pinned a custom value (perhaps "rejected_final"
for stricter tracking) keeps it on a re-run.

Why a migration instead of relying on the bootstrap defaults: the
per-gate reject-status isn't a feature flag — it's a category-of-state
decision. Pinning it explicitly here means a fresh DB lands with the
intended dashboards out of the box, and customers building their own
queues see the pattern modelled.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


_KEY = "approval_gate_topic_decision_reject_status"
_VALUE = "dismissed"
_DESCRIPTION = (
    "Status set on pipeline_tasks when a topic-decision gate rejects "
    "the topic (vs. the global default 'rejected'). Distinct so "
    "dashboards can separate rejected drafts from dismissed topics."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO app_settings (key, value, description, is_active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (key) DO NOTHING
            """,
            _KEY, _VALUE, _DESCRIPTION,
        )
        # asyncpg returns "INSERT 0 1" on a fresh insert, "INSERT 0 0"
        # on a no-op. Log either way so the operator can see the
        # seed ran.
        logger.info("0099: %s (%s = %s)", result, _KEY, _VALUE)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1 AND value = $2",
            _KEY, _VALUE,
        )
        logger.info("0099: removed %s seed", _KEY)
