"""RunDevDiaryPostJob — daily "what we shipped today" post generator.

Fires once per day at 9am ET (cron ``0 13 * * *`` UTC, which lands at
9am EDT during US daylight savings — close enough for an editorial
cadence; the job is idempotent per UTC date so a slight clock drift
doesn't fire it twice).

Flow
----

1. Pull the daily context bundle via
   :class:`services.topic_sources.dev_diary_source.DevDiarySource`.
2. If the bundle ``is_empty()`` (no PRs, no notable commits, no
   high-confidence decisions), skip + notify the operator with a
   "quiet day" message. Don't waste a draft slot on filler content.
3. Otherwise, create a content_tasks row tagged
   ``niche=dev_diary``, ``request_type=dev_diary``, ``--gates draft,final``.
   The standard pipeline picks the row up on its next sweep, runs the
   draft stage, and lands the post at the ``draft`` gate awaiting
   operator approval.
4. Notify the operator via Telegram/Discord that the draft has landed
   at the first gate.

Idempotency
-----------

Tracked via the ``app_settings`` row ``dev_diary_last_run_date``
(YYYY-MM-DD UTC). If today's date is already recorded, the job
returns ok=True with ``changes_made=0`` and the detail
"already ran today". This is robust against scheduler over-fire
(double-trigger, manual re-run, daemon restart).

Config (``plugin.job.run_dev_diary_post``)
------------------------------------------

- ``enabled`` (default true)
- ``cron_expression`` (default ``"0 13 * * *"`` — 9am ET, ~13:00 UTC)
- ``config.gates`` (default ``"draft,final"``)
- ``config.hours_lookback`` (default 24)
- ``config.confidence_floor`` (default 0.7)
- ``config.notify_on_draft`` (default true)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


_LAST_RUN_KEY = "dev_diary_last_run_date"
_NICHE_SLUG = "dev_diary"
_DEFAULT_GATES = "draft,final"


class RunDevDiaryPostJob:
    name = "run_dev_diary_post"
    description = "Generate the daily Glad Labs dev-diary post (gated for operator approval)"
    schedule = "0 13 * * *"  # 09:00 America/New_York during EDT
    idempotent = True  # The internal date marker handles double-fire

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        if pool is None:
            return JobResult(
                ok=False, detail="no DB pool — dev-diary job requires DB access",
                changes_made=0,
            )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # ---- 1. Idempotency check ----
        last_run = await _get_last_run_date(pool)
        if last_run == today:
            logger.info(
                "[dev-diary] already ran today (last_run=%s) — skipping",
                last_run,
            )
            return JobResult(
                ok=True,
                detail=f"already ran today (last_run={last_run})",
                changes_made=0,
            )

        # ---- 2. Gather context ----
        from services.topic_sources.dev_diary_source import DevDiarySource

        hours = int(config.get("hours_lookback", 24) or 24)
        confidence = float(config.get("confidence_floor", 0.7) or 0.7)

        source = DevDiarySource()
        try:
            ctx = await source.gather_context(
                pool,
                hours_lookback=hours,
                confidence_floor=confidence,
            )
        except Exception as exc:
            logger.exception("[dev-diary] gather_context failed: %s", exc)
            return JobResult(
                ok=False,
                detail=f"gather_context failed: {exc}",
                changes_made=0,
            )

        # ---- 3. Quiet-day skip ----
        if ctx.is_empty():
            logger.info("[dev-diary] no Glad Labs activity in last %dh — skipping", hours)
            await _set_last_run_date(pool, today)  # still mark as ran — don't retry today
            if bool(config.get("notify_on_draft", True)):
                try:
                    await _notify_operator(
                        f"Dev diary skipped for {today} — quiet day, "
                        f"no Glad Labs activity to report.",
                    )
                except Exception as notify_err:
                    logger.warning("[dev-diary] notify failed: %s", notify_err)
            return JobResult(
                ok=True,
                detail="skipped — quiet day, no Glad Labs activity to report",
                changes_made=0,
            )

        # ---- 4. Create the content task ----
        gates = str(config.get("gates", _DEFAULT_GATES)).strip() or _DEFAULT_GATES
        try:
            task_id = await _create_dev_diary_task(pool, ctx, gates)
        except Exception as exc:
            logger.exception("[dev-diary] task creation failed: %s", exc)
            return JobResult(
                ok=False,
                detail=f"task creation failed: {exc}",
                changes_made=0,
            )

        # ---- 5. Mark idempotency + notify operator ----
        await _set_last_run_date(pool, today)

        if bool(config.get("notify_on_draft", True)):
            try:
                await _notify_operator(_format_draft_landed_message(task_id, ctx, gates))
            except Exception as notify_err:
                logger.warning("[dev-diary] notify failed: %s", notify_err)

        logger.info(
            "[dev-diary] queued task %s for %s (prs=%d commits=%d gates=%s)",
            task_id, today, len(ctx.merged_prs), len(ctx.notable_commits), gates,
        )
        return JobResult(
            ok=True,
            detail=(
                f"queued dev_diary task {task_id} for {today} "
                f"(prs={len(ctx.merged_prs)} commits={len(ctx.notable_commits)} "
                f"decisions={len(ctx.brain_decisions)} gates={gates})"
            ),
            changes_made=1,
            metrics={
                "task_id": str(task_id),
                "merged_prs": len(ctx.merged_prs),
                "notable_commits": len(ctx.notable_commits),
                "brain_decisions": len(ctx.brain_decisions),
                "audit_resolved": len(ctx.audit_resolved),
                "recent_posts": len(ctx.recent_posts),
                "cost_total_usd": ctx.cost_summary.get("total_usd", 0.0),
            },
        )


# ---------------------------------------------------------------------------
# Persistence helpers (app_settings + content_tasks)
# ---------------------------------------------------------------------------


async def _get_last_run_date(pool: Any) -> str | None:
    """Read the YYYY-MM-DD marker from app_settings. None when unset."""
    try:
        return await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            _LAST_RUN_KEY,
        )
    except Exception as exc:
        logger.debug("[dev-diary] last-run-date fetch failed: %s", exc)
        return None


async def _set_last_run_date(pool: Any, date_str: str) -> None:
    """Upsert the YYYY-MM-DD marker. Best-effort — never raises."""
    try:
        await pool.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret)
            VALUES ($1, $2, $3, $4, FALSE)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = NOW()
            """,
            _LAST_RUN_KEY, date_str, "scheduling",
            "YYYY-MM-DD (UTC) of the last successful dev-diary job run. "
            "Idempotency marker — the job no-ops if this matches today.",
        )
    except Exception as exc:
        logger.warning("[dev-diary] last-run-date upsert failed: %s", exc)


