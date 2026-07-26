"""Operator-triggered container restart — brain's side of the intent queue
(poindexter#909).

The console writes a ``service_restart_requests`` row (worker route:
``routes/service_restart_routes.py``); this module claims it on brain's own
poll loop and restarts the container via the SAME
``brain_daemon.docker_restart_container`` helper the self-healing
firefighter's ``restart_container`` remediation action already uses
(fire-drill verified, see ``brain/remediation/registry.py``). Brain-image
isolation: resolves ``brain_daemon`` lazily exactly like
``alert_dispatcher._resolve_brain_daemon_module`` and imports nothing from
``services/`` — raw SQL only.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger("brain.service_restart")

# How many pending requests to claim per poll — a burst of operator clicks
# (e.g. restarting several sidecars) shouldn't need multiple poll cycles.
_CLAIM_BATCH_SIZE = 5


def _resolve_brain_daemon_module() -> Any | None:
    """Identical resolution to alert_dispatcher._resolve_brain_daemon_module —
    duplicated rather than imported to keep this module standalone-testable
    without pulling in the full brain_daemon import graph."""
    mod = sys.modules.get("brain_daemon") or sys.modules.get("brain.brain_daemon")
    if mod is not None:
        return mod
    try:
        import brain_daemon as mod  # type: ignore

        return mod
    except ImportError:
        try:
            from brain import brain_daemon as mod  # type: ignore

            return mod
        except ImportError:
            return None


async def _write_audit(pool: Any, *, event_type: str, details: dict[str, Any], severity: str) -> None:
    """Matches remediation/engine.py's _write_audit shape — same audit_log
    columns, so this shows up in the console's Audit tab / event stream
    alongside firefighter-driven restarts."""
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, task_id, details, severity) "
            "VALUES ($1, $2, $3, $4::jsonb, $5)",
            event_type, "brain:service_restart", None, json.dumps(details, default=str), severity,
        )
    except Exception:  # noqa: BLE001  # silent-ok: audit is a courtesy write, never worth breaking the restart over
        logger.debug("[service_restart] audit_log write failed", exc_info=True)


async def poll_and_execute_restart_requests(pool: Any) -> None:
    """Claim + execute pending operator-triggered container restarts.

    Best-effort like ``alert_dispatcher.poll_and_dispatch``: a claim or
    execution failure for one row is logged and the loop moves on next
    cycle — it never raises into the caller (``service_restart_loop``'s
    watchdog exists for wholesale task death, not per-row errors).
    """
    mod = _resolve_brain_daemon_module()
    if mod is None or not hasattr(mod, "docker_restart_container"):
        logger.warning(
            "[service_restart] brain_daemon.docker_restart_container unavailable "
            "— restart requests will accumulate unclaimed"
        )
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, container FROM service_restart_requests
                WHERE status = 'pending'
                ORDER BY requested_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
                _CLAIM_BATCH_SIZE,
            )
            if not rows:
                return
            ids = [r["id"] for r in rows]
            await conn.execute(
                "UPDATE service_restart_requests SET status = 'claimed', claimed_at = now() "
                "WHERE id = ANY($1::uuid[])",
                ids,
            )

    for row in rows:
        request_id, container = row["id"], row["container"]
        try:
            ok, detail = await mod.docker_restart_container(container, pool=pool)
        except Exception as e:  # noqa: BLE001 — one bad row must not kill the batch
            ok, detail = False, f"docker_restart_container raised: {e}"[:400]
        final_status = "done" if ok else "failed"
        await pool.execute(
            "UPDATE service_restart_requests "
            "SET status = $1, detail = $2, completed_at = now() WHERE id = $3",
            final_status, detail, request_id,
        )
        await _write_audit(
            pool,
            event_type="service_restart_completed",
            severity="info" if ok else "warning",
            details={"request_id": str(request_id), "container": container, "ok": ok, "detail": detail},
        )
        logger.info(
            "[service_restart] %s -> %s (%s)", container, final_status, detail,
        )
