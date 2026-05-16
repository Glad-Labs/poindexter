"""TopicAutoResolveJob — auto-rank + auto-resolve open topic_batches.

Closes the gap between topic discovery (which runs automatically via the
niche sweep) and pipeline kickoff (which previously required the operator
to manually run ``poindexter topics rank-batch`` + ``resolve-batch``).

When ``topic_discovery_auto_enabled=false`` (the niche-aware operator
flow), open topic_batches accumulate candidates but nothing promotes them
into ``content_tasks``. As of 2026-05-12 the pipeline went dark for 2+
days because the operator (Matt) didn't have time to run the CLI flow.

This job runs on a schedule, scans for OPEN batches with candidates, and:
  1. Copies ``rank_in_batch`` → ``operator_rank`` (the candidates were
     already LLM-ranked at discovery time; the score-based order is
     what an operator would replicate manually anyway).
  2. Calls :meth:`TopicBatchService.resolve_batch` which promotes the
     rank-1 candidate into the content pipeline.
  3. Writes an ``audit_log`` row tagged ``topic_auto_resolved`` so the
     operator can see what ran without them.

## Master switch + safety rails

- **``topic_auto_resolve_enabled``** (default ``false``) — opt-in.
  Operators who actually want to review candidates manually before they
  go live keep it off; operators who can't run the CLI flow daily
  (= Matt) flip it on.
- **Queue full**: respects ``pipeline_throttle.is_queue_full``. Won't
  stuff more tasks behind a full HITL approval gate.
- **Per-cycle cap**: ``topic_auto_resolve_max_per_cycle`` (default 1)
  limits how many batches resolve per run, so a backlog doesn't all
  fire at once.
- **Niche cooldown**: ``topic_auto_resolve_niche_cooldown_hours``
  (default 12) — at most one resolve per niche per window.

## Schedule

Default cron ``0 */2 * * *`` (every 2 hours). The job is cheap when
there's nothing to resolve (one indexed query); the work only fires
when an open batch with candidates is waiting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from plugins.job import JobResult

logger = logging.getLogger(__name__)


_DEFAULT_MAX_PER_CYCLE = 1
_DEFAULT_NICHE_COOLDOWN_HOURS = 12


class TopicAutoResolveJob:
    name = "topic_auto_resolve"
    description = (
        "Auto-rank + auto-resolve open topic_batches → content_tasks "
        "when the operator-driven CLI flow isn't being run manually"
    )
    # Every 2 hours. Cheap no-op when there's nothing to do.
    schedule = "0 */2 * * *"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # ---- Master switch ----
        enabled = await _read_bool_setting(
            pool, "topic_auto_resolve_enabled", False,
        )
        if not enabled:
            return JobResult(
                ok=True,
                detail="disabled (set topic_auto_resolve_enabled=true to opt in)",
                changes_made=0,
            )

        # ---- Queue throttle ----
        # Don't stuff more tasks behind a full approval queue. Reuses the
        # same gate the legacy idle_worker topic-discovery path uses.
        try:
            from services.pipeline_throttle import is_queue_full
            full, queue_size, queue_limit = await is_queue_full(pool)
            if full:
                return JobResult(
                    ok=True,
                    detail=(
                        f"queue full ({queue_size}/{queue_limit}); "
                        f"deferring auto-resolve until operator clears queue"
                    ),
                    changes_made=0,
                )
        except Exception as exc:
            # Non-fatal: if the throttle check fails we still run.
            # The downstream resolve_batch will fail loud if the task
            # row insertion has its own conflict.
            logger.warning(
                "[topic_auto_resolve] queue throttle check failed: %s — proceeding anyway",
                exc,
            )

        max_per_cycle = int(
            config.get("max_per_cycle")
            or await _read_int_setting(
                pool, "topic_auto_resolve_max_per_cycle", _DEFAULT_MAX_PER_CYCLE,
            )
            or _DEFAULT_MAX_PER_CYCLE
        )
        niche_cooldown_hours = int(
            config.get("niche_cooldown_hours")
            or await _read_int_setting(
                pool,
                "topic_auto_resolve_niche_cooldown_hours",
                _DEFAULT_NICHE_COOLDOWN_HOURS,
            )
            or _DEFAULT_NICHE_COOLDOWN_HOURS
        )

        # ---- Find candidates ----
        # Pull open batches with at least one candidate, plus the time of
        # the most-recent auto_resolve_topic_auto_resolved audit_log row per
        # niche so we can enforce the per-niche cooldown. ``picked_candidate_id``
        # is NULL on open batches by definition.
        rows = await pool.fetch(
            """
            SELECT
                tb.id AS batch_id,
                tb.niche_id,
                n.slug AS niche_slug,
                (SELECT COUNT(*) FROM topic_candidates tc WHERE tc.batch_id = tb.id) AS candidate_count
            FROM topic_batches tb
            JOIN niches n ON n.id = tb.niche_id
            WHERE tb.status = 'open'
              AND n.active = TRUE
              AND EXISTS (SELECT 1 FROM topic_candidates tc WHERE tc.batch_id = tb.id)
            ORDER BY tb.created_at ASC
            LIMIT $1
            """,
            max_per_cycle * 4,  # over-fetch; cooldown filter may discard some
        )

        if not rows:
            return JobResult(
                ok=True,
                detail="no open batches with candidates",
                changes_made=0,
            )

        # ---- Filter by per-niche cooldown ----
        cooldown_cutoff = datetime.now(timezone.utc) - timedelta(
            hours=niche_cooldown_hours,
        )
        eligible: list[dict[str, Any]] = []
        for row in rows:
            recent = await pool.fetchval(
                """
                SELECT MAX(timestamp) FROM audit_log
                WHERE event_type = 'topic_auto_resolved'
                  AND details::jsonb ->> 'niche_id' = $1
                """,
                str(row["niche_id"]),
            )
            if recent is not None and recent > cooldown_cutoff:
                logger.info(
                    "[topic_auto_resolve] niche %s skipped — last auto-resolve at %s (within %dh cooldown)",
                    row["niche_slug"], recent.isoformat(), niche_cooldown_hours,
                )
                continue
            eligible.append(dict(row))
            if len(eligible) >= max_per_cycle:
                break

        if not eligible:
            return JobResult(
                ok=True,
                detail=f"all {len(rows)} open batch(es) within per-niche cooldown",
                changes_made=0,
            )

        # ---- Resolve each eligible batch ----
        # Import here to avoid a module-load cycle (topic_batch_service
        # pulls in plenty of pipeline machinery; jobs/__init__ stays
        # light).
        from services.topic_batch_service import TopicBatchService

        svc = TopicBatchService(pool)
        resolved_count = 0
        errors: list[str] = []

        for batch in eligible:
            batch_id = batch["batch_id"]
            try:
                # 1. Apply operator_rank = rank_in_batch for all candidates
                #    in this batch. The candidates were already LLM-ranked
                #    at discovery time; we're just transcribing that
                #    score-based order into the operator-rank column that
                #    resolve_batch reads.
                #
                # The unified rank is split across TWO tables:
                #   - ``topic_candidates`` (external — Hacker News, dev.to,
                #     web search taps)
                #   - ``internal_topic_candidates`` (internal — embedding-
                #     corpus-derived from brain_knowledge / posts)
                # ``rank_in_batch`` is computed across both pools combined,
                # so the rank-1 winner can live in either table.
                # ``TopicBatchService.resolve_batch`` reads from the
                # unified view — we just need to make sure operator_rank
                # is set wherever the candidate lives.
                await pool.execute(
                    """
                    UPDATE topic_candidates
                    SET operator_rank = rank_in_batch
                    WHERE batch_id = $1
                      AND operator_rank IS NULL
                      AND rank_in_batch IS NOT NULL
                    """,
                    batch_id,
                )
                await pool.execute(
                    """
                    UPDATE internal_topic_candidates
                    SET operator_rank = rank_in_batch
                    WHERE batch_id = $1
                      AND operator_rank IS NULL
                      AND rank_in_batch IS NOT NULL
                    """,
                    batch_id,
                )
                # 2. Resolve — TopicBatchService picks operator_rank=1
                #    and promotes to a canonical_blog content_task.
                await svc.resolve_batch(batch_id=UUID(str(batch_id)))
                resolved_count += 1
                # 3. Audit log — searchable trail of every auto-resolution.
                await pool.execute(
                    """
                    INSERT INTO audit_log (event_type, source, details)
                    VALUES (
                        'topic_auto_resolved',
                        'topic_auto_resolve_job',
                        $1::jsonb
                    )
                    """,
                    _audit_details(batch),
                )
                logger.info(
                    "[topic_auto_resolve] resolved batch=%s niche=%s (candidate_count=%d)",
                    batch_id, batch["niche_slug"], batch["candidate_count"],
                )
            except Exception as exc:
                errors.append(
                    f"batch={batch_id} niche={batch['niche_slug']}: "
                    f"{type(exc).__name__}: {exc}"
                )
                logger.error(
                    "[topic_auto_resolve] resolve failed for batch=%s: %s",
                    batch_id, exc, exc_info=True,
                )

        if errors and resolved_count == 0:
            return JobResult(
                ok=False,
                detail="all resolves failed: " + "; ".join(errors),
                changes_made=0,
            )
        return JobResult(
            ok=True,
            detail=(
                f"resolved {resolved_count}/{len(eligible)} batch(es)"
                + (f"; {len(errors)} error(s)" if errors else "")
            ),
            changes_made=resolved_count,
        )


def _audit_details(batch: dict[str, Any]) -> str:
    """Build a JSON details payload for the audit_log row."""
    import json
    return json.dumps({
        "batch_id": str(batch["batch_id"]),
        "niche_id": str(batch["niche_id"]),
        "niche_slug": batch["niche_slug"],
        "candidate_count": batch["candidate_count"],
        "triggered_by": "topic_auto_resolve_job",
    })


# ---- app_settings helpers -----------------------------------------------
#
# Local copies so this module doesn't pull in the full admin_db /
# settings_service stack just to read a few bools. Matches the helper
# pattern used by morning_brief.py and other job modules.

async def _read_setting(pool, key: str, default: str) -> str:
    """Fetch a plaintext app_settings value, or default if missing/empty."""
    row = await pool.fetchrow(
        "SELECT value FROM app_settings WHERE key = $1", key,
    )
    if not row or not row["value"]:
        return default
    return row["value"]


async def _read_bool_setting(pool, key: str, default: bool) -> bool:
    raw = await _read_setting(pool, key, "true" if default else "false")
    return raw.strip().lower() in ("true", "1", "yes", "on")


async def _read_int_setting(pool, key: str, default: int) -> int:
    raw = await _read_setting(pool, key, str(default))
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default
