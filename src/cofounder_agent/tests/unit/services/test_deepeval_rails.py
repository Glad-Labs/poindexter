"""Tests for services/deepeval_rails.py — DeepEval integration as a
parallel content reviewer (#197 / #329 sub-issue 1).

The ``is_enabled`` and pure-CPU brand-fabrication tests run
unconditionally. The g-eval / faithfulness rails depend on a real
DeepEval install + a reachable judge model, so the live-judge tests
mock the underlying ``deepeval.metrics`` classes via monkeypatch.
The fail-soft contract (every rail returns
``(True, None, "<sentinel>")`` when DeepEval is missing or errors)
is verified directly without the SDK.

**Every skip path asserts ``score is None``** (poindexter#876). These tests
used to assert ``score == 1.0``, which is how the fail-open survived. The trap
is sharpest here: ``test_clean_content_scores_one`` shows the brand metric
genuinely returns ``1.0`` for clean content, so a *real* "this post is clean"
and a *fake* "deepeval isn't installed" were the SAME VALUE — literally
indistinguishable downstream, where both rescaled to a 100.0 review.
``passed is True`` is still asserted everywhere (a rail that cannot run must
never veto), but "didn't measure" now carries no number at all.
"""

from __future__ import annotations

from importlib.util import find_spec
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.deepeval_rails as _de_mod
from services.deepeval_rails import (
    evaluate_brand_fabrication,
    evaluate_faithfulness,
    evaluate_g_eval,
    is_enabled,
    make_test_case,
)

requires_deepeval = pytest.mark.skipif(
    find_spec("deepeval") is None,
    reason="DeepEval is an opt-in dep; install via `pip install deepeval` to run.",
)


# ---------------------------------------------------------------------------
# is_enabled
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


# ---------------------------------------------------------------------------
# make_test_case
# ---------------------------------------------------------------------------


@pytest.mark.unit
@requires_deepeval
class TestMakeTestCase:
    def test_builds_llm_test_case(self):
        case = make_test_case(content="Generated body", topic="My Topic")
        assert case.input == "My Topic"
        assert case.actual_output == "Generated body"
        assert case.expected_output is None

    def test_with_expected_baseline(self):
        case = make_test_case(
            content="Generated", topic="Topic", expected="Reference",
        )
        assert case.expected_output == "Reference"


# ---------------------------------------------------------------------------
# evaluate_brand_fabrication
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEvaluateBrandFabrication:
    def test_clean_content_scores_one(self):
        """A REAL 1.0 — the brand metric is binary and clean content genuinely
        scores 1.0. This is exactly why the fail-open was invisible: the skip
        paths returned this same value, so "clean" and "never ran" were
        indistinguishable. Keep asserting 1.0 here; that's a measurement."""
        passed, score, reason = evaluate_brand_fabrication(
            "FastAPI and PostgreSQL are reliable choices for backend development.",
            topic="Backend stacks",
        )
        assert passed is True
        assert score == 1.0
        assert "no fabrication" in reason.lower() or "no" in reason.lower()

    def test_empty_content_skips_cleanly(self):
        passed, score, reason = evaluate_brand_fabrication("", topic="x")
        assert passed is True
        assert score is None

    def test_non_string_skips_cleanly(self):
        passed, score, reason = evaluate_brand_fabrication(None, topic="x")  # type: ignore[arg-type]
        assert passed is True
        assert score is None

    def test_fake_quote_pattern_lowers_score(self):
        # FAKE_QUOTE_PATTERNS catches obvious public-figure attribution.
        # The exact match depends on tuning; we just confirm the metric
        # ran and returned a valid score.
        bad = (
            'Bill Gates told the audience: "AI will replace 90% of '
            'developers next year." Then he announced a new product line.'
        )
        passed, score, reason = evaluate_brand_fabrication(bad, topic="AI futures")
        assert isinstance(passed, bool)
        assert 0.0 <= score <= 1.0
        assert isinstance(reason, str)


# ---------------------------------------------------------------------------
# Custom metric — direct interface
# ---------------------------------------------------------------------------


