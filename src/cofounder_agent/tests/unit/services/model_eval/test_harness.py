"""Tests for the EvalHarness seam (Plan 1, Task 4).

The seam is what we test. The InMemory double is exercised directly; the
Langfuse adapter is driven through an injected fake client shaped like the
proven langfuse ^4.6 surface used in services.langfuse_experiments
(create_dataset / create_dataset_item / start_as_current_span / create_score).
No real langfuse install required.
"""

from __future__ import annotations

import contextlib

from services.model_eval.harness import InMemoryEvalHarness, LangfuseEvalHarness
from services.model_eval.types import GoldenCase, GoldenSet, MetricResult
from services.site_config import SiteConfig


def _results() -> list[MetricResult]:
    return [
        MetricResult("rag_rerank_model", "champ", "ndcg@10", 0.80, 10, 5, {"mrr": 0.70}),
        MetricResult("rag_rerank_model", "chall", "ndcg@10", 0.86, 10, 6, {"mrr": 0.75}),
    ]


def test_in_memory_harness_roundtrips_latest() -> None:
    h = InMemoryEvalHarness()
    h.record_results("run-1", _results())
    assert h.latest_by_model("rag_rerank_model", "ndcg@10") == {"champ": 0.80, "chall": 0.86}


def test_in_memory_latest_prefers_most_recent_run() -> None:
    h = InMemoryEvalHarness()
    h.record_results("run-1", [MetricResult("rag_rerank_model", "champ", "ndcg@10", 0.80, 1, 1)])
    h.record_results("run-2", [MetricResult("rag_rerank_model", "champ", "ndcg@10", 0.88, 1, 1)])
    assert h.latest_by_model("rag_rerank_model", "ndcg@10") == {"champ": 0.88}


class _FakeClient:
    """Shaped like the langfuse ^4.6 calls langfuse_experiments.py makes."""

    def __init__(self) -> None:
        self.datasets: list[dict] = []
        self.items: list[dict] = []
        self.scores: list[dict] = []
        self.spans: list[dict] = []

    def create_dataset(self, *, name, description="", metadata=None):  # type: ignore[no-untyped-def]
        self.datasets.append({"name": name, "metadata": metadata})
        return type("DS", (), {"id": name})()

    def create_dataset_item(self, *, dataset_name, input, metadata=None):  # type: ignore[no-untyped-def]
        self.items.append({"dataset_name": dataset_name, "input": input})

    def start_as_current_span(self, **kwargs):  # type: ignore[no-untyped-def]
        self.spans.append(kwargs)
        return contextlib.nullcontext()

    def create_score(self, *, trace_id, name, value, data_type=None):  # type: ignore[no-untyped-def]
        self.scores.append({"trace_id": trace_id, "name": name, "value": value})


def _sc() -> SiteConfig:
    return SiteConfig(
        initial_config={
            "langfuse_host": "h",
            "langfuse_public_key": "p",
            "langfuse_secret_key": "s",
        }
    )


def test_langfuse_harness_ensure_dataset_creates_items() -> None:
    fake = _FakeClient()
    h = LangfuseEvalHarness(site_config=_sc(), client=fake)
    gs = GoldenSet("reranker", 1, [GoldenCase("q", [{"doc_id": "1", "text": "t", "relevance": 1}])])
    ref = h.ensure_dataset(gs)
    assert len(fake.datasets) == 1
    assert len(fake.items) == 1
    assert ref  # non-empty dataset ref


def test_langfuse_harness_record_results_writes_scores_and_traces() -> None:
    fake = _FakeClient()
    h = LangfuseEvalHarness(site_config=_sc(), client=fake)
    h.record_results("run-1", _results())
    name_value = {(s["name"], s["value"]) for s in fake.scores}
    assert ("ndcg@10", 0.80) in name_value
    assert ("ndcg@10", 0.86) in name_value
    # one trace span per result, and distinct trace ids per model
    assert len(fake.spans) == 2
    trace_ids = {s["trace_context"]["trace_id"] for s in fake.spans}
    assert len(trace_ids) == 2


def test_langfuse_harness_missing_creds_fails_loud() -> None:
    # Pin the creds empty in initial_config. SiteConfig.get() priority is
    # DB > env > default and the DB layer is a *presence* check, so an explicit
    # "" here short-circuits the env fallback — otherwise CI's LANGFUSE_* vars
    # (set since #1996 re-enabled @observe tracing) would supply the creds and
    # mask the missing-creds path this test guards.
    h = LangfuseEvalHarness(
        site_config=SiteConfig(
            initial_config={
                "langfuse_host": "",
                "langfuse_public_key": "",
                "langfuse_secret_key": "",
            }
        )
    )
    import pytest

    with pytest.raises(RuntimeError, match="langfuse_host"):
        h.ensure_dataset(GoldenSet("r", 1, []))
