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

    report = run_slot_eval(
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
