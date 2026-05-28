"""Migration 20260528_001023: add quality signals columns to media_approvals.

Layer 1 of the media-quality eval system. Deterministic signals
(duration, silence ratio, RMS dB for audio; duration, FPS, black-frame
ratio for video) get computed via ffprobe immediately after a medium
lands on disk. Stored on the same row that holds the operator-approval
gate so the approval queue can show "here's the audio, here are the
red-flag signals, decide".

Why on the gate row instead of a separate table
================================================
Quality data is 1:1 with an approval decision. Joining is wasteful and
the signals shape is loose (ffprobe outputs vary per format / per
sample). One JSONB column lets us add new probes without
ALTER-column-per-feature churn — same pattern as
``pipeline_tasks.metadata`` and ``publishing_adapters.metadata``.

A separate ``quality_score`` numeric column makes "show me low-quality
podcasts" filterable without JSONB extraction overhead. Initially the
score is just a Layer 1 pass/fail (0.0 = auto-rejected, 1.0 = all
signals passed). Layer 2 (faithfulness / LLM-rated quality) lands a
follow-up PR — it'll blend into the same score column with a weighted
average.

Idempotent — ``ADD COLUMN IF NOT EXISTS``. Existing rows pre-date
the eval feature so their values are NULL; the read paths treat
NULL as "not yet evaluated, don't auto-reject" (conservative).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE media_approvals
                ADD COLUMN IF NOT EXISTS quality_score numeric,
                ADD COLUMN IF NOT EXISTS quality_signals jsonb,
                ADD COLUMN IF NOT EXISTS quality_evaluated_at timestamptz
            """,
        )
        logger.info(
            "Migration 20260528_001023_add_quality_signals_columns_to_media_approvals: applied",
        )


async def down(pool) -> None:
    """Revert: drop the columns. Quality data is recoverable by
    re-running the eval against the on-disk file, so this is safely
    reversible — just a one-cycle compute cost per medium."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE media_approvals
                DROP COLUMN IF EXISTS quality_score,
                DROP COLUMN IF EXISTS quality_signals,
                DROP COLUMN IF EXISTS quality_evaluated_at
            """,
        )