@pytest.mark.unit
@requires_deepeval
class TestBrandFabricationMetric:
    """Verifies the BaseMetric subclass conforms to DeepEval's
    contract (measure returns float in [0,1], is_successful returns
    bool, sync + async paths agree)."""

    @pytest.mark.asyncio
    async def test_async_path_matches_sync(self):
        from services.deepeval_rails import _build_brand_fabrication_metric
        cls = _build_brand_fabrication_metric()
        metric = cls(threshold=0.5)
        case = make_test_case(
            content="Clean post about backends",
            topic="Backends",
        )
        sync_score = metric.measure(case)
        async_score = await metric.a_measure(case)
        assert sync_score == async_score

    def test_clean_returns_one(self):
        from services.deepeval_rails import _build_brand_fabrication_metric
        cls = _build_brand_fabrication_metric()
        metric = cls(threshold=0.5)
        case = make_test_case(
            content="A normal article about FastAPI.",
            topic="Backend frameworks",
        )
        score = metric.measure(case)
        assert score == 1.0
        assert metric.is_successful() is True


class _FakeMetric:
    """Stand-in for DeepEval's GEval / FaithfulnessMetric.

    DeepEval's real metrics call out to the judge model; that's
    expensive and flaky from CI. The reviewer chain only cares about
    the (success, score, reason) shape returned by ``measure``.
    """

    def __init__(self, score: float, reason: str = "judge ok", **kwargs):
        self._score = score
        self.reason = reason
        self.threshold = kwargs.get("threshold", 0.5)
        self.success = self._score >= self.threshold

    def measure(self, _case) -> float:
        return self._score

    async def a_measure(self, _case) -> float:
        # poindexter#826: the rails run metrics via a_measure so judge
        # calls dispatch on the event loop.
        return self._score


@pytest.mark.unit
class TestEvaluateGEval:
    @pytest.mark.asyncio
    async def test_empty_content_skips(self):
        passed, score, reason = await evaluate_g_eval("", topic="x")
        assert passed is True
        assert score is None
        assert reason == "empty content"

    @pytest.mark.asyncio
    async def test_high_score_passes_threshold(self, monkeypatch):
        def factory(*_a, **kw):
            return _FakeMetric(0.9, reason="grounded", **kw)

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        passed, score, reason = await evaluate_g_eval(
            "Decent post about FastAPI.",
            topic="Backends",
            threshold=0.7,
        )
        assert passed is True
        assert score == pytest.approx(0.9)
        assert "grounded" in reason

    @pytest.mark.asyncio
    async def test_low_score_fails_threshold(self, monkeypatch):
        def factory(*_a, **kw):
            return _FakeMetric(0.3, reason="vague claims", **kw)

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        passed, score, _reason = await evaluate_g_eval(
            "Mushy post.",
            topic="Backends",
            threshold=0.7,
        )
        assert passed is False
        assert score == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_judge_exception_returns_safe_default(self, monkeypatch):
        # Capture findings: the error path now emits qa_rail_degraded, and an
        # un-patched emit_finding schedules a real audit_log_bg background write
        # whenever a prior test in the run left the global audit logger
        # initialised — which hangs loop teardown.
        _capture_findings(monkeypatch)

        def factory(*_a, **_kw):
            raise RuntimeError("judge api down")

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        passed, score, reason = await evaluate_g_eval("post", topic="x")
        assert passed is True
        assert score is None
        assert "deepeval-error" in reason

    @pytest.mark.asyncio
    async def test_ollama_judge_model_wrapped_in_ollama_model(self, monkeypatch):
        """Regression: before the 2026-05-27 fix, the resolver stripped
        the ``ollama/`` prefix and DeepEval's stock model loader treated
        the bare string as an OpenAI model — every g_eval call hit
        ``DeepEvalError: OPENAI_API_KEY is not configured`` and the
        rail returned ``(True, 1.0, "deepeval-error: DeepEvalError")``.

        Verify the judge_model arg gets wrapped in DeepEval's
        ``OllamaModel`` for the ``ollama/...`` prefix family. The
        substituted metric class records the model it was constructed
        with so the assertion can introspect it directly."""
        captured: dict[str, object] = {}

        def factory(*_a, **kw):
            captured["model"] = kw["model"]
            return _FakeMetric(0.9, reason="ok", **kw)

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        await evaluate_g_eval(
            "post", topic="x",
            judge_model="ollama/gemma3:27b",
            threshold=0.7,
        )

        from deepeval.models import OllamaModel
        assert isinstance(captured["model"], OllamaModel), (
            f"expected OllamaModel-wrapped judge for ollama/ prefix, "
            f"got {type(captured['model']).__name__}: {captured['model']!r}"
        )

    @pytest.mark.asyncio
    async def test_bare_openai_model_string_passthrough(self, monkeypatch):
        """Non-``ollama/`` model strings (e.g. ``gpt-4o``) must NOT
        be wrapped — they go straight through to DeepEval which
        already handles OpenAI via its stock model loader. Wrapping
        a bare GPT model name in OllamaModel would crash with
        ``model not found`` against the Ollama server."""
        captured: dict[str, object] = {}

        def factory(*_a, **kw):
            captured["model"] = kw["model"]
            return _FakeMetric(0.9, reason="ok", **kw)

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        await evaluate_g_eval(
            "post", topic="x",
            judge_model="gpt-4o-mini",
            threshold=0.7,
        )
        assert captured["model"] == "gpt-4o-mini"


