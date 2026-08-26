"""CrosspostToDevtoJob — syndicate published posts to Dev.to.

Replaces ``IdleWorker._crosspost_to_devto``. Runs every 4 hours by
default. Finds published posts without a ``metadata->>'devto_url'``,
cross-posts each via ``DevToCrossPostService``, and stores the
returned Dev.to URL back on the post's metadata so we don't re-post.

## Dedup (#397, #404)

The candidate query filters on THREE metadata flags so the cron can't
loop on a post forever:

- ``metadata->>'devto_url'`` set on 2xx success (existing behavior).
- ``metadata->>'devto_status' = 'gave_up'`` set after a permanent
  non-canonical Dev.to rejection (e.g. 415 unsupported media, 401
  bad key, generic 422 validation error). See
  ``services/devto_service.py`` for where this is written.
- ``metadata->>'devto_status' = 'already_exists'`` set after Dev.to
  specifically reports the canonical URL is taken — the article IS
  on Dev.to, just not from this run, so further attempts are a
  guaranteed 422 (#404).

Transient failures (5xx, 429, network) intentionally leave metadata
untouched so the next tick retries.

## Config (``plugin.job.crosspost_to_devto``)

- ``config.batch_size`` (default 3) — posts to cross-post per run
- ``config.file_gitea_issue`` (default false) — whether to file a
  dedup'd issue if any post errored. Default false because Dev.to
  errors are usually transient (rate limits, 5xx) and the job
  retries on its own cadence.

## Selective-syndication gate (app_settings, spec 2026-07-12 → content-type 2026-07-13)

Only posts whose CONTENT-TYPE is on an allowlist AND whose quality score
clears a floor are crossposted (not every published post):

- ``devto_syndicate_content_types`` (CSV, default ``""``) — content-type
  allowlist (labels from ``post_content_types``, populated by
  ClassifyContentTypesJob). **Empty = syndicate nothing** (opt-in). The job
  short-circuits to a no-op before touching the DB when this is empty.
- ``devto_syndicate_min_quality`` (default ``80``, 0–100) — the newest
  ``pipeline_versions.quality_score`` must be ≥ this. Unparseable value fails
  loud (``emit_finding``) and no-ops instead of syndicating everything.

Posts with no ``post_content_types`` row fail the candidate query's EXISTS
(fail-closed — we never syndicate a post the classifier hasn't labeled).
Publish posture (``devto_publish_immediately``) is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


def _parse_content_types(raw: str) -> list[str]:
    """Parse the ``devto_syndicate_content_types`` CSV into a normalized list.

    Strips, lowercases, de-dupes (order-preserving). Empty / whitespace-only
    input yields ``[]`` — the caller treats that as "syndicate nothing".
    """
    seen: dict[str, None] = {}
    for part in (raw or "").split(","):
        label = part.strip().lower()
        if label:
            seen.setdefault(label, None)
    return list(seen)


def _parse_min_quality(raw: str) -> float:
    """Parse ``devto_syndicate_min_quality`` (0–100). Raises ValueError on junk
    so the caller can fail loud instead of silently syndicating everything."""
    return float(raw)


# Selective-syndication candidate query (spec 2026-07-12, repointed to
# content-type 2026-07-13). Filters on the operator's content-type allowlist
# ($2) via a ``post_content_types`` EXISTS, and on the quality floor ($3) via
# the newest ``pipeline_versions.quality_score`` reached through the
# ``posts.metadata->>'pipeline_task_id' = pipeline_versions.task_id`` seam.
# A post with no content-type row fails the EXISTS (fail-closed — we never
# syndicate a post the classifier hasn't labeled). The terminal devto_status
# dedup (#397/#404) is preserved verbatim.
_CANDIDATE_SQL = """
    SELECT p.id, p.title, p.slug
    FROM posts p
    LEFT JOIN LATERAL (
        SELECT pv.quality_score
        FROM pipeline_versions pv
        WHERE pv.task_id = p.metadata->>'pipeline_task_id'
        ORDER BY pv.version DESC
        LIMIT 1
    ) pv ON TRUE
    WHERE p.status = 'published'
      AND (p.metadata IS NULL
           OR p.metadata->>'devto_url' IS NULL
           OR p.metadata->>'devto_url' = '')
      AND COALESCE(p.metadata->>'devto_status', '') NOT IN ('gave_up', 'already_exists')
      AND EXISTS (
          SELECT 1 FROM post_content_types pct
          WHERE pct.post_id = p.id AND pct.content_type = ANY($2::text[])
      )
      AND COALESCE(pv.quality_score, 0) >= $3
    ORDER BY p.published_at DESC
    LIMIT $1
"""

# Gate-throughput observability (spec 2026-07-12). The gate filters inside the
# candidate query, so the job never sees the rejected rows — these two cheap
# counts make the throughput visible. ``universe`` = published, un-syndicated
# posts (pre content-type/quality); ``eligible`` = those that pass the full
# gate (no LIMIT). ``posts_skipped_by_gate`` = universe − eligible.
_UNIVERSE_COUNT_SQL = """
    SELECT count(*) FROM posts p
    WHERE p.status='published'
      AND (p.metadata IS NULL OR p.metadata->>'devto_url' IS NULL OR p.metadata->>'devto_url'='')
      AND COALESCE(p.metadata->>'devto_status','') NOT IN ('gave_up','already_exists')
