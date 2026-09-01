"""Auto-publish helpers — quality-gated, daily-limited blog publishing.

Ported from ``services/task_executor.py`` during the Prefect cutover
Stage 4 (Glad-Labs/poindexter#410). The dispatch daemon was deleted;
this module owns the two pieces of logic that survived because
``services/post_pipeline_actions.py`` still needs them after every
successful pipeline run:

- :func:`get_auto_publish_threshold` — reads the
  ``auto_publish_threshold`` row from ``app_settings`` (default ``0``
  = disabled). The gate fires when the pipeline's QA quality_score
  meets or exceeds this floor AND ``require_human_approval=false``.
  The "do we even want to auto-publish?" gate lives in
  ``post_pipeline_actions._maybe_auto_publish``; this helper only
  answers "what's the threshold?".

- :func:`auto_publish_task` — flips a task to ``approved`` /
  ``publish_mode='auto'``, calls ``publish_service.publish_post_from_task``,
  and records the approval + distribution rows so the
  ``content_tasks`` view resolves ``approval_status='approved'`` for
  auto-published rows the same way the operator-curated path does.

Both helpers fail loud per ``feedback_no_silent_defaults``: anything
that bubbles up (DB error, missing task, missing featured image,
publish failure) is logged at WARNING or ERROR with enough context
to find the row in Grafana / Loki. The task lands in
``awaiting_approval`` whenever the auto-publish path can't complete
cleanly, which is the safe failure mode (an operator-visible row
is always better than a silent drop).

Per ``feedback_design_for_llm_consumers``: this module is the canonical
source for "how does auto-publish work?". The post_pipeline_actions
caller threads the wired DatabaseService through; this module never
touches a module-level singleton.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


async def _get_setting(
    database_service: Any,
    key: str,
    default: str,
) -> str:
    """Read an ``app_settings`` value via the DatabaseService cache.

    Mirrors the contract of the deleted ``TaskExecutor._get_setting`` so
    existing call sites behave identically: returns a str (DB values
    are coerced from int/bool to str), falls back to ``default`` on any
    DB / cache miss. Never raises — auto-publish is best-effort.
    """
    if database_service is None:
        return default
    try:
        raw = await database_service.get_setting_value(key, default)
        return str(raw) if raw is not None else default
    except Exception as e:
        # Best-effort read, but not SILENT — a swallowed settings error here
        # silently degrades auto-publish decisions to defaults (#2127). Log so
        # the fallback is visible; callers that need fail-closed wrap this.
        logger.warning(
            "[AUTO_PUBLISH] _get_setting(%s) failed (%s) — using default %r",
            key, e, default,
        )
        return default


async def get_auto_publish_threshold(database_service: Any) -> float:
    """Return the ``auto_publish_threshold`` app_settings value.

    Zero (the default) means auto-publish is disabled — the gate in
    ``post_pipeline_actions._maybe_auto_publish`` treats
    ``threshold <= 0`` as "never fire". Operators raise this above the
    desired quality_score floor to opt in.
    """
    try:
        value = await _get_setting(database_service, "auto_publish_threshold", "0")
        return float(value) if value else 0.0
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "[AUTO_PUBLISH] Failed to read auto_publish_threshold: %s", exc,
        )
        return 0.0


async def auto_publish_task(
    *,
    database_service: Any,
    task_id: str,
    quality_score: float,
    site_config: Any,
) -> bool:
    """Auto-approve and publish a task that has cleared the quality floor.

    Returns ``True`` when the publish succeeded (task is live on the
    public site), ``False`` when the path bailed for any reason
    (daily limit hit, missing featured image, publish_service error).
    Callers should suppress the operator notification when this
    returns ``True`` — ``publish_service`` sends its own
    "published" message.

    Failure modes (each leaves the task in ``awaiting_approval`` so
    the operator can intervene):

    - ``daily_post_limit`` reached (default ``1``/day)
    - task row not found by ``task_id``
    - task missing ``featured_image_url`` (we don't auto-publish
      image-less posts; the operator-facing approval queue is the
      right surface for that)
    - ``publish_post_from_task`` returned ``success=False``

    site_config (#272 Phase-2g): REQUIRED. Threaded down to
    ``publish_post_from_task`` (which now requires it). The Prefect
    post-pipeline path (``post_pipeline_actions._maybe_auto_publish``)
    passes its run-bound instance.
    """
    from services.publish_service import publish_post_from_task

    if database_service is None:
        logger.warning(
            "[AUTO_PUBLISH] database_service is None — task %s stays in awaiting_approval",
            task_id,
        )
        return False

    # Daily limit — check the cloud (production) posts table when we have
    # a separate cloud_pool, otherwise fall back to the local pool. The
    # date arithmetic runs on the DB so we never have a timezone-skew
    # bug between the worker's clock and the published_at column.
    try:
        daily_limit = int(await _get_setting(database_service, "daily_post_limit", "1"))
        check_pool = getattr(database_service, "cloud_pool", None) or database_service.pool
        published_today = await check_pool.fetchval(
            "SELECT COUNT(*) FROM posts "
            "WHERE status = 'published' AND published_at::date = CURRENT_DATE"
        )
        if published_today >= daily_limit:
            logger.info(
                "[AUTO_PUBLISH] Daily limit reached (%d/%d), task %s stays in awaiting_approval",
                published_today, daily_limit, task_id,
            )
            return False
    except Exception as exc:  # noqa: BLE001 — defensive
        # FAIL CLOSED (audit H3): the daily_post_limit rate cap is a safety
        # rail. If we can't verify how many posts went out today, we must NOT
        # publish — previously this fell through and published anyway, so a DB
        # blip could auto-publish an unbounded number of posts in a day. Leave
        # the task in awaiting_approval for the operator. Logged at error so a
        # persistent failure of this safety check reaches GlitchTip, not just
        # Loki.
        logger.error(
            "[AUTO_PUBLISH] daily-limit check failed for task %s — NOT "
            "auto-publishing (fail-closed); leaving in awaiting_approval: %s",
            task_id, exc, exc_info=True,
        )
        return False

    task = await database_service.get_task(task_id)
    if not task:
        logger.error("[AUTO_PUBLISH] Task %s not found", task_id)
        return False

    if not task.get("featured_image_url"):
        logger.info(
            "[AUTO_PUBLISH] Task %s missing featured image, stays in awaiting_approval",
            task_id,
        )
        return False

    # Status transition: pending → approved (single update, then refetch
    # so publish_post_from_task sees the fresh row).
    await database_service.update_task_status(task_id, "approved")

    existing_metadata = task.get("task_metadata") or {}
    if isinstance(existing_metadata, str):
        try:
            existing_metadata = json.loads(existing_metadata) if existing_metadata else {}
        except (json.JSONDecodeError, TypeError):
            existing_metadata = {}
    existing_metadata.update(
        {
            "auto_published": True,
            "auto_publish_quality_score": quality_score,
            "auto_published_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    await database_service.update_task(
        task_id,
        {
            "approval_status": "approved",
            "publish_mode": "auto",
            "task_metadata": json.dumps(existing_metadata, default=str),
        },
    )

    task = await database_service.get_task(task_id)
    if not task:
        logger.error("[AUTO_PUBLISH] Task %s not found after approval", task_id)
        return False

    # Veto window (2026-08-14): when the niche carries a positive
    # ``{niche}_auto_publish_delay_hours``, the gate's fire does NOT publish
    # immediately — it stages the post + assigns a scheduled slot at
    # now+delay and pings the operator. Doing nothing ships it at the slot;
    # rejecting the task from ANY surface cancels it (the scheduled_publisher
    # re-checks the task at fire time, same pattern as the social
    # scheduler's approve-at-fire-time gate). This inverts the operator's
    # role for trusted niches: veto-to-stop instead of approve-to-ship.
    niche = str(task.get("niche_slug") or "").strip()
    delay_hours = await _get_delay_hours(database_service, niche)
    if delay_hours > 0:
        return await _stage_with_veto_window(
            database_service=database_service,
            task=task,
            task_id=task_id,
            quality_score=quality_score,
            site_config=site_config,
            niche=niche,
            delay_hours=delay_hours,
        )

    result = await publish_post_from_task(
        database_service,
        task,
        task_id,
        publisher="auto_publish",
        trigger_revalidation=True,
        queue_social=True,
        site_config=site_config,
    )

    if not result.success:
        logger.error(
            "[AUTO_PUBLISH] Task %s auto-publish failed: %s",
            task_id, result.error,
        )
        return False

    logger.info(
        "[AUTO_PUBLISH] Task %s published as post %s (score: %s, slug: %s)",
        task_id, result.post_id, quality_score, result.post_slug,
    )

    # Record approval + distribution so the ``content_tasks`` view's
    # resolved ``approval_status`` / ``post_id`` / ``post_slug`` columns
    # are non-NULL for auto-published rows (same contract as the
    # operator-curated approve path). The post is already live, so a failure
    # here must NOT un-publish it — but it must be visible: these rows back an
    # operator-facing view, and swallowing the failure (the old
    # ``with suppress(Exception)``) left that view silently wrong, against
    # this module's own fail-loud docstring (silent-failure audit).
    try:
        await database_service.pool.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, gate_name, event_kind, feedback, actor, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            task_id,
            "auto_publish",
            "approved",
            f"Auto-approved at quality score {quality_score:.1f}",
            "auto_publish",
            json.dumps(
                {
                    "reviewer": "auto_publish",
                    "decision": "approved",
                    "quality_score": quality_score,
                },
                default=str,
            ),
        )
        from services.pipeline_db import SITE_TARGET, PipelineDB

        await PipelineDB(database_service.pool).add_distribution(
            task_id=task_id,
            target=SITE_TARGET,
            post_id=result.post_id,
            post_slug=result.post_slug,
            external_url=result.published_url,
            status="published",
        )
    except Exception as exc:
        logger.error(
            "[AUTO_PUBLISH] Task %s published as post %s but recording the "
            "approval/distribution rows failed — the content_tasks view will "
            "show NULL approval_status/post_id/post_slug until reconciled: %s",
            task_id, result.post_id, exc, exc_info=True,
        )
        emit_finding(
            source="auto_publish",
            kind="auto_publish_contract_write_failed",
            title=f"Auto-publish bookkeeping failed for task {task_id}",
            body=(
                f"Task {task_id} published as post {result.post_id} "
                f"({result.post_slug}) but the pipeline_gate_history + "
                f"distribution write failed: {describe_exception(exc)}. The content_tasks view "
                f"resolves approval_status / post_id / post_slug from these "
                f"rows, so they read NULL until backfilled."
            ),
            severity="warn",
            dedup_key=f"auto-publish-contract:{task_id}",
            extra={"task_id": str(task_id), "post_id": str(result.post_id)},
        )

    # model_performance.human_approved flip — learning signal that closes the
    # loop on whether the quality scorer's prediction matched the auto-publish
    # outcome (poindexter#271 Phase 3.A1). A failure here doesn't affect the
    # live post, but silently dropping it starves the scorer's feedback loop —
    # make it visible rather than swallowing it.
    try:
        await database_service.mark_model_performance_outcome(
            task_id, human_approved=True, post_published=True,
        )
    except Exception as exc:
        logger.error(
            "[AUTO_PUBLISH] Task %s — model_performance learning-signal flip "
            "failed; the quality scorer won't learn from this auto-publish "
            "outcome: %s",
            task_id, exc, exc_info=True,
        )
        emit_finding(
            source="auto_publish",
            kind="auto_publish_learning_signal_failed",
            title=f"Auto-publish learning-signal write failed for task {task_id}",
            body=(
                f"mark_model_performance_outcome failed for task {task_id} "
                f"after a successful auto-publish: {describe_exception(exc)}. The router's "
                f"quality feedback loop (poindexter#271) is missing this "
                f"outcome."
            ),
            severity="warn",
            dedup_key=f"auto-publish-learning:{task_id}",
            extra={"task_id": str(task_id)},
        )

    return True


async def _get_delay_hours(database_service: Any, niche: str) -> float:
    """Read ``{niche}_auto_publish_delay_hours`` (veto window). 0 = immediate.

    Niche-prefixed like the other four gate keys (2026-05-27 niche-leak fix):
    a niche without the key publishes immediately when its gate fires, and
    there is never a cross-niche fallback. Unparseable values degrade to 0
    (immediate) with a WARNING — the pre-veto-window behaviour, never a
    surprise hold.
    """
    if not niche:
        return 0.0
    raw = await _get_setting(
        database_service, f"{niche}_auto_publish_delay_hours", "0",
    )
    try:
        return max(0.0, float(raw or 0))
    except (TypeError, ValueError):
        logger.warning(
            "[AUTO_PUBLISH] %s_auto_publish_delay_hours=%r is not a number — "
            "treating as 0 (immediate publish)",
            niche, raw,
        )
        return 0.0


async def _stage_with_veto_window(
    *,
    database_service: Any,
    task: dict[str, Any],
    task_id: str,
    quality_score: float,
    site_config: Any,
    niche: str,
    delay_hours: float,
) -> bool:
    """Stage the gate-approved post + schedule it ``delay_hours`` out.

    The veto-window fire mode: ``publish_post_from_task(stage_only=True)``
    creates the posts row at ``status='approved'`` (records the edit-metrics
    clean-run row at the approve seam, exactly like the immediate path), then
    ``scheduling_service.assign_slot`` flips it to ``status='scheduled'`` with
    ``published_at = now + delay``. The existing ``scheduled_publisher`` loop
    promotes it at the slot — after re-checking the source task is still
    approved (``_demote_vetoed_auto_posts``), so ANY reject path doubles as a
    veto. The posts row carries ``metadata.auto_publish_veto_window='true'``
    so that fire-time guard scopes to auto-scheduled rows only, never the
    operator's hand-picked slots.

    Failure modes are fail-safe: any partial state leaves the post staged at
    ``status='approved'`` / the task at ``approved`` — operator-visible in the
    Approved-queue panel, and nothing publishes on its own. Returns ``True``
    only when the slot is assigned and the operator was pinged (callers
    suppress their own notification on ``True``, same contract as the
    immediate path).
    """
    from services.publish_service import publish_post_from_task

    result = await publish_post_from_task(
        database_service,
        task,
        task_id,
        publisher="auto_publish",
        trigger_revalidation=False,
        queue_social=True,
        stage_only=True,
        site_config=site_config,
    )
    if not result.success:
        logger.error(
            "[AUTO_PUBLISH] veto-window stage failed for task %s: %s",
            task_id, result.error,
        )
        return False

    pool = getattr(database_service, "cloud_pool", None) or database_service.pool
    publish_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)

    # Stamp the veto-window marker BEFORE scheduling, so the fire-time guard
    # can never observe a scheduled auto row without it.
    try:
        await pool.execute(
            """
            UPDATE posts
               SET metadata = COALESCE(metadata, '{}'::jsonb)
                   || jsonb_build_object(
                          'auto_publish_veto_window', 'true',
                          'auto_publish_veto_deadline', $2::text)
             WHERE id::text = $1
            """,
            str(result.post_id), publish_at.isoformat(),
        )
    except Exception as exc:  # noqa: BLE001
        # Without the marker the fire-time guard won't cover this row — do
        # not schedule it; leave it staged for the operator instead.
        logger.error(
            "[AUTO_PUBLISH] veto-window marker write failed for post %s "
            "(task %s) — leaving post staged, NOT scheduling: %s",
            result.post_id, task_id, exc, exc_info=True,
        )
        return False

    from services.scheduling_service import assign_slot

    slot = await assign_slot(
        result.post_id, publish_at, pool=pool, site_config=site_config,
    )
    if not slot.ok:
        logger.error(
            "[AUTO_PUBLISH] veto-window slot assignment failed for post %s "
            "(task %s): %s — post stays staged at status='approved'",
            result.post_id, task_id, slot.detail,
        )
        emit_finding(
            source="auto_publish",
            kind="auto_publish_veto_slot_failed",
            title=f"Veto-window slot assignment failed for task {task_id}",
            body=(
                f"publish_post_from_task staged post {result.post_id} for "
                f"task {task_id} but assign_slot refused: {slot.detail}. The "
                f"post sits at status='approved' (Approved-queue panel) and "
                f"will not publish until the operator schedules or promotes "
                f"it."
            ),
            severity="warn",
            dedup_key=f"auto-publish-veto-slot:{task_id}",
            extra={"task_id": str(task_id), "post_id": str(result.post_id)},
        )
        return False

    # Gate-history row — same shape as the immediate path, plus the window.
    try:
        await database_service.pool.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, gate_name, event_kind, feedback, actor, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            task_id,
            "auto_publish",
            "approved",
            (
                f"Auto-approved at quality score {quality_score:.1f}; "
                f"veto window until {publish_at.isoformat()}"
            ),
            "auto_publish",
            json.dumps(
                {
                    "reviewer": "auto_publish",
                    "decision": "approved",
                    "quality_score": quality_score,
                    "veto_window_hours": delay_hours,
                    "scheduled_publish_at": publish_at.isoformat(),
                    "post_id": str(result.post_id),
                },
                default=str,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[AUTO_PUBLISH] veto-window gate-history write failed for task "
            "%s (post is scheduled and WILL publish at %s): %s",
            task_id, publish_at.isoformat(), exc, exc_info=True,
        )

    await _notify_veto_window(
        site_config=site_config,
        pool=pool,
        task=task,
        task_id=task_id,
        quality_score=quality_score,
        niche=niche,
        delay_hours=delay_hours,
        publish_at=publish_at,
    )
    logger.info(
        "[AUTO_PUBLISH] Task %s staged with veto window: post %s publishes "
        "at %s (%.1fh) unless vetoed",
        task_id, result.post_id, publish_at.isoformat(), delay_hours,
    )
    return True


async def _notify_veto_window(
    *,
    site_config: Any,
    pool: Any,
    task: dict[str, Any],
    task_id: str,
    quality_score: float,
    niche: str,
    delay_hours: float,
    publish_at: datetime,
) -> None:
    """Operator ping for a veto-window hold — critical=True (Telegram).

    The whole feature is 'doing nothing ships it', so the ping MUST reach
    the operator's phone: same critical routing as the awaiting-approval
    queue indicator. Best-effort — a notify failure never unwinds the
    schedule (the slot is already visible on the Scheduled-publish queue
    panel and `poindexter auto-publish status`).
    """
    topic = str(task.get("topic") or task.get("title") or task_id)
    when_local = publish_at.isoformat()
    try:
        from services.clock import format_local, get_operator_tz

        tz = await get_operator_tz(pool)
        when_local = format_local(publish_at, tz, "%a %b %d, %I:%M %p %Z")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[AUTO_PUBLISH] operator-local time formatting failed "
            "(falling back to UTC ISO): %s", exc,
        )
    msg = (
        f"Auto-publish scheduled: \"{topic}\"\n"
        f"Score: {quality_score:.0f}/100 — {niche} gate passed\n"
        f"Publishes: {when_local} (in {delay_hours:g}h) unless vetoed\n"
        f"Veto: poindexter auto-publish veto {str(task_id)[:8]}\n"
        f"(rejecting the task from any surface also cancels the slot)"
    )
    try:
        from services.integrations.operator_notify import notify_operator

        await notify_operator(msg, critical=True, site_config=site_config)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[AUTO_PUBLISH] veto-window notification failed for %s: %s",
            task_id, exc,
        )


async def veto_auto_publish(pool: Any, task_id: str) -> dict[str, Any]:
    """Withdraw a veto-window auto-approval before the slot fires.

    One transaction: unschedule the staged post (``scheduled`` →
    ``approved`` + ``published_at=NULL``), park the task back at
    ``awaiting_approval`` for a normal operator pass, delete the stage-time
    ``published_post_edit_metrics`` row (``approver='auto_publish'``) so a
    vetoed run never counts toward the niche's trailing clean-run window —
    a veto is evidence AGAINST trust, not a clean run — and record a
    ``vetoed`` pipeline_gate_history row.

    Returns ``{"ok": bool, "detail": str, ...}``. ``ok=False`` when no
    scheduled post is linked to the task (already promoted, already vetoed,
    or never in a veto window).

    ``task_id`` may be a prefix (operators type short ids): resolution lives
    HERE, not in the adapters, per the transport-adapter contract — every
    surface (CLI today, MCP/HTTP tomorrow) gets the same short-id ergonomics
    without holding SQL of its own. An ambiguous prefix is a clean refusal.
    """
    tid_input = str(task_id).strip()
    async with pool.acquire() as conn:
        matches = await conn.fetch(
            "SELECT task_id::text AS task_id FROM pipeline_tasks "
            "WHERE task_id::text LIKE $1 || '%' LIMIT 5",
            tid_input,
        )
    if not matches:
        return {"ok": False, "detail": f"No task matches {tid_input!r}"}
    if len(matches) > 1:
        return {
            "ok": False,
            "detail": (
                f"Ambiguous prefix {tid_input!r} — matches "
                f"{', '.join(m['task_id'][:12] for m in matches)}"
            ),
        }
    tid = matches[0]["task_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            post = await conn.fetchrow(
                """
                UPDATE posts
                   SET status = 'approved', published_at = NULL,
                       updated_at = NOW()
                 WHERE metadata->>'pipeline_task_id' = $1
                   AND status = 'scheduled'
                RETURNING id, title
                """,
                tid,
            )
            if post is None:
                return {
                    "ok": False,
                    "detail": (
                        f"No scheduled post linked to task {tid} — already "
                        f"promoted, already vetoed, or never in a veto window."
                    ),
                }
            await conn.execute(
                """
                UPDATE pipeline_tasks
                   SET status = 'awaiting_approval', updated_at = NOW()
                 WHERE task_id = $1 AND status = 'approved'
                """,
                tid,
            )
            deleted = await conn.execute(
                "DELETE FROM published_post_edit_metrics "
                "WHERE task_id = $1 AND approver = 'auto_publish'",
                tid,
            )
            await conn.execute(
                """
                INSERT INTO pipeline_gate_history
                    (task_id, gate_name, event_kind, feedback, actor, metadata)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                tid,
                "auto_publish",
                "vetoed",
                "Operator vetoed the auto-publish window; task parked at "
                "awaiting_approval for a manual pass.",
                "operator",
                json.dumps({"post_id": str(post["id"])}, default=str),
            )
    logger.info(
        "[AUTO_PUBLISH] veto: task %s parked at awaiting_approval, post %s "
        "unscheduled (%s edit-metric row(s) removed)",
        tid, post["id"], deleted,
    )
    return {
        "ok": True,
        "detail": (
            f"Vetoed: post {post['id']} unscheduled, task {tid} parked at "
            f"awaiting_approval."
        ),
        "post_id": str(post["id"]),
        "title": post["title"],
    }


__all__ = [
    "auto_publish_task",
    "get_auto_publish_threshold",
    "veto_auto_publish",
]
