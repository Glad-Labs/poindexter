"""Top-level orchestrator for a reranker bakeoff (Plan 1, Tasks 8-9).

Ties the pieces together so both the CLI and the integration test share one
entry point (DRY): champion from ``app_settings``, golden set from posts,
``RerankerScorer``, the harness, the runner, and the promotion proposal.
"""

from __future__ import annotations

from typing import Any

from services.model_eval.golden_sets.reranker import build_reranker_golden_set
from services.model_eval.harness import EvalHarness, LangfuseEvalHarness
from services.model_eval.promotion import PromotionProposal, propose_promotion
from services.model_eval.runner import EvalReport, run_slot_eval
from services.model_eval.scorers.reranker import RerankerScorer
from services.model_eval.types import Scorer

_SLOT = "rag_rerank_model"


async def run_reranker_bakeoff(
    *,
    pool: Any,
    site_config: Any,
    challengers: list[str],
    harness: EvalHarness | None = None,
    scorer: Scorer | None = None,
    run_name: str | None = None,
) -> tuple[EvalReport, PromotionProposal | None]:
    """Run the reranker champion (the current ``rag_rerank_model``) against
    ``challengers`` on the posts-derived golden set; return the report + any
    promotion proposal. ``harness`` defaults to Langfuse and ``scorer`` to
    ``RerankerScorer()``; inject the in-memory double / a fake-encoder scorer
    for tests and offline runs.
    """
    champion = (site_config.get(_SLOT, "") or "").strip()
    if not champion:
        raise RuntimeError(
            f"{_SLOT} is unset in app_settings; cannot run a reranker bakeoff."
        )
    golden = await build_reranker_golden_set(pool=pool, site_config=site_config)
    used_scorer: Scorer = scorer or RerankerScorer()
    used_harness: EvalHarness = harness or LangfuseEvalHarness(site_config=site_config)
    margin = float(site_config.get("model_eval_promotion_margin", "0.02"))

    report = await run_slot_eval(
        slot=_SLOT,
        champion=champion,
        challengers=list(challengers),
        scorer=used_scorer,
        golden_set=golden,
        harness=used_harness,
        site_config=site_config,
        promotion_margin=margin,
        run_name=run_name,
    )
    proposal = propose_promotion(report=report, site_config=site_config)
    return report, proposal


_CRITIC_SLOT = "pipeline_critic_model"
_SELF_REVIEW_SLOT = "writer_self_review_review_model"


async def run_critic_bakeoff(
    *,
    pool: Any,
    site_config: Any,
    platform: Any,
    challengers: list[str],
    harness: EvalHarness | None = None,
    scorer: Scorer | None = None,
    run_name: str | None = None,
) -> tuple[EvalReport, PromotionProposal | None]:
    """Calibrate the critic judge (poindexter#985): score the current
    ``pipeline_critic_model`` champion (plus any ``challengers``) on the
    posts-derived critic golden set — approve-rate on known-good published
    posts, veto-rate on deterministic corruptions of those same posts.

    ``challengers`` may be empty: a champion-only run is the calibration
    baseline the issue asks for before/after any judge change. ``platform``
    is the kernel dispatch handle the production critic path needs.

    Emits a ``judge_calibration_run`` finding with the per-model summary so
    the run is visible on the Findings surface (a silent judge swap is how
    the 2026-06-29 approval collapse ran unnoticed for 5+ weeks).
    """
    from services.model_eval.golden_sets.critic import build_critic_golden_set
    from services.model_eval.scorers.critic import CriticScorer

    champion = (site_config.get(_CRITIC_SLOT, "") or "").strip()
    if not champion:
        raise RuntimeError(
            f"{_CRITIC_SLOT} is unset in app_settings; cannot run a critic bakeoff."
        )
    golden = await build_critic_golden_set(pool=pool, site_config=site_config)
    used_scorer: Scorer = scorer or CriticScorer(pool=pool, platform=platform)
    used_harness: EvalHarness = harness or LangfuseEvalHarness(site_config=site_config)
    margin = float(site_config.get("model_eval_promotion_margin", "0.02"))

    report = await run_slot_eval(
        slot=_CRITIC_SLOT,
        champion=champion,
        challengers=list(challengers),
        scorer=used_scorer,
        golden_set=golden,
        harness=used_harness,
        site_config=site_config,
        promotion_margin=margin,
        run_name=run_name,
    )
    proposal = propose_promotion(report=report, site_config=site_config)

    # Best-effort visibility: one finding per run with the headline numbers
    # (min-good-approve floor per model_eval_critic_min_good_approve).
    try:
        from utils.findings import emit_finding

        floor = float(site_config.get("model_eval_critic_min_good_approve", "0.7"))
        lines = []
        for r in report.results:
            d = r.detail
            verdict = "PASS" if float(d.get("good_approve_rate", 0.0)) >= floor else "BELOW FLOOR"
            lines.append(
                f"- `{r.model}`: balanced {r.value:.2f} | good-approve "
                f"{d.get('good_approve_rate')} ({verdict} vs floor {floor}) | "
                f"bad-veto {d.get('bad_veto_rate')} | unusable "
                f"{d.get('unusable_reviews')}"
            )
        emit_finding(
            source="services.model_eval.bakeoff",
            kind="judge_calibration_run",
            title=(
                f"Judge calibration: champion {champion!r} scored "
                f"{report.champion_score:.2f} balanced accuracy"
            ),
            body=(
                f"Critic golden set v{golden.version} "
                f"({len(golden.cases)} cases).\n" + "\n".join(lines)
            ),
            severity="info",
            dedup_key=f"judge_calibration_run:{report.champion}:{golden.version}",
            extra={
                "slot": _CRITIC_SLOT,
                "champion": champion,
                "results": [
                    {"model": r.model, "value": r.value, **r.detail} for r in report.results
                ],
            },
        )
    except Exception:  # noqa: BLE001 — observability never blocks the run
        # silent-ok: the report itself is returned to the caller regardless
        pass

    return report, proposal


