"""ReapStaleTopicBatchesJob — self-heal watchdog for wedged topic_batches.

## The bug class this closes

A ``topic_batch`` row stuck in ``status='open'`` is the recurring root
cause of "content went dark for ~2 days" (see the topic-batch-wedge
incidents 2026-05-12 / 2026-05-28 / 2026-06-11). Only **one** open batch
per niche is allowed (``uq_one_open_batch_per_niche``, a partial unique
index ``WHERE status='open'``), so a single stuck batch short-circuits
every future ``run_niche_topic_sweep`` for that niche
(``TopicBatchService._open_batch_exists``) — the niche silently stops
producing ``canonical_blog`` tasks while the worker itself stays healthy.
Each prior incident was a *different* upstream cause; the shared symptom
is always a wedged open batch. This job turns that whole class from
"silent multi-day stall" into "auto-recovering, observable alert".

## What it does — alert always, reap conservatively

Runs hourly. Finds ``status='open'`` batches on **active** niches that
have been open longer than ``topic_batch_stuck_hours`` (default 24), then:

1. **Alert (always, every stuck batch).** Emits a ``topic_batch_stuck``
   finding. Severity follows the self-heal ladder
   (``feedback_self_heal_not_suppress``):
     - **not reaped → ``warn``** so it passes ``findings_alert_router``'s
       severity floor and routes to the seeded
       ``findings.topic_batch_stuck.delivery='discord'`` ops channel —
       i.e. it pages. This is the "silent stall → ping" conversion.
     - **reaped → ``info``** (dashboard-only, no page): the niche already
       self-healed, so paging would be noise.

2. **Reap (gated, conservative).** Flips the batch to ``status='expired'``
   via :meth:`TopicBatchService.reject_batch` (the existing path that
   frees the one-open-batch-per-niche slot), but **only** when BOTH:
     - ``topic_batch_reaper_enabled=true`` (master switch, default
       **false**), AND
     - the batch is **already past its ``expires_at``** (a dead batch
       outside its review window).

   Reaping *only expired* batches is what makes auto-recovery safe:
     - A batch a manual-review operator is still deciding on, or one
       ``topic_auto_resolve`` is deferring because the approval queue is
       full, is **non-expired** → never reaped (only alerted). We never
       discard a live batch that could still resolve.
     - A long-dead expired batch from a niche that moved to its own
       content path (``dev_diary`` posts come from ``run_dev_diary_post``'s
       daily cron, not the sweep→resolve path) WOULD be reaped — and
       clearing it lets that niche's sweep reactivate. That is exactly
       why reaping is behind a default-off master switch: flipping it on
       is an explicit operator decision. Set the niche ``active=false``
       first if you don't want it swept (the reaper is scoped to active
       niches, so an inactive niche's batches are ignored — a structured
       seam, never a slug check).

## Why this is the safety net and not the fix

The individual wedge causes get fixed at the source (e.g. the 2026-06-11
two-table eligibility fix in ``topic_auto_resolve``). This job is
defense-in-depth: when a *new, unforeseen* cause wedges a batch, the
operator is alerted within ``topic_batch_stuck_hours`` instead of
discovering it days later, and once that batch ages past its review
window the reaper clears it so the niche self-recovers.

## Config

- ``topic_batch_reaper_enabled`` (default ``false``) — master switch for
  the destructive auto-expire action. Alerting runs regardless.
- ``topic_batch_stuck_hours`` (default ``24``) — age threshold for "stuck".
- ``findings.topic_batch_stuck.delivery`` (default ``discord``) — where the
  alert routes once it clears the severity floor.

## Schedule

``every 60 minutes``. Cheap no-op when nothing is stuck (one indexed query).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


_DEFAULT_STUCK_HOURS = 24


class ReapStaleTopicBatchesJob:
    name = "reap_stale_topic_batches"
    description = (
        "Alert on (and, when enabled, auto-expire) topic_batches stuck "
        "open past a threshold so a wedged batch can't silently take a "
        "niche content-dark"
    )
    # Hourly watchdog. Cheap no-op when nothing is stuck.
    schedule = "every 60 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        stuck_hours = await _read_int_setting(
            pool, "topic_batch_stuck_hours", _DEFAULT_STUCK_HOURS,
        )
        reaper_enabled = await _read_bool_setting(
            pool, "topic_batch_reaper_enabled", False,
        )

        # Active-niche open batches older than the stuck threshold. Candidate
        # counts span BOTH tables (external ``topic_candidates`` + internal
        # ``internal_topic_candidates``) — same two-table reality the
        # 2026-06-11 auto-resolve fix had to account for; here it just colours
        # the finding body (0 = empty-batch wedge vs N = has-candidates wedge).
        # ``is_expired`` / ``age_hours`` are computed in SQL to avoid Python
        # tz-comparison drift.
        rows = await pool.fetch(
            """
            SELECT
                tb.id AS batch_id,
                tb.niche_id,
                n.slug AS niche_slug,
                tb.expires_at,
                (tb.expires_at <= NOW()) AS is_expired,
                EXTRACT(EPOCH FROM (NOW() - tb.created_at)) / 3600.0 AS age_hours,
                (
                    (SELECT COUNT(*) FROM topic_candidates tc
                      WHERE tc.batch_id = tb.id)
                  + (SELECT COUNT(*) FROM internal_topic_candidates ic
                      WHERE ic.batch_id = tb.id)
                ) AS candidate_count
            FROM topic_batches tb
            JOIN niches n ON n.id = tb.niche_id
            WHERE tb.status = 'open'
              AND n.active = TRUE
              AND tb.created_at < NOW() - ($1::int * INTERVAL '1 hour')
            ORDER BY tb.created_at ASC
            """,
            stuck_hours,
        )

        if not rows:
            return JobResult(
                ok=True,
                detail=f"no open batches stuck > {stuck_hours}h on active niches",
                changes_made=0,
            )

        # Only build the service (which mints a SiteConfig dependency) when we
        # might actually reap — the alert-only default path stays independent
        # of the run-bound site_config being present.
        svc = None
        if reaper_enabled:
            from services.topic_batch_service import TopicBatchService
            svc = TopicBatchService(pool, site_config=config["_site_config"])

        reaped = 0
        for row in rows:
            batch_id = row["batch_id"]
            is_expired = bool(row["is_expired"])
            age_hours = float(row["age_hours"])

            did_reap = False
            if reaper_enabled and is_expired and svc is not None:
                try:
                    await svc.reject_batch(
                        batch_id=UUID(str(batch_id)),
                        reason=(
                            f"auto-expired by stale-batch reaper "
                            f"(open {age_hours:.0f}h, past review window)"
                        ),
                    )
                    did_reap = True
                    reaped += 1
                    logger.info(
                        "[reap_stale_topic_batches] auto-expired stuck batch "
                        "%s niche=%s (open %.0fh)",
                        batch_id, row["niche_slug"], age_hours,
                    )
                except Exception as exc:
                    # Reap failed → fall through and PAGE (warn) instead of
                    # silently leaving a self-heal that didn't happen.
                    logger.error(
                        "[reap_stale_topic_batches] reject_batch failed for "
                        "batch=%s niche=%s: %s",
                        batch_id, row["niche_slug"], exc, exc_info=True,
                    )

            emit_finding(
                source="reap_stale_topic_batches",
                kind="topic_batch_stuck",
                # Self-heal ladder: a batch we cleaned up is quiet (info,
                # dashboard-only — dropped by the router's severity floor); a
                # batch still wedging the niche pages (warn → Discord).
                severity="info" if did_reap else "warn",
                title=(
                    f"topic batch stuck {age_hours:.0f}h on niche "
                    f"'{row['niche_slug']}'"
                ),
                body=_finding_body(
                    row, age_hours, is_expired, did_reap, reaper_enabled,
                ),
                dedup_key=f"topic_batch_stuck:{batch_id}",
                extra={
                    "batch_id": str(batch_id),
                    "niche_id": str(row["niche_id"]),
                    "niche_slug": row["niche_slug"],
                    "age_hours": round(age_hours, 1),
                    "candidate_count": int(row["candidate_count"]),
                    "is_expired": is_expired,
                    "reaped": did_reap,
                    "expires_at": row["expires_at"].isoformat(),
                },
            )

        return JobResult(
            ok=True,
            detail=(
                f"stuck={len(rows)} reaped={reaped} "
                f"(reaper_enabled={reaper_enabled}, stuck_hours={stuck_hours})"
            ),
            changes_made=reaped,
        )


def _finding_body(
    row: Any,
    age_hours: float,
    is_expired: bool,
    did_reap: bool,
    reaper_enabled: bool,
) -> str:
    """Markdown-friendly finding body that states the facts and the action
    taken (or why none was)."""
    window = "EXPIRED — past its review window" if is_expired else (
        "still within its review window"
    )
    lines = [
        f"Batch `{row['batch_id']}` for niche **{row['niche_slug']}** has "
        f"been `open` for {age_hours:.1f}h "
        f"({int(row['candidate_count'])} candidate(s); "
        f"expires_at {row['expires_at']:%Y-%m-%d %H:%M UTC}, {window}).",
        "",
        "A stuck open batch blocks every future discovery sweep for this "
        "niche (`uq_one_open_batch_per_niche`), so the niche goes "
        "content-dark until it is cleared.",
        "",
    ]
    if did_reap:
        lines.append(
            "✅ Auto-expired by the stale-batch reaper — the niche is "
            "unblocked and its next sweep will open a fresh batch."
        )
    elif reaper_enabled and not is_expired:
        lines.append(
            "⏳ Reaper is enabled but this batch is still within its review "
            "window — leaving it for auto-resolve / manual review. It will "
            "be auto-expired after `expires_at` if still open."
        )
    else:
        lines.append(
            "Auto-recovery is OFF. Set `topic_batch_reaper_enabled=true` to "
            "let the reaper auto-expire dead (expired) batches, or clear this "
            "one manually with `poindexter topics reject-batch "
            f"{row['batch_id']}` (or resolve it)."
        )
    return "\n".join(lines)


# ---- app_settings helpers -----------------------------------------------
#
# Local copies so this module doesn't pull in the full settings_service
# stack just to read two values. Matches the helper pattern used by
# topic_auto_resolve.py / morning_brief.py.

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
