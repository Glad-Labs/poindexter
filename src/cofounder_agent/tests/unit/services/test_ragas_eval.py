"""Tests for services/ragas_eval.py — Ragas-based RAG evaluation (#205).

The guard tests stub out the underlying Ragas + Ollama calls so the
suite stays fast (no judge-LLM round-trips, no model downloads). The
stubbed-happy-path case below relies on the ``ragas`` SDK being
importable (so ``patch('ragas.evaluate', ...)`` can resolve the target);
it is skipped when Ragas is not importable — pyproject pins
``ragas = ">=0.2,<0.5"`` nowadays, but ragas 0.4.x has broken transitive
deps against langchain-community >=0.4.2 (see ``_ragas_importable``), so
the guard still earns its keep.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from importlib.util import find_spec
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# NOTE (Glad-Labs/poindexter#997): this module previously carried an
# UNCONDITIONAL ``pytest.skip(..., allow_module_level=True)`` for a Windows +
# Python 3.12 pyarrow native-init access violation (``from datasets import
# Dataset`` inside evaluate_sample). Because the skip wasn't platform-guarded
# it also skipped Linux CI, so these tests never ran anywhere. The repo has
# since moved to Python 3.13 (pyproject ``>=3.13,<3.14``), where pyarrow 24.x
# imports cleanly on Windows too — verified the import chain no longer
# segfaults — so the skip is stale on both counts and has been removed. The
# happy-path test still guards on Ragas being installed via ``requires_ragas``.
from services.ragas_eval import evaluate_sample, is_enabled


def _ragas_importable() -> bool:
    """Return True only when ragas is installed AND its transitive deps resolve.

    ragas 0.4.x imports langchain-community internals (chat_models.vertexai)
    that were removed in langchain-community 0.4.2.  A find_spec() check alone
    doesn't catch that breakage — try-import does.
    """
    if find_spec("ragas") is None:
        return False
    try:
        __import__("ragas")
        return True
    except ImportError:
        return False


requires_ragas = pytest.mark.skipif(
    not _ragas_importable(),
    reason="Ragas not importable (missing or has broken transitive deps).",
)


# ---------------------------------------------------------------------------
# is_enabled — operator gate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsEnabled:
    def test_no_site_config_returns_false(self):
        assert is_enabled(None) is False

    def test_default_returns_false(self):
        sc = MagicMock()
        sc.get_bool.return_value = False
        assert is_enabled(sc) is False

    def test_true_setting_enables(self):
        sc = MagicMock()
        sc.get_bool.return_value = True
        assert is_enabled(sc) is True

    def test_string_true_falls_back_through_get(self):
        sc = MagicMock()
        sc.get_bool.side_effect = AttributeError("no get_bool")
        sc.get.return_value = "true"
        assert is_enabled(sc) is True


# ---------------------------------------------------------------------------
# evaluate_sample — guards + error handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvaluateSampleGuards:
    @pytest.mark.asyncio
    async def test_empty_topic_returns_minus_one(self):
        result = await evaluate_sample(topic="", generated_content="content")
        assert result == {
            "faithfulness": -1.0,
            "answer_relevancy": -1.0,
            "context_precision": -1.0,
        }

    @pytest.mark.asyncio
    async def test_empty_content_returns_minus_one(self):
        result = await evaluate_sample(topic="Topic", generated_content="")
        assert result == {
            "faithfulness": -1.0,
            "answer_relevancy": -1.0,
            "context_precision": -1.0,
        }

    @pytest.mark.asyncio
    async def test_ragas_failure_returns_minus_one_no_raise(self):
        # Fake the ragas/datasets modules so the lazy imports succeed even
        # where ragas isn't importable — this test exercises the RUNTIME
        # failure path (backend down → sentinels), not import breakage
        # (which now fails loud by design — poindexter#839).
        with patch(
            "services.ragas_eval._build_ragas_models",
            side_effect=Exception("ollama down"),
        ), _inject_fake_modules({
            "datasets": MagicMock(),
            "ragas": MagicMock(),
            "ragas.metrics": MagicMock(),
        }):
            result = await evaluate_sample(
                topic="Topic", generated_content="content",
            )
        assert all(v == -1.0 for v in result.values())


# ---------------------------------------------------------------------------
# evaluate_sample — import breakage fails LOUD (poindexter#839)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvaluateSampleImportError:
    """A missing/broken ragas dependency is a deployment regression, not a
    transient eval failure. Swallowing it into all -1.0 sentinels made the
    2026-06-29→07-11 dead rail read as 'judge or embedding backend likely
    unreachable' for 12 days (ragas 0.4.3 imports
    langchain_community.chat_models.vertexai, removed in langchain-community
    0.4.2). evaluate_sample must re-raise ImportError so the caller can
    surface the true cause."""

    @pytest.mark.asyncio
    async def test_import_error_propagates(self):
        # Fake ragas/datasets so the lazy `from ragas import evaluate`
        # succeeds regardless of the local install, making the patched
        # _build_ragas_models raise the ONE ImportError under test.
        with patch(
            "services.ragas_eval._build_ragas_models",
            side_effect=ModuleNotFoundError(
                "No module named 'langchain_community.chat_models.vertexai'"
            ),
        ), _inject_fake_modules({
            "datasets": MagicMock(),
            "ragas": MagicMock(),
            "ragas.metrics": MagicMock(),
        }):
            with pytest.raises(
                ImportError, match="langchain_community.chat_models.vertexai",
            ):
                await evaluate_sample(topic="T", generated_content="c")


# ---------------------------------------------------------------------------
# _coerce_metric — the -1.0-sentinel boundary for raw Ragas metric values.
# The old expression ``float(scores_raw.get(k, -1.0) or -1.0)`` had two
# falsy-logic bugs: NaN is truthy (Ragas reports failed metrics as NaN under
# raise_exceptions=False, and the NaN sailed through into the ragas_score
# audit details, where json.dumps emitted the literal ``NaN`` that Postgres
# jsonb rejects — losing the row), and 0.0 is falsy (a genuine hard-zero
# score was silently replaced by the failure sentinel).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCoerceMetric:
    def test_nan_becomes_sentinel(self):
        from services.ragas_eval import _coerce_metric

        assert _coerce_metric(float("nan")) == -1.0

    def test_infinities_become_sentinel(self):
        from services.ragas_eval import _coerce_metric

        assert _coerce_metric(float("inf")) == -1.0
        assert _coerce_metric(float("-inf")) == -1.0

    def test_none_becomes_sentinel(self):
        from services.ragas_eval import _coerce_metric

        assert _coerce_metric(None) == -1.0

    def test_unparseable_becomes_sentinel(self):
        from services.ragas_eval import _coerce_metric

        assert _coerce_metric("not-a-number") == -1.0

    def test_zero_is_a_real_score_not_a_sentinel(self):
        from services.ragas_eval import _coerce_metric

        assert _coerce_metric(0.0) == 0.0

    def test_normal_score_passes_through(self):
        from services.ragas_eval import _coerce_metric

        assert _coerce_metric(0.85) == 0.85


# ---------------------------------------------------------------------------
# _build_ragas_models — JSON-format constraint regression (GH #1910)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildRagasModels:
    @pytest.mark.asyncio
    async def test_chat_ollama_receives_json_format_kwarg(self):
        """ChatOllama must be initialized with format='json'.

        Without Ollama's constrained decoding, phi4:14b (the ragas_judge_model
        fallback) wraps JSON responses in markdown code fences which cause
        RagasOutputParserException on every metric — including the fix_output_format
        retry. All Ragas 0.4.x internal prompts expect bare JSON, so JSON-mode is
        safe for all three metrics. Regression guard for GH #1910.

        Uses sys.modules injection so the test runs in CI even when ragas is not
        installed (the function's local imports become fakes; only the ChatOllama
        call_args matter)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.ragas_eval import _build_ragas_models

        mock_chat_cls = MagicMock()
        fake_langchain_ollama = MagicMock()
        fake_langchain_ollama.ChatOllama = mock_chat_cls
        fake_langchain_ollama.OllamaEmbeddings = MagicMock()

        fake_ragas_llms = MagicMock()
        fake_ragas_embeddings = MagicMock()

        with (
            patch(
                "services.ragas_eval._resolve_judge_model",
                new_callable=AsyncMock,
                return_value="phi4:14b",
            ),
            _inject_fake_modules({
                "langchain_ollama": fake_langchain_ollama,
                "ragas": MagicMock(),
                "ragas.llms": fake_ragas_llms,
                "ragas.embeddings": fake_ragas_embeddings,
            }),
        ):
            await _build_ragas_models(None)

        mock_chat_cls.assert_called_once()
        _, kwargs = mock_chat_cls.call_args
        assert kwargs.get("format") == "json", (
            "ChatOllama must be called with format='json' so Ollama's "
            "constrained decoding prevents markdown-wrapped JSON that causes "
            "RagasOutputParserException. See GH #1910."
        )


