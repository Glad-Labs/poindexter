"""CrossModelQAStage — multi-model QA review + iterative rewrite loop.

Stage 3.5 + 3.7 in the pre-refactor pipeline. The ~360-line inline
block in content_router_service.py moves here. Preserves observable
behavior:

- Reads ``pipeline_stages.cross_model_qa`` as the transitional enable
  gate (legacy table; the runner's ``plugin.stage.cross_model_qa.enabled``
  also applies once pipeline_stages → plugin.stage.* migration lands).
- Instantiates ``MultiModelQA`` with the DB-backed settings service so
  per-reviewer weights + thresholds are loaded from ``app_settings``.
- Runs up to ``qa_max_rewrites`` (default 2) rewrite attempts when QA
  rejects for fixable issues. Topic-delivery failures bail early —
  those can't be fixed with targeted edits.
- Issue aggregation: ValidationResult critical issues + non-approving
  reviewer feedback, capped at 30 lines. Warnings are advisory and
  don't trigger a rewrite on their own.
- Writer fallback: if primary writer returns <50% of expected length
  (thinking-mode models burning token budget on <think> tags), fall
  back to the model in ``qa_fallback_writer_model`` (default ``gemma3:27b``).
- Rejected content short-circuits the pipeline: stage returns
  ``continue_workflow=False`` with ``status=rejected``. StageRunner halts,
  orchestrator reads ``halted_at`` and exits before SEO / finalize.

## Context reads

- ``task_id``, ``topic``, ``content``, ``seo_title`` (optional),
  ``research_context`` (optional), ``quality_result`` (from early eval),
  ``quality_score`` (optional; merged via max with multi-model score)
- ``database_service`` — needed for the pool + update_task + log_cost
- ``_pool`` — convenience alias read first if present

## Context writes

- ``qa_final_score`` (float)
- ``qa_reviews`` (list of per-reviewer dicts)
- ``qa_rewrite_attempts`` (int)
- ``quality_score`` (float, max of early + multi-model)
- ``content`` (str, possibly rewritten)
- On rejection: ``status = "rejected"`` + short-circuit

## Why halts_on_failure=False

We want ``ok=True`` + ``continue_workflow=False`` when the content is
legitimately rejected — it's not a stage failure, it's a business
outcome. halts_on_failure is only checked when ``ok=False``, and we
never return that.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.stage import StageResult
from services.integrations.operator_notify import notify_operator
from services.llm_providers.dispatcher import resolve_tier_model
from services.prompt_manager import get_prompt_manager

logger = logging.getLogger(__name__)


async def _resolve_writer_model(
    *,
    pool: Any,
    settings_service: Any,
    setting_key: str,
    site: str,
) -> str | None:
    """Resolve a writer model via cost-tier API + per-call-site fallback.

    Lane B sweep migration. Order:
    1. ``resolve_tier_model(pool, "standard")`` — operator-tuned tier mapping.
    2. ``app_settings[setting_key]`` (e.g. ``pipeline_writer_model``,
       ``qa_fallback_writer_model``) — per-call-site backstop.
    3. Returns ``None`` after notify_operator — caller decides whether to
       give up. Per feedback_no_silent_defaults.md, missing config does
       not silently fall back to a hardcoded literal.
    """
    if pool is not None:
        try:
            return await resolve_tier_model(pool, "standard")
        except (RuntimeError, ValueError, AttributeError) as exc:
            tier_exc: Exception | None = exc
        else:
            tier_exc = None
    else:
        tier_exc = RuntimeError("no asyncpg pool available")

    fallback: str | None = None
    if settings_service is not None:
        try:
            fallback = await settings_service.get(setting_key)
        except Exception:
            fallback = None
    if fallback:
        await notify_operator(
            f"qa rewrite ({site}): cost_tier='standard' resolution failed "
            f"({tier_exc}); falling back to {setting_key}={fallback!r}",
            critical=False,
        )
        return str(fallback)

    await notify_operator(
        f"qa rewrite ({site}): cost_tier='standard' has no model AND "
        f"{setting_key} is empty — rewrite skipped: {tier_exc}",
        critical=True,
    )
    return None


# ---------------------------------------------------------------------------
# Issue aggregator (lifted to module scope for testability)
# ---------------------------------------------------------------------------


def aggregate_issues_to_fix(qa_result: Any) -> tuple[str, bool]:
    """Collect every flagged issue into a structured list for rewrite.

    Returns ``(issues_text, has_blocking_issue)``. ``has_blocking_issue``
    is True when at least one issue actually blocks approval — only
    then is a rewrite warranted. Harmless validator warnings on
    otherwise-approved posts don't burn LLM cycles.
    """
    lines: list[str] = []
    has_blocking = False

    # Programmatic validator — critical issues block; warnings are advisory.
    try:
        vr = qa_result.validation
        if vr is not None and vr.issues:
            for issue in vr.issues:
                lines.append(f"[{issue.severity}] {issue.category}: {issue.description}")
                if issue.severity == "critical":
                    has_blocking = True
    except Exception:
        pass

    # Reviewers — a non-approving reviewer blocks; borderline approvals
    # (score < 75) are advisory.
    for r in qa_result.reviews:
        if r.reviewer == "programmatic_validator":
            continue  # already surfaced via validation above
        if r.approved and r.score >= 75:
            continue
        severity = "critical" if not r.approved else "warning"
        lines.append(f"[{severity}] {r.reviewer}: {r.feedback}")
        if not r.approved:
            has_blocking = True

    return "\n".join(lines[:30]), has_blocking


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class CrossModelQAStage:
    name = "cross_model_qa"
    description = "Multi-model QA review with iterative rewrite on rejection"
    # Up to qa_max_rewrites × ~300s each + QA overhead.
    timeout_seconds = 1800
    halts_on_failure = False  # See module docstring on why.

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.audit_log import audit_log_bg
        from services.multi_model_qa import MultiModelQA
        from services.text_utils import normalize_text as _normalize_text

        database_service = context.get("database_service")
        task_id = context.get("task_id", "")
        topic = context.get("topic", "")
        content_text = context.get("content", "")
        quality_result = context.get("quality_result")

        if not database_service or not task_id or quality_result is None:
            return StageResult(
                ok=False,
                detail="context missing database_service / task_id / quality_result",
            )

        pool = getattr(database_service, "pool", None)

        # Enable-gate now lives on ``plugin.stage.cross_model_qa.enabled``
        # in app_settings — StageRunner handles it before we even get here.

        # Pull settings_service from context (Phase G1 — no container singleton).
        # The orchestrator threads it through as ``context["settings_service"]``
        # from the worker's lifespan. Falls back to the legacy container lookup
        # for callers that don't yet populate context (route-direct invocations
        # during the transition window).
        settings_service = context.get("settings_service")
        if settings_service is None:
            try:
                from services.container import get_service as _get_service
                settings_service = _get_service("settings")
            except Exception:
                settings_service = None

        # DETERMINISTIC_COMPOSITOR niches bypass cross-model QA entirely.
        # The compositor renders the post deterministically from the
        # context_bundle (PR title → H2, PR body verbatim → section text,
        # commit list, fixed footer). There is nothing for QA to improve;
        # any "fix" via the LLM rewrite path would replace verifiable
        # source-of-truth output with hallucinated freestyle content.
        # Pass straight through to the operator approval gate. Closes the
        # 2026-05-04 dev_diary fabrication saga where every QA-rewrite
        # turn produced "kbir-dev/daily-diary" / "Marek Rosa" tutorial
        # text regardless of writer model.
        try:
            gen_metrics = (context.get("generate_metrics") or {})
            wmode = str(gen_metrics.get("writer_rag_mode") or "").upper()
            logger.info(
                "[MULTI_QA] task %s context keys=%s generate_metrics keys=%s writer_rag_mode=%r",
                task_id[:8],
                sorted(list(context.keys()))[:20],
                sorted(list(gen_metrics.keys())) if isinstance(gen_metrics, dict) else type(gen_metrics).__name__,
                wmode,
            )
            if wmode == "DETERMINISTIC_COMPOSITOR":
                logger.info(
                    "[MULTI_QA] Skipping QA for DETERMINISTIC_COMPOSITOR task %s — "
                    "deterministic output, no rewrite needed",
                    task_id[:8],
                )
                return StageResult(
                    ok=True,
                    detail="skipped — DETERMINISTIC_COMPOSITOR (no rewrite, no veto)",
                    context_updates={
                        "qa_final_score": float(quality_result.overall_score),
                        "quality_score": float(quality_result.overall_score),
                        "qa_reviews": [{
                            "reviewer": "compositor_skip",
                            "score": float(quality_result.overall_score),
                            "approved": True,
                            "feedback": "deterministic compositor output — QA bypassed",
                            "provider": "none",
                        }],
                        "qa_rewrite_attempts": 0,
                        "content": content_text,
                    },
                    metrics={"final_score": float(quality_result.overall_score)},
                )
        except Exception as exc:
            logger.warning(
                "[MULTI_QA] DETERMINISTIC_COMPOSITOR check failed: %s — running QA normally",
                exc,
            )

        qa = MultiModelQA(pool=pool, settings_service=settings_service)

        max_rewrites = await _resolve_max_rewrites(settings_service, default=2)

        qa_result = None
        rewrite_attempts = 0
        while True:
            qa_result = await qa.review(
                title=_normalize_text(context.get("seo_title", topic)),
                content=_normalize_text(content_text),
                topic=topic,
                research_sources=context.get("research_context"),
            )
            if qa_result is None:
                break

            issues_to_fix, has_blocking = aggregate_issues_to_fix(qa_result)

            if qa_result.approved and not has_blocking:
                break

            # Topic-delivery failure → can't fix with targeted edits, bail.
            topic_delivery_failed = any(
                (not r.approved) and r.reviewer == "topic_delivery"
                for r in qa_result.reviews
            )
            if topic_delivery_failed:
                break

            if rewrite_attempts >= max_rewrites:
                break
            if not issues_to_fix:
                break

            issue_count = issues_to_fix.count("\n") + 1
            logger.warning(
                "[QA_REWRITE] Task %s: %d issues flagged, attempting aggregate rewrite (%d/%d)",
                task_id[:8], issue_count, rewrite_attempts + 1, max_rewrites,
            )
            audit_log_bg(
                "rewrite_decision", "content_router",
                {
                    "event": "rewrite_started",
                    "attempt": rewrite_attempts + 1,
                    "max_attempts": max_rewrites,
                    "issue_count": issue_count,
                    "issues_sample": issues_to_fix[:500],
                    "prior_score": float(qa_result.final_score),
                },
                task_id=task_id, severity="info",
            )

            revised = await _rewrite_draft(
                content_text=content_text,
                title=context.get("seo_title", topic),
                issues_to_fix=issues_to_fix,
                settings_service=settings_service,
                task_id=task_id,
                attempt=rewrite_attempts + 1,
                site_config=context.get("site_config"),
            )
            if revised is None:
                break

            content_text = revised
            await database_service.update_task(task_id, {"content": content_text})
            # Snapshot the rewrite so the feedback loop sees which QA issues
            # the model actually addressed between revisions (gitea#271 Phase 3.A2).
            try:
                from services.content_revisions_logger import log_revision
                # DI seam (glad-labs-stack#330)
                _sc_rev = context.get("site_config")
                _writer_model = (
                    _sc_rev.get("qa_writer_model") if _sc_rev is not None else None
                ) or "writer"
                await log_revision(
                    database_service.pool,
                    task_id=task_id,
                    content=content_text,
                    title=context.get("seo_title") or context.get("title") or topic,
                    change_type="qa_rewrite",
                    change_summary=(
                        f"Rewrite attempt {rewrite_attempts + 1} addressing "
                        f"{len(issues_to_fix)} QA issues"
                    ),
                    model_used=_writer_model,
                    quality_score=None,
                )
            except Exception as rev_err:
                logger.debug("[content_revisions] qa_rewrite snapshot failed: %s", rev_err)
            logger.info(
                "[QA_REWRITE] Task %s: rewrite succeeded (%d chars), re-running QA",
                task_id[:8], len(content_text),
            )
            rewrite_attempts += 1

        # ---------------- Result aggregation ----------------
        if qa_result is None:
            logger.warning(
                "Cross-model QA timed out for task %s — using early QA score",
                task_id[:8],
            )
            return StageResult(
                ok=True,
                detail="qa timed out; using early score",
                context_updates={
                    "qa_final_score": quality_result.overall_score,
                    "qa_reviews": [{
                        "reviewer": "timeout", "score": 0, "approved": True,
                        "feedback": "QA stage timed out — skipped",
                        "provider": "none",
                    }],
                    "content": content_text,
                },
            )

        qa_reviews_dicts = [
            {
                "reviewer": r.reviewer, "score": r.score, "approved": r.approved,
                "feedback": r.feedback, "provider": r.provider,
            }
            for r in qa_result.reviews
        ]

        # Promote multi-model score to canonical quality_score (max).
        current_quality = float(context.get("quality_score", 0) or 0)
        promoted_score = max(current_quality, float(qa_result.final_score))

        updates: dict[str, Any] = {
            "qa_final_score": qa_result.final_score,
            "quality_score": promoted_score,
            "qa_reviews": qa_reviews_dicts,
            "qa_rewrite_attempts": rewrite_attempts,
            "content": content_text,
        }

        # Per-reviewer audit rows.
        for r in qa_result.reviews:
            audit_log_bg(
                "qa_decision", "multi_model_qa",
                {
                    "reviewer": r.reviewer,
                    "provider": r.provider,
                    "score": float(r.score),
                    "approved": bool(r.approved),
                    "feedback": r.feedback[:500],
                    "stage": "multi_model_qa",
                    "rewrite_attempts_so_far": rewrite_attempts,
                },
                task_id=task_id,
                severity="info" if r.approved else "warning",
            )
        audit_log_bg(
            "qa_aggregate", "multi_model_qa",
            {
                "final_score": float(qa_result.final_score),
                "approved": bool(qa_result.approved),
                "reviewer_count": len(qa_result.reviews),
                "failed_reviewers": [
                    r.reviewer for r in qa_result.reviews if not r.approved
                ],
                "rewrite_attempts": rewrite_attempts,
            },
            task_id=task_id,
            severity="info" if qa_result.approved else "warning",
        )

        # Cost logging.
        cost_log = getattr(qa_result, "cost_log", None)
        if cost_log:
            try:
                cost_log["task_id"] = task_id
                await database_service.log_cost(cost_log)
                logger.info(
                    "QA cost logged: $%.4f (%s/%s)",
                    cost_log["cost_usd"], cost_log["provider"], cost_log["model"],
                )
                audit_log_bg("cost_logged", "content_router", {
                    "cost_usd": cost_log.get("cost_usd"),
                    "provider": cost_log.get("provider"),
                    "model": cost_log.get("model"),
                    "phase": "multi_model_qa",
                }, task_id=task_id)
            except Exception as e:
                logger.warning("QA cost logging failed (non-critical): %s", e)

        # Rejection short-circuit.
        if not qa_result.approved:
            logger.warning(
                "[MULTI_QA] Content rejected for task %s after %d rewrite attempt(s):\n%s",
                task_id[:8], rewrite_attempts, qa_result.summary,
            )
            audit_log_bg("qa_failed", "content_router", {
                "score": qa_result.final_score, "stage": "multi_model_qa",
                "summary": qa_result.summary[:300],
                "rewrite_attempts": rewrite_attempts,
            }, task_id=task_id, severity="warning")

            reason = _build_rejection_reason(qa_result)
            await database_service.update_task(task_id, {
                "status": "rejected",
                "error_message": reason,
                # Persist the QA score on pipeline_versions even when the
                # verdict is "rejected" — otherwise Grafana's QA-score
                # charts show nothing for rejected rows and operators lose
                # visibility into *how close* the reject was (borderline
                # 70 vs catastrophic 0).
                "quality_score": float(qa_result.final_score),
            })
            # Flip model_performance.human_approved=False for the learning
            # signal — mirror what the operator-reject and auto-curate
            # paths do, so QA rejections also contribute to model-selection
            # feedback (gitea#271 Phase 3.A1).
            try:
                await database_service.mark_model_performance_outcome(
                    task_id, human_approved=False,
                )
            except Exception as mp_err:
                logger.debug(
                    "[cross_model_qa] mark_model_performance_outcome failed: %s",
                    mp_err,
                )
            # Write the rejection to pipeline_gate_history so the
            # content_tasks view's approval_status resolves correctly.
            # Otherwise QA-rejected tasks show NULL in Grafana approval-
            # status charts, because the view's scalar subquery returns
            # nothing.
            try:
                await database_service.pool.execute(
                    """
                    INSERT INTO pipeline_gate_history
                        (task_id, gate_name, event_kind, feedback, metadata)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    """,
                    task_id,
                    "multi_model_qa",
                    "rejected",
                    reason[:2000],
                    json.dumps(
                        {"reviewer": "multi_model_qa", "decision": "rejected"},
                        default=str,
                    ),
                )
            except Exception as pr_err:
                logger.warning(
                    "[cross_model_qa] pipeline_gate_history write failed for %s: %s",
                    task_id[:8], pr_err,
                )

            updates["status"] = "rejected"
            return StageResult(
                ok=True,
                detail=f"rejected: {reason[:100]}",
                context_updates=updates,
                continue_workflow=False,  # halts downstream stages
            )

        audit_log_bg("qa_passed", "content_router", {
            "score": qa_result.final_score, "stage": "multi_model_qa",
            "rewrite_attempts": rewrite_attempts,
        }, task_id=task_id)
        logger.info(
            "[MULTI_QA] Content approved for task %s: %s",
            task_id[:8], qa_result.summary.split("\\n")[0],
        )

        return StageResult(
            ok=True,
            detail=f"approved score={qa_result.final_score:.1f}",
            context_updates=updates,
            metrics={
                "final_score": float(qa_result.final_score),
                "rewrite_attempts": rewrite_attempts,
                "reviewer_count": len(qa_result.reviews),
            },
        )


# ---------------------------------------------------------------------------
# Helpers (module-level for testability)
# ---------------------------------------------------------------------------


async def _resolve_max_rewrites(settings_service: Any, default: int) -> int:
    """Read qa_max_rewrites (or legacy qa_consistency_max_rewrites) from settings."""
    if settings_service is None:
        return default
    try:
        raw = (
            await settings_service.get("qa_max_rewrites")
            or await settings_service.get("qa_consistency_max_rewrites")
        )
        if raw is not None:
            return int(raw)
    except Exception:
        pass
    return default


async def _rewrite_draft(
    content_text: str,
    title: str,
    issues_to_fix: str,
    settings_service: Any,
    task_id: str,
    attempt: int,
    *,
    site_config: Any = None,
) -> str | None:
    """Call the writer to fix flagged issues. Returns the new draft or None.

    Writer-fallback semantics: if the primary writer returns <50% of the
    original length (thinking-mode model pattern — token budget eaten by
    <think> tags), retry with ``qa_fallback_writer_model``. If the
    fallback is also too short, give up.
    """
    from plugins.registry import get_all_llm_providers
    from services.audit_log import audit_log_bg

    prompt = get_prompt_manager().get_prompt(
        "qa.aggregate_rewrite",
        title=title,
        issues_to_fix=issues_to_fix,
        content=content_text,
    )

    # v2.3: Provider Protocol instead of concrete OllamaClient. Per-call
    # timeout rides on the timeout_s kwarg added in v2.1.
    timeout_s = (
        site_config.get_int("content_router_qa_rewrite_timeout_seconds", 240)
        if site_config is not None else 240
    )
    providers = {p.name: p for p in get_all_llm_providers()}
    provider = providers.get("ollama_native")
    if provider is None:
        logger.warning(
            "[QA_REWRITE] Task %s: ollama_native provider not registered; skipping",
            task_id[:8],
        )
        return None

    try:
        primary = (
            (await settings_service.get("pipeline_writer_model") if settings_service else None)
            or "gemma3:27b"
        )
        primary = primary.removeprefix("ollama/")
        max_tokens = (
            site_config.get_int("content_router_qa_rewrite_max_tokens", 8000)
            if site_config is not None else 8000
        )

        result = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            model=primary,
            temperature=0.4,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
        revised = (result.text or "").strip()

        min_chars = int(0.5 * len(content_text))
        if len(revised) < min_chars:
            logger.warning(
                "[QA_REWRITE] Task %s: primary writer %s returned %d chars — likely "
                "thinking-mode eating the token budget. Falling back to gemma3:27b.",
                task_id[:8], primary, len(revised),
            )
            audit_log_bg(
                "writer_fallback", "content_router",
                {
                    "configured_writer": primary,
                    "actual_writer": "gemma3:27b",
                    "reason": "primary_returned_empty_on_rewrite",
                    "stage": "qa_rewrite",
                    "attempt": attempt,
                    "primary_chars": len(revised),
                    "expected_min_chars": min_chars,
                },
                task_id=task_id, severity="warning",
            )
            fallback_model = (
                site_config.get("qa_fallback_writer_model", "gemma3:27b")
                if site_config is not None else "gemma3:27b"
            )
            fb_result = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                model=fallback_model,
                temperature=0.4,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
            )
            revised = (fb_result.text or "").strip()
    except Exception as e:
        logger.warning(
            "[QA_REWRITE] Task %s: rewrite failed (non-fatal): %s",
            task_id[:8], e,
        )
        return None

    if len(revised) >= int(0.5 * len(content_text)):
        return revised

    logger.warning(
        "[QA_REWRITE] Task %s: rewrite + fallback both returned too-short output (%d chars)",
        task_id[:8], len(revised),
    )
    return None


def _build_rejection_reason(qa_result: Any) -> str:
    """Build a human-readable rejection message naming the vetoing reviewer."""
    vetoer = next(
        (r for r in qa_result.reviews if not r.approved),
        qa_result.reviews[-1] if qa_result.reviews else None,
    )
    if vetoer is None:
        return (
            f"Multi-model QA rejected (score: {qa_result.final_score:.0f}): "
            "No reviews recorded"
        )
    feedback = (vetoer.feedback or "no feedback").strip()[:300]
    return (
        f"Multi-model QA rejected (score: {qa_result.final_score:.0f}, "
        f"veto: {vetoer.reviewer} @ {vetoer.score:.0f}): {feedback}"
    )
