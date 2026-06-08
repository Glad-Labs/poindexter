"""Regression tests for ``services/stages/generate_media_scripts.py``.

Pins the #517-Stage-A fix: a failure while parsing the video-scenes output
(e.g. the #272 ``_normalize_for_speech`` site_config regression) must NOT
discard a podcast_script that was already built — otherwise the downstream
``generate_video_shot_list`` director starves and produces 0 shot lists.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.stages.generate_media_scripts import GenerateMediaScriptsStage


@contextlib.asynccontextmanager
async def _fake_lock(*_a: Any, **_kw: Any):
    yield


def _ctx() -> dict[str, Any]:
    sc = MagicMock()
    sc.get.return_value = "llama3:latest"
    db = SimpleNamespace(pool=MagicMock())
    return {
        "title": "A Real Title",
        "content": "body " * 200,
        "site_config": sc,
        "database_service": db,
        "task_id": "t-mediascripts",
    }


@pytest.mark.asyncio
async def test_podcast_script_preserved_when_scene_parsing_fails():
    """Call 1 builds the script; Call-2 scene parsing raises — the script
    must still flow into context_updates so the director can run."""
    gpu = SimpleNamespace(lock=_fake_lock)
    result_obj = SimpleNamespace(text="PART1\n\nSHORT:\nsummary")
    # Seam 1 Wave 3d (#667): scenes go through the capability handle now.
    ctx = _ctx()
    ctx["platform"] = MagicMock()
    ctx["platform"].dispatch.complete = AsyncMock(return_value=result_obj)

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch(
             "services.podcast_service._build_script_with_llm",
             new=AsyncMock(return_value="A" * 500),
         ), \
         patch(
             "modules.content.stages.generate_media_scripts._parse_scene_output",
             side_effect=RuntimeError(
                 "podcast_service requires a site_config",
             ),
         ):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    # The scene parse raised, but the already-built script must survive.
    assert result.context_updates.get("podcast_script") == "A" * 500
    assert result.context_updates.get("podcast_script_length") == 500


@pytest.mark.asyncio
async def test_happy_path_propagates_podcast_script_and_scenes():
    """Sanity: when nothing fails, podcast_script + scenes propagate."""
    gpu = SimpleNamespace(lock=_fake_lock)
    result_obj = SimpleNamespace(text="1. a scene\n2. another\n\nSHORT:\nsummary")
    # Seam 1 Wave 3d (#667): scenes go through the capability handle now.
    ctx = _ctx()
    ctx["platform"] = MagicMock()
    ctx["platform"].dispatch.complete = AsyncMock(return_value=result_obj)

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch(
             "services.podcast_service._build_script_with_llm",
             new=AsyncMock(return_value="B" * 500),
         ):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok is True
    assert result.context_updates["podcast_script"] == "B" * 500


# ---------------------------------------------------------------------------
# Audio gen wiring tests (glad-labs-stack#621)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audio_gen_intro_called_when_enabled():
    """When audio_gen is enabled and podcast_script is long enough,
    generate_audio must be called with kind='intro'."""
    from plugins.audio_gen_provider import AudioGenResult
    from types import SimpleNamespace

    gpu = SimpleNamespace(lock=_fake_lock)
    audio_calls = []

    async def mock_generate_audio(prompt, kind, *, site_config, **kw):
        audio_calls.append({"prompt": prompt, "kind": kind})
        return AudioGenResult(file_path="/tmp/intro.wav", kind=kind)

    ctx = _ctx()
    # No platform/pool — video scenes call will be skipped gracefully
    with patch("services.gpu_scheduler.gpu", gpu), \
         patch(
             "services.podcast_service._build_script_with_llm",
             new=AsyncMock(return_value="C" * 500),
         ), \
         patch(
             "modules.content.stages.generate_media_scripts.is_audio_gen_enabled",
             return_value=True,
         ), \
         patch(
             "modules.content.stages.generate_media_scripts.generate_audio",
             new=mock_generate_audio,
         ), \
         patch(
             "modules.content.stages.generate_media_scripts.is_tts_enabled",
             return_value=False,
         ):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    intro_calls = [c for c in audio_calls if c["kind"] == "intro"]
    assert len(intro_calls) == 1
    assert "podcast intro sting" in intro_calls[0]["prompt"]


@pytest.mark.asyncio
async def test_audio_gen_skipped_when_disabled():
    """When audio_gen_enabled is False, generate_audio must not be called."""
    from types import SimpleNamespace

    gpu = SimpleNamespace(lock=_fake_lock)
    audio_calls = []

    async def mock_generate_audio(*a, **kw):
        audio_calls.append(True)

    ctx = _ctx()
    with patch("services.gpu_scheduler.gpu", gpu), \
         patch(
             "services.podcast_service._build_script_with_llm",
             new=AsyncMock(return_value="D" * 500),
         ), \
         patch(
             "modules.content.stages.generate_media_scripts.is_audio_gen_enabled",
             return_value=False,
         ), \
         patch(
             "modules.content.stages.generate_media_scripts.generate_audio",
             new=mock_generate_audio,
         ), \
         patch(
             "modules.content.stages.generate_media_scripts.is_tts_enabled",
             return_value=False,
         ):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    assert len(audio_calls) == 0


@pytest.mark.asyncio
async def test_tts_called_when_enabled():
    """When podcast_tts_enabled is True and script is long enough,
    synthesize_speech must be called."""
    from types import SimpleNamespace

    gpu = SimpleNamespace(lock=_fake_lock)
    tts_calls = []

    async def mock_synthesize_speech(text, *, site_config, output_path=None):
        tts_calls.append(text)
        return b"RIFF_fake_wav"

    ctx = _ctx()
    with patch("services.gpu_scheduler.gpu", gpu), \
         patch(
             "services.podcast_service._build_script_with_llm",
             new=AsyncMock(return_value="E" * 500),
         ), \
         patch(
             "modules.content.stages.generate_media_scripts.is_tts_enabled",
             return_value=True,
         ), \
         patch(
             "modules.content.stages.generate_media_scripts.synthesize_speech",
             new=mock_synthesize_speech,
         ), \
         patch(
             "modules.content.stages.generate_media_scripts.is_audio_gen_enabled",
             return_value=False,
         ):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    assert len(tts_calls) == 1


@pytest.mark.asyncio
async def test_ambient_path_returned_via_context_updates():
    gpu = SimpleNamespace(lock=_fake_lock)
    # Supply platform so video_scenes LLM call runs and returns scene text.
    scene_text = "1. a cinematic wide shot of mountains\n\nSHORT:\nsummary text"
    result_obj = SimpleNamespace(text=scene_text)
    ctx = _ctx()
    ctx["platform"] = MagicMock()
    ctx["platform"].dispatch.complete = AsyncMock(return_value=result_obj)

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch("services.podcast_service._build_script_with_llm",
               new=AsyncMock(return_value="P" * 600)), \
         patch("modules.content.stages.generate_media_scripts.is_audio_gen_enabled",
               return_value=True), \
         patch("modules.content.stages.generate_media_scripts.generate_audio",
               new=AsyncMock(return_value=SimpleNamespace(file_path="/tmp/ambient.wav"))):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    assert result.context_updates.get("video_ambient_audio_path") == "/tmp/ambient.wav"


# ---------------------------------------------------------------------------
# Podcast audio paths via context_updates (poindexter#690) — the podcast-audio
# twin of the #679 ambient discard. The TTS narration + intro sting were
# written via direct ``context[...] =`` (dropped by make_stage_node) AND were
# undeclared PipelineState channels (dropped by LangGraph). They must instead
# flow out via context_updates, and survive a later scene-parse failure since
# they are built before the video-scenes call.
# ---------------------------------------------------------------------------

class _FakeNamedTmp:
    """Stand-in for tempfile.NamedTemporaryFile so the test never touches disk
    (and dodges the ``:`` -in-suffix Windows footgun from the mock site_config).
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> "_FakeNamedTmp":
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


