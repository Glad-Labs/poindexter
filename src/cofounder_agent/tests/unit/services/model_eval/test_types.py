"""Tests for model_eval core types (Plan 1, Task 1)."""

from __future__ import annotations

import dataclasses

import pytest

from services.model_eval.types import GoldenCase, GoldenSet, MetricResult, Scorer


def test_metric_result_carries_provenance() -> None:
    r = MetricResult(
        slot="rag_rerank_model",
        model="x",
        metric_name="ndcg@10",
        value=0.83,
        n_cases=50,
        latency_ms=1200,
        detail={},
    )
    assert r.value == 0.83
    assert r.n_cases == 50
    assert r.slot == "rag_rerank_model"


def test_metric_result_is_frozen() -> None:
    r = MetricResult(
        slot="rag_rerank_model",
        model="x",
        metric_name="ndcg@10",
        value=0.83,
        n_cases=1,
        latency_ms=1,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.value = 0.9  # type: ignore[misc]


def test_metric_result_detail_defaults_to_empty_dict() -> None:
    r = MetricResult("s", "m", "ndcg@10", 0.5, 1, 1)
    assert r.detail == {}


def test_golden_set_groups_cases() -> None:
    gs = GoldenSet(
        name="reranker-v1",
        version=1,
        cases=[
            GoldenCase(
                query="q",
                candidates=[{"doc_id": "a", "text": "t", "relevance": 1}],
            )
        ],
    )
    assert gs.name == "reranker-v1"
    assert gs.version == 1
    assert len(gs.cases) == 1
    assert gs.cases[0].query == "q"


def test_scorer_is_runtime_checkable_protocol() -> None:
    class _Dummy:
        capability = "reranker"
        primary_metric = "ndcg@10"

        def score(self, *, model, golden_set, site_config):  # type: ignore[no-untyped-def]
            return MetricResult("s", model, "ndcg@10", 1.0, 0, 0)

    assert isinstance(_Dummy(), Scorer)
