"""HTTP-side approve-and-resume for interrupt()-paused pipeline gates.

The graph-node gates (``atoms.approval_gate``) pause a LIVE LangGraph run via
``interrupt()`` — clearing one is a two-step operation: record the approval
(``approval_service.approve``) **and re-invoke the graph with
``resume=True``** so LangGraph loads the Postgres checkpoint and continues
past the gate. Approval alone strands the task ``in_progress`` until the
stale-inprogress sweep resets it — and the sweep bumps ``retry_count``, which
invalidates the approval (the atom's stale-approval check), so the task just
re-parks at the gate. The resume is not optional.

That two-step flow previously existed only in the CLI
(``poindexter pipeline resume`` — :func:`poindexter.cli.pipeline._resume_one`),
which runs the graph in-process in the operator's terminal. This module is the
worker-side equivalent so HTTP surfaces (the operator console's NEEDS YOU gate
lane, ``POST /api/gates/pending/{task_id}/approve``) can clear a gate too:

1. Validate the task is paused at a gate (capturing the pause state for
   rollback) and record the approval — synchronous, so the caller's optimistic
   UI update matches reality.
2. Schedule the graph resume as a **background** asyncio task on the worker's
   event loop and return immediately (the route answers 202). ``seo_refresh``
   resumes in seconds (republish → R2 export + ISR revalidate), but a
   ``canonical_blog`` ``draft_gate`` resume runs the whole remaining pipeline —
   minutes — so the resume can never block the HTTP response.

Failure semantics mirror the CLI's resume-atomicity contract:

- Resume RAISES (graph never advanced past the gate) → the approval is rolled
  back (:func:`services.approval_service.rollback_resume_approval`): history
  row deleted, pause restored. The task reappears in the gate queue on the
  next poll and the operator is notified (Discord — routine, not a page).
- Resume completes but the graph HALTS downstream → the approval stands (the
  gate was durably passed); the halt is audited + notified.
- Worker restarts mid-resume → the task is left ``in_progress`` with an intact
  checkpoint and a recorded approval — exactly the "continue-resume" stranded
  shape ``poindexter pipeline resume`` already detects; the stale-inprogress
  sweep is the automatic fallback (reset → fresh run → the retry_count-stamped
  approval is ignored → the task re-parks for review). No unreviewed publish
  is possible on any path.

Defense against double-fire: a module-level in-flight set refuses a second
approve for a task whose resume is still running (409 at the route). The
``approve`` call itself is the cross-process guard — it flips the task off
``awaiting_gate``, so a concurrent approve in another process hits
``TaskNotPausedError``.

Issue: the seo_refresh gate-queue surfacing gap (SEO Harvest epic
Glad-Labs/poindexter#762 follow-up).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.approval_service import (
    ApprovalServiceError,
    TaskNotFoundError,
    TaskNotPausedError,
    rollback_resume_approval,
)
from services.approval_service import (
    approve as approve_service,
)
from services.audit_log import audit_log_bg

logger = logging.getLogger(__name__)

# Task ids with a resume currently running in THIS worker process. Guards the
# window between approve (gate columns cleared) and the background task
# finishing, where a repeated approve would otherwise raise a confusing
# TaskNotPausedError instead of an honest "already resuming".
_RESUMING: set[str] = set()

# Default ceiling on one background resume. A wedged resume would otherwise
# pin the in-flight guard until a worker restart. Cancelling the asyncio task
# aborts the graph mid-node; the rollback path then restores the pause, and
# the durable checkpoint still holds the gate state — safe to re-approve.
_DEFAULT_RESUME_TIMEOUT_S = 900.0


class ResumeInFlightError(ApprovalServiceError):
    """Raised when a resume for this task is already running in-process."""


def resuming_task_ids() -> frozenset[str]:
    """Snapshot of task ids with an in-flight background resume (observability)."""
    return frozenset(_RESUMING)


async def _fetch_gate_row(pool: Any, task_id: str) -> dict[str, Any] | None:
    """Return the gate-relevant columns for an exact ``task_id``, or None.

    HTTP callers send the full UUID they got from ``GET /api/gates/pending``,
    so no prefix resolution here (the CLI keeps that affordance).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT task_id::text AS task_id,
                   status,
                   awaiting_gate,
                   gate_artifact,
                   gate_paused_at,
                   topic,
                   template_slug
              FROM pipeline_tasks
             WHERE task_id::text = $1
            """,
            str(task_id),
        )
    return dict(row) if row is not None else None


async def approve_and_schedule_resume(
    *,
    task_id: str,
    feedback: str | None,
    actor: str,
    db_service: Any,
    site_config: Any,
    spawn: Any = None,
) -> dict[str, Any]:
    """Record the gate approval, then resume the graph in the background.

    Args:
        task_id: Full ``pipeline_tasks.task_id`` (as returned by the pending
            listing — no prefix resolution).
        feedback: Optional operator note, recorded on the approval row.
        actor: Who approved (``"human"`` for console/REST).
        db_service: The worker's live ``DatabaseService`` (pool + delegate
            methods; the resumed atoms call ``update_task`` etc. on it).
        site_config: The worker's loaded ``SiteConfig``.
        spawn: Callable scheduling the background coroutine — defaults to
            ``asyncio.create_task``; tests inject a synchronous runner.

    Returns:
        ``{"ok": True, "task_id", "gate_name", "template_slug",
        "mode": "approve_resume_started", "approval": {...}}``.

    Raises:
        TaskNotFoundError / TaskNotPausedError: from validation.
        ResumeInFlightError: a resume for this task is already running here.
        ApprovalServiceError: the task has no ``template_slug`` to resume.
    """
    pool = db_service.pool
    row = await _fetch_gate_row(pool, task_id)
    if row is None:
        raise TaskNotFoundError(f"Task {task_id} not found")

    task_id_str = str(row["task_id"])
    gate_name = row.get("awaiting_gate")
    if not gate_name:
        raise TaskNotPausedError(
            f"Task {task_id} is not paused at any gate "
            f"(current status={row.get('status')!r})"
        )
    template_slug = row.get("template_slug")
    if not template_slug:
        raise ApprovalServiceError(
            f"Task {task_id} has no template_slug — cannot resume"
        )
    if task_id_str in _RESUMING:
        raise ResumeInFlightError(
            f"Task {task_id} already has a resume in flight — refusing a "
            "second approve until it settles"
        )

    # Capture the pause state BEFORE approve clears it, so a failed resume
    # can be compensated back to exactly here (CLI resume-atomicity contract).
    original_artifact = row.get("gate_artifact")
    original_paused_at = row.get("gate_paused_at")

    approval = await approve_service(
        task_id=task_id_str,
        gate_name=gate_name,
        feedback=feedback,
        actor=actor,
        site_config=site_config,
        pool=pool,
    )

    # Reserve the in-flight slot before scheduling so a same-loop duplicate
    # approve can't slip in between create_task and the coroutine starting.
    _RESUMING.add(task_id_str)
    coro = _resume_in_background(
        task_id=task_id_str,
        gate_name=gate_name,
        template_slug=str(template_slug),
        topic=row.get("topic") or "",
        gate_history_id=approval.get("gate_history_id"),
        original_artifact=original_artifact,
        original_paused_at=original_paused_at,
        db_service=db_service,
        site_config=site_config,
    )
    try:
        (spawn or asyncio.create_task)(coro)
    except Exception:
        _RESUMING.discard(task_id_str)
        coro.close()
        raise

    return {
        "ok": True,
        "task_id": task_id_str,
        "gate_name": gate_name,
        "template_slug": template_slug,
        "mode": "approve_resume_started",
        "approval": approval,
    }


async def _resume_in_background(
    *,
    task_id: str,
    gate_name: str,
    template_slug: str,
    topic: str,
    gate_history_id: int | None,
    original_artifact: Any,
    original_paused_at: Any,
    db_service: Any,
    site_config: Any,
) -> None:
    """Run the checkpoint resume; roll the approval back if it raises.

    Never raises — every outcome lands in audit_log and (on failure/halt) a
    Discord note, because nothing awaits this coroutine.
    """
    try:
        timeout_s = float(
            site_config.get_float(
                "gate_resume_timeout_seconds", _DEFAULT_RESUME_TIMEOUT_S
            )
        )
    except Exception:  # noqa: BLE001  # silent-ok: a bad setting value must not block the resume; default is safe
        timeout_s = _DEFAULT_RESUME_TIMEOUT_S

    try:
        try:
            summary = await asyncio.wait_for(
                _run_resume(
                    task_id=task_id,
                    gate_name=gate_name,
                    template_slug=template_slug,
                    topic=topic,
                    db_service=db_service,
                    site_config=site_config,
                ),
                timeout=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 — every failure shape rolls back
            await _rollback_and_notify(
                task_id=task_id,
                gate_name=gate_name,
                gate_history_id=gate_history_id,
                original_artifact=original_artifact,
                original_paused_at=original_paused_at,
                db_service=db_service,
                site_config=site_config,
                exc=exc,
            )
            return

        if getattr(summary, "ok", False):
            audit_log_bg(
                event_type="gate_resume_completed",
                source="gate_resume",
                details={"gate_name": gate_name, "template_slug": template_slug},
                task_id=task_id,
                severity="info",
            )
        else:
            halted_at = getattr(summary, "halted_at", None)
            audit_log_bg(
                event_type="gate_resume_halted",
                source="gate_resume",
                details={
                    "gate_name": gate_name,
                    "template_slug": template_slug,
                    "halted_at": halted_at,
                },
                task_id=task_id,
                severity="warning",
            )
            await _notify(
                f"[gate resume] Task {task_id[:8]} resumed past "
                f"'{gate_name}' but the pipeline halted at {halted_at!r}. "
                f"Inspect: poindexter pipeline status {task_id}",
                site_config,
            )
    finally:
        _RESUMING.discard(task_id)


async def _run_resume(
    *,
    task_id: str,
    gate_name: str,
    template_slug: str,
    topic: str,
    db_service: Any,
    site_config: Any,
) -> Any:
    """Invoke TemplateRunner with ``resume=True`` (mirrors the CLI's call).

    Imports are lazy so the route/service import stays light — langgraph and
    the capability plugins only load when an operator actually clears a gate.
    """
    from services.di_wiring import build_platform_for_subprocess
    from services.template_runner import TemplateRunner

    platform = build_platform_for_subprocess(db_service.pool, site_config)
    runner = TemplateRunner(
        db_service.pool,
        # Explicit DSN — TemplateRunner's own fallback resolver degrades to
        # MemorySaver when it can't import brain.bootstrap, and a MemorySaver
        # "resume" re-runs the graph from entry with this thin initial state
        # (the CLI hit exactly that; see poindexter/cli/pipeline.py).
        checkpointer_dsn=getattr(db_service, "database_url", None),
        site_config=site_config,
    )
    return await runner.run(
        template_slug,
        {
            "task_id": task_id,
            "topic": topic,
            "database_service": db_service,
            "site_config": site_config,
            "platform": platform,
        },
        thread_id=task_id,
        resume=True,
        resume_value={"approved": True, "gate_name": gate_name},
    )


async def _rollback_and_notify(
    *,
    task_id: str,
    gate_name: str,
    gate_history_id: int | None,
    original_artifact: Any,
    original_paused_at: Any,
    db_service: Any,
    site_config: Any,
    exc: BaseException,
) -> None:
    """Compensate a failed resume: restore the pause, drop the approval row."""
    err = f"{type(exc).__name__}: {exc}"
    logger.exception(
        "[gate_resume] resume failed task=%s gate=%s — rolling approval back",
        task_id, gate_name,
    )
    try:
        await rollback_resume_approval(
            task_id=task_id,
            gate_name=gate_name,
            gate_history_id=gate_history_id,
            artifact=original_artifact,
            paused_at=original_paused_at,
            pool=db_service.pool,
        )
        rolled_back = True
    except Exception:
        # The rollback itself failed — the task is stranded in_progress with
        # an approval on record. The stale-inprogress sweep + the gate atom's
        # retry_count staleness check make this safe (it re-parks for review);
        # log loud so the operator knows the queue row will reappear late.
        logger.exception(
            "[gate_resume] rollback ALSO failed task=%s gate=%s — the "
            "stale-inprogress sweep will re-park it for review",
            task_id, gate_name,
        )
        rolled_back = False

    audit_log_bg(
        event_type="gate_resume_failed",
        source="gate_resume",
        details={
            "gate_name": gate_name,
            "error": err,
            "rolled_back": rolled_back,
        },
        task_id=task_id,
        severity="error",
    )
    await _notify(
        f"[gate resume] Task {task_id[:8]} resume past '{gate_name}' FAILED "
        f"({err}). "
        + (
            "Approval rolled back — the task is back in the gate queue."
            if rolled_back
            else "Rollback also failed — the stale sweep will re-park it."
        ),
        site_config,
    )


async def _notify(msg: str, site_config: Any) -> None:
    """Best-effort routine (Discord-tier) operator note. Never raises."""
    try:
        from services.integrations.operator_notify import notify_operator

        await notify_operator(msg, critical=False, site_config=site_config)
    except Exception as exc:  # noqa: BLE001  # silent-ok: notify_operator logs its own delivery failures; a notify miss must not mask the audit trail above
        logger.debug("[gate_resume] operator notify failed: %s", exc)


__all__ = [
    "ResumeInFlightError",
    "approve_and_schedule_resume",
    "resuming_task_ids",
]