"""

_ELIGIBLE_COUNT_SQL = """
    SELECT count(*) FROM posts p
    LEFT JOIN LATERAL (
        SELECT pv.quality_score FROM pipeline_versions pv
        WHERE pv.task_id = p.metadata->>'pipeline_task_id' ORDER BY pv.version DESC LIMIT 1
    ) pv ON TRUE
    WHERE p.status='published'
      AND (p.metadata IS NULL OR p.metadata->>'devto_url' IS NULL OR p.metadata->>'devto_url'='')
      AND COALESCE(p.metadata->>'devto_status','') NOT IN ('gave_up','already_exists')
      AND EXISTS (
          SELECT 1 FROM post_content_types pct
          WHERE pct.post_id = p.id AND pct.content_type = ANY($1::text[])
      )
      AND COALESCE(pv.quality_score,0) >= $2
"""


class CrosspostToDevtoJob:
    name = "crosspost_to_devto"
    description = "Syndicate published posts to Dev.to (dedup via metadata.devto_url)"
    schedule = "every 4 hours"
    idempotent = True  # dedup on metadata.devto_url — safe to retry

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.devto_service import DevToCrossPostService

        batch_size = int(config.get("batch_size", 3))
        file_issue = bool(config.get("file_gitea_issue", False))

        # glad-labs-stack#330: thread site_config through the DI seam.
        # Job dispatcher seeds ``_site_config`` on every config dict.
        svc = DevToCrossPostService(pool, site_config=config.get("_site_config"))

        # Early-out before we touch the DB: if there's no API key there's
        # nothing we can do.
        try:
            api_key = await svc._get_api_key()
        except Exception as e:
            logger.exception("CrosspostToDevtoJob: api key lookup failed: %s", describe_exception(e))
            return JobResult(
                ok=False, detail=f"api key lookup failed: {describe_exception(e)}", changes_made=0,
            )

        if not api_key:
            return JobResult(
                ok=True,
                detail="devto_api_key not configured — skipping",
                changes_made=0,
            )

        # Selective-syndication gate (spec 2026-07-12, content-type 2026-07-13).
        # Read from the DI'd SiteConfig. Empty allowlist = syndicate nothing
        # (opt-in default) — short-circuit before the DB so logs read clearly.
        # An unparseable quality floor fails loud rather than silently
        # syndicating everything.
        sc = config.get("_site_config")
        content_types = _parse_content_types(
            sc.get("devto_syndicate_content_types", "") if sc else ""
        )
        if not content_types:
            return JobResult(
                ok=True,
                detail="no syndication content-types configured — skipping",
                changes_made=0,
            )
        try:
            min_quality = _parse_min_quality(
                sc.get("devto_syndicate_min_quality", "80") if sc else "80"
            )
        except ValueError as e:
            emit_finding(
                source="crosspost_to_devto",
                kind="devto_min_quality_unparseable",
                severity="warning",
                title="devto_syndicate_min_quality is not a number — syndication paused",
                body=(
                    f"Value {(sc.get('devto_syndicate_min_quality') if sc else None)!r} "
                    f"is unparseable ({describe_exception(e)}); no posts syndicated this tick. Fix the "
                    "app_settings value to resume Dev.to syndication."
                ),
                dedup_key="devto_min_quality_unparseable",
            )
            return JobResult(
                ok=True, detail=f"min_quality unparseable: {describe_exception(e)}", changes_made=0,
            )

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    _CANDIDATE_SQL, batch_size, content_types, min_quality
                )
                # Gate-throughput counts (cheap; runs every 4h). Reported as
                # metrics + a log line so skipped-by-gate is visible.
                gate_universe = int(await conn.fetchval(_UNIVERSE_COUNT_SQL) or 0)
                gate_eligible = int(
                    await conn.fetchval(
                        _ELIGIBLE_COUNT_SQL, content_types, min_quality
                    ) or 0
                )
        except Exception as e:
            logger.exception("CrosspostToDevtoJob: candidate fetch failed: %s", describe_exception(e))
            return JobResult(ok=False, detail=f"fetch failed: {describe_exception(e)}", changes_made=0)

        skipped = max(0, gate_universe - gate_eligible)
        gate_metrics = {
            "gate_universe": gate_universe,
            "gate_eligible": gate_eligible,
            "posts_skipped_by_gate": skipped,
        }
        logger.info(
            "CrosspostToDevtoJob: %d eligible, %d skipped by content-type/quality gate "
            "(universe %d)",
            gate_eligible, skipped, gate_universe,
        )

        if not rows:
            return JobResult(
                ok=True,
                detail="all published posts already on Dev.to",
                changes_made=0,
                metrics=gate_metrics,
            )

        crossposted = 0
        errors: list[str] = []
        for row in rows:
            post_id = str(row["id"])
            try:
                devto_url = await svc.cross_post_by_post_id(post_id)
                if devto_url:
                    crossposted += 1
                    logger.info(
                        "CrosspostToDevtoJob: %s → %s", row["slug"], devto_url,
                    )
                else:
                    errors.append(f"{row['slug']}: no URL returned")
            except Exception as e:
                errors.append(f"{row['slug']}: {e}")

        if errors and file_issue:
            body = (
                "## Dev.to cross-post errors\n\n"
                + "\n".join(f"- {e}" for e in errors[:10])
            )
            emit_finding(
                source="crosspost_to_devto",
                kind="crosspost_failure",
                severity="warn",
                title=f"devto: {len(errors)} cross-post errors of {len(rows)} attempts",
                body=body,
                dedup_key="devto_crosspost",
                extra={"error_count": len(errors), "attempt_count": len(rows)},
            )

        detail = (
            f"attempted {len(rows)}, crossposted {crossposted}, "
            f"{len(errors)} errors"
        )
        logger.info("CrosspostToDevtoJob: %s", detail)
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=crossposted,
            metrics={
                **gate_metrics,
                "posts_attempted": len(rows),
                "posts_crossposted": crossposted,
                "errors": len(errors),
            },
        )
