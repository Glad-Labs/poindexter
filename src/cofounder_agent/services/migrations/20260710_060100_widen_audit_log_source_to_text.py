"""Migration 20260710_060100: widen ``audit_log.source`` from varchar(50) to text.

ISSUE: GlitchTip #863 (self-hosted Sentry, org ``glad-labs`` / project
``poindexter``) — alert-driven; no GitHub issue.

``audit_log.source`` was ``character varying(50)``, but the column semantically
holds the *arbitrary dotted module path* of whatever raised a finding. Atom
sources overran it — ``modules.content.atoms.content_llm_reconcile_citations``
is 53 chars — so every finding from that source failed the INSERT with
``asyncpg StringDataRightTruncationError: value too long for type character
varying(50)`` and was **lost**. That is the precise loss the #303 loud-drop
path exists to surface (it logged ``WARN finding lost on audit write`` to
GlitchTip), and it defeats the whole point of findings being persistent.

Widening to ``text`` (not a bigger varchar) removes the width cliff entirely so
no future long source name can ever silently drop a finding again — findings
must never be lost, per ``feedback_dont_silence_fix_dedup`` /
``feedback_no_silent_defaults``. ``varchar(n) -> text`` is catalog-only in
Postgres (no table rewrite) and preserves the existing ``NOT NULL`` constraint.

Idempotent: guarded on ``information_schema`` so a re-apply (or a fresh install
whose baseline already ships ``text`` after the next squash) is a clean no-op.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        current = await conn.fetchval(
            """
            SELECT data_type
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'audit_log'
               AND column_name = 'source'
            """
        )
        if current == "character varying":
            # varchar(50) -> text: no table rewrite, brief ACCESS EXCLUSIVE lock,
            # NOT NULL preserved. Existing rows are unaffected (they already fit).
            await conn.execute("ALTER TABLE audit_log ALTER COLUMN source TYPE text")
            logger.info(
                "widen_audit_log_source_to_text up: audit_log.source "
                "character varying(50) -> text"
            )
        else:
            logger.info(
                "widen_audit_log_source_to_text up: audit_log.source is already "
                "%s — no-op",
                current or "absent",
            )


async def down(pool) -> None:
    # No-op: re-narrowing to varchar(50) would truncate (or reject) any source
    # already longer than 50 chars and reintroduce GlitchTip #863's lost-finding
    # bug. This is a one-way widening. Same posture as
    # 20260622_200222_drop_pipeline_tasks_category.down().
    logger.info(
        "widen_audit_log_source_to_text down: no-op (refusing to re-narrow a "
        "widened column and reintroduce the lost-finding truncation)"
    )
