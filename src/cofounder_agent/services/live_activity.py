"""Best-effort live-activity ledger writes. EVERY function swallows its own
errors (logs, never raises) — this is observability and must never break the
job / pipeline / brain that calls it. Pool-based so the worker AND the
minimal-dependency brain daemon can both use it (the live_activity seam).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def begin(
    pool: Any,
    *,
    kind: str,
    ref_id: str | None,
    title: str,
    detail: dict | None = None,
) -> int | None:
    """Open a running activity row; return its id, or None on any failure."""
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "INSERT INTO live_activity (kind, ref_id, title, detail) "
                "VALUES ($1, $2, $3, $4::jsonb) RETURNING id",
                kind,
                ref_id,
                title,
                json.dumps(detail or {}),
            )
    except Exception as exc:  # noqa: BLE001 — never break the caller
        logger.debug("live_activity.begin swallowed: %s", exc)
        return None


async def update(
    pool: Any,
    activity_id: int | None,
    *,
    step: str | None = None,
    pct: int | None = None,
) -> None:
    """Advance a running row's step/pct and bump its heartbeat. No-op on None id."""
    if activity_id is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE live_activity SET step = COALESCE($2, step), "
                "progress_pct = COALESCE($3, progress_pct), updated_at = now() "
                "WHERE id = $1 AND finished_at IS NULL",
                activity_id,
                step,
                pct,
            )
    except Exception as exc:  # noqa: BLE001
        # silent-ok: best-effort pulse heartbeat; a failed ledger write must
        # never break the caller (observability, not control flow).
        logger.debug("live_activity.update swallowed: %s", exc)


async def finish(
    pool: Any, activity_id: int | None, *, status: str = "ok"
) -> None:
    """Close a running row with a terminal status. No-op on None id."""
    if activity_id is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE live_activity SET status = $2, updated_at = now(), "
                "finished_at = now() WHERE id = $1 AND finished_at IS NULL",
                activity_id,
                status,
            )
    except Exception as exc:  # noqa: BLE001
        # silent-ok: best-effort pulse heartbeat; a failed ledger write must
        # never break the caller (observability, not control flow).
        logger.debug("live_activity.finish swallowed: %s", exc)


async def heartbeat(
    pool: Any, activity_id: int | None, *, interval_seconds: float = 30.0
) -> None:
    """Keep a long-running producer's row fresh while it runs.

    A producer that only calls begin/finish leaves ``updated_at`` frozen at
    ``started_at`` for its whole duration, so ``get_live_activity``'s freshness
    window hides it after ``live_activity_freshness_seconds`` even though it is
    still running (the pulse-goes-blank-during-a-topic-sweep bug). The producer
    seam launches this as a sidecar task right after ``begin`` and cancels it in
    a ``finally``; it bumps ``updated_at`` every ``interval_seconds`` (which
    MUST be shorter than the freshness window) so a live job stays visible.

    No-op on a None id. Best-effort: a failed bump never kills the loop, and
    ``CancelledError`` propagates so the caller's cancel tears it down cleanly.
    """
    if activity_id is None:
        return
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await update(pool, activity_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # silent-ok: best-effort heartbeat; a failed bump must never kill
            # the loop or the producer it shadows.
            logger.debug("live_activity.heartbeat swallowed: %s", exc)


async def get_live_activity(
    pool: Any, *, freshness_seconds: int, recent_limit: int
) -> dict:
    """Return the live pulse: currently-running rows (heartbeat within the
    freshness window), the recent-finished trail, and a per-kind running
    summary. Advisory read — on any failure returns empty, never raises,
    so a transient DB blip surfaces as "idle" rather than a 500.
    """
    try:
        async with pool.acquire() as conn:
            running = await conn.fetch(
                "SELECT kind, ref_id, title, status, step, progress_pct, detail, "
                "  started_at, updated_at "
                "FROM live_activity "
                "WHERE finished_at IS NULL "
                "  AND updated_at > now() - ($1 || ' seconds')::interval "
                "ORDER BY (kind IN ('content','media')) DESC, started_at ASC",
                str(freshness_seconds),
            )
            recent = await conn.fetch(
                "SELECT kind, ref_id, title, status, started_at, finished_at, "
                "  EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000 AS duration_ms "
                "FROM live_activity WHERE finished_at IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT $1",
                recent_limit,
            )
        summary: dict[str, int] = {}
        for r in running:
            summary[r["kind"]] = summary.get(r["kind"], 0) + 1
        return {
            "running": [dict(r) for r in running],
            "recent": [dict(r) for r in recent],
            "summary": {"running_by_kind": summary},
        }
    except Exception as exc:  # noqa: BLE001 — the read is advisory; empty beats a 500
        logger.warning("live_activity.get_live_activity failed: %s", exc)
        return {"running": [], "recent": [], "summary": {"running_by_kind": {}}}


async def reap_stale(pool: Any, *, reaper_seconds: int) -> int:
    """Mark running rows whose heartbeat lapsed past the window as 'stale'
    (and finished), so orphaned rows from a dead producer don't show as
    running forever. Returns the number of rows reaped.
    """
    try:
        async with pool.acquire() as conn:
            res = await conn.execute(
                "UPDATE live_activity SET status = 'stale', finished_at = now() "
                "WHERE finished_at IS NULL "
                "  AND updated_at < now() - ($1 || ' seconds')::interval",
                str(reaper_seconds),
            )
        return int(str(res).split()[-1]) if str(res).startswith("UPDATE") else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("live_activity.reap_stale swallowed: %s", exc)
        return 0
