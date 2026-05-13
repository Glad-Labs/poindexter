"""Stage 3 of the Prefect cutover (#410) — fresh-install default flips to 'true'.

The Phase-0 seed (``20260510_182824_seed_prefect_cutover_flag.py``)
shipped the cutover flag with default ``'false'`` so existing installs
weren't surprised by a behavior change. After the operator flipped to
``'true'`` and the canary stayed healthy for 2-3 days through real
production traffic, the Prefect dispatch path is now the recommended
default for new installs.

This migration **only affects fresh databases** — the same
``ON CONFLICT DO NOTHING`` pattern as Phase 0 means an existing row
(whatever value the operator chose) is preserved. If an operator
explicitly set ``'false'`` they keep it; if they left it at the
Phase-0 default they keep it; only databases that never ran the
Phase-0 migration get the new ``'true'`` default.

Stage 4 of the cutover (delete ``services/task_executor.py``,
~1,500 LOC) is gated on this default being live for ~7 days without
regression. See ``docs/architecture/prefect-cutover.md`` for the
full runbook.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_DESCRIPTION = (
    "Stage-3 cutover flag for #410 (Prefect orchestration). 'true' "
    "means TaskExecutor's _process_loop short-circuits and Prefect's "
    "deployment owns dispatch entirely. Default flipped 2026-05-13 "
    "after the operator's 2-3 day canary on 'true' stayed clean (no "
    "regression in qa_pass_completed cadence, approval-rate, or "
    "rag_engine_fallback). Existing rows preserved via ON CONFLICT "
    "DO NOTHING. See docs/architecture/prefect-cutover.md."
)


async def run_migration(conn) -> None:
    # Insert with the new 'true' default. ON CONFLICT preserves any
    # value the Phase-0 migration or the operator already wrote — the
    # migration is strictly forward-compatible: it cannot silently
    # downgrade an explicitly-chosen 'false' to 'true' or vice versa.
    await conn.execute(
        """
        INSERT INTO app_settings
            (key, value, category, description, is_secret, is_active)
        VALUES (
            'use_prefect_orchestration',
            'true',
            'orchestration',
            $1,
            false,
            true
        )
        ON CONFLICT (key) DO NOTHING
        """,
        _DESCRIPTION,
    )
    logger.info(
        "20260513_161559: use_prefect_orchestration default flipped to "
        "'true' for fresh installs (Stage 3 of #410). Existing rows "
        "untouched by ON CONFLICT DO NOTHING."
    )
