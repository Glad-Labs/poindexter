"""Reject-path persistence for qa.aggregate (atom-cutover Plan 5, #355).

When qa.aggregate rejects a draft it must replicate the DB writes the
legacy cross_model_qa stage did (services/stages/cross_model_qa.py:460-556),
because `status` is not a PipelineState channel and the caller does no DB
re-read — the rejected state lives ONLY in the DB. Underscore-prefixed so
the atom registry skips it (helper, not an atom).

Every write is best-effort: a telemetry/version hiccup must not crash the
pipeline. update_task (the load-bearing status write) runs first; the rest
are wrapped individually like the legacy stage.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_reject_reason(reviews: list[dict[str, Any]], vetoed_by: list[Any], final_score: float) -> str:
    """Human-readable rejection reason from the failing rails."""
    failing = [r for r in reviews if not r.get("approved")]
    parts = [
        f"{r.get('reviewer')}: {str(r.get('feedback') or '').strip()[:200]}"
        for r in failing
    ] or [f"vetoed_by={list(vetoed_by)}"]
    return f"QA rejected (score {final_score:.0f}/100). " + "; ".join(parts)


def build_qa_feedback(reviews: list[dict[str, Any]], final_score: float, approved: bool) -> str:
    """Operator-facing QA feedback text (mirrors MultiModelResult.format_feedback_text)."""
    header = f"Final score: {final_score:.0f}/100 ({'APPROVED' if approved else 'REJECTED'})"
    lines = [header]
    for r in reviews:
        status = "pass" if r.get("approved") else "FAIL"
        fb = str(r.get("feedback") or "").strip() or "(no feedback)"
        lines.append(
            f"- {r.get('reviewer')} [{r.get('provider')}] "
            f"{float(r.get('score') or 0):.0f}/100 {status}: {fb}"
        )
    return "\n".join(lines)


async def persist_qa_reject(
    database_service: Any,
    *,
    task_id: str,
    reason: str,
    final_score: float,
    content: str,
    title: str,
    qa_feedback: str,
    models_used_by_phase: dict[str, Any],
) -> None:
    """Replicate the legacy cross_model_qa reject DB writes. Best-effort.

    1. pipeline_tasks: status=rejected + quality_score (load-bearing).
    2. pipeline_versions: persist the rejected draft (#473).
    3. model_performance.human_approved=False (learning signal).
    4. pipeline_gate_history: 'rejected' row (Grafana approval_status).
    """
    if database_service is None or not task_id:
        return

    # 1. Status write — the load-bearing one. If THIS fails, log loud.
    try:
        await database_service.update_task(task_id, {
            "status": "rejected",
            "error_message": reason,
            "quality_score": float(final_score),
        })
    except Exception as exc:  # noqa: BLE001
        logger.error("[qa.aggregate] reject status write failed for %s: %s", task_id[:8], exc)

    # 2. Rejected draft.
    try:
        from services.pipeline_db import PipelineDB
        await PipelineDB(database_service.pool).upsert_version(task_id, {
            "title": title,
            "content": content,
            "quality_score": int(round(float(final_score))),
            "qa_feedback": qa_feedback,
            "models_used_by_phase": models_used_by_phase or {},
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[qa.aggregate] pipeline_versions write failed for %s: %s", task_id[:8], exc)

    # 3. Learning signal.
    try:
        await database_service.mark_model_performance_outcome(task_id, human_approved=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[qa.aggregate] mark_model_performance_outcome failed: %s", exc)

    # 4. Gate-history row.
    try:
        await database_service.pool.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, gate_name, event_kind, feedback, actor, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            task_id, "multi_model_qa", "rejected", reason[:2000], "multi_model_qa",
            json.dumps({"reviewer": "multi_model_qa", "decision": "rejected"}, default=str),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[qa.aggregate] pipeline_gate_history write failed for %s: %s", task_id[:8], exc)


__all__ = ["build_qa_feedback", "build_reject_reason", "persist_qa_reject"]
