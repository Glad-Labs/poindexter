"""Add media_approvals.reject_reason, so a rejection says why.

Earned 2026-10-18. In the 45 days before, the operator rejected 10 of 19
Shorts and 24 of 29 long videos, and not one rejection carried a reason: the
console's Reject button sent no note, and ``notes`` was empty on every row.
The automatic media grade gave most of those rejected renders 95-100, so it
did not predict the decision, and nothing recorded what was wrong with them.
Without a reason there is nothing to tune the grade against, and no way to
auto-approve the kind of render the operator always approves.

``reject_reason`` is a short code from the ``media_reject_reasons`` setting
(``voice``, ``visuals``, ``hook`` ...), checked by
``media_approval_service.decide``. ``notes`` keeps any free text. NULL on
approvals, on rejections made before this column, and on automatic
rejections (``decided_by`` says who decided).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE media_approvals ADD COLUMN IF NOT EXISTS reject_reason varchar(40)"
        )
        await conn.execute(
            "COMMENT ON COLUMN media_approvals.reject_reason IS "
            "'Why the operator rejected it: a code from the media_reject_reasons setting. "
            "NULL on approvals, older rows and automatic rejections.'"
        )
    logger.info("Migration add_reject_reason_to_media_approvals: column present")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE media_approvals DROP COLUMN IF EXISTS reject_reason")