async def run_self_review_bakeoff(
    *,
    pool: Any,
    site_config: Any,
    challengers: list[str] | None = None,
    scorer: Scorer | None = None,
    harness: EvalHarness | None = None,
    run_name: str | None = None,
) -> tuple[Any, Any]:
    """Bake off contradiction DETECTORS on the self-review golden set.

    Answers the question poindexter#1031 could not: how often does
    ``writer_self_review`` actually catch a contradiction? Production cannot
    tell you, because a missed detection emits nothing — a false PASS and a
    genuinely clean draft are the same observation.

    The champion is ``writer_self_review_review_model``, which is seeded EMPTY
    meaning "use the reviser pin". An empty champion is therefore normal, not
    an error, so this resolves through to ``writer_self_review_model`` rather
    than failing the way the critic bakeoff does on an unset pin — otherwise
    the default configuration could never be measured, which is the one that
    matters most.
    """
    challengers = list(challengers or [])

    from services.model_eval.golden_sets.self_review import build_self_review_golden_set
    from services.model_eval.scorers.self_review import SelfReviewScorer

    champion = (site_config.get(_SELF_REVIEW_SLOT, "") or "").strip()
    if not champion:
        champion = (site_config.get("writer_self_review_model", "") or "").strip()
    if not champion:
        raise RuntimeError(
            f"{_SELF_REVIEW_SLOT} and writer_self_review_model are both unset; "
            "cannot run a self-review bakeoff."
        )

    golden = await build_self_review_golden_set(pool=pool, site_config=site_config)
    used_scorer: Scorer = scorer or SelfReviewScorer(pool=pool)
    used_harness: EvalHarness = harness or LangfuseEvalHarness(site_config=site_config)
    margin = float(site_config.get("model_eval_promotion_margin", "0.02"))

    report = await run_slot_eval(
        slot=_SELF_REVIEW_SLOT,
        champion=champion,
        challengers=challengers,
        scorer=used_scorer,
        golden_set=golden,
        harness=used_harness,
        site_config=site_config,
        promotion_margin=margin,
        run_name=run_name,
    )
    proposal = propose_promotion(report=report, site_config=site_config)

    # Best-effort visibility. The 1-of-4 detection rate went unnoticed because
    # nothing reported it; a bakeoff whose result also went unreported would
    # repeat that exact mistake.
    try:
        from utils.findings import emit_finding

        lines = [
            f"- `{r.model}`: balanced {r.value:.2f} | detection "
            f"{r.detail.get('detection_rate')} "
            f"({r.detail.get('detected')}/{r.detail.get('contradiction_n')}) | "
            f"false-positive {r.detail.get('false_positive_rate')} | "
            f"unusable {r.detail.get('unusable')}"
            for r in report.results
        ]
        emit_finding(
            source="services.model_eval.bakeoff",
            kind="self_review_calibration_run",
            title=(
                f"Self-review detector calibration: champion {champion!r} "
                f"scored {report.champion_score:.2f} balanced accuracy"
            ),
            body=(
                f"Self-review golden set v{golden.version} "
                f"({len(golden.cases)} cases).\n" + "\n".join(lines)
            ),
            severity="info",
            dedup_key=f"self_review_calibration:{golden.version}",
        )
    except Exception:  # noqa: BLE001 — never let reporting fail the run
        import logging

        logging.getLogger(__name__).warning(
            "[model_eval] self-review calibration finding failed", exc_info=True
        )

    return report, proposal
