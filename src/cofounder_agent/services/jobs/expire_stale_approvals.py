"""ExpireStaleApprovalsJob — auto-expire tasks stuck awaiting_approval.

Replaces ``IdleWorker._expire_stale_approvals``. Runs every 6 hours
by default.

Config (``plugin.job.expire_stale_approvals``):
- ``config.ttl_days`` (default 7) — how long a task can sit in
  ``awaiting_approval`` before auto-expire. Can also be read from the
  legacy ``approval_ttl_days`` app_settings key for back-compat.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class ExpireStaleApprovalsJob:
    name = "expire_stale_approvals"
    description = "Auto-expire content_tasks stuck in awaiting_approval past TTL"
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        ttl_days = int(config.get("ttl_days", 0))
        if not ttl_days:
            try:
                async with pool.acquire() as conn:
                    raw = await conn.fetchval(
                        "SELECT value FROM app_settings WHERE key = 'approval_ttl_days'"
                    )
                    if raw:
                        ttl_days = int(raw)
            except Exception:
                pass
        if not ttl_days:
            ttl_days = 7

        try:
            async with pool.acquire() as conn:
                expired = await conn.fetch(
                    """
                    UPDATE content_tasks
                    SET status = 'expired',
                        result = jsonb_build_object(
                          'reason', 'Auto-expired: exceeded approval TTL of '
                                    || $1 || ' days'
                        )
                    WHERE status = 'awaiting_approval'
                      AND updated_at < NOW() - make_interval(days => $1)
                    RETURNING task_id, topic
                    """,
                    ttl_days,
                )
            if expired:
                logger.info(
                    "ExpireStaleApprovalsJob: expired %d task(s) (TTL: %d days)",
                    len(expired), ttl_days,
                )
            return JobResult(
                ok=True,
                detail=f"expired {len(expired)} task(s) past {ttl_days}d TTL",
                changes_made=len(expired),
            )
        except Exception as e:
            logger.exception("ExpireStaleApprovalsJob failed: %s", e)
            return JobResult(ok=False, detail=str(e), changes_made=0)
