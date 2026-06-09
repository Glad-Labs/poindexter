"""qa.aggregate — combine the qa.* rail reviews into the QA gate decision.

Atom-cutover #355. Reads the ``qa_rail_reviews`` channel, applies the
DB-configurable weighted-score + non-advisory-veto + threshold aggregation
(_qa_rail_common.aggregate_rail_reviews), and acts as the QA-decision point
the cross_model_qa stage used to be:

- APPROVE: emit qa_final_score / qa_final_verdict, promote
  quality_score = max(early, qa) and populate qa_reviews (read by
  finalize_task for the approval-UI feedback).
- REJECT: do the same DB writes the legacy stage did (via _qa_persist) —
  status=rejected + rejected-draft + model_performance + gate_history —
  then set _halt so build_graph_from_spec's halt-aware router short-circuits
  the graph (skipping the rest of the pipeline), mirroring the legacy
  continue_workflow=False.
"""

from __future__ import annotations

import logging
from typing import Any

from modules.content.atoms._qa_rail_common import (
    aggregate_rail_reviews,
    missing_required_gates,
    resolve_gate_states,
)
from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="qa.aggregate",
    type="atom",
    version="2.0.0",
    description="Combine qa.* rail reviews into the QA gate decision (+ reject persistence).",
    inputs=(FieldSpec(name="qa_rail_reviews", type="list[dict]", description="per-rail reviews"),),
    outputs=(
        FieldSpec(name="qa_final_score", type="float", description="weighted QA score"),
        FieldSpec(name="qa_final_verdict", type="str", description="approve|reject"),
        FieldSpec(name="quality_score", type="float", description="promoted max(early, qa)"),
        FieldSpec(name="qa_reviews", type="list[dict]", description="reviews for the approval UI"),
    ),
    requires=("qa_rail_reviews",),
    produces=("qa_final_score", "qa_final_verdict", "quality_score", "qa_reviews"),
    capability_tier=None,
    cost_class="free",
    idempotent=False,
    side_effects=("writes pipeline_tasks/pipeline_versions/pipeline_gate_history on reject",),
    parallelizable=False,
)


def _weight(config: Any, key: str, default: float) -> float:
    if config is None:
        return default
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        return default


