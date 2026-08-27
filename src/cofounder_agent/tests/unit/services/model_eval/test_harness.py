"""Tests for the EvalHarness seam (Plan 1, Task 4).

The seam is what we test. The InMemory double is exercised directly; the
Langfuse adapter is driven through an injected fake client shaped like the
langfuse ^4.13 surface used in services.langfuse_experiments
(create_dataset / create_dataset_item / start_as_current_observation / create_score).
No real langfuse install required.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.model_eval.harness import (
    InMemoryEvalHarness,
    LangfuseEvalHarness,
    _eval_trace_id,
)
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
    """Shaped like the langfuse ^4.13 calls langfuse_experiments.py makes."""

    def __init__(self) -> None:
        self.datasets: list[dict] = []
        self.items: list[dict] = []
        self.scores: list[dict] = []
        self.spans: list[dict] = []
        self.flushes = 0

    def flush(self):  # type: ignore[no-untyped-def]
        self.flushes += 1

    def create_dataset(self, *, name, description="", metadata=None):  # type: ignore[no-untyped-def]
        self.datasets.append({"name": name, "metadata": metadata})
        return type("DS", (), {"id": name})()

    def create_dataset_item(self, *, dataset_name, input, metadata=None):  # type: ignore[no-untyped-def]
        self.items.append({"dataset_name": dataset_name, "input": input})

    def start_as_current_observation(self, **kwargs):  # type: ignore[no-untyped-def]
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


def test_eval_trace_id_is_a_valid_otel_trace_id() -> None:
    """langfuse ^4.13 parses the trace id as an OTel id and DROPS anything
    that isn't exactly 32 lowercase hex chars — with a warning, not an error.
    A prefixed id therefore loses every eval run silently, which is how
    ``lf-meval-<32 hex>`` (41 chars) went unnoticed. Pin the shape."""
    tid = _eval_trace_id("rag_rerank_model", "cross-encoder/ms-marco-MiniLM-L-6-v2", "run-1")
    assert len(tid) == 32, tid
    assert tid == tid.lower()
    int(tid, 16)  # must parse as hex — this is the check langfuse applies

    # Deterministic per (slot, model, run), distinct across models.
    assert tid == _eval_trace_id(
        "rag_rerank_model", "cross-encoder/ms-marco-MiniLM-L-6-v2", "run-1"
    )
    assert tid != _eval_trace_id("rag_rerank_model", "other-model", "run-1")


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
    # The SDK batches on a background exporter; the one-shot CLI exits right
    # after this call, so an unflushed batch is a lost run.
    assert fake.flushes == 1


class _FakeTrace:
    def __init__(self, model, value, ts, *, slot="rag_rerank_model", metric="ndcg@10"):
        self.metadata = {"slot": slot, "model": model, "metric_name": metric}
        self.output = {metric: value}
        self.timestamp = ts
        # langfuse ^4.13: this is a list of score IDs (str), NOT score objects.
        # Reading values off it is what silently returned {} before.
        self.scores = ["abc123", "def456"]


class _FakeTraceApi:
    """Models langfuse ^4.13 `client.api.trace`: fixed kwargs, no `filter=`."""

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[dict] = []

    def list(self, *, name=None, limit=None, page=None, tags=None, **kw):
        if kw:
            raise TypeError(f"trace.list got unexpected kwargs: {sorted(kw)}")
        self.calls.append({"name": name, "limit": limit})
        rows = [r for r in self._rows if name is None or r.metadata.get("_name", name) == name]
        return type("Res", (), {"data": rows})()


def _client_with_traces(rows):
    api = _FakeTraceApi(rows)
    client = MagicMock()
    client.api = type("Api", (), {"trace": api})()
    return client, api


@pytest.mark.asyncio
async def test_latest_by_model_reads_output_and_filters_server_side() -> None:
    rows = [
        _FakeTrace("champ", 0.7776, 3),
        _FakeTrace("chall", 0.8300, 2),
        _FakeTrace("other-slot-model", 0.99, 1, slot="pipeline_critic_model"),
        _FakeTrace("wrong-metric", 0.99, 1, metric="mrr"),
    ]
    client, api = _client_with_traces(rows)
    h = LangfuseEvalHarness(site_config=_sc(), client=client)

    got = await h.latest_by_model("rag_rerank_model", "ndcg@10")

    assert got == {"champ": 0.7776, "chall": 0.8300}
    # Must filter server-side by trace name — an unfiltered list would drag in
    # every unrelated trace in the project.
    assert api.calls and api.calls[0]["name"] == "model_eval_run"


@pytest.mark.asyncio
async def test_latest_by_model_prefers_most_recent_per_model() -> None:
    rows = [
        _FakeTrace("champ", 0.60, 1),  # older
        _FakeTrace("champ", 0.85, 9),  # newer — wins regardless of list order
    ]
    client, _ = _client_with_traces(rows)
    h = LangfuseEvalHarness(site_config=_sc(), client=client)
    assert await h.latest_by_model("rag_rerank_model", "ndcg@10") == {"champ": 0.85}


@pytest.mark.asyncio
async def test_latest_by_model_survives_a_list_failure() -> None:
    client = MagicMock()
    api = MagicMock()
    api.trace.list.side_effect = TypeError("unexpected keyword argument 'filter'")
    client.api = api
    h = LangfuseEvalHarness(site_config=_sc(), client=client)
    assert await h.latest_by_model("rag_rerank_model", "ndcg@10") == {}


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
async def test_langfuse_harness_uses_get_secret_for_both_keys() -> None:
    # #2131: site_config.get() cannot serve an is_secret=true key at all —
    # SiteConfig loads `WHERE is_secret = false` into its cache, so .get()
    # returns the *default*. langfuse_public_key AND langfuse_secret_key are
    # both is_secret=true on prod, so both must go through get_secret().
    #
    # The earlier version of this test modelled .get() as serving
    # "pk-lf-real" for the public key — a SiteConfig that cannot exist — and
    # so stayed green while the real credential check failed closed on every
    # call. Model the sync path the way it actually behaves: secrets absent.
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": {
        "langfuse_host": "https://cloud.langfuse.com",
    }.get(k, d)
    sc.get_secret = AsyncMock(
        side_effect=lambda k, d="": {
            "langfuse_public_key": "pk-lf-plaintext",
            "langfuse_secret_key": "sk-lf-plaintext",
        }.get(k, d)
    )

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
    assert captured["public_key"] == "pk-lf-plaintext"
