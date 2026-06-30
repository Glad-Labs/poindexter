"""Tests for promotion proposals (Plan 1, Task 7)."""

from __future__ import annotations

from services.model_eval.promotion import propose_promotion
from services.model_eval.runner import EvalReport
from services.model_eval.types import MetricResult
from services.site_config import SiteConfig


def _report(slot: str = "rag_rerank_model", beats: bool = True) -> EvalReport:
    return EvalReport(
        slot=slot,
        metric_name="ndcg@10",
        champion="champ",
        champion_score=0.80,
        best_challenger="chall",
        best_challenger_score=0.86,
        winner="chall" if beats else "champ",
        margin=0.075,
        beats_margin=beats,
        results=[MetricResult(slot, "champ", "ndcg@10", 0.80, 50, 1)],
    )


def test_no_proposal_when_within_margin() -> None:
    assert propose_promotion(report=_report(beats=False), site_config=SiteConfig(initial_config={})) is None


def test_default_proposal_is_pr() -> None:
    p = propose_promotion(report=_report(), site_config=SiteConfig(initial_config={}))
    assert p is not None
    assert p.kind == "pr"
    assert p.from_model == "champ"
    assert p.to_model == "chall"
    assert "champ" in p.body and "chall" in p.body
    assert "ndcg@10" in p.body


def test_reranker_auto_promote_opt_in_is_auto_swap() -> None:
    sc = SiteConfig(initial_config={"rag_rerank_model_auto_promote": "true"})
    p = propose_promotion(report=_report(), site_config=sc)
    assert p is not None
    assert p.kind == "auto_swap"


def test_non_stateless_slot_never_auto_swaps() -> None:
    # embeddings need a re-embed migration -> must always go through a PR,
    # even with the opt-in flag set.
    sc = SiteConfig(initial_config={"embed_model_auto_promote": "true"})
    p = propose_promotion(report=_report(slot="embed_model"), site_config=sc)
    assert p is not None
    assert p.kind == "pr"
