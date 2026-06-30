"""Tests for ranking metrics (Plan 1, Task 2).

The expected values are hand-computed — these assertions are the ground
truth the entire champion-challenger loop rests on, so they must be
verifiable by inspection, not by trusting the implementation.
"""

from __future__ import annotations

import math

from services.model_eval.metrics import mrr, ndcg_at_k


def test_ndcg_perfect_ranking_is_1() -> None:
    # Already in descending relevance order -> dcg == idcg.
    assert ndcg_at_k([3, 2, 1], k=3) == 1.0


def test_ndcg_worst_ordering_below_1() -> None:
    # Ascending relevance order -> dcg < idcg.
    assert ndcg_at_k([1, 2, 3], k=3) < 1.0


def test_ndcg_known_value() -> None:
    # ranked [1, 0, 1]:
    #   dcg  = 1/log2(2) + 0/log2(3) + 1/log2(4) = 1 + 0 + 0.5 = 1.5
    #   idcg (ideal order [1, 1, 0]) = 1/log2(2) + 1/log2(3) = 1 + 1/log2(3)
    expected = 1.5 / (1 + 1 / math.log2(3))
    assert math.isclose(ndcg_at_k([1, 0, 1], k=3), expected, rel_tol=1e-9)


def test_ndcg_respects_k_cutoff() -> None:
    # With k=1, only the first item counts; ranked first item is the best
    # possible (3), so nDCG@1 == 1.0 regardless of the tail.
    assert ndcg_at_k([3, 0, 0], k=1) == 1.0


def test_ndcg_all_zero_relevance_is_0() -> None:
    # idcg == 0 -> guard returns 0.0, not a ZeroDivisionError.
    assert ndcg_at_k([0, 0, 0], k=3) == 0.0


def test_mrr_first_relevant_at_rank_3() -> None:
    assert mrr([False, False, True]) == 1 / 3


def test_mrr_first_relevant_at_rank_1() -> None:
    assert mrr([True, False]) == 1.0


def test_mrr_none_relevant_is_0() -> None:
    assert mrr([False, False]) == 0.0
