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
from poindexter.services.ragas_eval import evaluate_sample, is_enabled


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
            "poindexter.services.ragas_eval._build_ragas_models",
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
            "poindexter.services.ragas_eval._build_ragas_models",
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
        from poindexter.services.ragas_eval import _coerce_metric

        assert _coerce_metric(float("nan")) == -1.0

    def test_infinities_become_sentinel(self):
        from poindexter.services.ragas_eval import _coerce_metric

        assert _coerce_metric(float("inf")) == -1.0
        assert _coerce_metric(float("-inf")) == -1.0

    def test_none_becomes_sentinel(self):
        from poindexter.services.ragas_eval import _coerce_metric

        assert _coerce_metric(None) == -1.0

    def test_unparseable_becomes_sentinel(self):
        from poindexter.services.ragas_eval import _coerce_metric

        assert _coerce_metric("not-a-number") == -1.0

    def test_zero_is_a_real_score_not_a_sentinel(self):
        from poindexter.services.ragas_eval import _coerce_metric

        assert _coerce_metric(0.0) == 0.0

    def test_normal_score_passes_through(self):
        from poindexter.services.ragas_eval import _coerce_metric

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

        from poindexter.services.ragas_eval import _build_ragas_models

        mock_chat_cls = MagicMock()
        fake_langchain_ollama = MagicMock()
        fake_langchain_ollama.ChatOllama = mock_chat_cls
        fake_langchain_ollama.OllamaEmbeddings = MagicMock()

        fake_ragas_llms = MagicMock()
        fake_ragas_embeddings = MagicMock()

        with (
            patch(
                "poindexter.services.ragas_eval._resolve_judge_model",
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

        from poindexter.services.ragas_eval import _build_ragas_models

        mock_chat_cls = MagicMock()
        fake_langchain_ollama = MagicMock()
        fake_langchain_ollama.ChatOllama = mock_chat_cls
        fake_langchain_ollama.OllamaEmbeddings = MagicMock()

        with (
            patch(
                "poindexter.services.ragas_eval._resolve_judge_model",
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

        from poindexter.services.ragas_eval import _build_dispatcher_ragas_wrappers

        dispatch_mock = AsyncMock(
            return_value=SimpleNamespace(text='{"statements": []}'),
        )
        monkeypatch.setattr(
            "poindexter.services.llm_providers.dispatcher.dispatch_complete", dispatch_mock,
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

        from poindexter.services.ragas_eval import _build_dispatcher_ragas_wrappers

        embed_mock = AsyncMock(return_value=[0.1, 0.2])
        monkeypatch.setattr(
            "poindexter.services.llm_providers.dispatcher.dispatch_embed", embed_mock,
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

        from poindexter.services.ragas_eval import _build_dispatcher_ragas_wrappers

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

        from poindexter.services.ragas_eval import _build_dispatcher_ragas_wrappers

        embed_mock = AsyncMock(return_value=[0.1, 0.2])
        monkeypatch.setattr(
            "poindexter.services.llm_providers.dispatcher.dispatch_embed", embed_mock,
        )
        with _inject_fake_modules(_identity_wrapper_modules()):
            _, embeddings = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        vec = embeddings.embed_query("some text")
        docs = embeddings.embed_documents(["a", "b"])

        assert vec == [0.1, 0.2]
        assert docs == [[0.1, 0.2], [0.1, 0.2]]

    def test_embed_sync_path_bridges_from_the_worker_thread(self, monkeypatch):
        """Reproduces the production call shape AFTER poindexter#1053.

        Ragas 0.4.3's ResponseRelevancy.calculate_similarity (the
        answer_relevancy metric) calls embed_query/embed_documents
        SYNCHRONOUSLY from inside its own async _ascore. That used to happen on
        the flow's own loop, where the only way through was nest_asyncio
        re-entrancy — which corrupted the loop's ready queue and crashed the
        flow with ``IndexError: pop from an empty deque``.

        Now ``ragas.evaluate`` runs in a worker thread, so the sync call
        arrives from off the flow thread and the bridge hands the pool-bound
        coroutine BACK to the flow's loop. This asserts the handoff actually
        lands there — the whole point is that asyncpg work stays on the loop
        that owns the pool.
        """
        import asyncio
        import threading

        from poindexter.services.ragas_eval import (
            _OWNING_LOOP,
            _build_dispatcher_ragas_wrappers,
        )

        ran_on: dict[str, Any] = {}

        async def _fake_embed(pool, text, model):
            ran_on["loop"] = asyncio.get_running_loop()
            ran_on["thread"] = threading.get_ident()
            return [0.3, 0.4]

        monkeypatch.setattr(
            "poindexter.services.llm_providers.dispatcher.dispatch_embed", _fake_embed,
        )
        with _inject_fake_modules(_identity_wrapper_modules()):
            _, embeddings = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        async def _flow():
            flow_loop = asyncio.get_running_loop()
            _OWNING_LOOP.set((flow_loop, threading.get_ident()))

            def _ragas_like_worker():
                # Synchronous call from Ragas's thread — what
                # calculate_similarity does, now off the flow thread.
                assert threading.get_ident() != ran_on.get("flow_thread")
                return embeddings.embed_query("some text")

            ran_on["flow_thread"] = threading.get_ident()
            result = await asyncio.to_thread(_ragas_like_worker)
            return result, flow_loop

        result, flow_loop = asyncio.run(_flow())

        assert result == [0.3, 0.4]
        # The pool-bound coroutine ran on the FLOW's loop, in the flow's
        # thread — not on whatever loop Ragas spun up in the worker.
        assert ran_on["loop"] is flow_loop
        assert ran_on["thread"] == ran_on["flow_thread"]

    def test_judge_call_is_bridged_to_the_owning_loop(self, monkeypatch):
        """The judge path touches the pool too (cost logging), and since
        poindexter#1053 ``_agenerate`` runs on Ragas's worker loop. Assert the
        dispatch lands on the FLOW's loop rather than driving asyncpg from a
        foreign one."""
        import asyncio
        import threading
        from types import SimpleNamespace

        from poindexter.services.ragas_eval import (
            _OWNING_LOOP,
            _build_dispatcher_ragas_wrappers,
        )

        ran_on: dict[str, Any] = {}

        async def _fake_dispatch(**kwargs):
            ran_on["loop"] = asyncio.get_running_loop()
            return SimpleNamespace(text="judged")

        monkeypatch.setattr(
            "poindexter.services.llm_providers.dispatcher.dispatch_complete",
            _fake_dispatch,
        )
        with _inject_fake_modules(_identity_wrapper_modules()):
            llm, _ = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        async def _flow():
            flow_loop = asyncio.get_running_loop()
            _OWNING_LOOP.set((flow_loop, threading.get_ident()))

            def _worker():
                # Ragas's own loop, in its own thread.
                return asyncio.run(
                    llm._agenerate([SimpleNamespace(type="human", content="hi")])
                )

            result = await asyncio.to_thread(_worker)
            return result, flow_loop

        result, flow_loop = asyncio.run(_flow())
        assert result.generations[0].message.content == "judged"
        assert ran_on["loop"] is flow_loop

    def test_bridge_refuses_to_re_enter_the_flow_loop(self, monkeypatch):
        """The #1053 shape itself: reaching the sync bridge ON the owning
        loop's thread means evaluate() was driven from the flow loop, which is
        what nest_asyncio used to paper over by re-entering it. Refuse loudly
        instead — a crashed flow that strands the task is far worse than a
        degraded rail."""
        import asyncio
        import threading

        from poindexter.services.ragas_eval import _OWNING_LOOP, _run_embed_coro

        async def _noop():
            return None

        async def _on_the_flow_loop():
            _OWNING_LOOP.set((asyncio.get_running_loop(), threading.get_ident()))
            coro = _noop()
            try:
                with pytest.raises(RuntimeError, match="owning loop's thread"):
                    _run_embed_coro(coro)
            finally:
                coro.close()

        asyncio.run(_on_the_flow_loop())

    def test_bridge_falls_back_to_asyncio_run_with_no_owning_loop(self):
        """The CLI path — no flow, no registered loop, no running loop."""
        from poindexter.services.ragas_eval import _OWNING_LOOP, _run_embed_coro

        async def _work():
            return "cli"

        token = _OWNING_LOOP.set(None)
        try:
            assert _run_embed_coro(_work()) == "cli"
        finally:
            _OWNING_LOOP.reset(token)

    def test_nest_asyncio_is_no_longer_imported_or_applied(self):
        """poindexter#1053 — the re-entrancy is gone, not merely guarded.

        A future edit that reintroduces ``nest_asyncio.apply()`` here brings
        the flow crash back, so pin its absence in the AST rather than trust
        the comment. Checks real usage, not the word: the module docstring
        explains the history and must stay free to name it.
        """
        import ast
        import inspect

        from poindexter.services import ragas_eval

        tree = ast.parse(inspect.getsource(ragas_eval))
        imported = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in (node.names or [])
            if "nest_asyncio" in (alias.name or "")
        ] + [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and "nest_asyncio" in (node.module or "")
        ]
        assert imported == [], f"nest_asyncio imported: {imported}"

        applied = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "nest_asyncio"
        ]
        assert applied == [], "nest_asyncio.* is referenced in code"


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
            "poindexter.services.ragas_eval._build_ragas_models",
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
                "poindexter.services.ragas_eval._build_ragas_models",
                return_value=(MagicMock(), MagicMock()),
            ),
            _inject_fake_modules({
                "datasets": fake_datasets,
                "ragas": fake_ragas,
                "ragas.metrics": MagicMock(),
            }),
            patch("poindexter.services.audit_log.audit_log_bg") as mock_bg,
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


# ---------------------------------------------------------------------------
# poindexter#1035 — the Ragas job timeout is a setting, not the library's 180 s
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunConfigFromSettings:
    """Each metric job is several sequential judge calls at 60–107 s on the
    thinking judge; Ragas's default 180 s per-job timeout collapsed
    faithfulness/context_precision to the -1.0 sentinel on 27 of 39 passes
    while the rail read as present. evaluate() must receive a RunConfig built
    from ``ragas_job_timeout_seconds`` / ``ragas_max_workers``."""

    @pytest.mark.asyncio
    async def test_evaluate_receives_run_config_from_site_config(self):
        from poindexter.services.site_config import SiteConfig

        captured: dict[str, Any] = {}

        def fake_evaluate(ds, **kwargs):
            captured.update(kwargs)
            result = MagicMock()
            result.scores = [{"faithfulness": 0.9, "answer_relevancy": 0.8, "context_precision": 0.7}]
            return result

        def fake_run_config(**kwargs):
            captured["run_config_kwargs"] = kwargs
            return ("RunConfig", kwargs)

        fake_ragas = MagicMock()
        fake_ragas.evaluate = fake_evaluate
        fake_ragas.RunConfig = fake_run_config
        fake_datasets = MagicMock()
        fake_datasets.Dataset.from_dict = lambda d: d
        sc = SiteConfig(initial_config={"ragas_job_timeout_seconds": "900", "ragas_max_workers": "2"})
        with patch(
            "poindexter.services.ragas_eval._build_ragas_models",
            return_value=(MagicMock(), MagicMock()),
        ), patch("poindexter.services.ragas_eval._emit_ragas_score_audit", lambda *a, **k: None), _inject_fake_modules({
            "datasets": fake_datasets,
            "ragas": fake_ragas,
            "ragas.metrics": MagicMock(),
        }):
            result = await evaluate_sample(
                topic="Topic", generated_content="content", site_config=sc,
            )
        assert result["faithfulness"] == 0.9
        assert captured["run_config_kwargs"] == {"timeout": 900, "max_workers": 2}
        assert captured["run_config"] == ("RunConfig", {"timeout": 900, "max_workers": 2})

    def test_int_setting_falls_back_to_default(self):
        from poindexter.services.ragas_eval import _int_setting
        from poindexter.services.site_config import SiteConfig

        assert _int_setting(None, "ragas_job_timeout_seconds", 600) == 600
        assert _int_setting(SiteConfig(initial_config={}), "ragas_job_timeout_seconds", 600) == 600
        assert _int_setting(SiteConfig(initial_config={"ragas_job_timeout_seconds": "abc"}), "ragas_job_timeout_seconds", 600) == 600
        assert _int_setting(SiteConfig(initial_config={"ragas_job_timeout_seconds": "42"}), "ragas_job_timeout_seconds", 600) == 42


# ---------------------------------------------------------------------------
# Degraded-metric findings must name their rail (poindexter#1035)
# ---------------------------------------------------------------------------


class TestDegradedFindingAttribution:
    """#1035 triaged three `qa_rail_degraded` alerts as separate per-rail bugs
    and found they moved as one. Reproducing that analysis today, the LARGEST
    bucket — 80 findings in 30 days — grouped under a blank rail, because this
    emitter was the only `qa_rail_degraded` producer not setting `extra.rail`.

    The dedup key was rail-scoped (`qa_rail_degraded:ragas:<metric>`) the whole
    time, so throttling worked and only the analysis surface was blind. That is
    the worst shape: the data looks present and is silently unattributable.
    """

    def _emit(self, monkeypatch, metrics):
        from poindexter.services import ragas_eval

        captured = {}
        monkeypatch.setattr(
            "poindexter.utils.findings.emit_finding",
            lambda **kw: captured.update(kw),
        )
        ragas_eval._emit_degraded_metrics_finding(metrics, "task-123")
        return captured

    def test_finding_names_the_rail(self, monkeypatch):
        kw = self._emit(monkeypatch, ["faithfulness"])
        assert kw["extra"]["rail"] == "ragas_eval"

    def test_existing_fields_are_preserved(self, monkeypatch):
        kw = self._emit(monkeypatch, ["faithfulness", "context_precision"])
        assert kw["extra"]["failed_metrics"] == ["faithfulness", "context_precision"]
        assert kw["extra"]["task_id"] == "task-123"
        assert kw["kind"] == "qa_rail_degraded"
        assert kw["severity"] == "warn"

    def test_dedup_key_stays_metric_scoped(self, monkeypatch):
        """Unchanged: a chronic per-metric failure pages once, not once per
        post. The rail label is for grouping, not throttling."""
        a = self._emit(monkeypatch, ["faithfulness"])
        b = self._emit(monkeypatch, ["context_precision"])
        assert a["dedup_key"] != b["dedup_key"]
        assert a["dedup_key"].startswith("qa_rail_degraded:ragas:")

    def test_emitter_never_raises(self, monkeypatch):
        from poindexter.services import ragas_eval

        monkeypatch.setattr(
            "poindexter.utils.findings.emit_finding",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("sink down")),
        )
        ragas_eval._emit_degraded_metrics_finding(["faithfulness"], None)


# ---------------------------------------------------------------------------
# GPU-busy skips must survive Ragas's executor (poindexter#914 P2)
# ---------------------------------------------------------------------------


class TestGpuBusySurvivesRagasExecutor:
    """`ragas.evaluate(raise_exceptions=False)` catches ANY exception raised
    inside a metric and collapses it to a sentinel — GpuBusyError included. So
    `evaluate_sample`'s `except GpuBusyError` sat outside `evaluate()` and could
    never fire on this path.

    Measured 2026-09-18: the scheduler recorded **706** `qa_ragas_judge`
    admission rejections and `qa_rail_gpu_busy_skip` fired **zero** times, ever
    — the skips were reported as `qa_rail_degraded` "metric(s) failing", which
    is exactly the confusion the distinct kind was introduced to prevent ("a
    contention skip and a broken rail produce the same sentinel scores").

    The wrapper now records the rejection on the way past. The exception still
    propagates into Ragas and still sentinels the metric, so scoring behaviour
    is unchanged; only the reported cause is right.
    """

    def test_recorder_captures_when_armed(self):
        from poindexter.services import ragas_eval

        seen: list = []
        token = ragas_eval._GPU_BUSY_SEEN.set(seen)
        try:
            ragas_eval._record_gpu_busy("busy-1")
            ragas_eval._record_gpu_busy("busy-2")
        finally:
            ragas_eval._GPU_BUSY_SEEN.reset(token)
        assert seen == ["busy-1", "busy-2"]

    def test_recorder_is_a_no_op_when_unarmed(self):
        """The CLI path calls the wrappers without arming the holder; a bare
        record must not raise there."""
        from poindexter.services import ragas_eval

        token = ragas_eval._GPU_BUSY_SEEN.set(None)
        try:
            ragas_eval._record_gpu_busy("busy")  # must not raise
        finally:
            ragas_eval._GPU_BUSY_SEEN.reset(token)

    def test_holder_crosses_the_worker_thread(self):
        """`evaluate()` runs under `asyncio.to_thread`, which copies the
        context — the whole mechanism depends on the list being shared across
        that hop, the same way `_OWNING_LOOP` is."""
        import asyncio

        from poindexter.services import ragas_eval

        async def _main():
            seen: list = []
            ragas_eval._GPU_BUSY_SEEN.set(seen)

            def _in_worker():
                ragas_eval._record_gpu_busy("from-worker")

            await asyncio.to_thread(_in_worker)
            return seen

        assert asyncio.run(_main()) == ["from-worker"]


class TestGpuBusyIsReportedAsContentionNotBreakage:
    """End to end: a judge call refused by admission must surface as
    `qa_rail_gpu_busy_skip`, not as `qa_rail_degraded` metric failure."""

    async def test_busy_inside_evaluate_reports_a_contention_skip(self):
        """Simulates what Ragas actually does: the metric raises internally and
        `raise_exceptions=False` swallows it, so `evaluate()` returns sentinels
        and nothing propagates. Before the fix the caller had no way to tell
        this apart from a broken judge."""
        from poindexter.services import ragas_eval
        from poindexter.services.gpu_admission import GpuBusyError

        busy = GpuBusyError("no_fit", 240.0)

        def _fake_evaluate(*a, **k):
            # what a judge call does on the way past, before Ragas eats it
            ragas_eval._record_gpu_busy(busy)
            out = MagicMock()
            out.scores = [{}]           # metrics collapsed to sentinels
            return out

        with patch(
            "poindexter.services.ragas_eval._build_ragas_models",
            return_value=(MagicMock(), MagicMock()),
        ), patch("ragas.evaluate", _fake_evaluate), patch(
            "datasets.Dataset.from_dict", return_value=MagicMock(),
        ), patch(
            "poindexter.services.ragas_eval._surface_gpu_busy_skip"
        ) as skip, patch(
            "poindexter.services.ragas_eval._emit_degraded_metrics_finding"
        ) as degraded:
            result = await ragas_eval.evaluate_sample(
                topic="t", generated_content="c", retrieved_contexts=["ctx"],
            )

        assert skip.call_count == 1, "contention must report as a gpu-busy skip"
        assert degraded.call_count == 0, (
            "a contention skip must NOT be reported as metric degradation — "
            "that conflation is what poindexter#914 P2's distinct kind exists "
            "to prevent"
        )
        # Same fail-soft result the caller already handles.
        assert result == {
            "faithfulness": -1.0,
            "answer_relevancy": -1.0,
            "context_precision": -1.0,
        }

    async def test_a_clean_run_still_reports_nothing(self):
        from poindexter.services import ragas_eval

        ok = MagicMock()
        ok.scores = [{"faithfulness": 0.9, "answer_relevancy": 0.8,
                      "context_precision": 0.7}]
        with patch(
            "poindexter.services.ragas_eval._build_ragas_models",
            return_value=(MagicMock(), MagicMock()),
        ), patch("ragas.evaluate", return_value=ok), patch(
            "datasets.Dataset.from_dict", return_value=MagicMock(),
        ), patch(
            "poindexter.services.ragas_eval._surface_gpu_busy_skip"
        ) as skip:
            result = await ragas_eval.evaluate_sample(
                topic="t", generated_content="c", retrieved_contexts=["ctx"],
            )
        assert skip.call_count == 0
        assert result["faithfulness"] == 0.9
