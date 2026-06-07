"""FixMissingSeoJob — auto-populate missing SEO metadata for published posts.

Runs every 24 hours by default. Selects published posts whose
``seo_title`` or ``seo_description`` is missing, generates fallback
SEO assets from the title/content, and updates the post row.

Config (``plugin.job.fix_missing_seo``):
- ``config.limit`` (default 10) — max posts to examine per run
- ``config.file_gitea_issue`` (default true)
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.seo_content_generator import ContentMetadataGenerator
from services.site_config import SiteConfig
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


class FixMissingSeoJob:
    name = "fix_missing_seo"
    description = "Auto-fill missing SEO title and description for published posts"
    schedule = "every 24 hours"
    idempotent = True  # Re-running is safe: existing SEO metadata is preserved

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        limit = int(config.get("limit", 10))
        file_issue = bool(config.get("file_gitea_issue", True))
        excluded_templates: list[str] = list(
            config.get("excluded_templates", ["dev_diary"])
        )

        site_config = config.get("_site_config") or SiteConfig()
        metadata_generator = ContentMetadataGenerator(site_config=site_config)

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT p.id, p.title, p.content,
                           p.seo_title, p.seo_description, p.seo_keywords
                    FROM posts p
                    LEFT JOIN pipeline_tasks pt
                           ON pt.task_id::text = p.metadata->>'pipeline_task_id'
                    WHERE p.status = 'published'
                      AND COALESCE(pt.template_slug, '') != ALL($2::text[])
                      AND (p.seo_title IS NULL OR p.seo_title = ''
                           OR p.seo_description IS NULL OR p.seo_description = '')
                    LIMIT $1
                    """,
                    limit,
                    excluded_templates,
                )
        except Exception as e:
            logger.exception("FixMissingSeoJob: query failed: %s", e)
            return JobResult(ok=False, detail=f"query failed: {e}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail="all published posts already have SEO metadata",
                changes_made=0,
            )

        fixed = 0
        for row in rows:
            title = row["title"] or ""
            content = row["content"] or ""
            seo_title = row["seo_title"] or ""
            seo_description = row["seo_description"] or ""
            seo_keywords = row["seo_keywords"] or ""

            if seo_title and seo_description and seo_keywords:
                continue

            assets = metadata_generator.generate_seo_assets(
                title=title,
                content=content,
                topic="",
            )
            new_title = seo_title or assets["seo_title"]
            new_description = seo_description or assets["meta_description"]
            new_keywords = seo_keywords or ", ".join(assets["meta_keywords"])

            try:
                await conn.execute(
                    "UPDATE posts SET seo_title = $1, seo_description = $2, seo_keywords = $3, updated_at = NOW() WHERE id = $4",
                    new_title,
                    new_description,
                    new_keywords,
                    row["id"],
                )
                fixed += 1
            except Exception as e:
                logger.warning(
                    "FixMissingSeoJob: update failed for %s: %s",
                    row.get("id"), e,
                )

        if fixed and file_issue:
            emit_finding(
                source="fix_missing_seo",
                kind="missing_seo_autofixed",
                title=f"seo: auto-filled SEO metadata for {fixed} post(s)",
                body=(
                    "Auto-populated missing seo_title, seo_description, "
                    "and seo_keywords for published posts. Review generated "
                    "metadata and adjust if needed."
                ),
                dedup_key="missing_seo_autofix",
                extra={"posts_fixed": fixed},
            )

        detail = f"updated SEO metadata for {fixed} of {len(rows)} post(s)"
        logger.info("FixMissingSeoJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=fixed,
            metrics={
                "posts_scanned": len(rows),
                "posts_fixed": fixed,
            },
        )
