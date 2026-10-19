"""GeneratePostSummariesJob — the "In brief" summary for each published post.

See ``services.post_summaries`` for why. Each run summarizes up to
``config.limit`` (default 6) published main-feed posts that have none, newest
first, then re-exports each one so the site, its Markdown version and
/llms.txt pick the summary up. The backlog of posts published before this job
existed drains a few per run.

Off unless ``post_summary_enabled`` is true.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.utils.exception_format import describe_exception

logger = logging.getLogger(__name__)


class GeneratePostSummariesJob:
    name = "generate_post_summaries"
    description = "Write an answer-first 'In brief' summary for published posts that have none"
    schedule = "every 2 hours"
    idempotent = True  # a post that already has a summary is never selected again

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config = config.get("_site_config")
        if site_config is None or str(site_config.get("post_summary_enabled", "false")).lower() not in ("true", "1", "yes", "on"):
            return JobResult(ok=True, detail="post_summary_enabled is off", changes_made=0)

        from poindexter.services.post_summaries import fill_missing_summaries
        from poindexter.services.static_export_service import export_post

        try:
            result = await fill_missing_summaries(pool, site_config=site_config, limit=int(config.get("limit", 6)))
        except Exception as exc:  # noqa: BLE001
            logger.exception("GeneratePostSummariesJob: run failed: %s", describe_exception(exc))
            return JobResult(ok=False, detail=f"run failed: {describe_exception(exc)}", changes_made=0)

        exported = 0
        for slug in result["written"]:
            try:
                if await export_post(pool, slug, site_config=site_config):
                    exported += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("GeneratePostSummariesJob: re-export of %s failed: %s", slug, describe_exception(exc))
        detail = (
            f"{len(result['written'])} summarized ({exported} re-exported), "
            f"{len(result['failed'])} failed the checks twice"
        )
        return JobResult(
            ok=True, detail=detail, changes_made=len(result["written"]),
            metrics={"written": len(result["written"]), "failed": len(result["failed"]), "exported": exported},
        )
