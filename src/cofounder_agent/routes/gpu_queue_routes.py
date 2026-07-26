"""``GET /api/gpu/queue`` — the GPU scheduler's observable state
(poindexter#914 P0, plan Task A5).

Holder + waiters + rolling duration stats for the console GPU panel and
Grafana. The holder is THIS process's in-process view (``gpu_scheduler``'s
``_current_owner``/``_current_model``); a holder in another process shows up
indirectly via the cross-process ``gpu_queue`` waiter rows' ``pid`` — the
route is honest about a holder it can't see (``holder: null`` while waiters
are listed). Thin adapter: SQL lives in ``services/gpu_queue_mirror`` and
``services/gpu_lease_stats`` (adapter-purity, epic #1340).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from middleware.api_token_auth import verify_api_token
from services.gpu_lease_stats import list_stats
from services.gpu_queue_mirror import list_waiters
from services.gpu_scheduler import gpu
from services.logger_config import get_logger

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
    waiters: list[GpuWaiter] = Field(default_factory=list)
    stats: list[GpuLeaseStat] = Field(default_factory=list)


def _current_holder() -> GpuHolder | None:
    owner = getattr(gpu, "_current_owner", None)
    if not owner:
        return None
    acquired_at = getattr(gpu, "_acquired_at", None)
    held = max(0.0, time.monotonic() - acquired_at) if acquired_at else 0.0
    return GpuHolder(
        owner=owner,
        model=getattr(gpu, "_current_model", None),
        held_for_s=round(held, 1),
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
    return GpuQueueResponse(
        holder=_current_holder(),
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
