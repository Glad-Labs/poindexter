"""QA-decision persistence for qa.aggregate (atom-cutover Plan 5, #355).

When qa.aggregate rejects a draft it must replicate the DB writes the
legacy cross_model_qa stage did (services/stages/cross_model_qa.py:460-556),
because `status` is not a PipelineState channel and the caller does no DB
re-read — the rejected state lives ONLY in the DB. Underscore-prefixed so
the atom registry skips it (helper, not an atom).

2026-07-03 (stale-reclaim last-run-wins incident): two additions —

- :func:`persist_qa_approved_snapshot` makes the APPROVE decision durable
  the moment qa.aggregate makes it. Before this, the approved draft lived
  only in the LangGraph checkpoint until ``content.persist_task`` (~7 nodes
  later, past the SEO/media block where the overnight Docker/WSL crashes
  hit); the stale sweep then cleared the checkpoint and the re-run
  regenerated from scratch — usually worse, and last-run-wins overwrote the
  approved version. The snapshot rides ``pipeline_versions.stage_data``
  under the ``qa_approved_snapshot`` key, which the stale sweep
  (``tasks_db.sweep_stale_tasks``) and the flow crash handler use to promote
  the task to ``awaiting_approval`` instead of re-running it.
- :func:`persist_qa_reject` now refuses to overwrite a version that carries
  that marker: a re-run whose fresh draft rejects restores the task to
  ``awaiting_approval`` with the stored approved draft (keep-best across
  runs, per feedback_iterate_with_qa_not_oneshot). Operator rejection clears
  the marker (``PipelineDB.clear_qa_approved_snapshot``), so content a human
  explicitly threw away is never resurrected.

Every write is best-effort: a telemetry/version hiccup must not crash the
pipeline. update_task (the load-bearing status write) runs first; the rest
are wrapped individually like the legacy stage.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


async def _read_qa_approved_marker(database_service: Any, task_id: str) -> dict[str, Any] | None:
    """Return ``{"quality_score": ...}`` when the task's pipeline_versions row
    carries the ``qa_approved_snapshot`` marker, else None. Best-effort: any
    read failure returns None (keep-best is protection, not a gate — a blip
    here degrades to the pre-2026-07 reject behavior)."""
    try:
        row = await database_service.pool.fetchrow(
            """
            SELECT quality_score,
                   (stage_data ? 'qa_approved_snapshot') AS has_qa_approved_snapshot
            FROM pipeline_versions
            WHERE task_id = $1 AND version = 1
            """,
            task_id,
        )
    except Exception as exc:  # noqa: BLE001 — degrade to normal reject
        logger.warning(
            "[qa.aggregate] qa_approved_snapshot probe failed for %s "
            "(keep-best guard skipped this run): %s", task_id[:8], exc,
        )
        return None
    if row and dict(row).get("has_qa_approved_snapshot"):
        return {"quality_score": dict(row).get("quality_score")}
    return None


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
) -> str | None:
    """Replicate the legacy cross_model_qa reject DB writes. Best-effort.

    1. pipeline_tasks: status=rejected + quality_score (load-bearing).
    2. pipeline_versions: persist the rejected draft (#473).
    3. model_performance.human_approved=False (learning signal).
    4. pipeline_gate_history: 'rejected' row (Grafana approval_status).

    Keep-best guard (2026-07-03): when the stored version carries the
    ``qa_approved_snapshot`` marker, this run is a re-run of a task whose
    earlier draft already PASSED QA (crash-after-approve recovery). The
    reject then persists nothing over it — the task is restored to
    ``awaiting_approval`` at the stored approved score and the fresh
    (rejected) draft is discarded. Returns ``"kept_qa_approved"`` in that
    case, ``"rejected"`` on the normal path, ``None`` on the no-op path.
    """
    if database_service is None or not task_id:
        return None

    marker = await _read_qa_approved_marker(database_service, task_id)
    if marker is not None:
        approved_score = float(marker.get("quality_score") or 0.0)
        logger.warning(
            "[qa.aggregate] keep-best: task %s re-run rejected at %.1f but an "
            "earlier QA-APPROVED draft (score %.1f) is stored in "
            "pipeline_versions — restoring awaiting_approval instead of "
            "overwriting it", task_id[:8], float(final_score), approved_score,
        )
        try:
            await database_service.update_task(task_id, {
                "status": "awaiting_approval",
                "error_message": None,
                "quality_score": approved_score,
            })
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[qa.aggregate] keep-best restore write failed for %s: %s",
                task_id[:8], exc,
            )
        try:
            await database_service.pool.execute(
                """
                INSERT INTO pipeline_gate_history
                    (task_id, gate_name, event_kind, feedback, actor, metadata)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                task_id, "multi_model_qa", "kept_qa_approved",
                (
                    f"Re-run rejected at {final_score:.0f} — kept the stored "
                    f"QA-approved draft (score {approved_score:.0f}). {reason}"
                )[:2000],
                "multi_model_qa",
                json.dumps({
                    "reviewer": "multi_model_qa",
                    "decision": "kept_qa_approved",
                    "rerun_score": float(final_score),
                    "approved_score": approved_score,
                }, default=str),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[qa.aggregate] keep-best gate_history write failed for %s: %s",
                task_id[:8], exc,
            )
        emit_finding(
            source="qa.aggregate",
            kind="qa_rerun_kept_approved_draft",
            severity="warn",
            title="Re-run rejected — kept the earlier QA-approved draft",
            body=(
                f"Task {task_id[:8]}: a re-run (typically a stale-sweep recovery "
                f"after a crash) produced a draft QA rejected at "
                f"{final_score:.0f}, but pipeline_versions already holds a "
                f"QA-approved draft (score {approved_score:.0f}) from an earlier "
                f"run. The approved draft was kept and the task restored to "
                f"awaiting_approval; the worse re-run draft was discarded."
            ),
            dedup_key=f"qa-rerun-kept-approved:{task_id}",
            extra={
                "task_id": task_id,
                "rerun_score": float(final_score),
                "approved_score": approved_score,
            },
        )
        return "kept_qa_approved"

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
        # The warning above is below the GlitchTip ERROR gate, so a systematic
        # failure here (schema drift, pool exhaustion) would otherwise be
        # invisible. The rejected draft is the operator's only record of WHAT
        # was rejected — losing it silently means rejects pile up with no
        # reviewable artifact. emit_finding routes to the Findings dashboard +
        # Discord (dedup_key collapses a sustained failure to one alert). It is
        # fire-and-forget and never raises, so the reject path stays intact.
        emit_finding(
            source="qa.aggregate",
            kind="qa_reject_version_persist_failed",
            severity="warn",
            title="QA reject: rejected draft not archived",
            body=(
                f"persist_qa_reject could not write the rejected draft for task "
                f"{task_id[:8]} to pipeline_versions: {describe_exception(exc)}. "
                f"The reject still applied (status=rejected), but the draft + QA "
                f"feedback were not saved for review/learning."
            ),
            dedup_key="qa_reject_version_persist_failed",
        )

    # 3. Learning signal.
    try:
        await database_service.mark_model_performance_outcome(task_id, human_approved=False)
    except Exception as exc:  # noqa: BLE001
        # Matches the pipeline_versions write directly above, which already
        # emits a finding on failure — this sibling was the one left silent.
        # Losing the negative outcome means the model that produced the
        # rejected draft keeps its score and gets selected again.
        # (emit_finding is imported at module level here — do NOT add a
        # function-local import, it would make the name local to this whole
        # function and UnboundLocalError the two earlier uses above.)
        logger.warning(
            "[qa.aggregate] mark_model_performance_outcome failed for %s "
            "(%s: %s)",
            task_id[:8], type(exc).__name__, exc,
        )
        emit_finding(
            source="qa.aggregate",
            kind="model_performance_outcome_write_failed",
            title="QA reject never reached the router's learning signal",
            body=(
                f"mark_model_performance_outcome raised {type(exc).__name__}: "
                f"{describe_exception(exc)} for task {task_id[:8]}. The reject applied, but the "
                f"model that produced the draft keeps its prior score and can "
                f"be selected again for the same work."
            ),
            dedup_key="qa_model_performance_outcome_write_failed",
        )

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

    return "rejected"


