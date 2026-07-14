"""Tests for the EvalHarness seam (Plan 1, Task 4).

The seam is what we test. The InMemory double is exercised directly; the
Langfuse adapter is driven through an injected fake client shaped like the
proven langfuse ^4.6 surface used in services.langfuse_experiments
(create_dataset / create_dataset_item / start_as_current_span / create_score).
No real langfuse install required.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.model_eval.harness import InMemoryEvalHarness, LangfuseEvalHarness
from services.model_eval.types import GoldenCase, GoldenSet, MetricResult
from services.site_config import SiteConfig


def _results() -> list[MetricResult]:
    return [
        MetricResult("rag_rerank_model", "champ", "ndcg@10", 0.80, 10, 5, {"mrr": 0.70}),
        MetricResult("rag_rerank_model", "chall", "ndcg@10", 0.86, 10, 6, {"mrr": 0.75}),
    ]


@pytest.mark.asyncio
async def test_in_memory_harness_roundtrips_latest() -> None:
    h = InMemoryEvalHarness()
    await h.record_results("run-1", _results())
    assert await h.latest_by_model("rag_rerank_model", "ndcg@10") == {"champ": 0.80, "chall": 0.86}


@pytest.mark.asyncio
async def test_in_memory_latest_prefers_most_recent_run() -> None:
    h = InMemoryEvalHarness()
    await h.record_results("run-1", [MetricResult("rag_rerank_model", "champ", "ndcg@10", 0.80, 1, 1)])
    await h.record_results("run-2", [MetricResult("rag_rerank_model", "champ", "ndcg@10", 0.88, 1, 1)])
    assert await h.latest_by_model("rag_rerank_model", "ndcg@10") == {"champ": 0.88}


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


@pytest.mark.asyncio
async def test_langfuse_harness_ensure_dataset_creates_items() -> None:
    fake = _FakeClient()
    h = LangfuseEvalHarness(site_config=_sc(), client=fake)
    gs = GoldenSet("reranker", 1, [GoldenCase("q", [{"doc_id": "1", "text": "t", "relevance": 1}])])
    ref = await h.ensure_dataset(gs)
    assert len(fake.datasets) == 1
    assert len(fake.items) == 1
    assert ref  # non-empty dataset ref


@pytest.mark.asyncio
async def test_langfuse_harness_record_results_writes_scores_and_traces() -> None:
    fake = _FakeClient()
    h = LangfuseEvalHarness(site_config=_sc(), client=fake)
    await h.record_results("run-1", _results())
    name_value = {(s["name"], s["value"]) for s in fake.scores}
    assert ("ndcg@10", 0.80) in name_value
    assert ("ndcg@10", 0.86) in name_value
    # one trace span per result, and distinct trace ids per model
    assert len(fake.spans) == 2
    trace_ids = {s["trace_context"]["trace_id"] for s in fake.spans}
    assert len(trace_ids) == 2


@pytest.mark.asyncio
async def test_langfuse_harness_missing_creds_fails_loud() -> None:
    # Pin the creds empty. host/public_key stay on the sync SiteConfig(
    # initial_config=...) double (DB > env > default, and the explicit ""
    # short-circuits env). secret_key now reads via get_secret(), which
    # (no pool attached) falls through to env > default — the mock below
    # pins that path directly so a stray LANGFUSE_SECRET_KEY in the test
    # environment can't mask the missing-creds path this test guards, same
    # as the sync keys already guard against CI's LANGFUSE_* vars (#1996).
    sc = SiteConfig(
        initial_config={"langfuse_host": "", "langfuse_public_key": ""}
    )
    sc.get_secret = AsyncMock(return_value="")  # type: ignore[method-assign]
    h = LangfuseEvalHarness(site_config=sc)

    with pytest.raises(RuntimeError, match="langfuse_host"):
        await h.ensure_dataset(GoldenSet("r", 1, []))


@pytest.mark.asyncio
async def test_langfuse_harness_uses_get_secret_not_get_for_secret_key() -> None:
    # #2131: site_config.get() on an is_secret=true key returns ciphertext.
    # The harness must call get_secret() for langfuse_secret_key specifically
    # — prove it by returning obviously-different values from each and
    # asserting the Langfuse() constructor receives the get_secret one.
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": {
        "langfuse_host": "https://cloud.langfuse.com",
        "langfuse_public_key": "pk-lf-real",
    }.get(k, d)
    sc.get_secret = AsyncMock(return_value="sk-lf-plaintext")

    captured: dict = {}

    class _FakeLangfuse:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import services.model_eval.harness as harness_mod
    original = harness_mod.Langfuse
    harness_mod.Langfuse = _FakeLangfuse
    try:
        h = LangfuseEvalHarness(site_config=sc)
        await h._get_client()
    finally:
        harness_mod.Langfuse = original

    assert captured["secret_key"] == "sk-lf-plaintext"