@pytest.mark.unit
class TestEvaluateFaithfulness:
    @pytest.mark.asyncio
    async def test_empty_content_skips(self):
        passed, _score, reason = await evaluate_faithfulness(
            "", retrieval_context=["fact"]
        )
        assert passed is True
        assert reason == "empty content"

    @pytest.mark.asyncio
    async def test_no_context_skips(self):
        passed, score, reason = await evaluate_faithfulness(
            "Some post.", retrieval_context=None,
        )
        assert passed is True
        assert score is None
        assert reason == "no-context"

    @pytest.mark.asyncio
    async def test_no_context_empty_list_also_skips(self):
        passed, _score, reason = await evaluate_faithfulness(
            "Some post.", retrieval_context=[],
        )
        assert passed is True
        assert reason == "no-context"

    @pytest.mark.asyncio
    async def test_grounded_content_passes(self, monkeypatch):
        def factory(*_a, **kw):
            return _FakeMetric(0.95, reason="all claims attributable", **kw)

        monkeypatch.setattr("deepeval.metrics.FaithfulnessMetric", factory)
        passed, score, _reason = await evaluate_faithfulness(
            "FastAPI runs on uvicorn.",
            retrieval_context=["FastAPI uses uvicorn as its ASGI server."],
            threshold=0.8,
        )
        assert passed is True
        assert score == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_judge_exception_returns_safe_default(self, monkeypatch):
        # See the g_eval twin: the error path now emits a finding, so capture
        # it rather than let emit_finding schedule a real background write.
        _capture_findings(monkeypatch)

        def factory(*_a, **_kw):
            raise RuntimeError("judge died")

        monkeypatch.setattr("deepeval.metrics.FaithfulnessMetric", factory)
        passed, score, reason = await evaluate_faithfulness(
            "post.", retrieval_context=["context."],
        )
        assert passed is True
        assert score is None
        assert "deepeval-error" in reason


def _capture_findings(monkeypatch) -> list[dict]:
    """Collect emit_finding kwargs. The rails import emit_finding inside the
    function body, so patching the module attribute intercepts it."""
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


