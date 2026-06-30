"""RerankerScorer — scores a cross-encoder reranker on a golden set (Plan 1, Task 3).

Mirrors the model-invocation pattern of ``rag_engine.CrossEncoderRerankRetriever``:
``sentence_transformers.CrossEncoder(name, device).predict([(query, doc), ...])``
returns a relevance score per pair. The encoder is built through an injectable
``encoder_factory`` so unit tests stay offline (no model download, no GPU).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from services.model_eval.metrics import mrr, ndcg_at_k
from services.model_eval.types import GoldenSet, MetricResult

_K = 10
_SLOT = "rag_rerank_model"


def _default_encoder_factory(name: str, device: str) -> Any:
    """Load a real cross-encoder. Imported lazily — ``sentence_transformers``
    is heavy, and unit tests inject a fake factory instead."""
    from sentence_transformers import CrossEncoder

    return CrossEncoder(name, device=device)


class RerankerScorer:
    """Implements ``services.model_eval.types.Scorer`` for the reranker slot."""

    capability = "reranker"
    primary_metric = f"ndcg@{_K}"

    def __init__(
        self,
        *,
        encoder_factory: Callable[[str, str], Any] = _default_encoder_factory,
    ) -> None:
        self._encoder_factory = encoder_factory

    def score(self, *, model: str, golden_set: GoldenSet, site_config: Any) -> MetricResult:
        device = (site_config.get("rag_rerank_device", "cpu") or "cpu").strip()
        encoder = self._encoder_factory(model, device)

        t0 = time.monotonic()
        ndcgs: list[float] = []
        mrrs: list[float] = []
        for case in golden_set.cases:
            pairs = [(case.query, c["text"]) for c in case.candidates]
            scores = list(encoder.predict(pairs))
            # Rank candidate indices by descending reranker score.
            order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            ranked_rel = [float(case.candidates[i]["relevance"]) for i in order]
            ndcgs.append(ndcg_at_k(ranked_rel, _K))
            mrrs.append(mrr([rel > 0 for rel in ranked_rel]))

        n = len(golden_set.cases)
        avg_ndcg = sum(ndcgs) / n if n else 0.0
        avg_mrr = sum(mrrs) / n if n else 0.0
        return MetricResult(
            slot=_SLOT,
            model=model,
            metric_name=self.primary_metric,
            value=avg_ndcg,
            n_cases=n,
            latency_ms=int((time.monotonic() - t0) * 1000),
            detail={"mrr": avg_mrr, "golden_version": golden_set.version},
        )
