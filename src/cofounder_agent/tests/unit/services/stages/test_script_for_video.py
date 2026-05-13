"""Unit tests for ``services/stages/script_for_video.py``.

ScriptForVideoStage drives two LLM calls (long-form + short-form). We
patch the dispatcher + GPU lock at the stage's import sites so the
tests exercise the stage's own control flow — JSON extraction, scene
normalization, fallback handling — not the writer model itself.

Glad-Labs/poindexter#407: the stage now routes through
``services.llm_providers.dispatcher.dispatch_complete`` instead of
constructing OllamaClient directly. Tests mock ``dispatch_complete``
to return a ``Completion``-shaped object (we use SimpleNamespace with
a ``text`` attribute for cheap fidelity).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from plugins.stage import Stage
from services.stages.script_for_video import (
    ScriptForVideoStage,
    _default_script,
    _extract_json,
    _normalize_scenes,
    _strip_markdown,
)


_FAKE_SITE_CONFIG = SimpleNamespace(
    get=lambda _k, _d="": _d,
    get_int=lambda _k, _d=0: _d,
    get_float=lambda _k, _d=0.0: _d,
    get_bool=lambda _k, _d=False: _d,
    _pool=None,
)


def _completion(text: str) -> SimpleNamespace:
    """Build a fake dispatcher Completion that only exposes .text."""
    return SimpleNamespace(text=text)


@asynccontextmanager
async def _no_gpu_lock(*_args, **_kwargs):
    yield None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_bare_json(self):
        out = _extract_json('{"a": 1, "b": "x"}')
        assert out == {"a": 1, "b": "x"}

    def test_fenced_json_block(self):
        out = _extract_json('```json\n{"k": 42}\n```')
        assert out == {"k": 42}

    def test_fenced_block_no_lang(self):
        out = _extract_json("```\n{\"k\": 7}\n```")
        assert out == {"k": 7}

    def test_prose_preamble_with_braces(self):
        text = "Sure! Here is the JSON you asked for:\n\n{\"scenes\": [1, 2]}\n\nLet me know if that works."
        out = _extract_json(text)
        assert out == {"scenes": [1, 2]}

    def test_total_garbage_returns_none(self):
        assert _extract_json("nope no json here") is None

    def test_empty_string_returns_none(self):
        assert _extract_json("") is None

    def test_unbalanced_braces_returns_none(self):
        # Brace-counter bails when it can't close the block.
        assert _extract_json("Here we go {still going") is None

    def test_nested_braces_balanced(self):
        out = _extract_json('Stuff {"a": {"b": {"c": 1}}} trailing')
        assert out == {"a": {"b": {"c": 1}}}


class TestNormalizeScenes:
    def test_drops_scenes_missing_required_fields(self):
        raw = [
            {"narration_text": "narr1", "visual_prompt": "vis1"},
            {"narration_text": "", "visual_prompt": "x"},  # blank narration
            {"narration_text": "narr2"},  # missing visual
            {"visual_prompt": "vis3"},  # missing narration
            "not a dict",  # type error
        ]
        out = _normalize_scenes(raw, fallback_duration=30)
        assert len(out) == 1
        assert out[0]["narration_text"] == "narr1"

    def test_clamps_duration_into_range(self):
        raw = [
            {"narration_text": "n", "visual_prompt": "v", "duration_s_hint": 1},
            {"narration_text": "n", "visual_prompt": "v", "duration_s_hint": 200},
            {"narration_text": "n", "visual_prompt": "v", "duration_s_hint": 30},
        ]
        out = _normalize_scenes(raw, fallback_duration=30)
        assert out[0]["duration_s_hint"] == 5  # clamped up
        assert out[1]["duration_s_hint"] == 90  # clamped down
        assert out[2]["duration_s_hint"] == 30  # in range

    def test_invalid_duration_falls_back(self):
        raw = [
            {"narration_text": "n", "visual_prompt": "v", "duration_s_hint": "bogus"},
            {"narration_text": "n", "visual_prompt": "v", "duration_s_hint": None},
        ]
        out = _normalize_scenes(raw, fallback_duration=30)
        assert all(scene["duration_s_hint"] == 30 for scene in out)

    def test_preserves_extra_keys(self):
        raw = [{
            "narration_text": "n", "visual_prompt": "v",
            "duration_s_hint": 30,
            "camera": "dolly", "music": "cue1",
        }]
        out = _normalize_scenes(raw, fallback_duration=30)
        assert out[0]["camera"] == "dolly"
        assert out[0]["music"] == "cue1"

    def test_non_list_returns_empty(self):
        assert _normalize_scenes(None, 30) == []
        assert _normalize_scenes("not a list", 30) == []
        assert _normalize_scenes({"key": "value"}, 30) == []

    def test_empty_list_returns_empty(self):
        assert _normalize_scenes([], 30) == []


class TestStripMarkdown:
    def test_strips_headings_code_links(self):
        body = (
            "# Title\n"
            "## Subtitle\n"
            "Some `inline_code` and a [link](https://example.com).\n"
            "```python\nprint('hello')\n```\n"
            "**bold** and _italic_."
        )
        out = _strip_markdown(body)
        assert "#" not in out
        assert "`" not in out
        assert "https://example.com" not in out  # link URL stripped, label preserved
        assert "link" in out
        assert "print('hello')" not in out
        assert "bold" in out and "italic" in out

    def test_empty_input(self):
        assert _strip_markdown("") == ""

    def test_plain_prose_unchanged(self):
        assert _strip_markdown("Just plain text.") == "Just plain text."


class TestDefaultScript:
    def test_shape(self):
        s = _default_script()
        assert "long_form" in s
        assert "short_form" in s
        assert s["long_form"]["scenes"] == []
        assert s["short_form"]["scenes"] == []
        assert s["long_form"]["intro_hook"] == ""
        assert s["long_form"]["outro_cta"] == ""


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms(self):
        assert isinstance(ScriptForVideoStage(), Stage)

    def test_metadata(self):
        s = ScriptForVideoStage()
        assert s.name == "video.script"
        assert s.halts_on_failure is False
        assert s.timeout_seconds == 300


# ---------------------------------------------------------------------------
# Stage.execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecute:
    async def test_missing_title_returns_not_ok(self):
        ctx = {"title": "", "content": "body", "site_config": _FAKE_SITE_CONFIG}
        result = await ScriptForVideoStage().execute(ctx, {})
        assert result.ok is False
        assert "title" in result.detail or "content" in result.detail
        assert result.context_updates["video_script"] == _default_script()
        assert result.metrics.get("skipped") is True

    async def test_missing_content_returns_not_ok(self):
        ctx = {"title": "T", "content": "  ", "site_config": _FAKE_SITE_CONFIG}
        result = await ScriptForVideoStage().execute(ctx, {})
        assert result.ok is False
        assert result.context_updates["video_script"] == _default_script()

    async def test_missing_site_config_returns_not_ok(self):
        ctx = {"title": "T", "content": "body"}
        result = await ScriptForVideoStage().execute(ctx, {})
        assert result.ok is False
        assert "site_config" in result.detail
        assert result.metrics.get("skipped") is True

    async def test_happy_path_both_passes_succeed(self):
        long_form_json = (
            '{"intro_hook": "Hook?", "outro_cta": "Subscribe.",'
            ' "scenes": [{"narration_text": "n", "visual_prompt": "v",'
            ' "duration_s_hint": 30}]}'
        )
        short_form_json = (
            '{"intro_hook": "Boom!", "scenes": ['
            '{"narration_text": "s", "visual_prompt": "v",'
            ' "duration_s_hint": 13}]}'
        )
        # First call returns long, second returns short.
        dispatch_mock = AsyncMock(side_effect=[
            _completion(long_form_json),
            _completion(short_form_json),
        ])
        ctx: dict[str, Any] = {
            "task_id": "t1", "title": "T", "content": "Body content here.",
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            dispatch_mock,
        ), patch(
            "services.gpu_scheduler.gpu",
            SimpleNamespace(lock=_no_gpu_lock),
        ):
            result = await ScriptForVideoStage().execute(ctx, {})

        assert result.ok is True
        u = result.context_updates
        assert u["video_script"]["long_form"]["intro_hook"] == "Hook?"
        assert u["video_script"]["long_form"]["outro_cta"] == "Subscribe."
        assert len(u["video_script"]["long_form"]["scenes"]) == 1
        assert u["video_script"]["short_form"]["intro_hook"] == "Boom!"
        assert len(u["video_script"]["short_form"]["scenes"]) == 1
        assert u["stages"]["video.script"] is True
        assert result.metrics["long_form_ok"] is True
        assert result.metrics["short_form_ok"] is True
        assert result.metrics["long_form_scenes"] == 1
        assert result.metrics["short_form_scenes"] == 1
        # #407 contract: dispatcher MUST be invoked with tier=standard so
        # the litellm provider fires the langfuse_otel callback.
        for call in dispatch_mock.await_args_list:
            assert call.kwargs.get("tier") == "standard"

    async def test_one_pass_fails_other_succeeds(self):
        # Long-form raises, short-form returns parseable JSON. Stage
        # is "ok" because EITHER pass worked.
        short_form_json = (
            '{"intro_hook": "S", "scenes": [{"narration_text": "n",'
            ' "visual_prompt": "v", "duration_s_hint": 10}]}'
        )

        calls: list[int] = []

        async def _dispatch(*_a, **_kw):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("dispatcher down")
            return _completion(short_form_json)

        ctx = {
            "task_id": "t", "title": "T", "content": "B",
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            new=_dispatch,
        ), patch(
            "services.gpu_scheduler.gpu",
            SimpleNamespace(lock=_no_gpu_lock),
        ):
            result = await ScriptForVideoStage().execute(ctx, {})

        assert result.ok is True  # at least one pass succeeded
        assert result.metrics["long_form_ok"] is False
        assert result.metrics["short_form_ok"] is True

    async def test_both_passes_fail_returns_not_ok(self):
        dispatch_mock = AsyncMock(side_effect=RuntimeError("dispatcher unreachable"))
        ctx = {
            "task_id": "t", "title": "T", "content": "B",
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            dispatch_mock,
        ), patch(
            "services.gpu_scheduler.gpu",
            SimpleNamespace(lock=_no_gpu_lock),
        ):
            result = await ScriptForVideoStage().execute(ctx, {})
        assert result.ok is False
        assert result.metrics["long_form_ok"] is False
        assert result.metrics["short_form_ok"] is False

    async def test_unparseable_json_marks_pass_failed(self):
        dispatch_mock = AsyncMock(
            return_value=_completion("this is not JSON at all"),
        )
        ctx = {
            "task_id": "t", "title": "T", "content": "B",
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            dispatch_mock,
        ), patch(
            "services.gpu_scheduler.gpu",
            SimpleNamespace(lock=_no_gpu_lock),
        ):
            result = await ScriptForVideoStage().execute(ctx, {})
        assert result.ok is False
        assert "no parseable JSON" in result.detail or result.metrics["long_form_ok"] is False

    async def test_strips_ollama_prefix_from_model(self):
        """``pipeline_writer_model`` may be set to ``ollama/<model>``;
        the stage strips that prefix before passing to the dispatcher.

        With ``_pool=None`` the tier-resolution path is skipped, so the
        stage falls through to the ``pipeline_writer_model`` site_config
        key (preserves the legacy escape hatch from before #407)."""
        cfg = SimpleNamespace(
            get=lambda k, d="": "ollama/glm-4.7-5090" if k == "pipeline_writer_model" else d,
            get_int=lambda _k, _d=0: _d,
            get_float=lambda _k, _d=0.0: _d,
            get_bool=lambda _k, _d=False: _d,
            _pool=None,
        )
        dispatch_mock = AsyncMock(return_value=_completion('{"scenes": []}'))
        ctx = {
            "task_id": "t", "title": "T", "content": "B",
            "site_config": cfg,
        }
        with patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            dispatch_mock,
        ), patch(
            "services.gpu_scheduler.gpu",
            SimpleNamespace(lock=_no_gpu_lock),
        ):
            result = await ScriptForVideoStage().execute(ctx, {})
        assert result.metrics["writer_model"] == "glm-4.7-5090"
        # Each dispatcher call should have used the resolved model name.
        for call in dispatch_mock.await_args_list:
            assert call.kwargs.get("model") == "glm-4.7-5090"


