"""RunRetentionJob — schedule the integrations retention_runner.

Companion to ``RunTapsJob``: ``retention_runner.run_all`` was only
callable from ``poindexter retention run``, so the 6 retention_policies
rows hadn't fired since 2026-05-01 19:05 UTC. This Job restores their
scheduled execution.

Cadence is 6 hours rather than the 1-hour floor used by taps because
retention is a sweep-everything operation (deletes/summarizations
across audit_log, embeddings.*, brain_decisions, gpu_metrics) and
running it more often than necessary just shifts CPU around without
recovering anything. The longest individual policy (audit_log
retention with summarization) takes minutes; six-hour cadence keeps
total daily wall time manageable.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class RunRetentionJob:
    name = "run_retention"
    description = "Walk enabled retention_policies rows and invoke each handler"
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.integrations import retention_runner

        site_config = config.get("_site_config")
        try:
            summary = await retention_runner.run_all(pool, site_config=site_config)
        except Exception as exc:
            logger.exception("RunRetentionJob failed: %s", exc)
            return JobResult(ok=False, detail=str(exc), changes_made=0)

        if summary.total_failed:
            return JobResult(
                ok=False,
                detail=(
                    f"{summary.total_failed} policy(ies) failed; "
                    f"deleted={summary.total_deleted} "
                    f"summarized={summary.total_summarized}"
                ),
                changes_made=summary.total_deleted + summary.total_summarized,
            )

        return JobResult(
            ok=True,
            detail=(
                f"deleted={summary.total_deleted} "
                f"summarized={summary.total_summarized}"
            ),
            changes_made=summary.total_deleted + summary.total_summarized,
        )
