"""UrlValidationStage — stage 2B.1 of the content pipeline.

Extracts URLs from the generated draft and validates each one. Populates
a summary on the context for downstream observability/UI; never halts
the pipeline — a broken link doesn't block publishing in the legacy
flow, it just gets flagged.

Ports the block at content_router_service.py stage 2B.1 (line 2417).

## Legacy dual-gate note

The legacy block has TWO gates:
1. ``pipeline_stages`` table (via ``_is_stage_enabled(pool, "url_validation")``)
2. (implicit — exception handler catches everything and records as error)

After Phase E cutover, the plugin runner's own ``plugin.stage.url_validation.enabled``
gate becomes the single source of truth. During the transition window
this Stage still consults the legacy ``pipeline_stages`` table so
operators who disabled url_validation that way aren't surprised when
the new runner starts invoking stages.

Follow-up: migrate ``pipeline_stages`` rows into ``plugin.stage.*.enabled``
during the Phase E cutover commit and drop the legacy check.

## Context reads

- ``task_id`` (str), ``content`` (str)
- ``database_service`` (for the pool lookup, used by the legacy gate)

## Context writes

- ``url_validation`` (dict with total_urls / valid / invalid / broken_urls
  OR {"skipped": True} OR {"error": str})
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


class UrlValidationStage:
    name = "url_validation"
    description = "Extract URLs from the draft and verify each one is reachable"
    timeout_seconds = 60
    # Legacy: exceptions caught, pipeline continues.
    halts_on_failure = False

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        content_text = context.get("content", "")
        task_id = context.get("task_id", "")

        if not content_text:
            return StageResult(
                ok=True,
                detail="no content to validate",
                context_updates={
                    "url_validation": {
                        "total_urls": 0, "valid": 0, "invalid": 0, "broken_urls": [],
                    }
                },
                metrics={"skipped": True},
            )

        try:
            from services.url_validator import get_url_validator
            validator = get_url_validator()
            urls = validator.extract_urls(content_text)
            if not urls:
                return StageResult(
                    ok=True,
                    detail="no URLs found",
                    context_updates={
                        "url_validation": {
                            "total_urls": 0, "valid": 0, "invalid": 0, "broken_urls": [],
                        }
                    },
                )

            results = await validator.validate_urls(urls)
            broken = {u: s for u, s in results.items() if s == "invalid"}
            summary = {
                "total_urls": len(urls),
                "valid": sum(1 for v in results.values() if v == "valid"),
                "invalid": len(broken),
                "broken_urls": list(broken.keys()),
            }
            if broken:
                logger.warning(
                    "URL validation: %d/%d broken links in task %s: %s",
                    len(broken), len(urls), task_id[:8] if task_id else "?",
                    ", ".join(list(broken.keys())[:5]),
                )
            else:
                logger.info(
                    "URL validation: all %d links valid for task %s",
                    len(urls), task_id[:8] if task_id else "?",
                )
            return StageResult(
                ok=True,
                detail=f"{summary['valid']}/{summary['total_urls']} valid",
                context_updates={"url_validation": summary},
                metrics=summary,
            )
        except Exception as e:  # noqa: BLE001 — legacy non-critical
            logger.warning("URL validation failed (non-critical): %s", e)
            return StageResult(
                ok=False,
                detail=f"validation raised: {e}",
                context_updates={"url_validation": {"error": str(e)}},
            )
