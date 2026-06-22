"""Migration 20260622_042749: add regen counters + pending flags to pipeline_tasks.

ISSUE: component-scoped regen gate (preview_gate) —
docs/architecture/2026-06-21-component-scoped-regen-gate.md

The ``preview_gate`` lets the operator surgically regenerate just the images or
just the text of a post under review. Each component needs two durable bits of
state on the task row:

- ``regen_images_attempts`` / ``regen_text_attempts`` — monotonic counters. The
  operator surface (``approval_service.regen_at_gate``) bumps these and refuses a
  regen once a component hits ``app_settings.regen_<c>_max_attempts``. Also the
  source for the Grafana regen panels.
- ``regen_images_pending`` / ``regen_text_pending`` — one-shot consume flags. The
  surface sets ``pending=true`` on a regen request; the ``approval_gate`` atom
  reads it BEFORE pausing, clears it, and routes ``_goto`` to the image/writer
  block. Because the flag is cleared (consumed) the loop-back finds it ``false``
  and falls through to a single fresh review page — no infinite loop, no
  redundant page. (Routing the decision via the LangGraph resume value can't do
  this: ``interrupt()`` only returns after ``pause_at_gate`` has already paged.)

Metadata-only on PG11+ (NOT NULL + constant DEFAULT, no table rewrite).
Idempotent (IF NOT EXISTS) + light-env safe; a no-op on a fresh baseline DB
other than adding the columns.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UP = """
ALTER TABLE pipeline_tasks
    ADD COLUMN IF NOT EXISTS regen_images_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS regen_images_pending  boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS regen_text_attempts   integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS regen_text_pending    boolean NOT NULL DEFAULT false
"""

_DOWN = """
ALTER TABLE pipeline_tasks
    DROP COLUMN IF EXISTS regen_images_attempts,
    DROP COLUMN IF EXISTS regen_images_pending,
    DROP COLUMN IF EXISTS regen_text_attempts,
    DROP COLUMN IF EXISTS regen_text_pending
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_UP)
    logger.info(
        "Migration 20260622_042749: added pipeline_tasks regen counters + pending flags"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DOWN)
    logger.info(
        "Migration 20260622_042749: dropped pipeline_tasks regen counters + pending flags"
    )