async def run(state: dict[str, Any]) -> dict[str, Any]:
    # Seam 1 Wave 3e (#667): QA-weight config reads go through the capability
    # handle. ``platform`` is already threaded into atom state (Wave 3c, used
    # for audit below); None-tolerant — a missing handle falls the weights back
    # to their defaults, exactly as the prior ``site_config``-None seam did.
    _platform = state.get("platform")
    config = _platform.config if _platform is not None else None
    reviews = state.get("qa_rail_reviews") or []
    threshold = _weight(config, "qa_final_score_threshold", 70.0)
    # #661: qa.programmatic sets this flag when its ONLY critical was a
    # known_wrong_fact (the stale-regex false-positive on a real post-cutoff
    # product). When set, an approved web_factcheck review suppresses the
    # validator veto — restoring the legacy review() rescue so legit
    # post-cutoff content stops getting hard-rejected with no web second
    # opinion. This PREVENTS a wrong reject; it never introduces a new one.
    known_wrong_fact_only = bool(state.get("qa_known_wrong_fact_only"))
    result = aggregate_rail_reviews(
        reviews,
        validator_weight=_weight(config, "qa_validator_weight", 0.4),
        critic_weight=_weight(config, "qa_critic_weight", 0.6),
        gate_weight=_weight(config, "qa_gate_weight", 0.3),
        threshold=threshold,
        known_wrong_fact_only=known_wrong_fact_only,
    )
    final_score = result["qa_final_score"]
    approved = bool(result["approved"])

    # Vacuous-pass guard (poindexter#680): a required rail that emits NO review
    # still passes silently because aggregate_rail_reviews sees nothing to veto.
    # Load gate states and fail closed when any required-to-pass rail is absent.
    pool = getattr(state.get("database_service"), "pool", None)
    site_config = state.get("site_config")
    settings_service = state.get("settings_service")
    if pool is not None and site_config is not None and approved:
        try:
            from modules.content.multi_model_qa import MultiModelQA
            _qa = MultiModelQA(
                pool=pool, settings_service=settings_service,
                site_config=site_config, platform=state.get("platform"),
            )
            gate_states = await resolve_gate_states(_qa)
            # Alias-aware presence check (reviewer name != gate name for the
            # critic etc.) — see missing_required_gates. The prior raw
            # ``gate_name not in {r["reviewer"]}`` test rejected EVERY passing
            # post because the required ``llm_critic`` gate is emitted as
            # ``ollama_critic``.
            missing_required = missing_required_gates(reviews, gate_states)
            if missing_required:
                logger.warning(
                    "[qa.aggregate] required rail(s) produced no review — "
                    "failing closed: %s (task=%s)",
                    missing_required, str(state.get("task_id") or "?")[:8],
                )
                approved = False
                result = {
                    **result,
                    "approved": False,
                    "qa_final_verdict": "reject",
                    "vetoed_by": result.get("vetoed_by", []) + [
                        f"missing_required:{g}" for g in missing_required
                    ],
                }
        except Exception as _guard_err:  # noqa: BLE001
            logger.debug("[qa.aggregate] vacuous-pass guard skipped: %s", _guard_err)
    if result.get("known_wrong_fact_rescued"):
        logger.info(
            "[qa.aggregate] web fact-check rescued a known_wrong_fact-only "
            "validator rejection (task=%s) — the stale regex flagged a real "
            "post-cutoff product the web confirmed (#661)",
            str(state.get("task_id") or "?")[:8],
        )

    # Promote the canonical quality_score (max of early-eval + QA), mirroring
    # the legacy stage so downstream finalize_task / auto-publish use the QA score.
    early = 0.0
    try:
        early = float(state.get("quality_score") or 0.0)
    except (TypeError, ValueError):
        early = 0.0
    promoted = max(early, float(final_score))

    out: dict[str, Any] = {
        "qa_final_score": final_score,
        "qa_final_verdict": result["qa_final_verdict"],
        "quality_score": promoted,
        # qa_reviews uses an operator.add reducer; it's empty before this node
        # in canonical_blog (rails write qa_rail_reviews), so this populates it
        # for finalize_task's qa_feedback.
        "qa_reviews": list(reviews),
        "qa_rewrite_attempts": 0,
        # Surface veto reasons for callers and tests; empty list on approve.
        "vetoed_by": result.get("vetoed_by", []),
    }

    if not approved:
        from modules.content.atoms._qa_persist import (
            build_qa_feedback,
            build_reject_reason,
            persist_qa_reject,
        )
        reason = build_reject_reason(reviews, result["vetoed_by"], float(final_score))
        await persist_qa_reject(
            state.get("database_service"),
            task_id=str(state.get("task_id") or ""),
            reason=reason,
            final_score=float(final_score),
            content=str(state.get("content") or ""),
            title=str(state.get("title") or state.get("topic") or ""),
            qa_feedback=build_qa_feedback(reviews, float(final_score), approved=False),
            models_used_by_phase=state.get("models_used_by_phase") or {},
        )
        out["_halt"] = True
        out["_halt_reason"] = f"qa.aggregate: reject (score={final_score}, {reason[:120]})"
        # Belt-and-suspenders: the DB write above is load-bearing (status is
        # not a PipelineState channel), but set it in state too in case a
        # caller reads final_state.
        out["status"] = "rejected"

    # Bump the qa_gates run counters (total_runs / total_rejections) for
    # every rail that produced a review. The legacy cross_model_qa stage did
    # this via MultiModelQA.review() -> record_chain_run; #355 routed QA
    # through the qa.* atoms, which bypass review(), so the counters froze at
    # 0 and the operator dashboard showed every gate as last_run_at=NEVER
    # (poindexter#553). Fire it here so the counters reflect every
    # qa.aggregate pass (approve and reject). Best-effort — a telemetry
    # write must never break the pipeline.
    pool = getattr(state.get("database_service"), "pool", None)
    if pool is not None:
        try:
            from services.qa_gates_db_writer import record_chain_run
            await record_chain_run(pool, reviews)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[qa.aggregate] qa_gates counter update skipped: %s", exc)

    # One audit row per QA pass with the full reviewer breakdown. The
    # legacy MultiModelQA.review() emitted this; #355 bypassed review() so
    # the row froze — darkening the /d/qa-rails dashboard (its panels query
    # audit_log WHERE event_type='qa_pass_completed' and project details)
    # AND removing the "N passes" denominator the QaRailFullySkipped alert
    # needs (poindexter#553). Re-emit it here. Best-effort — a telemetry
    # write must never break the pipeline.
    try:
        # Seam 1 Wave 3c (#667) — audit through the capability handle. The
        # handle's write_bg preserves the fire-and-forget + #303 loud-on-reject
        # (severity='warning') behavior the raw audit_log_bg call had.
        _platform = state.get("platform")
        if _platform is not None:
            _platform.audit.write_bg(
                "qa_pass_completed",
                source="qa.aggregate",
                details={
                    "approved": approved,
                    "final_score": round(float(final_score), 2),
                    "approval_threshold": float(threshold),
                    "reviewer_count": len(reviews),
                    "reviews": [
                        {
                            "reviewer": r.get("reviewer"),
                            "provider": r.get("provider"),
                            "approved": bool(r.get("approved")),
                            "score": round(float(r.get("score") or 0.0), 2),
                            "advisory": bool(r.get("advisory")),
                            # First 200 chars for debugging; full text in logs.
                            "feedback_preview": (str(r.get("feedback") or ""))[:200],
                        }
                        for r in reviews
                    ],
                },
                task_id=(str(state.get("task_id")) or None) if state.get("task_id") else None,
                severity="info" if approved else "warning",
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[qa.aggregate] qa_pass_completed audit skipped: %s", exc)

    return out


__all__ = ["ATOM_META", "run"]