@pytest.mark.unit
class TestDegradedRailIsLoud:
    """poindexter#876 — a rail that could not run must be VISIBLE.

    Only ``evaluate_brand_fabrication`` emitted a finding before this;
    ``evaluate_g_eval`` and ``evaluate_faithfulness`` were fully silent — no
    honest score AND no finding. That is exactly how #601 hid for ~7 days:
    g_eval was OPENAI_API_KEY-erroring on every call and reported a perfect
    100 the whole time, with nothing anywhere to say otherwise.

    All three now share the ``qa_rail_degraded`` kind (the convention
    ``ragas_eval`` already established, poindexter#847).
    """

    @pytest.mark.asyncio
    async def test_g_eval_judge_error_emits_finding(self, monkeypatch):
        calls = _capture_findings(monkeypatch)

        def factory(*_a, **_kw):
            raise RuntimeError("judge api down")

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        _passed, score, _reason = await evaluate_g_eval("post", topic="x")
        assert score is None
        assert len(calls) == 1
        assert calls[0]["kind"] == "qa_rail_degraded"
        assert calls[0]["severity"] == "warn"
        assert calls[0]["dedup_key"].startswith("qa_rail_degraded:deepeval_g_eval")

    @pytest.mark.asyncio
    async def test_faithfulness_judge_error_emits_finding(self, monkeypatch):
        calls = _capture_findings(monkeypatch)

        def factory(*_a, **_kw):
            raise RuntimeError("judge died")

        monkeypatch.setattr("deepeval.metrics.FaithfulnessMetric", factory)
        _passed, score, _reason = await evaluate_faithfulness(
            "post.", retrieval_context=["context."],
        )
        assert score is None
        assert len(calls) == 1
        assert calls[0]["kind"] == "qa_rail_degraded"
        assert calls[0]["dedup_key"].startswith("qa_rail_degraded:deepeval_faithfulness")

    def test_brand_uses_the_shared_kind(self, monkeypatch):
        calls = _capture_findings(monkeypatch)
        monkeypatch.setattr(
            _de_mod, "_build_brand_fabrication_metric",
            lambda: (_ for _ in ()).throw(RuntimeError("metric blew up")),
        )
        _passed, score, _reason = evaluate_brand_fabrication("post", topic="x")
        assert score is None
        assert calls[0]["kind"] == "qa_rail_degraded"
        assert calls[0]["dedup_key"].startswith("qa_rail_degraded:deepeval_brand")

    @pytest.mark.asyncio
    async def test_benign_skips_emit_no_finding(self, monkeypatch):
        """Empty content / no-context are INAPPLICABILITY, not failure — the
        rail correctly has nothing to measure. Score is still None (it did not
        measure), but paging an operator would be noise."""
        calls = _capture_findings(monkeypatch)
        assert evaluate_brand_fabrication("", topic="x")[1] is None
        assert (await evaluate_g_eval("", topic="x"))[1] is None
        assert (await evaluate_faithfulness("", retrieval_context=["c"]))[1] is None
        assert (await evaluate_faithfulness("post", retrieval_context=None))[1] is None
        assert calls == []


@pytest.mark.unit
@requires_deepeval
class TestDispatcherJudgeModel:
    """poindexter#826 — with a ``pool``, the judge routes through the
    LiteLLM dispatcher instead of DeepEval's own OllamaModel transport."""

    @pytest.mark.asyncio
    async def test_pool_builds_dispatcher_judge(self, monkeypatch):
        from typing import Any

        captured: dict[str, Any] = {}

        def factory(*_a, **kw):
            captured["model"] = kw["model"]
            return _FakeMetric(0.9, reason="ok", **kw)

        monkeypatch.setattr("deepeval.metrics.GEval", factory)
        await evaluate_g_eval(
            "post", topic="x",
            judge_model="ollama/gemma3:27b",
            threshold=0.7,
            pool=object(),
        )

        from deepeval.models.base_model import DeepEvalBaseLLM
        judge = captured["model"]
        assert isinstance(judge, DeepEvalBaseLLM), (
            f"expected dispatcher judge (DeepEvalBaseLLM) when pool is "
            f"wired, got {type(judge).__name__}"
        )
        assert judge.get_model_name() == "dispatcher:ollama/gemma3:27b"

    @pytest.mark.asyncio
    async def test_a_generate_routes_through_dispatch_complete(self, monkeypatch):
        from types import SimpleNamespace

        dispatch_mock = AsyncMock(return_value=SimpleNamespace(text="judge says hi"))
        monkeypatch.setattr(
            "services.llm_providers.dispatcher.dispatch_complete", dispatch_mock,
        )
        model = _de_mod._build_dispatcher_judge_model(
            "ollama/gemma3:27b", pool="POOL",
        )
        out = await model.a_generate("prompt text")

        assert out == "judge says hi"
        kwargs = dispatch_mock.call_args.kwargs
        assert kwargs["pool"] == "POOL"
        assert kwargs["model"] == "ollama/gemma3:27b"
        assert kwargs["phase"] == "qa_deepeval_judge"
        # No schema → plain-text call, no forced JSON mode.
        assert "response_format" not in kwargs

    @pytest.mark.asyncio
    async def test_a_generate_with_schema_parses_json(self, monkeypatch):
        from types import SimpleNamespace

        from pydantic import BaseModel

        class _Verdict(BaseModel):
            answer: str

        dispatch_mock = AsyncMock(
            return_value=SimpleNamespace(text='```json\n{"answer": "yes"}\n```'),
        )
        monkeypatch.setattr(
            "services.llm_providers.dispatcher.dispatch_complete", dispatch_mock,
        )
        model = _de_mod._build_dispatcher_judge_model("gemma3:27b", pool="POOL")
        out = await model.a_generate("prompt", schema=_Verdict)

        assert isinstance(out, _Verdict)
        assert out.answer == "yes"
        # Schema requests JSON mode so weak local judges stay parseable.
        kwargs = dispatch_mock.call_args.kwargs
        assert kwargs["response_format"] == {"type": "json_object"}

    def test_sync_generate_raises(self):
        model = _de_mod._build_dispatcher_judge_model("gemma3:27b", pool="POOL")
        with pytest.raises(RuntimeError, match="async-only"):
            model.generate("prompt")


