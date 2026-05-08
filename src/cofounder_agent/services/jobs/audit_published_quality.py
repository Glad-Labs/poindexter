"""AuditPublishedQualityJob — periodic quality re-check of published posts.

Replaces ``IdleWorker._audit_published_quality``. Runs every 6 hours by
default. Picks the oldest-published N posts that haven't been audited
in the last ``cooldown_days`` days, checks each for two cheap quality
signals (word count, presence of headings), records the audit to
``audit_log`` so they're skipped on the next cycle, and files a
dedup'd Gitea issue if any findings surface.

The heuristic is intentionally lightweight — the full QA pipeline is
expensive, so this acts as a first-pass filter that flags posts
worth deeper review.

Config (``plugin.job.audit_published_quality``):
- ``config.batch_size`` (default 5) — posts to audit per run
- ``config.cooldown_days`` (default 7) — don't re-audit a post within
  this window
- ``config.min_words`` (default 500) — below this counts as a finding
- ``config.file_gitea_issue`` (default true)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


class AuditPublishedQualityJob:
    name = "audit_published_quality"
    description = "Re-score a batch of published posts against cheap quality signals (word count, headings)"
    schedule = "every 6 hours"
    idempotent = True  # Writes are audit_log inserts (append-only) + Gitea issues (dedup'd)

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        batch_size = int(config.get("batch_size", 5))
        cooldown_days = int(config.get("cooldown_days", 7))
        min_words = int(config.get("min_words", 500))
        file_issue = bool(config.get("file_gitea_issue", True))

        # posts.id is UUID, audit_log.task_id is VARCHAR — cast on compare.
        query = """
            SELECT p.id, p.title, p.slug, LEFT(p.content, 3000) as content_preview
            FROM posts p
            WHERE p.status = 'published'
              AND p.id::text NOT IN (
                  SELECT DISTINCT task_id FROM audit_log
                  WHERE event_type = 'idle_quality_audit'
                    AND task_id IS NOT NULL
                    AND timestamp > NOW() - INTERVAL '1 day' * $1
              )
            ORDER BY p.published_at ASC
            LIMIT $2
        """

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, cooldown_days, batch_size)
        except Exception as e:
            logger.exception("AuditPublishedQualityJob: fetch failed: %s", e)
            return JobResult(ok=False, detail=f"fetch failed: {e}", changes_made=0)

        if not rows:
            return JobResult(
                ok=True,
                detail="all published posts recently audited",
                changes_made=0,
            )

        issues: list[str] = []
        for row in rows:
            content = row["content_preview"] or ""
            word_count = len(content.split())
            has_headings = "##" in content or "<h2" in content
            title = (row["title"] or "")[:40]

            if word_count < min_words:
                issues.append(f"{title}: only {word_count} words")
            if not has_headings:
                issues.append(f"{title}: no headings found")

            # Mark post as audited so we skip it on the next cycle. Best-
            # effort — if audit_log is unavailable we still return the
            # findings, we just might re-audit sooner than cooldown_days.
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO audit_log (event_type, source, task_id, details, severity) "
                        "VALUES ($1, $2, $3, $4::jsonb, $5)",
                        "idle_quality_audit",
                        "audit_published_quality_job",
                        str(row["id"]),
                        json.dumps({"title": row["title"], "word_count": word_count}),
                        "info",
                    )
            except Exception as e:
                logger.debug(
                    "AuditPublishedQualityJob: audit_log insert failed for %s: %s",
                    row.get("id"), e,
                )

        if issues and file_issue:
            body = "## Quality Audit Findings\n\n" + "\n".join(f"- {i}" for i in issues)
            emit_finding(
                source="audit_published_quality",
                kind="quality_regression",
                severity="warn",
                title=f"quality: {len(issues)} issues in {len(rows)} audited posts",
                body=body,
                dedup_key="quality_audit",
            )

        detail = f"audited {len(rows)} post(s), found {len(issues)} issue(s)"
        logger.info("AuditPublishedQualityJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=len(issues),
            metrics={
                "posts_audited": len(rows),
                "issues_found": len(issues),
            },
        )
