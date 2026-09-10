"""Promotion proposals for the model-eval loop (Plan 1, Task 7).

Turns a winning ``EvalReport`` into a ``PromotionProposal``. This module only
*decides and renders*. Today the proposal is *surfaced* — the CLI ``run``
command prints the PR-ready body and the decided ``kind``, and the operator acts
on it (opens the PR, or flips the setting for an opted-in ``auto_swap``).
Auto-*executing* a promotion (auto-open the PR / auto-write ``app_settings``) is
a deferred follow-up; keeping the decision logic pure here makes it
unit-testable independent of that.

Promotion shape by slot:
- **PR-based** by default — a winner opens a PR changing the slot's default,
  reviewed under the normal CI-green gate.
- **auto_swap** only for *stateless* slots (the reranker — no migration) AND
  only when the operator opted in via ``{slot}_auto_promote=true``. Stateful
  slots (embeddings need a re-embed migration) never auto-swap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from services.model_eval.runner import EvalReport

logger = logging.getLogger(__name__)

# Slots whose promotion is a pure setting flip with no data migration. Only
# these are eligible for opt-in auto-swap; everything else is PR-only.
_STATELESS_SLOTS = frozenset({"rag_rerank_model"})

# Absolute floor a challenger must clear before it can be proposed at all,
# per slot. The margin check alone is RELATIVE, so it happily promotes a
# useless model over a worse one — which is not hypothetical: the first real
# self-review bakeoff (2026-08-30) scored the champion 0.375 and a challenger
# exactly 0.500, and 0.500 is the degenerate score for a balanced-accuracy
# metric. A detector that flags every draft and one that flags none both score
# 0.5; neither carries any information, and one of them "won" by a 33%
# relative margin.
#
# Keyed by metric name rather than by slot, because the floor is a property of
# what the number MEANS. Balanced accuracy over two classes has a known chance
# line at 0.5, so anything at or below it is noise regardless of which slot
# produced it. Metrics with no entry here have no floor — the same behaviour
# as before — so this can only ever block a promotion that was already
# meaningless, never a real one.
_METRIC_FLOORS: dict[str, float] = {
    "judge_balanced_accuracy": 0.5,
    "self_review_balanced_accuracy": 0.5,
}


def _floor_for(metric_name: str, site_config: Any) -> float | None:
    """Floor for this metric, operator-overridable per slot's metric.

    ``model_eval_floor.<metric_name>`` wins when set, so an operator can raise
    the bar (demand 0.7, not merely better-than-chance) or clear it with an
    empty value without a code change.
    """
    override = site_config.get(f"model_eval_floor.{metric_name}", None)
    if override is not None and str(override).strip() != "":
        try:
            return float(override)
        except (TypeError, ValueError):
            # Loud, not silent: falling back here means the operator's
            # configured floor is NOT being applied, so a promotion they
            # expected to be blocked may go through. Silence would hide a
            # misconfiguration behind correct-looking behaviour.
            logger.warning(
                "[model_eval] model_eval_floor.%s is not a number (%r) — "
                "ignoring it and using the built-in floor %r instead",
                metric_name, override, _METRIC_FLOORS.get(metric_name),
            )
    return _METRIC_FLOORS.get(metric_name)


@dataclass(frozen=True)
class PromotionProposal:
    slot: str
    from_model: str
    to_model: str
    metric_name: str
    metric_delta: float
    margin: float
    kind: str  # "pr" | "auto_swap"
    body: str


def propose_promotion(*, report: EvalReport, site_config: Any) -> PromotionProposal | None:
    """Return a proposal when a challenger won, else ``None``."""
    best_score = report.best_challenger_score
    if not report.beats_margin or report.best_challenger is None or best_score is None:
        return None

    # Beating the champion is necessary but not sufficient: the winner must
    # also clear the metric's absolute floor. Without this, "better than a
    # broken champion" is enough to get promoted, and a metric's degenerate
    # score can win outright.
    floor = _floor_for(report.metric_name, site_config)
    if floor is not None and best_score <= floor:
        return None

    slot = report.slot
    from_model = report.champion
    to_model = report.best_challenger
    delta = best_score - report.champion_score

    opted_in = (site_config.get(f"{slot}_auto_promote", "false") or "false").strip().lower() == "true"
    kind = "auto_swap" if (opted_in and slot in _STATELESS_SLOTS) else "pr"

    body = _render_body(report, from_model=from_model, to_model=to_model, best_score=best_score, delta=delta)
    return PromotionProposal(
        slot=slot,
        from_model=from_model,
        to_model=to_model,
        metric_name=report.metric_name,
        metric_delta=delta,
        margin=report.margin,
        kind=kind,
        body=body,
    )


def _render_body(
    report: EvalReport, *, from_model: str, to_model: str, best_score: float, delta: float
) -> str:
    n_cases = report.results[0].n_cases if report.results else 0
    return (
        f"## Model promotion: `{report.slot}`\n\n"
        f"Challenger **`{to_model}`** beats champion **`{from_model}`** "
        f"on `{report.metric_name}`.\n\n"
        f"| model | {report.metric_name} |\n"
        f"| --- | --- |\n"
        f"| `{from_model}` (champion) | {report.champion_score:.4f} |\n"
        f"| `{to_model}` (challenger) | {best_score:.4f} |\n\n"
        f"- Absolute delta: {delta:+.4f}\n"
        f"- Relative margin: {report.margin:.2%}\n"
        f"- Golden cases: {n_cases}\n\n"
        f"Proposed change: set `{report.slot}` "
        f"(default in `services/settings_defaults.py`) "
        f"from `{from_model}` to `{to_model}`.\n"
    )
