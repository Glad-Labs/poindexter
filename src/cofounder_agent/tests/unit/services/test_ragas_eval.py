"""Tests for services/ragas_eval.py — Ragas-based RAG evaluation (#205).

The guard tests stub out the underlying Ragas + Ollama calls so the
suite stays fast (no judge-LLM round-trips, no model downloads). The
stubbed-happy-path case below relies on the ``ragas`` SDK being
importable (so ``patch('ragas.evaluate', ...)`` can resolve the target);
it is skipped when Ragas is not installed (CI default — Ragas is
opt-in via ``app_settings.ragas_enabled`` and not pinned in pyproject).
"""

from __future__ import annotations

from importlib.util import find_spec
from unittest.mock import MagicMock, patch

import pytest

# NOTE (Glad-Labs/glad-labs-stack#997): this module previously carried an
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
        with patch(
            "services.ragas_eval._build_ragas_models",
            side_effect=Exception("ollama down"),
        ):
            result = await evaluate_sample(
                topic="Topic", generated_content="content",
            )
        assert all(v == -1.0 for v in result.values())


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
        import sys
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
            patch.dict(sys.modules, {
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


def _identity_wrapper_modules():
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


@pytest.mark.unit
class TestDispatcherWrappers:
    """With a ``pool``, Ragas judge + embeddings route through the
    LiteLLM dispatcher instead of langchain-ollama's own transport."""

    @pytest.mark.asyncio
    async def test_pool_prefers_dispatcher_over_chat_ollama(self):
        import sys
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
            patch.dict(sys.modules, {
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
        import sys
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from langchain_core.messages import HumanMessage

        from services.ragas_eval import _build_dispatcher_ragas_wrappers

        dispatch_mock = AsyncMock(
            return_value=SimpleNamespace(text='{"statements": []}'),
        )
        monkeypatch.setattr(
            "services.llm_providers.dispatcher.dispatch_complete", dispatch_mock,
        )
        with patch.dict(sys.modules, _identity_wrapper_modules()):
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
        import sys
        from unittest.mock import AsyncMock, patch

        from services.ragas_eval import _build_dispatcher_ragas_wrappers

        embed_mock = AsyncMock(return_value=[0.1, 0.2])
        monkeypatch.setattr(
            "services.llm_providers.dispatcher.dispatch_embed", embed_mock,
        )
        with patch.dict(sys.modules, _identity_wrapper_modules()):
            _, embeddings = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        vec = await embeddings.aembed_query("some text")
        docs = await embeddings.aembed_documents(["a", "b"])

        assert vec == [0.1, 0.2]
        assert docs == [[0.1, 0.2], [0.1, 0.2]]
        assert embed_mock.await_count == 3

    @pytest.mark.asyncio
    async def test_sync_paths_raise(self):
        import sys
        from unittest.mock import patch

        from langchain_core.messages import HumanMessage

        from services.ragas_eval import _build_dispatcher_ragas_wrappers

        with patch.dict(sys.modules, _identity_wrapper_modules()):
            llm, embeddings = _build_dispatcher_ragas_wrappers(
                pool="POOL", judge_model="phi4:14b", embed_model="nomic-embed-text",
            )

        with pytest.raises(NotImplementedError):
            llm._generate([HumanMessage(content="x")])
        with pytest.raises(NotImplementedError):
            embeddings.embed_query("x")
        with pytest.raises(NotImplementedError):
            embeddings.embed_documents(["x"])


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
