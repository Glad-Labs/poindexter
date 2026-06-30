"""Tests for RerankerScorer (Plan 1, Task 3).

The CrossEncoder is injected via ``encoder_factory`` so these tests are
deterministic and offline — no sentence-transformers download, no GPU.
"""

from __future__ import annotations

from services.model_eval.scorers.reranker import RerankerScorer
from services.model_eval.types import GoldenCase, GoldenSet
from services.site_config import SiteConfig


class _FakeEncoder:
    """Scores a (query, doc) pair high iff the doc text starts with 'good'."""

    def predict(self, pairs):  # type: ignore[no-untyped-def]
        return [1.0 if doc.startswith("good") else 0.0 for (_q, doc) in pairs]


class _InvertedEncoder:
    """Pathological reranker: scores the irrelevant doc highest."""

    def predict(self, pairs):  # type: ignore[no-untyped-def]
        return [0.0 if doc.startswith("good") else 1.0 for (_q, doc) in pairs]


def _site_config() -> SiteConfig:
    return SiteConfig(initial_config={"rag_rerank_device": "cpu"})


def _one_case_set() -> GoldenSet:
    return GoldenSet(
        name="reranker",
        version=7,
        cases=[
            GoldenCase(
                query="q",
                candidates=[
                    {"doc_id": "1", "text": "good doc", "relevance": 1},
                    {"doc_id": "2", "text": "bad doc", "relevance": 0},
                ],
            )
        ],
    )


def test_perfect_reranker_scores_ndcg_1() -> None:
    scorer = RerankerScorer(encoder_factory=lambda name, device: _FakeEncoder())
    result = scorer.score(model="cross-encoder/x", golden_set=_one_case_set(), site_config=_site_config())
    assert result.metric_name == "ndcg@10"
    assert result.value == 1.0
    assert result.n_cases == 1
    assert result.slot == "rag_rerank_model"
    assert result.model == "cross-encoder/x"


def test_inverted_reranker_scores_below_1() -> None:
    scorer = RerankerScorer(encoder_factory=lambda name, device: _InvertedEncoder())
    result = scorer.score(model="cross-encoder/x", golden_set=_one_case_set(), site_config=_site_config())
    assert result.value < 1.0


def test_detail_carries_mrr_and_golden_version() -> None:
    scorer = RerankerScorer(encoder_factory=lambda name, device: _FakeEncoder())
    result = scorer.score(model="m", golden_set=_one_case_set(), site_config=_site_config())
    assert result.detail["golden_version"] == 7
    assert result.detail["mrr"] == 1.0  # relevant doc ranked first


def test_averages_over_multiple_cases() -> None:
    gs = GoldenSet(
        name="reranker",
        version=1,
        cases=[
            GoldenCase(query="a", candidates=[{"doc_id": "1", "text": "good", "relevance": 1},
                                              {"doc_id": "2", "text": "bad", "relevance": 0}]),
            GoldenCase(query="b", candidates=[{"doc_id": "3", "text": "good", "relevance": 1},
                                              {"doc_id": "4", "text": "bad", "relevance": 0}]),
        ],
    )
    scorer = RerankerScorer(encoder_factory=lambda name, device: _FakeEncoder())
    result = scorer.score(model="m", golden_set=gs, site_config=_site_config())
    assert result.n_cases == 2
    assert result.value == 1.0
