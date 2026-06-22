"""content.evaluate_auto_publish — auto-publish gate evaluation.

Extracted from FinalizeTaskStage. Calls auto_publish_gate.evaluate and
surfaces the decision dict on the pipeline state for observability.

Idempotent — observe-only by default (dry_run=true until edit-distance
track record exists per feedback_auto_publish_requires_edit_distance_track_record).

Produces: auto_publish_gate (dict).

Issue: Glad-Labs/poindexter#362.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.evaluate_auto_publish",
    type="atom",
    version="1.0.0",
    description=(
        "Evaluate the auto-publish gate (observe-only by default via dry_run). "
        "Logs audit entry via platform handle. Produces auto_publish_gate dict. "
        "As the graph's terminal node, re-asserts the awaiting_approval status "
        "(guarded) so the graph is authoritative about its own end state on "
        "every exit path (preview_gate approve-resume fix)."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="pipeline task id"),
        FieldSpec(name="quality_score", type="float", description="final quality score", required=False),
        FieldSpec(name="niche_slug", type="str", description="niche slug", required=False),
        FieldSpec(name="category", type="str", description="content category", required=False),
        FieldSpec(name="database_service", type="object", description="DB service"),
        FieldSpec(name="platform", type="object", description="capability handle", required=False),
    ),
    outputs=(
        FieldSpec(name="auto_publish_gate", type="dict", description="{would_fire, dry_run, gate_state, reason}"),
    ),
    requires=("task_id",),
    produces=("auto_publish_gate",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("db_read", "db_write"),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


# Mid-graph working statuses this terminal node may finalize FROM. A task the
# forward path already auto-published ('published'/'approved') or a QA gate
# already hard-rejected ('rejected'/'failed'/'cancelled') is NOT in this set, so
# the guarded write below leaves it untouched — the graph's terminal node never
# reverts an already-decided task.
_FINALIZE_ALLOWED_FROM = ("in_progress", "awaiting_gate")


async def _finalize_awaiting_approval(database_service: Any, task_id: Any) -> None:
    """Re-assert the terminal ``awaiting_approval`` status (graph-authoritative).

    ``content.persist_task`` set ``awaiting_approval`` earlier in the graph, but
    when ``preview_gate`` is enabled the HITL pause overwrites it
    (``awaiting_gate`` via ``approval_service.pause_at_gate``) and the operator
    approve cycle leaves it at ``in_progress`` before resuming the graph
    (``approval_service.approve``). The CLI/MCP resume path does NOT run
    ``services.post_pipeline_actions``, so without this the approve-resumed task
    would END at ``in_progress`` and the stale-inprogress sweep
    (``tasks_db.sweep_stale_tasks``) would later reset it to ``pending`` and
    silently re-run an already-approved post (live incident 2026-06-22).

    Making the graph's terminal node finalize the status keeps the end state
    correct for every caller (CLI resume / MCP approve / forward Prefect flow).
    The write is guarded (``update_task_status_guarded``) to the mid-graph
    working statuses only, so a task the forward path already auto-published or a
    QA gate already rejected is never reverted; a ``None`` return (already
    terminal, or already ``awaiting_approval`` on the forward path) is a benign
    no-op here — NOT the fatal race it is in ``content.persist_task``.
    """
    guarded = getattr(database_service, "update_task_status_guarded", None)
    if guarded is None:
        logger.warning(
            "[content.evaluate_auto_publish] db has no update_task_status_guarded "
            "— cannot finalize terminal status for task %s",
            task_id,
        )
        return
    try:
        prev = await guarded(
            task_id=str(task_id),
            new_status="awaiting_approval",
            allowed_from=_FINALIZE_ALLOWED_FROM,
        )
    except Exception as exc:  # noqa: BLE001 — a terminal node must not fail the graph
        logger.warning(
            "[content.evaluate_auto_publish] terminal status finalize raised for "
            "task %s (row may stay in_progress; stale-sweep will re-run it): %s",
            task_id,
            exc,
        )
        return
    if prev is None:
        logger.debug(
            "[content.evaluate_auto_publish] status finalize no-op for task %s "
            "(already terminal or awaiting_approval)",
            task_id,
        )
    elif prev != "awaiting_approval":
        logger.info(
            "[content.evaluate_auto_publish] finalized task %s status %s -> "
            "awaiting_approval (graph-authoritative terminal node)",
            task_id,
            prev,
        )


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the auto-publish gate and return the decision."""
    task_id = state.get("task_id")
    database_service = state.get("database_service")
    if not task_id or database_service is None:
        return {}

    category = state.get("category", "")
    quality_score = float(state.get("quality_score") or 0)
    niche_slug = state.get("niche_slug") or state.get("niche")
    platform = state.get("platform")

    gate_decision = None
    try:
        from modules.content.auto_publish_gate import evaluate as _gate_check
        db_pool = getattr(database_service, "pool", None)
        gate_decision = await _gate_check(
            db_pool,
            task_id=str(task_id),
            niche_slug=niche_slug,
            category=category,
            quality_score=quality_score,
            platform=platform,
            # Self-heal-before-paging: a flagged draft never auto-publishes.
            qa_flagged=bool(state.get("qa_flagged")),
        )
        if platform is not None:
            platform.audit.write_bg(
                "auto_publish_gate",
                source="content.evaluate_auto_publish",
                details={
                    "would_fire": gate_decision.would_fire,
                    "dry_run": gate_decision.dry_run,
                    "gate_state": gate_decision.gate_state,
                    "reason": gate_decision.reason,
                    "quality_score": gate_decision.quality_score,
                    "threshold": gate_decision.threshold,
                    "trailing_clean_runs": gate_decision.trailing_clean_runs,
                    "required_clean_runs": gate_decision.required_clean_runs,
                },
                task_id=task_id,
                severity="info",
            )
        logger.info(
            "[content.evaluate_auto_publish] state=%s would_fire=%s dry_run=%s reason=%s",
            gate_decision.gate_state, gate_decision.would_fire,
            gate_decision.dry_run, gate_decision.reason,
        )
    except Exception as _gate_err:
        # Silent-failure audit H2a: a throwing gate evaluation previously
        # logged at DEBUG and vanished. The pipeline falls through to the
        # default observe-only decision (auto_publish_gate=None below) so no
        # unsafe publish can occur — but the gate is not actually evaluating,
        # and given the 2026-05-26 unauthorized-publish incident that blind
        # spot deserves a signal. Promote to WARNING + emit a finding
        # (severity=warning routes to the operator via FindingsAlertRouterJob).
        logger.warning(
            "[content.evaluate_auto_publish] gate eval failed (non-fatal): %s",
            _gate_err,
            exc_info=True,
        )
        try:
            from utils.findings import emit_finding

            emit_finding(
                source="content.evaluate_auto_publish",
                kind="auto_publish_gate_eval_failed",
                severity="warning",
                title=(
                    f"Auto-publish gate evaluation raised "
                    f"{type(_gate_err).__name__} for task {str(task_id)[:8]}"
                ),
                body=(
                    f"The auto-publish gate evaluation failed for task "
                    f"{task_id}. The pipeline fell through to the default "
                    f"observe-only decision (no unsafe publish), but the gate "
                    f"is not evaluating. Error: {_gate_err!r}. Investigate "
                    f"modules/content/auto_publish_gate.evaluate."
                ),
                dedup_key=f"auto_publish_gate_eval_failed_{type(_gate_err).__name__}",
            )
        except Exception:
            # emit_finding is best-effort; never let observability failure
            # break the finalize path.
            pass

    # Terminal node: re-assert awaiting_approval so the graph is authoritative
    # about its own end state (preview_gate approve-resume fix, 2026-06-22).
    # Without this the CLI/MCP resume path — which does NOT run
    # post_pipeline_actions — leaves an approved task at in_progress and the
    # stale-inprogress sweep re-runs it. See _finalize_awaiting_approval.
    await _finalize_awaiting_approval(database_service, task_id)

    return {
        "auto_publish_gate": (
            {
                "would_fire": gate_decision.would_fire,
                "dry_run": gate_decision.dry_run,
                "gate_state": gate_decision.gate_state,
                "reason": gate_decision.reason,
            } if gate_decision else None
        ),
    }


__all__ = ["ATOM_META", "run"]
