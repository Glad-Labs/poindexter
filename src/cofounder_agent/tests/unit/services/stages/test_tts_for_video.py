"""Unit tests for ``services/stages/tts_for_video.py``.

TtsForVideoStage walks the video_script + drives a registered
TTSProvider per scene. We patch the registry + provider class so the
tests exercise the stage's own control flow — engine resolution
fallback, per-scene synth ordering, intro/outro audio capture — not a
real TTS engine.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.stage import Stage
from plugins.tts_provider import TTSResult
from services.stages.tts_for_video import (
    TtsForVideoStage,
    _default_tts,
    _read_engine,
    _resolve_tts_provider,
)


def _make_site_config(overrides: dict[str, Any] | None = None):
    overrides = overrides or {}

    def _get(key, default=""):
        return overrides.get(key, default)

    return SimpleNamespace(
        get=_get, get_int=_get, get_float=_get, get_bool=_get,
    )


def _scene(idx: int, text: str | None = None):
    return {
        "narration_text": text if text is not None else f"narration {idx}",
        "visual_prompt": "v",
        "duration_s_hint": 13,
    }


def _video_script(long_form_count: int = 2, short_form_count: int = 1):
    return {
        "long_form": {
            "intro_hook": "Long intro!",
            "outro_cta": "Subscribe.",
            "scenes": [_scene(i) for i in range(long_form_count)],
        },
        "short_form": {
            "intro_hook": "Short hook!",
            "scenes": [_scene(i) for i in range(short_form_count)],
        },
    }


class _FakeTTSProvider:
    """Records every call + writes a real (empty) tempfile so the stage
    sees the file exists.

    Used in lieu of patching ``_synthesize_one`` so the file-existence
    branch in the stage gets exercised end-to-end.
    """

    def __init__(self, name: str = "edge_tts", duration_s: float = 5.0):
        self.name = name
        self.default_format = "mp3"
        self.duration_s = duration_s
        self.calls: list[dict[str, Any]] = []

    async def synthesize(self, text, output_path: Path, *, voice=None, config=None):
        self.calls.append({"text": text, "output_path": str(output_path), "voice": voice})
        # Touch the output file so os.path.exists() inside _synthesize_one
        # returns True.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")
        return TTSResult(
            audio_path=output_path,
            duration_seconds=int(self.duration_s),
            voice=voice or "default",
            audio_format="mp3",
        )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestReadEngine:
    def test_video_engine_wins(self):
        cfg = _make_site_config({
            "video_tts_engine": "kokoro",
            "podcast_tts_engine": "edge_tts",
        })
        assert _read_engine(cfg) == "kokoro"

    def test_falls_back_to_podcast_engine(self):
        cfg = _make_site_config({"podcast_tts_engine": "kokoro"})
        assert _read_engine(cfg) == "kokoro"

    def test_default_when_neither_set(self):
        cfg = _make_site_config({})
        assert _read_engine(cfg) == "edge_tts"

    def test_none_site_config_returns_default(self):
        assert _read_engine(None) == "edge_tts"


class TestResolveTtsProvider:
    def test_returns_matching_provider(self):
        prov_a = SimpleNamespace(name="kokoro")
        prov_b = SimpleNamespace(name="edge_tts")
        with patch(
            "plugins.registry.get_tts_providers",
            return_value=[prov_a, prov_b],
        ):
            assert _resolve_tts_provider("kokoro") is prov_a
            assert _resolve_tts_provider("edge_tts") is prov_b

    def test_returns_none_for_unknown_engine(self):
        with patch(
            "plugins.registry.get_tts_providers",
            return_value=[SimpleNamespace(name="other")],
        ):
            assert _resolve_tts_provider("nope") is None

    def test_registry_failure_returns_none(self):
        with patch(
            "plugins.registry.get_tts_providers",
            side_effect=RuntimeError("registry boom"),
        ):
            assert _resolve_tts_provider("anything") is None


class TestDefaultTts:
    def test_shape(self):
        v = _default_tts()
        assert v["long_form"]["scenes"] == []
        assert v["short_form"]["scenes"] == []
        assert v["long_form"]["intro_audio_path"] == ""
        assert v["long_form"]["outro_audio_path"] == ""
        assert v["short_form"]["intro_audio_path"] == ""
        # short_form has no outro field per the docstring contract


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms(self):
        assert isinstance(TtsForVideoStage(), Stage)

    def test_metadata(self):
        s = TtsForVideoStage()
        assert s.name == "video.tts"
        assert s.halts_on_failure is False


# ---------------------------------------------------------------------------
# Stage.execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecute:
    async def test_missing_site_config_returns_not_ok(self):
        result = await TtsForVideoStage().execute({}, {})
        assert result.ok is False
        assert "site_config" in result.detail
        assert result.metrics.get("skipped") is True

    async def test_no_scenes_returns_not_ok(self):
        ctx = {
            "site_config": _make_site_config(),
            "video_script": {"long_form": {"scenes": []}, "short_form": {"scenes": []}},
        }
        result = await TtsForVideoStage().execute(ctx, {})
        assert result.ok is False
        assert "no scenes" in result.detail

    async def test_no_provider_registered_returns_not_ok(self):
        ctx = {
            "site_config": _make_site_config(),
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.tts_for_video._resolve_tts_provider",
            return_value=None,
        ):
            result = await TtsForVideoStage().execute(ctx, {})
        assert result.ok is False
        assert "TTSProvider" in result.detail

    async def test_explicit_engine_not_registered_falls_back_to_default(self):
        # Configured engine "kokoro" not present, but "edge_tts" is.
        provider = _FakeTTSProvider(name="edge_tts")

        def _resolve(engine: str):
            return provider if engine == "edge_tts" else None

        ctx = {
            "site_config": _make_site_config({"video_tts_engine": "kokoro"}),
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.tts_for_video._resolve_tts_provider",
            side_effect=_resolve,
        ):
            result = await TtsForVideoStage().execute(ctx, {})
        assert result.ok is True
        # Stage tried kokoro, fell back to edge_tts
        assert result.metrics["engine"] == "edge_tts"

    async def test_happy_path_renders_intro_scenes_outro(self):
        provider = _FakeTTSProvider(name="edge_tts", duration_s=4.0)
        ctx = {
            "task_id": "t1",
            "site_config": _make_site_config(),
            "video_script": _video_script(long_form_count=2, short_form_count=1),
        }
        with patch(
            "services.stages.tts_for_video._resolve_tts_provider",
            return_value=provider,
        ):
            result = await TtsForVideoStage().execute(ctx, {})

        assert result.ok is True
        long_payload = result.context_updates["video_tts"]["long_form"]
        short_payload = result.context_updates["video_tts"]["short_form"]
        # 1 intro + 2 scenes + 1 outro for long-form
        assert long_payload["intro_audio_path"] != ""
        assert long_payload["outro_audio_path"] != ""
        assert len(long_payload["scenes"]) == 2
        assert all(s["audio_path"] for s in long_payload["scenes"])
        # Short-form: 1 intro + 1 scene; no outro
        assert short_payload["intro_audio_path"] != ""
        assert len(short_payload["scenes"]) == 1
        # Total duration accumulates per call (provider.duration_s × calls)
        assert long_payload["total_duration_s"] > 0
        assert short_payload["total_duration_s"] > 0
        # Provider got called for: long_intro, 2 long_scenes, long_outro,
        # short_intro, 1 short_scene = 6 calls
        assert len(provider.calls) == 6

    async def test_per_scene_ordering_long_then_short(self):
        provider = _FakeTTSProvider()
        ctx = {
            "task_id": "t",
            "site_config": _make_site_config(),
            "video_script": _video_script(long_form_count=2, short_form_count=2),
        }
        with patch(
            "services.stages.tts_for_video._resolve_tts_provider",
            return_value=provider,
        ):
            await TtsForVideoStage().execute(ctx, {})

        # Assert call order: long_intro → long_scene_0 → long_scene_1 →
        # long_outro → short_intro → short_scene_0 → short_scene_1
        texts = [call["text"] for call in provider.calls]
        # Confirm the long-form block precedes the short-form block.
        long_intro_idx = texts.index("Long intro!")
        long_outro_idx = texts.index("Subscribe.")
        short_intro_idx = texts.index("Short hook!")
        assert long_intro_idx < long_outro_idx < short_intro_idx

    async def test_provider_failure_does_not_kill_stage(self):
        # _synthesize_one swallows provider errors and returns ("", 0.0).
        provider = SimpleNamespace(
            name="edge_tts", default_format="mp3",
            synthesize=AsyncMock(side_effect=RuntimeError("tts boom")),
        )
        ctx = {
            "task_id": "t",
            "site_config": _make_site_config(),
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.tts_for_video._resolve_tts_provider",
            return_value=provider,
        ):
            result = await TtsForVideoStage().execute(ctx, {})

        # All scenes failed to render → ok is False (rendered count != requested)
        assert result.ok is False
        long_payload = result.context_updates["video_tts"]["long_form"]
        # Stage-level audio_path should be empty for the failed scene.
        for s in long_payload["scenes"]:
            assert s["audio_path"] == ""

    async def test_skips_empty_narration(self):
        # _synthesize_one short-circuits on blank text.
        provider = _FakeTTSProvider()
        script = {
            "long_form": {
                "intro_hook": "",  # blank → skipped
                "outro_cta": "",
                "scenes": [
                    {"narration_text": "", "visual_prompt": "v", "duration_s_hint": 30},
                ],
            },
            "short_form": {"scenes": []},
        }
        ctx = {
            "task_id": "t",
            "site_config": _make_site_config(),
            "video_script": script,
        }
        with patch(
            "services.stages.tts_for_video._resolve_tts_provider",
            return_value=provider,
        ):
            result = await TtsForVideoStage().execute(ctx, {})

        # Provider never called for the blank intro / empty narration.
        # (provider.synthesize gets called only when text is non-empty.)
        assert len(provider.calls) == 0
        long_payload = result.context_updates["video_tts"]["long_form"]
        assert long_payload["scenes"][0]["audio_path"] == ""

    async def test_records_metrics(self):
        provider = _FakeTTSProvider()
        ctx = {
            "task_id": "t",
            "site_config": _make_site_config(),
            "video_script": _video_script(long_form_count=1, short_form_count=1),
        }
        with patch(
            "services.stages.tts_for_video._resolve_tts_provider",
            return_value=provider,
        ):
            result = await TtsForVideoStage().execute(ctx, {})

        m = result.metrics
        assert m["engine"] == "edge_tts"
        assert m["long_rendered"] == 1
        assert m["short_rendered"] == 1
        assert "long_total_s" in m
        assert "short_total_s" in m
