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
        "Logs audit entry via platform handle. Produces auto_publish_gate dict."
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
    side_effects=("db_read",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
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