async def _create_dev_diary_task(pool: Any, ctx: Any, gates: str) -> str:
    """Insert a pipeline task tagged for the dev_diary niche.

    Uses raw SQL rather than the ContentTaskStore wrapper because the
    job runs in the bare ``pool`` context (no DatabaseService instance).
    The pipeline picks up rows by status='pending'; the niche tag
    (carried via the ``category`` column == "dev_diary") routes the
    row through the dev_diary writer-prompt override.

    #188: ``content_tasks`` is a view — INSERT into the underlying
    ``pipeline_tasks`` + ``pipeline_versions`` tables directly. The
    ``task_metadata`` JSON blob lands inside ``pipeline_versions.stage_data``
    under the ``task_metadata`` key so the view's
    ``stage_data -> 'task_metadata'`` projection still surfaces it
    unchanged for downstream readers (writer dispatcher reads
    ``task["task_metadata"]["context_bundle"]``).
    """
    import json
    from uuid import uuid4

    task_id = str(uuid4())
    topic = ctx.headline()
    bundle_dict = ctx.to_dict()
    task_metadata = {
        "niche": _NICHE_SLUG,
        "gates": gates,
        "request_type": "dev_diary",
        # Kept here for backward compat with any reader that still
        # expects task_metadata.context_bundle, but THIS COPY GETS
        # WIPED by the content_tasks_update_redirect trigger on first
        # writer update (the trigger does
        # ``stage_data || {'task_metadata': NEW.task_metadata}`` which
        # replaces the entire task_metadata sub-object). The
        # authoritative copy lives at stage_data._dev_diary_bundle —
        # see below.
        "context_bundle": bundle_dict,
        "generate_featured_image": True,
        "source": "scheduled_dev_diary_job",
    }
    # Place the bundle at a top-level stage_data key so the trigger's
    # JSONB merge (which only touches 'metadata', 'result',
    # 'task_metadata') leaves it untouched. _read_context_bundle reads
    # from here. Underscore prefix signals "preserved by convention".
    stage_data = {
        "task_metadata": task_metadata,
        "_dev_diary_bundle": bundle_dict,
    }

    # Pull writer_rag_mode from the niche row so the task routes
    # through the writer_rag_modes dispatcher (TWO_PASS / DETERMINISTIC_
    # COMPOSITOR / etc.) when the legacy generate_content stage runs
    # inside the template. NULL is fine — generate_content falls back
    # to its legacy path when unset.
    async with pool.acquire() as _niche_conn:
        niche_mode = await _niche_conn.fetchval(
            "SELECT writer_rag_mode FROM niches WHERE slug = $1",
            _NICHE_SLUG,
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO pipeline_tasks (
                    task_id, task_type, status, topic, stage,
                    style, tone, target_length,
                    target_audience, category, niche_slug,
                    writer_rag_mode, template_slug,
                    created_at, updated_at
                ) VALUES (
                    $1, 'blog_post', 'pending', $2, 'pending',
                    'first_person', 'candid', 600,
                    $3, $4, $5,
                    $6, $7,
                    NOW(), NOW()
                )
                """,
                task_id,
                topic,
                "indie-devs, ai-curious, build-in-public",
                _NICHE_SLUG,
                # Persist the niche slug into the dedicated column too.
                # Was previously only set on `category` and on
                # `task_metadata.niche` — the dedicated niche_slug
                # column stayed NULL, which broke per-niche lookups
                # like the `niches.writer_prompt_override` consumer
                # added in PR #222 (lookup keys off niche_slug, not
                # task_metadata, so dev_diary tasks silently used the
                # generic-default writer prompt instead of the
                # dev-diary-specific one).
                _NICHE_SLUG,
                niche_mode,
                # template_slug routes the task through the v1
                # LangGraph TemplateRunner. The 'dev_diary' template
                # skips QA, auto-curator, SEO, and media-script stages —
                # none fit a status-report artifact. See
                # docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md
                # and Glad-Labs/poindexter#359.
                "dev_diary",
            )
            await conn.execute(
                """
                INSERT INTO pipeline_versions (
                    task_id, version, title, stage_data, created_at
                ) VALUES (
                    $1, 1, $2, $3::jsonb, NOW()
                )
                ON CONFLICT (task_id, version) DO NOTHING
                """,
                task_id,
                topic[:140],
                json.dumps(stage_data, default=str),
            )
    return task_id


# ---------------------------------------------------------------------------
# Operator notification
# ---------------------------------------------------------------------------


def _format_draft_landed_message(task_id: str, ctx: Any, gates: str) -> str:
    """Build the Telegram/Discord message for a freshly queued draft."""
    lines = [
        f"Dev diary draft queued for {ctx.date}",
        f"Task id: `{task_id}`",
        f"Gates: `{gates}`",
        "",
        "Activity captured:",
        f"- {len(ctx.merged_prs)} merged PRs",
        f"- {len(ctx.notable_commits)} notable commits",
        f"- {len(ctx.brain_decisions)} high-confidence brain decisions",
        f"- {len(ctx.audit_resolved)} resolved audit events",
        f"- {len(ctx.recent_posts)} recently published posts",
    ]
    cost = ctx.cost_summary.get("total_usd", 0.0)
    inferences = ctx.cost_summary.get("total_inferences", 0)
    if cost or inferences:
        lines.append(f"- ${cost:.4f} spent across {inferences} inferences")
    lines.extend([
        "",
        "Pipeline will draft + land at the `draft` gate for your review.",
    ])
    return "\n".join(lines)


async def _notify_operator(message: str) -> None:
    """Best-effort Telegram/Discord ping via the standard dispatcher."""
    try:
        from services.integrations.operator_notify import notify_operator
        await notify_operator(message, critical=False)
    except Exception as e:  # noqa: BLE001 — operator notify must never crash the job
        logger.warning("[dev-diary] no notification path available: %s", e)
