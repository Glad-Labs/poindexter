"""What "a media render is in flight" means, for anything that restarts the worker.

Two things restart ``poindexter-worker`` on their own: the host deploy sync
(``scripts/linux/deploy-checkout-sync.sh``) and the brain's migration-drift
auto-recover (``brain/migration_drift_probe.py``). A media render runs inside
the worker's scheduler job, so a restart mid-render kills it, and a render is
20-40 GPU-minutes (poindexter#964).

The deploy sync learned to see renders, one incident at a time, through
three database signals. The brain's probe checked only ``pipeline_tasks``
rows in ``in_progress``, which a render never is, and one of its restarts
(2026-08-15 05:15 UTC) landed while two render claims were open
(poindexter#1120). This module is the brain's copy of those three signals,
each named after the bash function it mirrors.
``tests/unit/brain/test_stack_busy.py`` pins that the bash script still
checks the same ones, so the two definitions of "busy" cannot drift apart:

- ``gpu_work_running``: any granted advisory lock. A render holds the GPU
  advisory lock for its GPU phases.
- ``media_render_running``: a render claim (``media_pipeline_dispatched_at``)
  younger than the claim window with no ``video`` asset yet. That covers the
  CPU phases (TTS, ASR, captions, the ffmpeg composite), which hold no lock.
- ``media_live_activity_running``: a fresh, unfinished ``live_activity`` row
  of kind ``media``. It spans both lanes of a dual-lane piece, which the
  claim check goes blind to once the long video's asset lands.

The deploy script's fourth signal, a RUNNING Prefect flow, is not here. A
content flow shows up as an ``in_progress`` task, which the probe already
counts, and the brain has no Prefect client.

Fail-open like the bash script: a query that fails reads as not busy, so a
broken read can never block migration recovery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The deploy sync's defaults (SYNC_MEDIA_CLAIM_MAX_MIN, SYNC_MEDIA_LIVE_HEARTBEAT_MAX_MIN).
DEFAULT_RENDER_CLAIM_MAX_MINUTES = 90
DEFAULT_RENDER_HEARTBEAT_MAX_MINUTES = 3

RENDER_SIGNALS_SQL = """
SELECT
  (SELECT count(*) FROM pg_locks
    WHERE locktype = 'advisory' AND granted) AS advisory_locks,
  (SELECT count(*) FROM pipeline_tasks pt
    WHERE pt.media_pipeline_dispatched_at > NOW() - make_interval(mins => $1::int)
      AND NOT EXISTS (
            SELECT 1 FROM media_assets ma
             WHERE ma.task_id = pt.task_id AND ma.type = 'video')) AS render_claims,
  (SELECT count(*) FROM live_activity
    WHERE kind = 'media'
      AND finished_at IS NULL
      AND updated_at > NOW() - make_interval(mins => $2::int)) AS media_heartbeats
"""


@dataclass(frozen=True)
class RenderSignals:
    advisory_locks: int = 0
    render_claims: int = 0
    media_heartbeats: int = 0

    @property
    def busy(self) -> bool:
        return bool(self.advisory_locks or self.render_claims or self.media_heartbeats)

    def describe(self) -> str:
        parts = [
            f"{n} {label}"
            for n, label in (
                (self.advisory_locks, "GPU/advisory lock(s) held"),
                (self.render_claims, "media render claim(s) open"),
                (self.media_heartbeats, "live media render heartbeat(s)"),
            )
            if n
        ]
        return ", ".join(parts) or "no render in flight"


def _count(row: Any, key: str) -> int:
    try:
        value = row[key]
    except (KeyError, TypeError, IndexError):
        return 0
    # Postgres count(*) is an int; anything else (a test double) reads as 0.
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


async def render_signals(
    pool: Any,
    *,
    claim_max_minutes: int = DEFAULT_RENDER_CLAIM_MAX_MINUTES,
    heartbeat_max_minutes: int = DEFAULT_RENDER_HEARTBEAT_MAX_MINUTES,
) -> RenderSignals:
    """The three render signals, in one round trip. Never raises."""
    try:
        row = await pool.fetchrow(
            RENDER_SIGNALS_SQL, max(1, int(claim_max_minutes)), max(1, int(heartbeat_max_minutes)),
        )
    except Exception as exc:  # noqa: BLE001 — fail-open, same as the deploy sync
        logger.warning("[STACK_BUSY] render-signal query failed (%s) — treating as not busy", exc)
        return RenderSignals()
    if row is None:
        return RenderSignals()
    return RenderSignals(
        advisory_locks=_count(row, "advisory_locks"),
        render_claims=_count(row, "render_claims"),
        media_heartbeats=_count(row, "media_heartbeats"),
    )


__all__ = [
    "DEFAULT_RENDER_CLAIM_MAX_MINUTES",
    "DEFAULT_RENDER_HEARTBEAT_MAX_MINUTES",
    "RENDER_SIGNALS_SQL",
    "RenderSignals",
    "render_signals",
]
