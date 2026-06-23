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

from modules.content.stages.generate_media_scripts import (
    GenerateMediaScriptsStage,
    _build_video_narration_prompt,
    _parse_scene_output,
    _resolve_media_title,
)


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
    from types import SimpleNamespace

    from plugins.audio_gen_provider import AudioGenResult

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

    def __enter__(self) -> _FakeNamedTmp:
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


# ---------------------------------------------------------------------------
# poindexter#716 — cost-tier resolver replaces hardcoded model fallbacks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_model_resolves_via_tier():
    """When site_config returns 'auto', resolve_tier_model is called instead
    of silently pinning llama3:latest."""
    sc = MagicMock()
    sc.get.return_value = "auto"  # video_scene_model = "auto"
    db = SimpleNamespace(pool=MagicMock())
    ctx = {
        "title": "A Real Title",
        "content": "body " * 200,
        "site_config": sc,
        "database_service": db,
        "task_id": "t-tier-resolve",
    }
    ctx["platform"] = MagicMock()
    ctx["platform"].dispatch.complete = AsyncMock(
        return_value=SimpleNamespace(text="1. a scene\n\nSHORT:\nsummary")
    )
    gpu = SimpleNamespace(lock=_fake_lock)

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch("services.podcast_service._build_script_with_llm",
               new=AsyncMock(return_value="S" * 500)), \
         patch(
             "services.llm_providers.dispatcher.resolve_tier_model",
             new=AsyncMock(return_value="ollama/gemma3:27b"),
         ) as mock_resolve:
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    mock_resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_model_and_no_pool_skips_gracefully():
    """When site_config returns nothing AND there is no DB pool, the stage
    must skip (ok=True, skipped=True) rather than hardcode a model name."""
    sc = MagicMock()
    sc.get.return_value = None  # all settings keys return None
    ctx = {
        "title": "A Real Title",
        "content": "body " * 200,
        "site_config": sc,
        "database_service": None,  # no pool
        "task_id": "t-no-pool",
    }
    gpu = SimpleNamespace(lock=_fake_lock)

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch("services.podcast_service._build_script_with_llm",
               new=AsyncMock(return_value="T" * 500)):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    assert result.metrics.get("skipped") is True


# ---------------------------------------------------------------------------
# Tolerant SHORT: marker parsing (Glad-Labs/poindexter#689)
#
# Stage-2 short-form video (``video_short``) never rendered on prod because the
# Shorts narration was never extracted: ``_SHORT_SPLIT`` required the ``SHORT:``
# marker to sit ALONE on its line (trailing ``\s*\n``), but the local phi4:14b
# writer puts the narration inline after the marker — or decorates it
# (``**SHORT:**``, ``PART 2 - SHORT:``). The split then returned 1 part and
# ``short_summary`` fell back to "" on 100% of prod runs (5/5 recent
# pipeline_versions: video_scenes populated, short_summary_script 0 chars).
# Same tolerant-parse philosophy as the #1445 director reconcile.
# ---------------------------------------------------------------------------


def _identity(text: str) -> str:
    """Stand-in for ``_normalize_for_speech`` — isolates the splitter."""
    return text


# Scene descriptions must clear the >20-char floor in _parse_scene_output.
_SCENE_A = "a cinematic wide shot of misty mountains at dawn"
_SCENE_B = "a glowing server rack in a dark data center"
_NARRATION = (
    "Ever wondered how AI writes blogs? Here are three takeaways you can use "
    "today. Full article at our site."
)


def test_short_marker_inline_same_line():
    """phi4:14b writes the narration INLINE: ``SHORT: <text>``. The real prod
    failure mode — must capture the narration, not drop it."""
    output = f"1. {_SCENE_A}\n2. {_SCENE_B}\n\nSHORT: {_NARRATION}"

    scenes, short = _parse_scene_output(output, _identity)

    assert short == _NARRATION
    assert scenes == [_SCENE_A, _SCENE_B]


def test_short_marker_own_line_still_works():
    """The original happy path (marker alone on its line) must NOT regress."""
    output = f"1. {_SCENE_A}\n2. {_SCENE_B}\n\nSHORT:\n{_NARRATION}"

    scenes, short = _parse_scene_output(output, _identity)

    assert short == _NARRATION
    assert scenes == [_SCENE_A, _SCENE_B]


def test_short_marker_bold_decorated():
    """Models often bold the marker: ``**SHORT:**``. Tolerate the markdown."""
    output = f"1. {_SCENE_A}\n\n**SHORT:** {_NARRATION}"

    scenes, short = _parse_scene_output(output, _identity)

    assert short == _NARRATION
    assert scenes == [_SCENE_A]


def test_short_marker_with_part2_prefix():
    """``PART 2 - SHORT:`` echoes the prompt's PART labels — tolerate the
    lead-in and the dash separator."""
    output = f"1. {_SCENE_A}\n\nPART 2 — SHORT: {_NARRATION}"

    scenes, short = _parse_scene_output(output, _identity)

    assert short == _NARRATION
    assert scenes == [_SCENE_A]