@pytest.mark.asyncio
async def test_podcast_audio_path_returned_via_context_updates():
    gpu = SimpleNamespace(lock=_fake_lock)
    ctx = _ctx()  # no platform → scene call skipped, only the TTS block runs

    async def _mock_tts(text, *, site_config, output_path=None):
        return b"RIFF_fake_wav"

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch("services.podcast_service._build_script_with_llm",
               new=AsyncMock(return_value="P" * 600)), \
         patch("modules.content.stages.generate_media_scripts.is_tts_enabled",
               return_value=True), \
         patch("modules.content.stages.generate_media_scripts.synthesize_speech",
               new=_mock_tts), \
         patch("modules.content.stages.generate_media_scripts.is_audio_gen_enabled",
               return_value=False), \
         patch("tempfile.NamedTemporaryFile",
               return_value=_FakeNamedTmp("/tmp/podcast_tts.wav")):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    assert result.context_updates.get("podcast_audio_path") == "/tmp/podcast_tts.wav"


@pytest.mark.asyncio
async def test_intro_sting_path_returned_via_context_updates():
    gpu = SimpleNamespace(lock=_fake_lock)
    ctx = _ctx()  # no platform → only the intro-sting block fires (not ambient)

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch("services.podcast_service._build_script_with_llm",
               new=AsyncMock(return_value="Q" * 600)), \
         patch("modules.content.stages.generate_media_scripts.is_audio_gen_enabled",
               return_value=True), \
         patch("modules.content.stages.generate_media_scripts.generate_audio",
               new=AsyncMock(return_value=SimpleNamespace(file_path="/tmp/intro.wav"))), \
         patch("modules.content.stages.generate_media_scripts.is_tts_enabled",
               return_value=False):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    assert result.context_updates.get("podcast_intro_audio_path") == "/tmp/intro.wav"


