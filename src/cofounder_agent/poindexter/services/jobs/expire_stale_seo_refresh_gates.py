"""Scheduled job: expire seo_refresh tasks parked too long at the approval gate.

The seo_refresh pipeline is approval-FIRST (``seo_refresh_gate`` ships
enabled), so every run parks at the gate until the operator acts. Left alone,
unreviewed runs accumulate one-per-enqueue and go stale — the proposed meta
was generated against GSC metrics that keep drifting, and the "queue" silently
becomes a graveyard (the observed failure: 6 runs parked, the oldest 9 days).

This sweep is the gate-parked complement of ``reclaim_stale_inprogress_tasks``
(which only handles crashed ``in_progress`` runs): any ``seo_refresh`` task
paused at ``seo_refresh_gate`` for more than
``seo.refresh.gate_max_parked_days`` (default 14; ``0`` disables the sweep) is
dismissed through the normal rejection machinery —
``approval_service.reject`` with ``actor='staleness_sweep'``,
``status_override='dismissed'`` and ``record_outcome=False`` (an expiry is
queue hygiene, not a human quality judgment, so it must not feed the
variant-weight learning loop). The ``seo_refresh_gate`` rejection handler sees
the sweep actor and reopens the linked ``seo_opportunities`` row to
``'open'`` — the enqueue job can re-propose the post later with fresh metrics
and a fresh meta rewrite, instead of the opportunity dying unjudged.

Deliberately NOT gated on ``seo.refresh.enabled``: if the operator turns the
refresh loop off while runs are parked, those runs can never be re-proposed —
expiring them is exactly the cleanup wanted. With no parked rows the sweep is
a no-op either way.

Issue: SEO Harvest epic Glad-Labs/poindexter#762 follow-up (gate-queue
surfacing + staleness).
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.approval_service import reject as reject_gate
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

_SELECT_STALE_SQL = """
SELECT task_id::text AS task_id,
       topic,
       gate_paused_at,
       NOW() - gate_paused_at AS parked_for
  FROM pipeline_tasks
 WHERE template_slug = 'seo_refresh'
   AND status = 'awaiting_gate'
   AND awaiting_gate = 'seo_refresh_gate'
   AND gate_paused_at < NOW() - make_interval(secs => $1)
 ORDER BY gate_paused_at ASC
"""


class ExpireStaleSeoRefreshGatesJob:
    name = "expire_stale_seo_refresh_gates"
    description = (
        "Dismiss seo_refresh runs parked at seo_refresh_gate longer than "
        "seo.refresh.gate_max_parked_days (0 disables); their opportunity "
        "rows reopen for a fresh future proposal"
    )
    schedule = "every 24 hours"
    idempotent = False  # mutates task + opportunity status

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        max_days = 14.0
        if sc is not None:
            max_days = float(sc.get_float("seo.refresh.gate_max_parked_days", 14))
        if max_days <= 0:
            return JobResult(
                ok=True, detail="seo.refresh.gate_max_parked_days<=0; disabled"
            )

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_SELECT_STALE_SQL, max_days * 86400.0)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[expire_stale_seo_refresh_gates] stale query failed: %s",
                describe_exception(e), exc_info=True,
            )
            return JobResult(
                ok=False, detail=f"stale query failed: {describe_exception(e)}"
            )

        expired: list[dict[str, Any]] = []
        for r in rows:
            task_id = r["task_id"]
            parked_days = getattr(r["parked_for"], "days", "?")
            try:
                await reject_gate(
                    task_id=task_id,
                    gate_name="seo_refresh_gate",
                    reason=(
                        f"auto-expired: parked at seo_refresh_gate for "
                        f"{parked_days}d (> {max_days:g}d cap); opportunity "
                        "reopened for a fresh proposal"
                    ),
                    actor="staleness_sweep",
                    site_config=sc,
                    pool=pool,
                    status_override="dismissed",
                    record_outcome=False,
                )
                expired.append({"task_id": task_id, "topic": r["topic"] or ""})
            except Exception as e:  # noqa: BLE001 — one contested row (e.g. approved mid-sweep) never aborts the run
                logger.warning(
                    "[expire_stale_seo_refresh_gates] expire failed for %s: %s",
                    task_id, describe_exception(e),
                )

        if expired:
            body = (
                f"## SEO refresh — {len(expired)} gate-parked run(s) expired\n\n"
                "Parked past the review window with no operator decision; "
                "dismissed and their opportunities reopened for a fresh "
                "future proposal.\n\n"
                + "\n".join(
                    f"- **{e['topic']}** (task {e['task_id'][:8]})"
                    for e in expired
                )
            )
            emit_finding(
                source="expire_stale_seo_refresh_gates",
                kind="seo_refresh_gate_expired",
                title=(
                    f"SEO: {len(expired)} unreviewed refresh run(s) expired "
                    f"after {max_days:g}d"
                ),
                body=body,
                # 'warn' so findings_alert_router picks it up (it floors out
                # 'info'); routine Discord-tier notification, not a page.
                severity="warn",
                extra={"count": len(expired)},
            )

        logger.info(
            "[expire_stale_seo_refresh_gates] expired %d gate-parked task(s)",
            len(expired),
        )
        return JobResult(
            ok=True,
            detail=f"expired {len(expired)} gate-parked seo_refresh task(s)",
            changes_made=len(expired),
            metrics={"expired": len(expired)},
        )
