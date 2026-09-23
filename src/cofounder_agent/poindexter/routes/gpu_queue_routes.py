"""``GET /api/gpu/queue`` — the GPU scheduler's observable state
(poindexter#914 P0, plan Task A5).

Holder + waiters + rolling duration stats for the console GPU panel and
Grafana.

The holder is resolved from POSTGRES, not from this process. It used to read
``gpu_scheduler``'s ``_current_owner`` — a module global describing whichever
process answered the request — so the console (served by the FastAPI worker)
reported "lock free · nothing holding the GPU" while a render in
``poindexter-prefect-worker`` held the card, with the DB-mirrored waiter list
right underneath it showing the queue that holder had created. Two sources,
two scopes, printed side by side.

``gpu_scheduler.list_pg_holders`` closes that: the advisory lock sits on a
connection stamped with ``_holder_tag`` (poindexter#1018), so any process can
read the holder back out of ``pg_stat_activity``. ``holder`` stays as the
primary holder for existing consumers; ``holders`` carries all of them, since
device scoping (#3457 Phase 2) lets two scoped sessions hold different cards
at once. The in-process view is the FALLBACK, used only when Postgres can
name nobody — and it is labelled as such in ``source``.

Thin adapter: SQL lives in ``services/gpu_queue_mirror``,
``services/gpu_lease_stats`` and ``services/gpu_scheduler`` (adapter-purity,
epic #1340).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from middleware.api_token_auth import verify_api_token
from poindexter.services.gpu_lease_stats import list_stats
from poindexter.services.gpu_queue_mirror import list_waiters
from poindexter.services.gpu_scheduler import gpu, list_pg_holders
from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/gpu",
    tags=["gpu"],
    dependencies=[Depends(verify_api_token)],
)


class GpuHolder(BaseModel):
    owner: str
    model: str | None = None
    held_for_s: float = Field(..., ge=0.0)
    # Everything below is additive (poindexter#914 follow-on): existing
    # consumers keep reading owner/model/held_for_s unchanged.
    phase: str | None = None
    task_id: str | None = None
    pid: int | None = None
    #: "postgres" = cross-process truth; "in_process" = this process's own
    #: view, used only when Postgres named nobody. A consumer that wants to
    #: know whether the answer is trustworthy across containers reads this
    #: rather than inferring it.
    source: str = "postgres"
    #: Advisory-lock keys held. One entry is the whole GPU; more than one
    #: means device scoping is on and this session holds specific cards.
    keys: list[int] = Field(default_factory=list)


class GpuWaiter(BaseModel):
    pid: int
    owner: str
    model: str | None = None
    phase: str | None = None
    priority: str = "pipeline"
    waiting_s: float = Field(..., ge=0.0)


class GpuLeaseStat(BaseModel):
    owner: str
    phase: str
    samples: int
    ewma_ms: float | None = None
    p50_ms: float | None = None
    p90_ms: float | None = None
    updated_at: datetime | None = None


class GpuQueueResponse(BaseModel):
    holder: GpuHolder | None = None
    #: Every current holder. Usually 0 or 1; device scoping (#3457 Phase 2)
    #: permits two scoped sessions on different cards concurrently. ``holder``
    #: is ``holders[0]`` — kept so existing consumers are untouched.
    holders: list[GpuHolder] = Field(default_factory=list)
    waiters: list[GpuWaiter] = Field(default_factory=list)
    stats: list[GpuLeaseStat] = Field(default_factory=list)


def _current_holder() -> GpuHolder | None:
    """This process's own view — the fallback when Postgres names nobody."""
    owner = getattr(gpu, "_current_owner", None)
    if not owner:
        return None
    acquired_at = getattr(gpu, "_acquired_at", None)
    held = max(0.0, time.monotonic() - acquired_at) if acquired_at else 0.0
    return GpuHolder(
        owner=owner,
        model=getattr(gpu, "_current_model", None),
        held_for_s=round(held, 1),
        phase=getattr(gpu, "_current_phase", None),
        source="in_process",
    )


def _to_holder(row: dict[str, Any]) -> GpuHolder:
    """One ``list_pg_holders`` row as the wire model.

    An UNTAGGED session still becomes a holder — named by its raw
    ``application_name``, or "unknown" when even that is blank. Dropping it
    would put us back where we started: a real holder rendering as an empty
    lock.
    """
    owner = row.get("owner") or row.get("application_name") or "unknown"
    held = row.get("held_for_s")
    return GpuHolder(
        owner=str(owner),
        # The holder tag has no room for a model name (application_name is
        # capped at 63 bytes), so a pg-resolved holder reports None here
        # rather than a guess. NULL means "not recorded", never "no model".
        model=None,
        held_for_s=round(float(held), 1) if held is not None else 0.0,
        phase=row.get("phase"),
        task_id=row.get("task_id"),
        pid=row.get("pid") or row.get("backend_pid"),
        source="postgres",
        keys=list(row.get("keys") or []),
    )


@router.get(
    "/queue",
    response_model=GpuQueueResponse,
    summary="GPU lock holder, waiters, and rolling hold-duration stats",
)
async def get_gpu_queue(
    token: str = Depends(verify_api_token),
) -> GpuQueueResponse:
    del token
    waiter_rows: list[dict[str, Any]] = await list_waiters()
    stat_rows: list[dict[str, Any]] = await list_stats()
    holders = [_to_holder(r) for r in await list_pg_holders()]
    if not holders:
        # Postgres named nobody. That is usually the truth (the lock is free),
        # but it is also what an unreachable DB looks like, so fall back to
        # whatever THIS process knows rather than asserting "free" on a failed
        # lookup.
        in_process = _current_holder()
        if in_process is not None:
            holders = [in_process]
    return GpuQueueResponse(
        holder=holders[0] if holders else None,
        holders=holders,
        waiters=[
            GpuWaiter(
                pid=int(r["pid"]),
                owner=r["owner"],
                model=r.get("model"),
                phase=r.get("phase"),
                priority=r.get("priority") or "pipeline",
                waiting_s=round(float(r.get("waiting_s") or 0.0), 1),
            )
            for r in waiter_rows
        ],
        stats=[
            GpuLeaseStat(
                owner=r["owner"],
                phase=r["phase"],
                samples=int(r.get("samples") or 0),
                ewma_ms=r.get("ewma_ms"),
                p50_ms=r.get("p50_ms"),
                p90_ms=r.get("p90_ms"),
                updated_at=r.get("updated_at"),
            )
            for r in stat_rows
        ],
    )
