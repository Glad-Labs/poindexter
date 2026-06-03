"""``/api/modules/probes`` — Module v1 Phase 4 brain-probe inventory.

The brain daemon polls this endpoint on its cycle to learn which
module-contributed probes the worker is offering. Each entry is a
spec (module / name / description / interval); the brain decides
when + how to invoke the probe callable on its side.

The actual probe invocation path (worker callback URL, brain daemon
RPC, or direct shared-process call) is a follow-up — this endpoint
delivers the *discovery* half of the cross-process bridge.

Returns 503 if the brain probe registry is missing (lifespan never
ran or app.state was stripped). 503 + a remediation message beats
silently returning an empty list (would mask a misconfigured
worker per ``feedback_no_silent_defaults``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from middleware.api_token_auth import verify_api_token

router = APIRouter(prefix="/api/modules", tags=["modules"])


@router.get("/probes", response_model=None)
async def list_module_probes(
    request: Request,
    _principal: str = Depends(verify_api_token),
) -> dict[str, Any]:
    """List brain probes contributed by Module v1 modules.

    Response shape::

        {
          "count": 0,
          "probes": [
            {"module": "content", "name": "embedding_backlog",
             "fqid": "content.embedding_backlog",
             "description": "Embedding backlog depth under threshold",
             "interval_seconds": 300}
          ]
        }

    The reference release ships with ``{"count": 0, "probes": []}``
    — no module registers a brain probe yet. The endpoint exists so:

    1. The brain daemon can poll without 404-ing during its cycle.
    2. Future modules can add probes without changing this route.
    """
    registry = getattr(request.app.state, "brain_probe_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "brain_probe_registry not initialised on app.state — "
                "lifespan did not complete. Restart the worker and "
                "check the boot log for [LIFESPAN] Module v1 errors."
            ),
        )
    probes = registry.probes()
    return {
        "count": len(probes),
        "probes": [
            {
                "module": p.module,
                "name": p.name,
                "fqid": p.fqid,
                "description": p.description,
                "interval_seconds": p.interval_seconds,
            }
            for p in probes
        ],
    }


__all__ = ["router"]