# ---------------------------------------------------------------------------
# Dispatcher-backed wrappers (poindexter#826)
# ---------------------------------------------------------------------------


def _identity_wrapper_modules() -> dict[str, Any]:
    """sys.modules fakes where the Ragas wrappers are identity functions.

    Lets the tests reach the inner LangChain adapters without a working
    ragas install (its 0.4.x transitive deps are broken in some envs —
    see ``requires_ragas``). ``langchain_core`` is a real dependency.
    """
    from unittest.mock import MagicMock

    fake_ragas_llms = MagicMock()
    fake_ragas_llms.LangchainLLMWrapper = MagicMock(side_effect=lambda x: x)
    fake_ragas_embeddings = MagicMock()
    fake_ragas_embeddings.LangchainEmbeddingsWrapper = MagicMock(
        side_effect=lambda x: x,
    )
    return {
        "ragas": MagicMock(),
        "ragas.llms": fake_ragas_llms,
        "ragas.embeddings": fake_ragas_embeddings,
    }


@contextmanager
def _inject_fake_modules(fake_modules: dict[str, Any]):
    """Insert fake ``sys.modules`` entries, restoring ONLY those keys on exit.

    ``patch.dict(sys.modules, ...)`` is the obvious tool, but it's a footgun for
    these tests: on exit it clears ``sys.modules`` wholesale and repopulates it
    from an enter-time snapshot, DROPPING any module imported *inside* the block.
    ``_build_ragas_models`` / ``_build_dispatcher_ragas_wrappers`` lazily import
    ``langchain_core`` (-> ``transformers`` -> ``torch``) inside these blocks.
    torch's C extension attaches docstrings to native functions at import and is
    NOT re-import-safe, so once it's dropped the next test's re-import raises
    ``RuntimeError: function '_has_torch_function' already has a docstring`` --
    a test-order-dependent intra-file failure where the first dispatcher test
    passes and poisons every one after it.

    Touching only the faked keys leaves the real heavy modules cached across
    tests, so the pollution can't happen regardless of test ordering.
    """
    saved = {name: sys.modules.get(name) for name in fake_modules}
    sys.modules.update(fake_modules)
    try:
        yield
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.mark.unit
class TestDispatcherWrappers:
    """With a ``pool``, Ragas judge + embeddings route through the
    LiteLLM dispatcher instead of langchain-ollama's own transport."""

    @pytest.mark.asyncio
    async def test_pool_prefers_dispatcher_over_chat_ollama(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from services.ragas_eval import _build_ragas_models

        mock_chat_cls = MagicMock()
        fake_langchain_ollama = MagicMock()
        fake_langchain_ollama.ChatOllama = mock_chat_cls
        fake_langchain_ollama.OllamaEmbeddings = MagicMock()

        with (
            patch(
                "services.ragas_eval._resolve_judge_model",
                new_callable=AsyncMock,
                return_value="phi4:14b",
            ),
            _inject_fake_modules({
                "langchain_ollama": fake_langchain_ollama,
                **_identity_wrapper_modules(),
            }),
        ):
            llm, embeddings = await _build_ragas_models(None, pool="POOL")

        mock_chat_cls.assert_not_called()
        # Identity wrappers → the adapters themselves come back.
        assert type(llm).__name__ == "_DispatcherChatModel"
        assert type(embeddings).__name__ == "_DispatcherEmbeddings"

    @pytest.mark.asyncio
    async def test_agenerate_routes_through_dispatch_complete(self, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from langchain_core.messages import HumanMessage

        from services.ragas_eval import _build_dispatcher_ragas_wrappers

        dispatch_mock = AsyncMock(
            return_value=SimpleNamespace(text='{"statements": []}'),
        )
        monkeypatch.setattr(
            "services.llm_providers.dispatcher.dispatch_complete", dispatch_mock,
        )
        with _inject_fake_modules(_identity_wrapper_modules()):
            llm, _ = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        result = await llm._agenerate([HumanMessage(content="judge this")])

        assert result.generations[0].message.content == '{"statements": []}'
        kwargs = dispatch_mock.call_args.kwargs
        assert kwargs["pool"] == "POOL"
        assert kwargs["model"] == "phi4:14b"
        assert kwargs["phase"] == "qa_ragas_judge"
        # The #1910 JSON-mode constraint rides response_format now.
        assert kwargs["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_aembed_routes_through_dispatch_embed(self, monkeypatch):
        from unittest.mock import AsyncMock

        from services.ragas_eval import _build_dispatcher_ragas_wrappers

        embed_mock = AsyncMock(return_value=[0.1, 0.2])
        monkeypatch.setattr(
            "services.llm_providers.dispatcher.dispatch_embed", embed_mock,
        )
        with _inject_fake_modules(_identity_wrapper_modules()):
            _, embeddings = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        vec = await embeddings.aembed_query("some text")
        docs = await embeddings.aembed_documents(["a", "b"])

        assert vec == [0.1, 0.2]
        assert docs == [[0.1, 0.2], [0.1, 0.2]]
        assert embed_mock.await_count == 3

    @pytest.mark.asyncio
    async def test_llm_sync_path_raises(self):
        """Ragas always drives the chat model through _agenerate, never
        _generate, so this one stays a loud raise (unchanged by #847)."""
        from langchain_core.messages import HumanMessage

        from services.ragas_eval import _build_dispatcher_ragas_wrappers

        with _inject_fake_modules(_identity_wrapper_modules()):
            llm, _ = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        with pytest.raises(NotImplementedError):
            llm._generate([HumanMessage(content="x")])

    def test_embed_sync_paths_bridge_with_no_running_loop(self, monkeypatch):
        """No event loop running (a plain script/CLI call reaching these
        methods directly) — the sync embed_query/embed_documents must
        still return real vectors instead of raising (poindexter#847)."""
        from unittest.mock import AsyncMock

        from services.ragas_eval import _build_dispatcher_ragas_wrappers

        embed_mock = AsyncMock(return_value=[0.1, 0.2])
        monkeypatch.setattr(
            "services.llm_providers.dispatcher.dispatch_embed", embed_mock,
        )
        with _inject_fake_modules(_identity_wrapper_modules()):
            _, embeddings = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        vec = embeddings.embed_query("some text")
        docs = embeddings.embed_documents(["a", "b"])

        assert vec == [0.1, 0.2]
        assert docs == [[0.1, 0.2], [0.1, 0.2]]

    def test_embed_sync_paths_bridge_from_inside_ragas_nested_loop(self, monkeypatch):
        """Reproduces the actual production failure: Ragas 0.4.3's
        ResponseRelevancy.calculate_similarity (the answer_relevancy
        metric) calls embed_query/embed_documents SYNCHRONOUSLY from
        inside its own async _ascore — always after Ragas's own
        Executor.results() has nest_asyncio-patched the running loop
        (ragas/executor.py::apply_nest_asyncio). Simulates that exact
        nesting without needing a real Ragas install, so the test proves
        the bridge survives the actual call shape, not just the trivial
        no-loop case above."""
        import asyncio
        from unittest.mock import AsyncMock

        from services.ragas_eval import _build_dispatcher_ragas_wrappers

        embed_mock = AsyncMock(return_value=[0.3, 0.4])
        monkeypatch.setattr(
            "services.llm_providers.dispatcher.dispatch_embed", embed_mock,
        )
        with _inject_fake_modules(_identity_wrapper_modules()):
            _, embeddings = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        async def _ragas_like_ascore():
            import nest_asyncio

            nest_asyncio.apply()
            # Synchronous call from inside a running (now-patched) loop —
            # exactly what ResponseRelevancy.calculate_similarity does.
            return embeddings.embed_query("some text")

        result = asyncio.run(_ragas_like_ascore())
        assert result == [0.3, 0.4]


# ---------------------------------------------------------------------------
# evaluate_sample — happy path with stubbed Ragas
# ---------------------------------------------------------------------------


@pytest.mark.unit
@requires_ragas
class TestEvaluateSampleStubbed:
    @pytest.mark.asyncio
    async def test_returns_three_metric_scores(self):
        """Stub the entire ragas.evaluate path so the test doesn't hit
        Ollama. Verifies the result shape + score extraction."""
        fake_result = MagicMock()
        fake_result.scores = [{
            "faithfulness": 0.85,
            "answer_relevancy": 0.91,
            "context_precision": 0.72,
        }]

        with patch(
            "services.ragas_eval._build_ragas_models",
            return_value=(MagicMock(), MagicMock()),
        ), patch("ragas.evaluate", return_value=fake_result), patch(
            "datasets.Dataset.from_dict", return_value=MagicMock(),
        ):
            result = await evaluate_sample(
                topic="Bootstrapping a SaaS",
                generated_content="A long blog post...",
                retrieved_contexts=["Indie hacker forum thread", "HN comments"],
            )

        # Floats in the [0, 1] range, all three metrics present.
        assert set(result.keys()) == {
            "faithfulness", "answer_relevancy", "context_precision",
        }
        assert result["faithfulness"] == 0.85
        assert result["answer_relevancy"] == 0.91
        assert result["context_precision"] == 0.72


# ---------------------------------------------------------------------------
# evaluate_sample — NaN metric handling (fake-module stubbed so it runs even
# where ragas is not installed, unlike the requires_ragas-guarded class above)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvaluateSampleNonFinite:
    @pytest.mark.asyncio
    async def test_nan_metric_coerced_to_sentinel_and_kept_out_of_audit(self):
        """Ragas (raise_exceptions=False) reports a failed metric as NaN.
        NaN is truthy, so the old ``or -1.0`` guard let it through — and
        the ragas_score audit write died on Postgres jsonb rejecting the
        NaN literal (Loki: 'Failed to write audit log event=ragas_score',
        40+ drops 2026-06-18→06-28). NaN must collapse to the documented
        -1.0 sentinel at the evaluate_sample boundary, and the audit
        emission must stay JSON-compliant."""
        fake_result = MagicMock()
        fake_result.scores = [{
            "faithfulness": 0.85,
            "answer_relevancy": float("nan"),
            "context_precision": 0.72,
        }]
        fake_ragas = MagicMock()
        fake_ragas.evaluate = MagicMock(return_value=fake_result)
        fake_datasets = MagicMock()
        fake_datasets.Dataset.from_dict = MagicMock(return_value=MagicMock())

        with (
            patch(
                "services.ragas_eval._build_ragas_models",
                return_value=(MagicMock(), MagicMock()),
            ),
            _inject_fake_modules({
                "datasets": fake_datasets,
                "ragas": fake_ragas,
                "ragas.metrics": MagicMock(),
            }),
            patch("services.audit_log.audit_log_bg") as mock_bg,
        ):
            result = await evaluate_sample(
                topic="Topic",
                generated_content="content",
                retrieved_contexts=["ctx"],
            )

        assert result["faithfulness"] == 0.85
        assert result["answer_relevancy"] == -1.0
        assert result["context_precision"] == 0.72

        # Two audit_log_bg calls: the ragas_score row itself, plus the
        # qa_rail_degraded finding _emit_degraded_metrics_finding raises for
        # the failed metric (poindexter#847 Ask #2, PR #2424). Locate by
        # event_type rather than call count/order — whether utils.findings
        # happens to already be import-cached from an earlier test in the
        # same process changes which mock call it resolves against.
        ragas_score_calls = [
            c for c in mock_bg.call_args_list if c.args and c.args[0] == "ragas_score"
        ]
        assert len(ragas_score_calls) == 1
        # The failed metric is a sentinel (excluded from the average), and
        # the details dict json-serializes under RFC-compliant rules — the
        # exact property whose absence killed the Postgres insert.
        details = ragas_score_calls[0].args[2]
        json.dumps(details, allow_nan=False)  # raises ValueError on NaN/inf
        assert details["answer_relevancy"] == -1.0
        assert details["metric_count"] == 2
        assert details["score"] == pytest.approx((0.85 + 0.72) / 2, abs=1e-4)
