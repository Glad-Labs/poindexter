"""Migration 20260626_150803: drop orphaned SDXL-era app_settings keys.

Two keys survived the SDXL→image-gen rename (PR #1947) because no reader
ever accesses them in the current codebase:

  - ``pipeline_explicit_writer_unload_before_sdxl`` — original gate key;
    the code now reads ``pipeline_writer_unload_before_image_gen`` (the
    "explicit_" prefix was dropped in an earlier refactor and
    settings_defaults.py seeds the live key on every boot).

  - ``sdxl_prompt_model`` — SDXL image-prompt LLM override; the
    image-gen server selects its prompt model internally and this setting
    has no reader anywhere in Python code.

Both keys are safe to delete: no code path reads them, they will not
resurface via seeds, and removing them reduces confusion for future
audits of the app_settings table.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEAD_KEYS = [
    "pipeline_explicit_writer_unload_before_sdxl",
    "sdxl_prompt_model",
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key in _DEAD_KEYS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                key,
            )
    logger.info("drop_orphaned_sdxl_settings_keys: removed %d dead key(s)", len(_DEAD_KEYS))


async def down(pool) -> None:  # noqa: ARG001
    # One-way deletion — the values are not recoverable from migration state.
    # Re-insert manually if rollback is needed.
    return
