"""Pin the cycle-5 UnifiedPromptManager migrations (poindexter#829).

The last three inline-only LLM prompts joined the SKILL.md catalog so the
Langfuse read-only mirror (SyncPromptCatalogToLangfuseJob, poindexter#825)
can surface them for review:

* ``services.image_captioner._prompt`` → ``image.caption_alt_text``
* ``services.deepeval_rails._DEFAULT_G_EVAL_CRITERION`` →
  ``qa.deepeval_g_eval_criterion``
* ``services.integrations.handlers.retention_embeddings_collapse``
  → wired to the already-registered
  ``memory.collapse_old_embeddings.summary`` key (the 2026-06-24
  job-to-handler fold had left the handler on its inline constant).

Each resolver pulls from UnifiedPromptManager and falls back to the
inline constant on any lookup failure — same pattern as the cycle-3/-4
migrations (#612). Byte-agreement between the SKILL.md default and the
inline fallback is enforced by test_prompt_fallback_drift.py; this file
pins the resolver *behavior* (PM path taken, fallback on failure, and
the explicit-override precedence rules).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# image.caption_alt_text (services/image_captioner.py)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_image_caption_resolver_uses_prompt_manager():
    from services import image_captioner

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value.get_prompt.return_value = "PM caption prompt"
        result = image_captioner._prompt(budget=125)
    assert result == "PM caption prompt"
    mock_pm.return_value.get_prompt.assert_called_once_with(
        "image.caption_alt_text", budget=125,
    )


@pytest.mark.unit
def test_image_caption_resolver_falls_back_on_pm_failure():
    from services import image_captioner

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=RuntimeError("pm broken"),
    ):
        result = image_captioner._prompt(budget=97)
    assert "under 97 characters" in result
    assert "ONLY what is actually visible" in result


# ---------------------------------------------------------------------------
# qa.deepeval_g_eval_criterion (services/deepeval_rails.py)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_g_eval_criterion_resolver_uses_prompt_manager():
    from services import deepeval_rails

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value.get_prompt.return_value = "PM criterion\n"
        result = deepeval_rails._resolve_g_eval_criterion()
    # The loader's trailing newline is stripped — the criterion is a bare
    # rubric sentence handed to GEval, mirrored by the newline-free
    # deepeval_g_eval_criterion app_settings seed.
    assert result == "PM criterion"


@pytest.mark.unit
def test_g_eval_criterion_resolver_falls_back_on_pm_failure():
    from services import deepeval_rails

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=RuntimeError("pm broken"),
    ):
        result = deepeval_rails._resolve_g_eval_criterion()
    assert result == deepeval_rails._DEFAULT_G_EVAL_CRITERION
    assert "well-grounded" in result


@pytest.mark.unit
def test_g_eval_fallback_matches_seeded_app_setting_shape():
    """The inline fallback must stay newline-free: it mirrors the seeded
    ``app_settings.deepeval_g_eval_criterion`` value, so all three sources
    (constant, seed, stripped SKILL.md body) grade against the same rubric."""
    from services import deepeval_rails

    assert deepeval_rails._DEFAULT_G_EVAL_CRITERION == (
        deepeval_rails._DEFAULT_G_EVAL_CRITERION.strip()
    )


class _FakeGEvalMetric:
    """Mirrors test_deepeval_rails._FakeMetric — stands in for
    ``deepeval.metrics.GEval`` (constructed with keyword config, exposes
    measure/reason/success)."""

    def __init__(self, score: float = 0.9, reason: str = "judge ok", **kwargs):
        self._score = score
        self.reason = reason
        self.threshold = kwargs.get("threshold", 0.5)
        self.success = self._score >= self.threshold

    def measure(self, _case) -> float:
        return self._score

    async def a_measure(self, _case) -> float:
        # poindexter#826: evaluate_g_eval runs the metric via a_measure so
        # the judge dispatches through the LiteLLM dispatcher on the loop.
        return self._score


@pytest.mark.unit
async def test_evaluate_g_eval_explicit_criterion_wins(monkeypatch):
    """An explicit criterion (the multi_model_qa app_settings override)
    must reach the metric untouched — the catalog resolver only fires
    when no criterion is passed."""
    from services import deepeval_rails

    captured: dict[str, object] = {}

    def factory(*_a, **kw):
        captured["criteria"] = kw["criteria"]
        return _FakeGEvalMetric(0.9, **{k: v for k, v in kw.items() if k == "threshold"})

    monkeypatch.setattr("deepeval.metrics.GEval", factory)
    with patch(
        "services.deepeval_rails._resolve_g_eval_criterion",
    ) as resolver:
        passed, score, _reason = await deepeval_rails.evaluate_g_eval(
            "body", "topic",
            criterion="Operator override rubric",
            judge_model="gpt-4o-mini",
            threshold=0.7,
        )
    assert passed is True
    assert score == pytest.approx(0.9)
    assert captured["criteria"] == "Operator override rubric"
    resolver.assert_not_called()


@pytest.mark.unit
async def test_evaluate_g_eval_resolves_criterion_when_none(monkeypatch):
    """criterion=None (the multi_model_qa default when the app_setting is
    unset) routes through the SKILL.md catalog resolver."""
    from services import deepeval_rails

    captured: dict[str, object] = {}

    def factory(*_a, **kw):
        captured["criteria"] = kw["criteria"]
        return _FakeGEvalMetric(0.9, **{k: v for k, v in kw.items() if k == "threshold"})

    monkeypatch.setattr("deepeval.metrics.GEval", factory)
    with patch(
        "services.deepeval_rails._resolve_g_eval_criterion",
        return_value="Catalog rubric",
    ) as resolver:
        await deepeval_rails.evaluate_g_eval(
            "body", "topic", judge_model="gpt-4o-mini", threshold=0.7,
        )
    resolver.assert_called_once()
    assert captured["criteria"] == "Catalog rubric"


# ---------------------------------------------------------------------------
# memory.collapse_old_embeddings.summary
# (services/integrations/handlers/retention_embeddings_collapse.py)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collapse_resolver_returns_raw_template_from_prompt_manager():
    """The collapse handler needs the *raw* template — placeholders are
    filled downstream by ``build_summary_text_via_llm``. The resolver goes
    through the gated ``_resolve_template_with_meta`` seam (poindexter#825;
    same shape as the #2103 fix in retention_summarize_to_table), never
    ``_fetch_from_langfuse``."""
    from services.integrations.handlers import retention_embeddings_collapse

    with patch("services.prompt_manager.get_prompt_manager") as mock_pm:
        mock_pm.return_value._resolve_template_with_meta.return_value = (
            "PM RAW: {n}/{source_table}/{joined}",
            "yaml",
            None,
        )
        result = retention_embeddings_collapse._resolve_summary_prompt_template()
    assert "{n}" in result
    assert "{source_table}" in result
    assert "{joined}" in result
    mock_pm.return_value._resolve_template_with_meta.assert_called_once_with(
        "memory.collapse_old_embeddings.summary",
    )


@pytest.mark.unit
def test_collapse_resolver_default_path_never_consults_langfuse():
    """The default (overrides-off) path must serve the SKILL.md-registered
    template without ever touching Langfuse."""
    from unittest.mock import MagicMock

    from services import prompt_manager as pm_mod
    from services.integrations.handlers import retention_embeddings_collapse

    pm = pm_mod.UnifiedPromptManager()
    with patch.object(
        pm, "_fetch_from_langfuse_with_meta",
        MagicMock(return_value=("LANGFUSE COPY", 1)),
    ) as lf, patch.object(pm_mod, "get_prompt_manager", return_value=pm):
        template = retention_embeddings_collapse._resolve_summary_prompt_template()
    lf.assert_not_called()
    assert template == pm.prompts["memory.collapse_old_embeddings.summary"]["template"]


@pytest.mark.unit
def test_collapse_resolver_falls_back_on_pm_failure():
    from services.integrations.handlers import retention_embeddings_collapse

    with patch(
        "services.prompt_manager.get_prompt_manager",
        side_effect=RuntimeError("pm broken"),
    ):
        result = retention_embeddings_collapse._resolve_summary_prompt_template()
    assert result == retention_embeddings_collapse._DEFAULT_SUMMARY_PROMPT


@pytest.mark.unit
async def test_collapse_llm_summary_prefers_explicit_prompt_template():
    """A per-policy-row ``config.prompt_template`` wins over the catalog
    default — the resolver must not even be consulted."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from services.integrations.handlers import retention_embeddings_collapse as mod

    # poindexter#827: the summarizer routes through dispatch_complete now,
    # so mock the dispatcher (not OllamaClient) and read the prompt off the
    # captured messages payload.
    dispatch_mock = AsyncMock(return_value=SimpleNamespace(text="a summary"))
    with patch(
        "services.llm_providers.dispatcher.dispatch_complete", dispatch_mock,
    ), patch.object(
        mod, "_resolve_summary_prompt_template",
    ) as resolver:
        result = await mod.build_summary_text_via_llm(
            ["one preview", "two preview"],
            source_table="claude_sessions",
            model="m",
            timeout_s=5,
            prompt_template="POLICY ROW: {n} {source_table} {joined}",
            pool="POOL",
        )
    assert result == "a summary"
    prompt = dispatch_mock.call_args.kwargs["messages"][0]["content"]
    assert prompt.startswith("POLICY ROW: 2 claude_sessions")
    resolver.assert_not_called()


@pytest.mark.unit
async def test_collapse_llm_summary_uses_catalog_template_when_config_unset():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from services.integrations.handlers import retention_embeddings_collapse as mod

    dispatch_mock = AsyncMock(return_value=SimpleNamespace(text="a summary"))
    with patch(
        "services.llm_providers.dispatcher.dispatch_complete", dispatch_mock,
    ), patch.object(
        mod, "_resolve_summary_prompt_template",
        return_value="CATALOG: {n} {source_table} {joined}",
    ) as resolver:
        result = await mod.build_summary_text_via_llm(
            ["one preview"],
            source_table="brain",
            model="m",
            timeout_s=5,
            prompt_template=None,
            pool="POOL",
        )
    assert result == "a summary"
    prompt = dispatch_mock.call_args.kwargs["messages"][0]["content"]
    assert prompt.startswith("CATALOG: 1 brain")
    resolver.assert_called_once()
