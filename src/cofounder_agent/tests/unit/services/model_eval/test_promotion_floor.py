"""The promotion floor (found by the first real self-review bakeoff).

`propose_promotion` applied only a RELATIVE margin, so "better than a broken
champion" was enough to get promoted. The 2026-08-30 self-review bakeoff made
that concrete: champion 0.375, challenger exactly 0.500 — and 0.500 is the
degenerate score for a balanced-accuracy metric. A detector that flags every
draft and one that flags none both score 0.5; neither carries information. The
worthless one "won" by a 33% relative margin and would have opened a promotion
PR.
"""

from __future__ import annotations

import pytest

from services.model_eval.promotion import propose_promotion
from services.model_eval.runner import EvalReport
from services.model_eval.types import MetricResult


class _SC:
    def __init__(self, values=None):
        self._v = values or {}

    def get(self, key, default=None):
        return self._v.get(key, default)


def _report(metric, champ_score, chal_score, promotion_margin=0.02):
    """Mirror what run_slot_eval produces, including its margin arithmetic."""
    champ = MetricResult(slot="s", model="champ", metric_name=metric,
                         value=champ_score, n_cases=16, latency_ms=1)
    chal = MetricResult(slot="s", model="chal", metric_name=metric,
                        value=chal_score, n_cases=16, latency_ms=1)
    rel = (chal_score - champ_score) / champ_score if champ_score else 0.0
    return EvalReport(
        slot="s",
        metric_name=metric,
        champion="champ",
        champion_score=champ_score,
        best_challenger="chal",
        best_challenger_score=chal_score,
        winner="chal" if chal_score > champ_score else "champ",
        margin=rel,
        beats_margin=(chal_score > champ_score and rel >= promotion_margin),
        results=[champ, chal],
    )


@pytest.mark.unit
class TestDegenerateScoresCannotWin:
    def test_the_actual_bakeoff_result_is_refused(self) -> None:
        """champion 0.375, challenger 0.500 — the real numbers."""
        r = _report("self_review_balanced_accuracy", 0.375, 0.500)
        assert r.beats_margin, "precondition: it does beat the relative margin"
        assert propose_promotion(report=r, site_config=_SC()) is None

    def test_the_critic_slot_is_guarded_too(self) -> None:
        """Same shared logic, same exposure — not a self-review-only bug."""
        r = _report("judge_balanced_accuracy", 0.30, 0.50)
        assert propose_promotion(report=r, site_config=_SC()) is None

    def test_a_genuinely_better_model_still_promotes(self) -> None:
        """The floor must not block real wins — that would be worse than the bug."""
        r = _report("self_review_balanced_accuracy", 0.375, 0.82)
        p = propose_promotion(report=r, site_config=_SC())
        assert p is not None and p.to_model == "chal"

    def test_a_metric_with_no_floor_is_unchanged(self) -> None:
        """Existing slots keep their exact prior behaviour."""
        r = _report("ndcg@10", 0.30, 0.50)
        assert propose_promotion(report=r, site_config=_SC()) is not None


@pytest.mark.unit
class TestOperatorCanTuneTheFloor:
    def test_floor_can_be_raised(self) -> None:
        """Better-than-chance is a low bar; an operator may demand more."""
        r = _report("self_review_balanced_accuracy", 0.55, 0.65)
        sc = _SC({"model_eval_floor.self_review_balanced_accuracy": "0.7"})
        assert propose_promotion(report=r, site_config=sc) is None
        assert propose_promotion(report=r, site_config=_SC()) is not None

    def test_floor_can_be_cleared_with_an_empty_value(self) -> None:
        r = _report("self_review_balanced_accuracy", 0.375, 0.500)
        sc = _SC({"model_eval_floor.self_review_balanced_accuracy": ""})
        assert propose_promotion(report=r, site_config=sc) is None, "empty = fall back to default"

    def test_a_junk_override_falls_back_rather_than_crashing(self) -> None:
        r = _report("self_review_balanced_accuracy", 0.375, 0.500)
        sc = _SC({"model_eval_floor.self_review_balanced_accuracy": "not-a-number"})
        assert propose_promotion(report=r, site_config=sc) is None
