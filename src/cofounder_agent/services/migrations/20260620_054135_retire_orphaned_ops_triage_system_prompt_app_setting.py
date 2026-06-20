"""Migration 20260620_054135_retire_orphaned_ops_triage_system_prompt_app_setting: retire orphaned ops triage system prompt app setting

ISSUE: Glad-Labs/poindexter#485   (firefighter prompt-as-skill migration)

The ``ops_triage_system_prompt`` app_settings key seeded the firefighter
triage system prompt before poindexter#485 moved that prompt onto the
UnifiedPromptManager stack (Langfuse ``production`` label → the
``skills/ops/triage/SKILL.md`` ``ops.triage.system_prompt`` default).
``services/firefighter_service.py`` now resolves the prompt exclusively
through ``_resolve_system_prompt`` and never reads this key — so the seeded
row is an orphaned third copy of the prompt text that can silently drift
from the canonical SKILL.md body.

This migration deletes the dead row from existing databases (the matching
seed was removed from ``0000_baseline.seeds.sql`` in the same change, so
fresh installs never seed it). Operators who had customised the key should
re-apply the override via Langfuse or a ``prompt_templates`` row keyed
``ops.triage.system_prompt`` — see ``docs/architecture/prompt-management.md``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_RETIRED_KEY = "ops_triage_system_prompt"


async def up(pool) -> None:
    """Delete the orphaned ``ops_triage_system_prompt`` row.

    Idempotent: deleting an absent key is a no-op, so this is safe to re-run
    and a no-op on fresh installs (the seed was removed from the baseline in
    the same change).
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = $1",
            _RETIRED_KEY,
        )
    logger.info("retired orphaned app_settings key %r (%s)", _RETIRED_KEY, result)


async def down(pool) -> None:
    """One-way cleanup — intentionally not reverted.

    The key was orphaned (no code path read it) and its sole canonical source
    is now ``skills/ops/triage/SKILL.md``. Re-seeding the row on rollback would
    only re-introduce the drift-prone duplicate this migration removed, so
    ``down()`` is an explicit no-op.
    """
    return