def test_no_marker_falls_back_to_trailing_paragraph():
    """No SHORT: marker at all — fall back to the trailing prose paragraph
    rather than returning an empty short_summary, and don't mistake that prose
    for a scene."""
    output = f"1. {_SCENE_A}\n2. {_SCENE_B}\n\n{_NARRATION}"

    scenes, short = _parse_scene_output(output, _identity)

    assert short == _NARRATION
    assert scenes == [_SCENE_A, _SCENE_B]


def test_no_marker_no_trailing_prose_leaves_short_empty():
    """All numbered scenes, no narration, no marker — short stays empty rather
    than fabricating one from a scene line."""
    output = f"1. {_SCENE_A}\n2. {_SCENE_B}"

    scenes, short = _parse_scene_output(output, _identity)

    assert short == ""
    assert scenes == [_SCENE_A, _SCENE_B]


# ---------------------------------------------------------------------------
# Distinct long-form VIDEO narration script (poindexter#689)
#
# The long video must narrate its OWN script (paced to on-screen visuals),
# not reuse the podcast script. generate_media_scripts emits it via
# context_updates as ``video_long_script``.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_long_script_emitted_via_context_updates():
    """A distinct video_long_script is generated + flows via context_updates."""
    gpu = SimpleNamespace(lock=_fake_lock)

    def _complete(*, messages, **_kw):
        # The video-narration call is the one whose prompt asks for a
        # "voiceover narration"; the scene call gets canned scene text.
        prompt = messages[0]["content"]
        if "voiceover narration" in prompt:
            return SimpleNamespace(
                text="The new GPU changes the math for local inference.",
            )
        return SimpleNamespace(text="1. a cinematic shot\n\nSHORT: quick hook here")

    ctx = _ctx()
    ctx["platform"] = MagicMock()
    ctx["platform"].dispatch.complete = AsyncMock(side_effect=_complete)

    with patch("services.gpu_scheduler.gpu", gpu), \
         patch("services.podcast_service._build_script_with_llm",
               new=AsyncMock(return_value="A" * 500)), \
         patch("services.podcast_service._normalize_for_speech",
               new=lambda text, **_k: text), \
         patch("modules.content.stages.generate_media_scripts.is_tts_enabled",
               return_value=False), \
         patch("modules.content.stages.generate_media_scripts.is_audio_gen_enabled",
               return_value=False):
        result = await GenerateMediaScriptsStage().execute(ctx, {})

    assert result.ok
    vls = result.context_updates.get("video_long_script", "")
    assert vls.strip() != ""
    assert "inference" in vls.lower()


# --- Bug A: long-video narration must not direct the viewer's eye ---------
# The renderer pairs narration with generic static images, so a script that
# says "on screen / here we see / watch as" promises visuals the footage can't
# deliver (the mismatch a viewer notices). The prompt must produce pure spoken
# narration. See fix/media-script-narration-and-title.

_STAGE_DIRECTION_TELLS = (
    "on screen",
    "on-screen",
    "here we see",
    "as you can see",
    "you can see",
    "watch as",
    "pictured",
    "in this image",
    "in this shot",
    "in this clip",
)


def test_video_narration_prompt_has_no_visual_stage_directions():
    prompt = _build_video_narration_prompt("My Title", "Some article body.").lower()
    for tell in _STAGE_DIRECTION_TELLS:
        assert tell not in prompt, f"narration prompt still invites '{tell}'"


def test_video_narration_prompt_signals_audio_only_framing():
    # Guards against silently reverting to a visuals-referencing prompt: the
    # rewrite must positively frame the narration as standalone audio.
    prompt = _build_video_narration_prompt("My Title", "Some article body.").lower()
    assert any(w in prompt for w in ("spoken", "audio", "for the ear", "listener"))
    assert "my title" in prompt and "some article body." in prompt


# --- Bug C2: media narration must speak the real title, not a polluted one --
# content.generate_title can leak a style-rubric line into the title channel;
# the podcast intro ("Today's episode: {title}") then speaks rubric text. The
# clean title lives in seo_title / the content H1.

def test_resolve_media_title_prefers_clean_seo_title_over_polluted_title():
    ctx = {
        "title": "Avoids the \"Version/Phase\" style: No mention of PRs or commits.",
        "seo_title": "Mechanical Keyboard Switches: Linear vs Tactile vs Clicky",
        "content": "# Some H1\n\nbody",
    }
    assert _resolve_media_title(ctx) == (
        "Mechanical Keyboard Switches: Linear vs Tactile vs Clicky"
    )


def test_resolve_media_title_falls_back_to_content_h1_when_no_seo_title():
    ctx = {
        "title": "polluted rubric line, not a title",
        "content": "# Mechanical Keyboard Switches Explained\n\nbody text",
    }
    assert _resolve_media_title(ctx) == "Mechanical Keyboard Switches Explained"


def test_resolve_media_title_last_resort_raw_title():
    ctx = {"title": "Only Title Here", "content": "no heading body"}
    assert _resolve_media_title(ctx) == "Only Title Here"