async def persist_qa_approved_snapshot(
    database_service: Any,
    *,
    task_id: str,
    title: str,
    content: str,
    final_score: float,
    qa_feedback: str,
    models_used_by_phase: dict[str, Any],
    featured_image_url: str | None = None,
) -> None:
    """Durably persist the QA-APPROVED draft the moment qa.aggregate approves.

    Writes the approved body + score to ``pipeline_versions`` with the
    ``qa_approved_snapshot`` stage_data marker. ``content.persist_task``
    overwrites the columns with the final (SEO'd) values ~7 nodes later on a
    healthy run; the marker itself survives that upsert (stage_data is a
    shallow JSONB merge). If the run crashes in between — the observed
    overnight window — the stale sweep / flow crash handler see the marker and
    promote the task to ``awaiting_approval`` instead of regenerating.

    Best-effort: a failure here must never block the approve (the healthy
    path still persists at finalize), but it removes the crash protection,
    so it emits a warn finding.
    """
    if database_service is None or not task_id or not content:
        return
    try:
        from services.pipeline_db import PipelineDB
        await PipelineDB(database_service.pool).upsert_version(task_id, {
            "title": title,
            "content": content,
            "featured_image_url": featured_image_url,
            "quality_score": int(round(float(final_score))),
            "qa_feedback": qa_feedback,
            "models_used_by_phase": models_used_by_phase or {},
            "qa_approved_snapshot": {
                "score": float(final_score),
                "approved_at": datetime.now(timezone.utc).isoformat(),
            },
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[qa.aggregate] approved-snapshot write failed for %s: %s",
            task_id[:8], exc,
        )
        emit_finding(
            source="qa.aggregate",
            kind="qa_approved_snapshot_persist_failed",
            severity="warn",
            title="QA approve: durable snapshot not written",
            body=(
                f"persist_qa_approved_snapshot could not write the approved "
                f"draft for task {task_id[:8]} to pipeline_versions: "
                f"{describe_exception(exc)}. The approve still proceeds, but "
                f"a crash before content.persist_task would lose this draft to "
                f"a from-scratch re-run (the 2026-07-03 last-run-wins class)."
            ),
            dedup_key="qa_approved_snapshot_persist_failed",
        )


__all__ = [
    "build_qa_feedback",
    "build_reject_reason",
    "persist_qa_approved_snapshot",
    "persist_qa_reject",
]
