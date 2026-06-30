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

from dataclasses import dataclass
from typing import Any

from services.model_eval.runner import EvalReport

# Slots whose promotion is a pure setting flip with no data migration. Only
# these are eligible for opt-in auto-swap; everything else is PR-only.
_STATELESS_SLOTS = frozenset({"rag_rerank_model"})


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
