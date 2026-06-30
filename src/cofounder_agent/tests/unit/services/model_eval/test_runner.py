"""Tests for the eval runner + EvalReport comparison (Plan 1, Task 6)."""

from __future__ import annotations

from services.model_eval.harness import InMemoryEvalHarness
from services.model_eval.runner import run_slot_eval
from services.model_eval.types import GoldenSet, MetricResult

_SLOT = "rag_rerank_model"


class _StubScorer:
    capability = "reranker"
    primary_metric = "ndcg@10"

    def __init__(self, table: dict[str, float]) -> None:
        self._table = table

    def score(self, *, model, golden_set, site_config):  # type: ignore[no-untyped-def]
        return MetricResult(_SLOT, model, "ndcg@10", self._table[model], 1, 1, {})


def _run(table: dict[str, float], *, challengers, margin=0.02, harness=None):
    return run_slot_eval(
        slot=_SLOT,
        champion="champ",
        challengers=challengers,
        scorer=_StubScorer(table),
        golden_set=GoldenSet("r", 1, []),
        harness=harness or InMemoryEvalHarness(),
        site_config=None,
        promotion_margin=margin,
    )


def test_challenger_beating_margin_wins() -> None:
    rep = _run({"champ": 0.80, "chall": 0.86}, challengers=["chall"])
    assert rep.winner == "chall"
    assert rep.beats_margin is True
    assert rep.best_challenger == "chall"


def test_challenger_within_margin_keeps_champion() -> None:
    rep = _run({"champ": 0.80, "chall": 0.805}, challengers=["chall"])  # +0.6% < 2%
    assert rep.winner == "champ"
    assert rep.beats_margin is False


def test_best_of_several_challengers_is_chosen() -> None:
    rep = _run({"champ": 0.80, "a": 0.83, "b": 0.88}, challengers=["a", "b"])
    assert rep.best_challenger == "b"
    assert rep.winner == "b"


def test_no_challengers_keeps_champion() -> None:
    rep = _run({"champ": 0.80}, challengers=[])
    assert rep.winner == "champ"
    assert rep.best_challenger is None
    assert rep.beats_margin is False


def test_results_recorded_to_harness() -> None:
    h = InMemoryEvalHarness()
    _run({"champ": 0.80, "chall": 0.86}, challengers=["chall"], harness=h)
    latest = h.latest_by_model(_SLOT, "ndcg@10")
    assert set(latest) == {"champ", "chall"}


def test_zero_champion_score_does_not_divide_by_zero() -> None:
    rep = _run({"champ": 0.0, "chall": 0.5}, challengers=["chall"])
    assert rep.beats_margin is True
    assert rep.winner == "chall"
