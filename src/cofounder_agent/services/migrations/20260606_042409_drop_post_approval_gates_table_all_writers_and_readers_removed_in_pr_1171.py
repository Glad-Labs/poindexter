"""Migration 20260606_042409: drop post_approval_gates table.

ISSUE: Glad-Labs/poindexter#559 (media gate half-cutover cleanup)

All writers, readers, and orchestration for the PRE-publish gate were removed
in PR #1171:

  - ``services/gates/post_approval_gates.py`` deleted
  - ``publish_service.create_gates_for_post`` call removed
  - CLI ``post approve / reject / revise / reopen / show`` commands deleted
  - Brain gate probes deleted (gate_auto_expire_probe, gate_pending_summary_probe)

The live gate is ``media_approvals`` (POST-publish). The table had 143 rows on
prod, all ``gate_name='final'`` — confirming zero media-gate rows were ever
produced by the PRE-publish path.

Also deletes the stale ``gate_pending_summary_enabled`` app_settings key whose
only reader (``brain/gate_pending_summary_probe.py``) was deleted in the same PR.

Idempotent: both statements use ``IF EXISTS`` / ``WHERE key = ...``.
``down()`` is a no-op — archival final-gate rows have no restore path.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_GATE_KEYS = (
    "gate_auto_expire_poll_interval_minutes",
    "gate_pending_max_age_hours",
    "gate_pending_summary_discord_per_cycle",
    "gate_pending_summary_enabled",
    "gate_pending_summary_min_age_minutes",
    "gate_pending_summary_poll_interval_minutes",
    "gate_pending_summary_telegram_dedup_minutes",
    "gate_pending_summary_telegram_growth_threshold",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        r1 = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_GATE_KEYS),
        )
        logger.info(
            "Migration drop_post_approval_gates: deleted gate probe settings (%s)", r1,
        )
        r2 = await conn.execute(
            "DROP TABLE IF EXISTS post_approval_gates",
        )
        logger.info(
            "Migration drop_post_approval_gates: table dropped (%s)", r2,
        )


async def down(_pool) -> None:
    """No-op — archival gate rows have no meaningful restore path."""
    logger.info("Migration drop_post_approval_gates down: nothing to restore")
