"""HITL approval-gate service module — single source of truth (#145).

The CLI, MCP server, and any future REST endpoints all call into the
functions defined here. There is no business logic in the CLI / MCP
wrappers; they translate user input into a service call and render
the response.

Design rules:

- **DI seam.** Every public function takes ``site_config`` and ``pool``
  in its signature — no module-level singletons, no
  ``site_config.singleton``, no ``database_service.global_pool``.
  Tests pass mocks; production wires up via
  ``app.state.container.site_config`` and ``database_service.pool``.
- **DB-first config.** Gate enable flags live in ``app_settings`` under
  the ``pipeline_gate_<gate_name>`` key. ``set_gate_enabled`` writes
  there. No env vars, no code constants.
- **Bail loudly.** ``pause_at_gate`` writes an ``audit_log`` row on every
  call so the timeline is reconstructable. ``approve`` / ``reject``
  log a WARNING when the task isn't found — the caller bubbles that
  up so the operator sees it.
- **No silent fallback.** When ``awaiting_gate`` is set on a task and
  the operator approves an UNRELATED gate, raise
  :class:`GateMismatchError` so the wrapper surfaces it. Never
  silently flip the wrong gate.

Glossary
--------

A *gate* is a configurable pause-and-wait boundary in a pipeline.
The ``ApprovalGateStage`` reads a gate name from its config dict,
checks whether that gate is enabled in ``app_settings``, persists the
artifact under review on the task row, fires a notification, and
halts the workflow. A human operator clears the gate via
``poindexter approve <task_id>`` (CLI), the MCP ``approve`` tool, or
the future REST endpoint — all of which call :func:`approve` here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from services.audit_log import audit_log_bg
from services.gate_machinery import (
    GateServiceError,
    ensure_gate_match,
    iso_or_none,
    resolve_reject_status,
)
from services.gate_machinery import coerce_artifact as _coerce_artifact
from services.logger_config import get_logger

logger = get_logger(__name__)


# Gate-enable flag prefix in app_settings. The full key is
# ``pipeline_gate_<gate_name>``; the value is ``"on"`` / ``"off"``
# (lowercase). New gates default to off so adding a Stage to a pipeline
# doesn't accidentally start blocking on a human until the operator
# explicitly enables the gate.
_GATE_SETTING_PREFIX = "pipeline_gate_"

# Status the task carries while paused. Distinct from ``awaiting_approval``
# (the existing final-media special case) so dashboards can tell the
# difference between "old final-media gate" and "new HITL gate."
PAUSED_STATUS = "awaiting_gate"

# Status set on rejection. Default — gate config can override per-gate
# via ``reject_status``. ``rejected_retry`` puts the task back into the
# pipeline; ``rejected_final`` ends it.
DEFAULT_REJECT_STATUS = "rejected"
DEFAULT_REJECT_STATUS_DISMISS = "dismissed"


# ---------------------------------------------------------------------------
# Exceptions — wrappers raise these and translate to user-visible errors
# ---------------------------------------------------------------------------


class ApprovalServiceError(GateServiceError):
    """Base class — every service-level failure derives from this.

    Derives from the shared :class:`services.gate_machinery.GateServiceError`
    root so a caller can ``except GateServiceError`` to catch a failure from
    either approval service (#622)."""


class TaskNotFoundError(ApprovalServiceError):
    """Raised when ``approve`` / ``reject`` / ``show_pending`` can't find
    the task ID. CLI prints a friendly error and exits non-zero; MCP
    returns the message in its tool response."""


class TaskNotPausedError(ApprovalServiceError):
    """Raised when the operator tries to approve / reject a task that
    isn't paused at any gate. Distinct from TaskNotFoundError so the
    operator knows the task exists but the gate has already cleared
    (race with another operator, or the sweeper auto-rejected)."""


class GateMismatchError(ApprovalServiceError):
    """Raised when the operator passes ``--gate X`` for a task currently
    paused at gate Y. Loud failure per the no-silent-fallback rule —
    the operator picks the wrong gate name and the system tells them
    instead of approving the wrong artifact."""


class RegenCapReachedError(ApprovalServiceError):
    """Raised when a ``regen_at_gate`` request would exceed the per-component
    cap (``app_settings.regen_<component>_max_attempts``). HITL means the
    operator is the loop bound; this cap is the runaway guard. On cap the task
    stays paused at the gate so the operator must approve or reject instead of
    looping again."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gate_setting_key(gate_name: str) -> str:
    """Return the ``app_settings`` key for a gate's enable flag."""
    return f"{_GATE_SETTING_PREFIX}{gate_name}"


def is_gate_enabled(gate_name: str, site_config: Any) -> bool:
    """Return True iff the gate is enabled in app_settings.

    Default is OFF. New Stages drop into the pipeline inert — the
    operator opts in by flipping the setting to ``on``. Mirrors the
    "feature flag" pattern used everywhere else in app_settings.
    """
    if site_config is None:
        return False
    raw = site_config.get(_gate_setting_key(gate_name), "off")
    return str(raw).strip().lower() in ("on", "true", "1", "yes")


async def _record_router_outcome(
    *,
    pool: Any,
    task_id: str,
    decision: str,
    site_config: Any,
) -> None:
    """Fire the outcome→variant-weight feedback loop (#361 part 1).

    Lazily imported to avoid an import cycle at module load. Best-effort —
    every failure is logged + swallowed so the learning loop can NEVER
    break an operator approve/reject (per ``feedback_human_approval``).
    """
    try:
        from services.router_outcome_feedback import record_task_outcome

        await record_task_outcome(
            pool=pool,
            task_id=task_id,
            decision=decision,
            site_config=site_config,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[approval_service] router outcome feedback failed task=%s: %s",
            task_id, exc,
        )


async def _record_approve_brain_signal(
    *,
    pool: Any,
    task_id: str,
    gate_name: str,
    topic: str | None,
    feedback: str,
) -> None:
    """Best-effort: write a brain_knowledge weight-up row on non-empty approval feedback (#149).

    Operator notes on a manual approve encode preference signal that auto-metrics
    can't capture — "loved the personal tone" shapes the next writer call.
    Written to ``brain_knowledge`` so the brain's topic-ranking and
    ``search_memory`` pick it up on the next BrainKnowledgeTap cycle (hourly).
    Never raises — a failure here must never affect the approval outcome.
    """
    entity = f"topic:{(topic or '')[:200]}" if topic else f"gate:{gate_name}"
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge
                (entity, attribute, value, confidence, source, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT DO NOTHING
            """,
            entity,
            "approved_by_operator",
            feedback[:500],
            0.7,
            f"approval_service.approve.{gate_name}",
        )
    except Exception as exc:  # noqa: BLE001  # silent-ok: best-effort brain signal — pool errors must never affect approval outcome
        logger.debug(
            "[approval_service] brain approve-signal write failed "
            "(task=%s gate=%s): %s",
            task_id[:8], gate_name, exc,
        )


async def _fetch_task_row(pool: Any, task_id: str) -> dict[str, Any] | None:
    """Return the minimal task row needed for gate decisions, or None.

    Reads the BASE TABLE ``pipeline_tasks`` directly (not the
    ``content_tasks`` view) so we can UPDATE the gate columns through
    the same connection. The view was the spec's reference point but
    Postgres won't let us ALTER columns or UPDATE-through a view that
    aggregates subqueries. ``task_id`` here is the VARCHAR external
    identifier (``pipeline_tasks.task_id``) — same value the operator
    sees in the CLI.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT pt.task_id AS id,
                   pt.status,
                   pt.awaiting_gate,
                   pt.gate_artifact,
                   pt.gate_paused_at,
                   pt.retry_count,
                   pt.topic,
                   pv.title AS title
              FROM pipeline_tasks pt
              LEFT JOIN pipeline_versions pv
                     ON pv.task_id::text = pt.task_id::text
                    AND pv.version = (
                        SELECT MAX(version) FROM pipeline_versions
                         WHERE task_id::text = pt.task_id::text
                    )
             WHERE pt.task_id::text = $1
            """,
            str(task_id),
        )
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Pause — called by ApprovalGateStage when a gate trips
# ---------------------------------------------------------------------------


async def pause_at_gate(
    *,
    task_id: str,
    gate_name: str,
    artifact: dict[str, Any],
    site_config: Any,
    pool: Any,
    notify: bool = True,
) -> dict[str, Any]:
    """Persist the gate state and (optionally) notify the operator.

    Called by :class:`modules.content.stages.approval_gate.ApprovalGateStage`.
    Idempotent — re-pausing at the same gate just refreshes the
    artifact and timestamp, doesn't insert a duplicate row anywhere.

    Args:
        task_id: UUID-as-string of the content_tasks row.
        gate_name: Stable slug, e.g. ``"topic_decision"``.
        artifact: JSON-serializable dict the operator will review.
        site_config: SiteConfig instance for telegram/discord lookup.
        pool: asyncpg pool.
        notify: When False, skip the notification fan-out (used by
            tests so they don't depend on Telegram / Discord config).

    Returns:
        Dict with ``ok``, ``gate_name``, ``paused_at``, plus the
        notify result so callers can log delivery state.

    Audit trail:
        Always writes ``audit_log`` row ``approval_gate_paused`` —
        even if the DB update fails — so the timeline of "we tried
        to pause" survives transient outages.
    """
    paused_at = datetime.now(timezone.utc)
    artifact_json = json.dumps(artifact or {}, default=str)

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pipeline_tasks
                   SET awaiting_gate = $1,
                       gate_artifact = $2::jsonb,
                       gate_paused_at = $3,
                       status = 'awaiting_gate',
                       updated_at = NOW()
                 WHERE task_id::text = $4
                """,
                gate_name,
                artifact_json,
                paused_at,
                str(task_id),
            )
    except Exception:
        logger.exception(
            "[approval_service] pause_at_gate DB write failed task=%s gate=%s",
            task_id, gate_name,
        )
        # Still emit the audit row so the operator can see we *tried*.
        audit_log_bg(
            event_type="approval_gate_pause_failed",
            source="approval_service",
            details={"gate_name": gate_name, "error": "db_write_failed"},
            task_id=str(task_id),
            severity="error",
        )
        raise

    audit_log_bg(
        event_type="approval_gate_paused",
        source="approval_service",
        details={
            "gate_name": gate_name,
            "artifact_keys": sorted((artifact or {}).keys()),
            "paused_at": paused_at.isoformat(),
        },
        task_id=str(task_id),
        severity="info",
    )

    notify_result: dict[str, Any] = {"sent": False, "reason": "skipped"}
    if notify:
        notify_result = await _notify_gate_tripped(
            task_id=str(task_id),
            gate_name=gate_name,
            artifact=artifact or {},
            site_config=site_config,
        )

    return {
        "ok": True,
        "task_id": str(task_id),
        "gate_name": gate_name,
        "paused_at": paused_at.isoformat(),
        "notify": notify_result,
    }


async def _notify_gate_tripped(
    *,
    task_id: str,
    gate_name: str,
    artifact: dict[str, Any],
    site_config: Any,
) -> dict[str, Any]:
    """Fire a Discord+Telegram notification through the existing path.

    Routes through the declarative outbound dispatcher
    (``discord_ops`` / ``telegram_ops`` rows in ``webhook_endpoints``),
    falling back to the legacy direct Discord webhook when the row
    is disabled or the dispatcher framework is unavailable. Failures
    are swallowed — never raises.

    The notification helper used to live in
    ``services.task_executor._notify_alert``; with the Prefect Stage 4
    cutover (Glad-Labs/poindexter#410) the dispatch daemon was
    deleted and the helper moved into
    :mod:`services.integrations.operator_notify`. The call signature
    is now ``notify_operator(msg, critical=..., site_config=...)``.
    """
    from services.integrations.operator_notify import notify_operator

    artifact_summary = _summarize_artifact(artifact)
    msg = (
        f"[approval gate] Task {task_id[:8]} paused at gate '{gate_name}'.\n"
        f"{artifact_summary}\n"
        f"Approve: poindexter approve {task_id} --gate {gate_name}\n"
        f"Reject:  poindexter reject  {task_id} --gate {gate_name} --reason '...'"
    )
    try:
        await notify_operator(msg, critical=False, site_config=site_config)
        return {"sent": True, "reason": "ok"}
    except Exception as exc:
        logger.warning(
            "[approval_service] notify_gate_tripped failed task=%s gate=%s: %s",
            task_id, gate_name, exc,
        )
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}


def _summarize_artifact(artifact: dict[str, Any]) -> str:
    """One-line preview of the artifact for the notification body.

    Picks a short, useful field if it exists — title, topic, image_url
    — else dumps a truncated key list so the operator at least knows
    what kind of thing they need to review.
    """
    if not artifact:
        return "(empty artifact)"
    for preferred in ("title", "topic", "image_url", "preview_url", "summary"):
        val = artifact.get(preferred)
        if isinstance(val, str) and val.strip():
            return f"{preferred}: {val[:120]}"
    keys = sorted(artifact.keys())
    return f"keys: {', '.join(keys[:6])}" + (" ..." if len(keys) > 6 else "")


# ---------------------------------------------------------------------------
# Approve — operator clears the gate
# ---------------------------------------------------------------------------


async def approve(
    *,
    task_id: str,
    gate_name: str | None = None,
    feedback: str | None = None,
    actor: str = "human",
    site_config: Any,
    pool: Any,
) -> dict[str, Any]:
    """Clear the named gate on a task and re-queue the pipeline.

    When ``gate_name`` is None, clears whatever gate the task is
    currently paused at (most-recent gate by definition — only one
    gate can be active at a time). When ``gate_name`` is supplied
    and doesn't match the active gate, raises :class:`GateMismatchError`.

    Args:
        task_id: UUID of the content_tasks row.
        gate_name: Optional name to assert. None = "any active gate."
        feedback: Optional operator note recorded on the audit row.
        actor: Who triggered the approval — 'human' for CLI/MCP/REST,
            'auto_publish' for the quality-score gate.
        site_config: SiteConfig (DI seam).
        pool: asyncpg pool.

    Returns:
        Dict ``{"ok": True, "task_id": ..., "gate_name": ...,
        "previous_status": ..., "feedback": ...}``.

    Raises:
        TaskNotFoundError: ID isn't in ``content_tasks``.
        TaskNotPausedError: task exists but ``awaiting_gate`` is NULL.
        GateMismatchError: ``gate_name`` doesn't match active gate.
    """
    row = await _fetch_task_row(pool, task_id)
    if row is None:
        logger.warning(
            "[approval_service] approve: task %s not found", task_id
        )
        raise TaskNotFoundError(f"Task {task_id} not found")

    cleared_gate = ensure_gate_match(
        row,
        gate_name,
        entity_label="Task",
        entity_id=str(task_id),
        not_paused_exc=TaskNotPausedError,
        mismatch_exc=GateMismatchError,
        verb="approve",
    )
    previous_status = row.get("status")
    # The run attempt this approval belongs to. The stale-inprogress sweep
    # (services.tasks_db.sweep_stale_tasks) bumps retry_count when it resets
    # a crashed resume to 'pending'; stamping it here lets the gate atom
    # reject a *stale* approval on the next fresh run instead of silently
    # re-publishing regenerated content with no operator review.
    approved_at_retry_count = int(row.get("retry_count") or 0)

    # Clear the gate columns and restore status to 'in_progress'. The
    # pipeline_gate_history row below is what the resume-pass idempotency
    # check reads (modules/content/atoms/approval_gate.py). Setting
    # in_progress here makes the stale sweeper the fallback for a crashed
    # resume run, and keeps the task visible as actively running while
    # runner.run(resume=True) executes. Both writes share one transaction so
    # a task can never end up gate-cleared without its history row (or vice
    # versa).
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE pipeline_tasks
                   SET awaiting_gate = NULL,
                       gate_artifact = '{}'::jsonb,
                       gate_paused_at = NULL,
                       status = 'in_progress',
                       updated_at = NOW()
                 WHERE task_id::text = $1
                """,
                str(task_id),
            )

            # RETURNING id so a failed resume can target THIS exact row for
            # rollback (rollback_resume_approval), rather than guessing the
            # "latest approved" row under concurrency.
            gate_history_id = await conn.fetchval(
                """
                INSERT INTO pipeline_gate_history
                    (task_id, gate_name, event_kind, feedback, actor, metadata)
                VALUES ($1, $2, 'approved', $3, $4, $5::jsonb)
                RETURNING id
                """,
                str(task_id),
                cleared_gate,
                feedback or "",
                actor,
                json.dumps(
                    {
                        "previous_status": previous_status,
                        "approved_at_retry_count": approved_at_retry_count,
                    },
                    default=str,
                ),
            )

    audit_log_bg(
        event_type="approval_gate_approved",
        source="approval_service",
        details={
            "gate_name": cleared_gate,
            "feedback": feedback or "",
            "previous_status": previous_status,
        },
        task_id=str(task_id),
        severity="info",
    )

    # Outcome → variant-weight feedback loop (#361 part 1). Gate-based
    # approval surface (CLI / MCP / REST). Attributes the verdict to the
    # task's experiment variant(s) + backfills atom_runs.decision.
    # Best-effort — never breaks the approval.
    await _record_router_outcome(
        pool=pool, task_id=str(task_id), decision="approved",
        site_config=site_config,
    )

    # Operator approval feedback → brain learning signal (#149).
    # Only when the operator included a non-empty note — a bare approve
    # without feedback is not meaningful enough to encode as a preference.
    if feedback:
        await _record_approve_brain_signal(
            pool=pool,
            task_id=str(task_id),
            gate_name=cleared_gate,
            topic=row.get("topic"),
            feedback=feedback,
        )

    return {
        "ok": True,
        "task_id": str(task_id),
        "gate_name": cleared_gate,
        "previous_status": previous_status,
        "feedback": feedback or "",
        "gate_history_id": gate_history_id,
    }


# ---------------------------------------------------------------------------
# Rollback — compensating action when a resume fails after approve (#resume)
# ---------------------------------------------------------------------------


async def rollback_resume_approval(
    *,
    task_id: str,
    gate_name: str,
    gate_history_id: int | None,
    artifact: Any,
    paused_at: Any,
    pool: Any,
) -> dict[str, Any]:
    """Undo an :func:`approve` whose subsequent graph resume failed.

    ``poindexter pipeline resume`` records the approval (clearing the gate
    columns + writing an ``approved`` history row) BEFORE re-invoking the
    graph. If the resume raises *before the gate is durably passed* (e.g. the
    Postgres checkpointer can't be set up, so the graph never advances), the
    approval is left dangling: the task is no longer paused (so it can't be
    re-resumed) and the stale ``approved`` row would auto-pass a later fresh
    run's gate. This compensating action reverses both effects atomically:

    1. Restore the paused-at-gate columns (``awaiting_gate`` / ``gate_artifact``
       / ``gate_paused_at`` / ``status='awaiting_gate'``) so the operator can
       simply ``resume`` again.
    2. Delete the dangling ``approved`` ``pipeline_gate_history`` row (by id,
       when known) so nothing can silently auto-pass the gate.

    Args:
        task_id: External task identifier (``pipeline_tasks.task_id``).
        gate_name: The gate the task was paused at before approve cleared it.
        gate_history_id: Id of the ``approved`` row to delete (from
            :func:`approve`'s return). ``None`` skips the delete.
        artifact: The gate artifact captured BEFORE approve cleared it — any
            JSON-coercible value; restored verbatim.
        paused_at: The original ``gate_paused_at`` timestamp to restore.
        pool: asyncpg pool.

    Returns:
        ``{"ok": True, "task_id": ..., "gate_name": ..., "deleted_row": bool}``.
    """
    artifact_json = json.dumps(_coerce_artifact(artifact), default=str)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE pipeline_tasks
                   SET awaiting_gate = $2,
                       gate_artifact = $3::jsonb,
                       gate_paused_at = $4,
                       status = 'awaiting_gate',
                       updated_at = NOW()
                 WHERE task_id::text = $1
                """,
                str(task_id),
                gate_name,
                artifact_json,
                paused_at,
            )

            deleted_row = False
            if gate_history_id is not None:
                await conn.execute(
                    "DELETE FROM pipeline_gate_history WHERE id = $1",
                    gate_history_id,
                )
                deleted_row = True

    audit_log_bg(
        event_type="approval_gate_resume_rolled_back",
        source="approval_service",
        details={
            "gate_name": gate_name,
            "gate_history_id": gate_history_id,
            "deleted_row": deleted_row,
        },
        task_id=str(task_id),
        severity="warning",
    )

    return {
        "ok": True,
        "task_id": str(task_id),
        "gate_name": gate_name,
        "deleted_row": deleted_row,
    }


async def latest_approved_gate(pool: Any, task_id: str) -> str | None:
    """Return the gate_name of the most-recent ``approved`` row for a task.

    Used by ``poindexter pipeline resume`` to name the gate a task stranded
    *past* its gate (``awaiting_gate`` cleared by approve) already passed, so a
    continue-resume can reference it. Returns ``None`` when the task has no
    approval on record — the CLI reads that as "not a resumable post-gate
    state" and falls through to the not-paused error.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT gate_name
              FROM pipeline_gate_history
             WHERE task_id = $1
               AND event_kind = 'approved'
             ORDER BY created_at DESC
             LIMIT 1
            """,
            str(task_id),
        )
    return row["gate_name"] if row else None


# ---------------------------------------------------------------------------
# Reject — operator vetoes the artifact
# ---------------------------------------------------------------------------


async def reject(
    *,
    task_id: str,
    gate_name: str | None = None,
    reason: str | None = None,
    actor: str = "human",
    site_config: Any,
    pool: Any,
) -> dict[str, Any]:
    """Reject the artifact at the named gate.

    Sets the task status to the gate's configured reject status
    (``rejected`` for hard veto, ``dismissed`` for soft skip — the
    Stage decides via its config; default is ``rejected``).

    Same gate-matching rules as :func:`approve`. When ``gate_name`` is
    None the most recent (only active) gate is rejected.

    Args:
        task_id: UUID of the content_tasks row.
        gate_name: Optional name to assert.
        reason: Optional operator-supplied veto reason.
        actor: Who triggered the rejection — 'human' for CLI/MCP/REST.
        site_config: SiteConfig (DI).
        pool: asyncpg pool.

    Returns:
        ``{"ok": True, "task_id": ..., "gate_name": ...,
        "new_status": "rejected" | "dismissed"}``.
    """
    row = await _fetch_task_row(pool, task_id)
    if row is None:
        logger.warning(
            "[approval_service] reject: task %s not found", task_id
        )
        raise TaskNotFoundError(f"Task {task_id} not found")

    rejected_gate = ensure_gate_match(
        row,
        gate_name,
        entity_label="Task",
        entity_id=str(task_id),
        not_paused_exc=TaskNotPausedError,
        mismatch_exc=GateMismatchError,
        verb="reject",
    )

    # Per-gate reject status — operators / Stages can pin a specific
    # value via app_settings (``approval_gate_<gate>_reject_status``).
    # Fallback is the global ``rejected``. This lets a topic_decision
    # gate dismiss-not-reject (so the task is closed cleanly) while a
    # final_media gate fully rejects (so retry logic kicks in).
    new_status = resolve_reject_status(site_config, rejected_gate, DEFAULT_REJECT_STATUS)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pipeline_tasks
               SET status = $2,
                   awaiting_gate = NULL,
                   gate_artifact = '{}'::jsonb,
                   gate_paused_at = NULL,
                   error_message = COALESCE($3, error_message),
                   updated_at = NOW()
             WHERE task_id::text = $1
            """,
            str(task_id),
            new_status,
            f"gate '{rejected_gate}' rejected: {reason}" if reason else None,
        )

        await conn.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, gate_name, event_kind, feedback, actor, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            str(task_id),
            rejected_gate,
            new_status,
            reason or "",
            actor,
            json.dumps({"new_status": new_status}, default=str),
        )

    audit_log_bg(
        event_type="approval_gate_rejected",
        source="approval_service",
        details={
            "gate_name": rejected_gate,
            "reason": reason or "",
            "new_status": new_status,
        },
        task_id=str(task_id),
        severity="warning",
    )

    # Outcome → variant-weight feedback loop (#361 part 1). Gate-based
    # rejection surface. Backfills atom_runs.decision (the reject path's
    # historic gap) + nudges the task's variant weight(s) down.
    # Best-effort — never breaks the rejection.
    await _record_router_outcome(
        pool=pool, task_id=str(task_id), decision="rejected",
        site_config=site_config,
    )

    # Per-gate rejection handler — turns the rejection into a learning
    # signal (#148). Topic decisions weight-down the brain; preview
    # rejections enqueue a draft regen with the reason as steering.
    # Failures inside the handler are logged + swallowed so this never
    # makes a successful rejection look like a CLI error.
    try:
        from services.rejection_handlers import (
            RejectionContext,
            dispatch_rejection,
        )

        ctx = RejectionContext(
            gate_name=rejected_gate,
            task_id=str(task_id),
            post_id=None,
            reason=reason,
            artifact=_coerce_artifact(row.get("gate_artifact")),
            pool=pool,
            site_config=site_config,
        )
        await dispatch_rejection(ctx)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "[approval_service] rejection handler dispatch failed: %s", exc,
        )

    return {
        "ok": True,
        "task_id": str(task_id),
        "gate_name": rejected_gate,
        "new_status": new_status,
        "reason": reason or "",
    }


# ---------------------------------------------------------------------------
# Regen — preview_gate component-scoped regeneration (images | text)
# ---------------------------------------------------------------------------

# Static, component-keyed SQL — no f-string interpolation into the statement.
# ``component`` is whitelisted to these two keys, so there is no dynamic-SQL
# surface; the operator surface just picks the right prepared statement.
_REGEN_ATTEMPTS_SELECT = {
    "images": (
        "SELECT regen_images_attempts FROM pipeline_tasks "
        "WHERE task_id::text = $1 FOR UPDATE"
    ),
    "text": (
        "SELECT regen_text_attempts FROM pipeline_tasks "
        "WHERE task_id::text = $1 FOR UPDATE"
    ),
}
_REGEN_UPDATE = {
    "images": """
        UPDATE pipeline_tasks
           SET awaiting_gate = NULL,
               gate_artifact = '{}'::jsonb,
               gate_paused_at = NULL,
               status = 'in_progress',
               regen_images_attempts = regen_images_attempts + 1,
               regen_images_pending = true,
               updated_at = NOW()
         WHERE task_id::text = $1
    """,
    "text": """
        UPDATE pipeline_tasks
           SET awaiting_gate = NULL,
               gate_artifact = '{}'::jsonb,
               gate_paused_at = NULL,
               status = 'in_progress',
               regen_text_attempts = regen_text_attempts + 1,
               regen_text_pending = true,
               updated_at = NOW()
         WHERE task_id::text = $1
    """,
}
_REGEN_DEFAULT_CAP = {"images": 3, "text": 2}


async def regen_at_gate(
    *,
    task_id: str,
    component: str,
    steering: str | None = None,
    gate_name: str | None = None,
    actor: str = "human",
    site_config: Any,
    pool: Any,
) -> dict[str, Any]:
    """Request a surgical regen of one component (``images``/``text``) of a
    post paused at a gate.

    Sets the one-shot ``pipeline_tasks.regen_<component>_pending`` flag the
    ``approval_gate`` atom consumes on resume (routing the graph's backward loop
    edge to the image/writer block), bumps the monotonic
    ``regen_<component>_attempts`` counter, and writes a ``regen_<component>``
    ``pipeline_gate_history`` audit row. Like :func:`approve`, it clears the gate
    columns and sets ``status='in_progress'`` so the CALLER resumes the graph
    (the resume is the caller's job — CLI/MCP — exactly as for approve).

    Bounded by ``app_settings.regen_<component>_max_attempts`` (defaults: images
    3, text 2): at the cap this raises :class:`RegenCapReachedError` and leaves
    the task paused so the operator must approve or reject. ``steering`` is an
    optional free-text note recorded on the audit row (prompt threading is a
    follow-up).

    Args:
        task_id: External ``pipeline_tasks.task_id``.
        component: ``"images"`` or ``"text"`` — anything else raises ``ValueError``.
        steering: Optional operator guidance, recorded as the audit feedback.
        gate_name: Optional gate to assert; None = the task's active gate.
        actor: Who triggered it (CLI/MCP = 'human').
        site_config: SiteConfig (DI).
        pool: asyncpg pool.

    Returns:
        ``{"ok": True, "task_id", "gate_name", "component", "attempts",
        "max_attempts", "steering", "previous_status"}``.

    Raises:
        ValueError: ``component`` is not 'images'/'text'.
        TaskNotFoundError / TaskNotPausedError / GateMismatchError: as approve.
        RegenCapReachedError: the component is already at its attempt cap.
    """
    if component not in _REGEN_UPDATE:
        raise ValueError(
            f"regen component must be 'images' or 'text', got {component!r}"
        )

    row = await _fetch_task_row(pool, task_id)
    if row is None:
        logger.warning("[approval_service] regen: task %s not found", task_id)
        raise TaskNotFoundError(f"Task {task_id} not found")

    cleared_gate = ensure_gate_match(
        row,
        gate_name,
        entity_label="Task",
        entity_id=str(task_id),
        not_paused_exc=TaskNotPausedError,
        mismatch_exc=GateMismatchError,
        verb="regen",
    )
    previous_status = row.get("status")
    cap = site_config.get_int(
        f"regen_{component}_max_attempts", _REGEN_DEFAULT_CAP[component]
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchval(
                _REGEN_ATTEMPTS_SELECT[component], str(task_id)
            )
            current = int(current or 0)
            if current >= cap:
                # Leave the task paused (the transaction rolls back having
                # changed nothing) so the operator must approve or reject.
                raise RegenCapReachedError(
                    f"regen cap reached for {component} "
                    f"({current}/{cap}) on task {task_id} — approve or reject"
                )
            new_attempts = current + 1

            await conn.execute(_REGEN_UPDATE[component], str(task_id))

            await conn.execute(
                """
                INSERT INTO pipeline_gate_history
                    (task_id, gate_name, event_kind, feedback, actor, metadata)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                str(task_id),
                cleared_gate,
                f"regen_{component}",
                steering or "",
                actor,
                json.dumps(
                    {
                        "component": component,
                        "attempts": new_attempts,
                        "previous_status": previous_status,
                    },
                    default=str,
                ),
            )

    audit_log_bg(
        event_type="approval_gate_regen",
        source="approval_service",
        details={
            "gate_name": cleared_gate,
            "component": component,
            "attempts": new_attempts,
            "steering": steering or "",
        },
        task_id=str(task_id),
        severity="info",
    )

    return {
        "ok": True,
        "task_id": str(task_id),
        "gate_name": cleared_gate,
        "component": component,
        "attempts": new_attempts,
        "max_attempts": cap,
        "steering": steering or "",
        "previous_status": previous_status,
    }


# ---------------------------------------------------------------------------
# List + show — read-side helpers for CLI / MCP / dashboard
# ---------------------------------------------------------------------------


async def list_pending(
    *,
    pool: Any,
    gate_name: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return every task currently paused at any gate (or one gate).

    Ordered oldest-first so the operator works through the queue
    chronologically. Each row carries the parsed artifact dict so the
    caller can render it without a second DB hit.

    Args:
        pool: asyncpg pool.
        gate_name: When set, filter to a specific gate.
        limit: Max rows to return — protects against runaway queues.

    Returns:
        List of dicts, each with task_id, gate_name, artifact,
        paused_at, status, topic, title.
    """
    where = "WHERE awaiting_gate IS NOT NULL"
    args: list[Any] = []
    if gate_name:
        where += " AND awaiting_gate = $1"
        args.append(gate_name)
    args.append(limit)
    limit_param = f"${len(args)}"

    # Read from the content_tasks view so the title field (which lives
    # on pipeline_versions, joined inside the view) is available
    # without re-coding the join here.
    sql = f"""
        SELECT task_id::text AS task_id,
               awaiting_gate AS gate_name,
               gate_artifact,
               gate_paused_at,
               status,
               topic,
               title
          FROM content_tasks
          {where}
         ORDER BY gate_paused_at ASC NULLS LAST
         LIMIT {limit_param}
    """  # nosec B608  # where is built from local literals; limit_param is "${N}" placeholder; values use $N params

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["artifact"] = _coerce_artifact(d.pop("gate_artifact", None))
        # Stringify timestamp for JSON-friendliness — CLI and MCP both
        # want serializable shapes.
        d["gate_paused_at"] = iso_or_none(d.get("gate_paused_at"))
        out.append(d)
    return out


async def show_pending(
    *,
    pool: Any,
    task_id: str,
) -> dict[str, Any]:
    """Return the single task's gate state + artifact, or raise.

    Raises:
        TaskNotFoundError: task ID doesn't exist.
        TaskNotPausedError: task exists but isn't paused at a gate.
    """
    row = await _fetch_task_row(pool, task_id)
    if row is None:
        raise TaskNotFoundError(f"Task {task_id} not found")
    ensure_gate_match(
        row,
        None,
        entity_label="Task",
        entity_id=str(task_id),
        not_paused_exc=TaskNotPausedError,
        mismatch_exc=GateMismatchError,
        verb="approve",
    )

    artifact = _coerce_artifact(row.get("gate_artifact"))
    paused_at = iso_or_none(row.get("gate_paused_at"))

    return {
        "task_id": row["id"],
        "gate_name": row["awaiting_gate"],
        "artifact": artifact,
        "gate_paused_at": paused_at,
        "status": row.get("status"),
        "topic": row.get("topic"),
        "title": row.get("title"),
    }


# ---------------------------------------------------------------------------
# Gate enable/disable management
# ---------------------------------------------------------------------------


async def set_gate_enabled(
    *,
    gate_name: str,
    enabled: bool,
    pool: Any,
    site_config: Any = None,
) -> dict[str, Any]:
    """Toggle the ``pipeline_gate_<gate_name>`` app_settings row.

    Upserts the row so a brand-new gate can be enabled before it's
    been seen by the pipeline. Updates ``site_config``'s in-memory
    cache when one is supplied so the change is visible to the
    current process without a restart.

    Args:
        gate_name: Stable slug.
        enabled: True → ``"on"``, False → ``"off"``.
        pool: asyncpg pool.
        site_config: optional — when supplied the in-memory cache is
            updated too so the same process sees the new value.

    Returns:
        ``{"ok": True, "gate_name": ..., "enabled": True/False,
        "key": "pipeline_gate_..."}``.
    """
    key = _gate_setting_key(gate_name)
    value = "on" if enabled else "off"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, description, is_active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (key) DO UPDATE
               SET value = EXCLUDED.value,
                   is_active = TRUE,
                   updated_at = NOW()
            """,
            key,
            value,
            f"HITL approval gate {gate_name!r}: on/off (auto-managed by approval_service)",
        )

    if site_config is not None:
        try:
            site_config._config[key] = value  # type: ignore[attr-defined]
        except Exception:
            # Test fakes may not expose ``_config``; safe to ignore.
            logger.debug(
                "[approval_service] could not patch site_config cache for %s", key,
            )

    audit_log_bg(
        event_type="approval_gate_setting_changed",
        source="approval_service",
        details={"gate_name": gate_name, "enabled": enabled, "key": key},
        severity="info",
    )

    return {"ok": True, "gate_name": gate_name, "enabled": enabled, "key": key}


async def list_gates(
    *,
    pool: Any,
    site_config: Any = None,  # noqa: ARG001 — kept for API consistency with other gate fns; callers pass it
) -> list[dict[str, Any]]:
    """Return every gate the system has ever heard of, plus its state.

    Sources of "known gates":

    1. Every ``pipeline_gate_*`` row in app_settings (operator already
       configured these).
    2. Every distinct ``awaiting_gate`` value currently on any
       ``content_tasks`` row (live gates, even if the setting hasn't
       been written yet).

    Returns each gate with its enabled flag and how many tasks are
    currently paused on it.
    """
    async with pool.acquire() as conn:
        # All known gate-enable settings.
        setting_rows = await conn.fetch(
            """
            SELECT key, value, is_active
              FROM app_settings
             WHERE key LIKE $1
            """,
            f"{_GATE_SETTING_PREFIX}%",
        )
        # Live in-flight gates — base table read so the count is
        # accurate even mid-migration if the view hasn't been refreshed.
        live_rows = await conn.fetch(
            """
            SELECT awaiting_gate AS gate_name, COUNT(*) AS pending_count
              FROM pipeline_tasks
             WHERE awaiting_gate IS NOT NULL
             GROUP BY awaiting_gate
            """,
        )

    gates: dict[str, dict[str, Any]] = {}
    for row in setting_rows:
        gate_name = row["key"][len(_GATE_SETTING_PREFIX):]
        if not gate_name:
            # Skip a hypothetical key that's exactly the prefix.
            continue
        enabled = (
            str(row["value"]).strip().lower() in ("on", "true", "1", "yes")
            and bool(row.get("is_active", True))
        )
        gates[gate_name] = {
            "gate_name": gate_name,
            "enabled": enabled,
            "setting_key": row["key"],
            "pending_count": 0,
        }

    for row in live_rows:
        gate_name = row["gate_name"]
        gates.setdefault(
            gate_name,
            {
                "gate_name": gate_name,
                "enabled": False,
                "setting_key": _gate_setting_key(gate_name),
                "pending_count": 0,
            },
        )
        gates[gate_name]["pending_count"] = int(row["pending_count"])

    return sorted(gates.values(), key=lambda g: g["gate_name"])


__all__ = [
    "ApprovalServiceError",
    "TaskNotFoundError",
    "TaskNotPausedError",
    "GateMismatchError",
    "PAUSED_STATUS",
    "DEFAULT_REJECT_STATUS",
    "DEFAULT_REJECT_STATUS_DISMISS",
    "is_gate_enabled",
    "pause_at_gate",
    "approve",
    "rollback_resume_approval",
    "latest_approved_gate",
    "reject",
    "list_pending",
    "show_pending",
    "set_gate_enabled",
    "list_gates",
]
