"""Core types for the model-eval loop (Plan 1, Task 1).

These carry no behaviour — they are the stable vocabulary the scorer,
harness, runner, and promotion modules share. The ``Scorer`` Protocol is
the one disposable seam: Wave 1 ships ``RerankerScorer`` against it; later
waves add judge/perceptual scorers without touching the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class GoldenCase:
    """One test case. ``candidates`` is a list of dicts shaped
    ``{"doc_id": str, "text": str, "relevance": int}`` — the relevance
    label is the ground truth the metric scores a model's ranking against.
    """

    query: str
    candidates: list[dict[str, Any]]


@dataclass(frozen=True)
class GoldenSet:
    """A versioned collection of golden cases for one capability.

    ``version`` lets metric deltas be compared over time and ties a stored
    eval run back to the exact case set it scored.
    """

    name: str
    version: int
    cases: list[GoldenCase]


@dataclass(frozen=True)
class MetricResult:
    """The outcome of scoring one model against one golden set.

    ``value`` is the primary metric (higher is better). ``detail`` holds
    secondary metrics / provenance (e.g. MRR, golden-set version) so the
    harness can persist a full picture without widening this contract.
    """

    slot: str
    model: str
    metric_name: str
    value: float
    n_cases: int
    latency_ms: int
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Scorer(Protocol):
    """Computes a capability-appropriate metric for a model.

    Implementations are stateless w.r.t. the loop: they take a model id +
    a golden set + a ``SiteConfig`` and return a ``MetricResult``. The
    loop never inspects how the score was computed.
    """

    capability: str  # e.g. "reranker"
    primary_metric: str  # e.g. "ndcg@10"

    def score(
        self, *, model: str, golden_set: GoldenSet, site_config: Any
    ) -> MetricResult: ...