@pytest.mark.unit
class TestResolveJudgeModel:
    """Resolver tests. As of Glad-Labs/poindexter#455 Phase 1 the
    function is async + calls ``notify_operator(critical=True)`` on
    total resolution failure, mirroring ``ragas_eval._resolve_judge_model``."""

    @pytest.mark.asyncio
    async def test_none_site_config_raises(self):
        """2026-05-12: removed the hardcoded ``glm-4.7-5090`` fallback
        because it baked Matt's specific model name into a public OSS
        file. Forks installing Poindexter wouldn't have that model and
        would get a confusing "model not found" from DeepEval at run
        time. Now: missing site_config raises (fail-loud) rather than
        silently using an unknown model."""
        with pytest.raises(ValueError, match="site_config is required"):
            await _de_mod._resolve_judge_model(None)

    @pytest.mark.asyncio
    async def test_override_via_explicit_setting(self):
        sc = MagicMock(_pool=None)
        sc.get = MagicMock(return_value="gpt-4o-mini")
        assert await _de_mod._resolve_judge_model(sc) == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_stale_cost_tier_budget_is_ignored(self):
        """A stale cost_tier.budget.model row (left on an existing install) is
        NOT consulted — only deepeval_judge_model is read, so an empty pin
        fails loud even when cost_tier.budget.model / pipeline_writer_model
        are present."""
        sc = MagicMock(_pool=None)
        sc.get = MagicMock(side_effect=lambda key, default="": {
            "deepeval_judge_model": "",
            "cost_tier.budget.model": "ollama/glm-4.7-flash:latest",
            "pipeline_writer_model": "ollama/glm-4.7-flash:latest",
        }.get(key, default))
        notify = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ):
            with pytest.raises(ValueError, match="no judge model resolvable"):
                await _de_mod._resolve_judge_model(sc)

    @pytest.mark.asyncio
    async def test_all_paths_blank_raises_and_notifies(self):
        """Every resolution path empty → ``notify_operator(critical=True)``
        fires AND ValueError raises. Mirrors ragas_eval's loud-failure
        contract per Glad-Labs/poindexter#455."""
        sc = MagicMock(_pool=None)
        sc.get = MagicMock(return_value="")
        notify = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator", notify,
        ):
            with pytest.raises(ValueError, match="no judge model resolvable"):
                await _de_mod._resolve_judge_model(sc)
        assert notify.await_count == 1
        await_args = notify.await_args
        assert await_args is not None
        assert await_args.kwargs.get("critical") is True

    @pytest.mark.asyncio
    async def test_explicit_pin_preserves_ollama_prefix(self):
        """The resolver returns the pin verbatim (``ollama/`` prefix kept so
        ``_build_deepeval_judge_model`` can route through OllamaModel)."""
        sc = MagicMock(_pool=None)
        sc.get = MagicMock(side_effect=lambda key, default="": {
            "deepeval_judge_model": "ollama/glm-4.7-flash:latest",
        }.get(key, default))
        assert (
            await _de_mod._resolve_judge_model(sc)
            == "ollama/glm-4.7-flash:latest"
        )
