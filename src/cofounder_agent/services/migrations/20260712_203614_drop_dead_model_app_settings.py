"""Migration 20260712_203614: drop dead model app_settings

ISSUE: Glad-Labs/poindexter#844   (zero-reader app_settings trim)

Removes five confirmed-dead, zero-reader ``app_settings`` keys that the
2026-06-23 cleanup retired from the seed but never deleted from existing
installs:

    pipeline_research_model, pipeline_refinement_model, pipeline_social_model,
    model_role_factchecker, default_model_tier

They are vestiges of the deleted 6-stage StageRunner flow, the unread
``model_role_*`` scheme, and an unwired global cost-tier knob; model
selection is now per-step ``*_model`` pins, so no production code path reads
them (guarded by
``tests/unit/services/migrations/test_drop_dead_model_settings.py``).

Why a forward-only migration is needed: the 2026-06-23 removal took the keys
out of the seed (``0000_baseline.seeds.sql``), which keeps *fresh* installs
clean — but the drop of the existing rows never landed. It was later folded
into the Phase G baseline squash, which only does ``CREATE ... IF NOT
EXISTS`` / ``INSERT ... ON CONFLICT DO NOTHING`` and so can add a row but
never delete one prod already carries. Prod therefore still holds all five
rows (a single ``updated_at`` 2026-06-24 batch re-insert). This DELETE
removes them.

Idempotent: deleting absent rows is a no-op, and the keys are gone from every
seed source (``baseline.seeds.sql`` / ``brain/seed_app_settings.json``) and
from ``settings_defaults.DEFAULTS``, so nothing re-seeds them on boot and the
removal sticks (guarded by ``scripts/ci/settings_seed_drift_lint.py``).

NOTE: ``scripts/ci/migrations_smoke.py`` runs the real runner against the
live prod DB in CI (bootstrap DSN = prod, no throwaway), so this DELETE
executes against prod at CI time, not only at worker boot. That is
intentional and safe for a scoped, idempotent dead-key removal.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Delete the five retired model settings from existing installs.

    Idempotent — ``DELETE`` of absent rows is a no-op, and nothing re-seeds
    these keys, so the removal is durable.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key IN ("
            "'pipeline_research_model', "
            "'pipeline_refinement_model', "
            "'pipeline_social_model', "
            "'model_role_factchecker', "
            "'default_model_tier'"
            ")"
        )
    logger.info("Migration drop_dead_model_app_settings: applied (%s)", result)


async def down(pool) -> None:
    """One-way migration — intentionally not reverted.

    These keys have no production reader and are absent from every seed
    source, so re-creating them on rollback would only re-introduce dead
    rows. Leave them gone.
    """
    return
