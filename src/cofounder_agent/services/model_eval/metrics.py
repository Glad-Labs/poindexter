"""Deterministic ranking metrics for the model-eval loop (Plan 1, Task 2).

Pure functions, no I/O, no model calls — this is the ``calculated`` side of
the calculated-vs-generated split: a regression here is unarguable, which is
exactly why Wave 1 builds the loop on these before any judge-based scorer.
"""

from __future__ import annotations

import math


def _dcg(relevances: list[float], k: int) -> float:
    """Discounted cumulative gain over the first ``k`` items."""
    return sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))


def ndcg_at_k(ranked_relevances: list[float], k: int) -> float:
    """Normalized DCG@k.

    ``ranked_relevances`` are the graded relevance labels in the order the
    model ranked them. iDCG is the DCG of the best possible ordering of the
    same labels. Returns 0.0 when every label is 0 (no ideal ordering),
    rather than dividing by zero.
    """
    idcg = _dcg(sorted(ranked_relevances, reverse=True), k)
    if idcg == 0:
        return 0.0
    return _dcg(ranked_relevances, k) / idcg


def mrr(ranked_is_relevant: list[bool]) -> float:
    """Reciprocal rank of the first relevant item (0.0 if none are relevant)."""
    for i, is_rel in enumerate(ranked_is_relevant):
        if is_rel:
            return 1.0 / (i + 1)
    return 0.0
