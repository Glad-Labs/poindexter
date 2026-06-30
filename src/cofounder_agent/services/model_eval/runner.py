"""Eval runner — orchestrates a champion-vs-challengers bakeoff (Plan 1, Task 6).

Scores every model on the golden set, records the run to the harness, and
returns an ``EvalReport`` with the relative-margin comparison the promotion
step consumes. The comparison is done in-memory within one invocation, so it
never depends on a harness read-back.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from services.model_eval.harness import EvalHarness
from services.model_eval.types import GoldenSet, MetricResult, Scorer


@dataclass(frozen=True)
class EvalReport:
    slot: str
    metric_name: str
    champion: str
    champion_score: float
    best_challenger: str | None
    best_challenger_score: float | None
    winner: str
    margin: float  # relative improvement of best challenger over champion
    beats_margin: bool
    results: list[MetricResult]


def run_slot_eval(
    *,
    slot: str,
    champion: str,
    challengers: list[str],
    scorer: Scorer,
    golden_set: GoldenSet,
    harness: EvalHarness,
    site_config: Any,
    promotion_margin: float,
    run_name: str | None = None,
) -> EvalReport:
    """Score champion + challengers, record the run, and compare.

    A challenger wins only if it strictly beats the champion AND the relative
    improvement is at least ``promotion_margin`` (keep-best / no-regress).
    """
    models = [champion, *challengers]
    results = [scorer.score(model=m, golden_set=golden_set, site_config=site_config) for m in models]
    by_model: dict[str, MetricResult] = {r.model: r for r in results}

    run = run_name or f"{slot}-v{golden_set.version}-{int(time.time())}"
    harness.record_results(run, results)

    champion_score = by_model[champion].value
    metric_name = by_model[champion].metric_name

    best_challenger: str | None = None
    best_challenger_score: float | None = None
    for c in challengers:
        v = by_model[c].value
        if best_challenger_score is None or v > best_challenger_score:
            best_challenger, best_challenger_score = c, v

    if best_challenger_score is None:
        margin = 0.0
        beats_margin = False
        winner = champion
    else:
        if champion_score > 0:
            margin = (best_challenger_score - champion_score) / champion_score
        else:
            # Champion scored 0 — any positive challenger is an unbounded win.
            margin = float("inf") if best_challenger_score > 0 else 0.0
        beats_margin = best_challenger_score > champion_score and margin >= promotion_margin
        winner = best_challenger if (beats_margin and best_challenger is not None) else champion

    return EvalReport(
        slot=slot,
        metric_name=metric_name,
        champion=champion,
        champion_score=champion_score,
        best_challenger=best_challenger,
        best_challenger_score=best_challenger_score,
        winner=winner,
        margin=margin,
        beats_margin=beats_margin,
        results=results,
    )