@pytest.mark.asyncio
async def test_podcast_audio_paths_preserved_on_scene_failure():
    """TTS + intro sting are built before the video-scenes call. A later
    scene-parse failure must NOT discard them (same preservation contract as
    podcast_script)."""
    gpu = SimpleNamespace(lock=_fake_lock)
    ctx = _ctx()
    ctx["platform"] = MagicMock()
    ctx["platform"].dispatch.complete = AsyncMock(
        return_value=SimpleNamespace(text="PART1\n\nSHORT:\nsummary"),
    )

    async def _mock_tts(text, *, site_config, output_path=None):
        return b"RIFF_fake_wav"

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch("services.podcast_service._build_script_with_llm",
               new=AsyncMock(return_value="R" * 600)), \
         patch("modules.content.stages.generate_media_scripts.is_tts_enabled",
               return_value=True), \
         patch("modules.content.stages.generate_media_scripts.synthesize_speech",
               new=_mock_tts), \
         patch("modules.content.stages.generate_media_scripts.is_audio_gen_enabled",
               return_value=True), \
         patch("modules.content.stages.generate_media_scripts.generate_audio",
               new=AsyncMock(return_value=SimpleNamespace(file_path="/tmp/intro.wav"))), \
         patch("tempfile.NamedTemporaryFile",
               return_value=_FakeNamedTmp("/tmp/podcast_tts.wav")), \
         patch("modules.content.stages.generate_media_scripts._parse_scene_output",
               side_effect=RuntimeError("podcast_service requires a site_config")):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    # Scene parse raised, but the audio built beforehand must survive.
    assert result.context_updates.get("podcast_script") == "R" * 600
    assert result.context_updates.get("podcast_audio_path") == "/tmp/podcast_tts.wav"
    assert result.context_updates.get("podcast_intro_audio_path") == "/tmp/intro.wav"