@pytest.mark.asyncio
class TestDispatcherWiring:
    """Glad-Labs/poindexter#407 contract tests.

    These tests pin the migration's central guarantee: the stage routes
    every LLM call through ``dispatch_complete`` with ``tier='standard'``.
    Without this the litellm ``langfuse_otel`` callback never fires and
    Langfuse stays empty.
    """

    async def test_uses_cost_tier_standard_model_when_pool_present(self):
        """When site_config has a pool, the stage resolves the tier model
        and never falls back to pipeline_writer_model."""
        cfg = SimpleNamespace(
            get=lambda k, d="": "pipeline-writer-not-used" if k == "pipeline_writer_model" else d,
            get_int=lambda _k, _d=0: _d,
            get_float=lambda _k, _d=0.0: _d,
            get_bool=lambda _k, _d=False: _d,
            _pool=object(),  # truthy sentinel — patched resolve_tier_model
        )
        dispatch_mock = AsyncMock(return_value=_completion('{"scenes": []}'))
        resolve_mock = AsyncMock(return_value="ollama/tier-model")
        ctx = {
            "task_id": "t", "title": "T", "content": "B",
            "site_config": cfg,
        }
        with patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            dispatch_mock,
        ), patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            resolve_mock,
        ), patch(
            "services.gpu_scheduler.gpu",
            SimpleNamespace(lock=_no_gpu_lock),
        ):
            result = await ScriptForVideoStage().execute(ctx, {})

        # tier model wins over pipeline_writer_model
        assert result.metrics["writer_model"] == "tier-model"
        # both passes used the tier-resolved model
        for call in dispatch_mock.await_args_list:
            assert call.kwargs.get("model") == "tier-model"
            assert call.kwargs.get("tier") == "standard"

    async def test_falls_back_to_pipeline_writer_model_when_tier_unset(self):
        """If ``resolve_tier_model`` raises (no row), stage falls through
        to ``pipeline_writer_model`` site_config key — preserves operator
        backstop pre-#407 behaviour."""
        cfg = SimpleNamespace(
            get=lambda k, d="": "legacy-writer" if k == "pipeline_writer_model" else d,
            get_int=lambda _k, _d=0: _d,
            get_float=lambda _k, _d=0.0: _d,
            get_bool=lambda _k, _d=False: _d,
            _pool=object(),
        )
        dispatch_mock = AsyncMock(return_value=_completion('{"scenes": []}'))
        resolve_mock = AsyncMock(side_effect=RuntimeError("no tier mapping"))
        ctx = {
            "task_id": "t", "title": "T", "content": "B",
            "site_config": cfg,
        }
        with patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            dispatch_mock,
        ), patch(
            "services.llm_providers.dispatcher.resolve_tier_model",
            resolve_mock,
        ), patch(
            "services.gpu_scheduler.gpu",
            SimpleNamespace(lock=_no_gpu_lock),
        ):
            result = await ScriptForVideoStage().execute(ctx, {})

        assert result.metrics["writer_model"] == "legacy-writer"
