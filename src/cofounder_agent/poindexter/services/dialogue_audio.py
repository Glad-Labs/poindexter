"""Dialogue audio — a screenplay in, one voiced track plus a timeline out.

Creative studio phase 3 (poindexter#1137; epic poindexter#1134). The
``media.render_dialogue`` atom delegates here.

Each ``line`` beat is synthesized with its speaker's own persona voice — a
Kokoro voice id or a Chatterbox clone — through
``PodcastService.synthesize_line``, the same path the narration uses, so the
pronunciation and number rules apply to every character. The narrator is a
persona too (``story_default_narrator_persona``); with none set it speaks in
the house voice.

The track is assembled from those clips plus silence, and the timeline
records where every beat sits in it:

- between two lines in a scene: ``story_dialogue_gap_seconds``;
- at a scene change: ``story_scene_gap_seconds``;
- an ``action`` beat: ``story_action_beat_seconds`` of silence, so the shot
  that shows the action has screen time even though nobody speaks.

Durations are MEASURED with ffprobe after each clip is normalized, never
estimated from text: the shot list cuts lip-sync windows out of this track,
and an estimate would put a character's mouth on the wrong words.

A line that fails to synthesize is retried once and then raises. Swapping in
another voice would put one character's words in another's mouth, and a
missing line would leave a hole the shots are timed around.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Every clip is normalized to this before concatenation, so the concat demuxer
#: can join them with a stream copy and probed durations are exact.
_SAMPLE_RATE = 44100


class DialogueAudioError(RuntimeError):
    """The dialogue track could not be produced. The message names the beat."""


async def _run(argv: list[str], timeout: float = 300.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"timed out after {timeout:.0f}s"
    text = (out or b"").decode("utf-8", "replace") + (err or b"").decode("utf-8", "replace")
    return proc.returncode or 0, text


async def probe_seconds(path: str) -> float:
    rc, out = await _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path,
    ], timeout=60.0)
    if rc != 0:
        raise DialogueAudioError(f"ffprobe failed on {path}: {out[-300:]}")
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, TypeError) as exc:
        raise DialogueAudioError(f"ffprobe gave no duration for {path}") from exc


async def _normalize(src: str, dst: str) -> None:
    rc, out = await _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
        "-ar", str(_SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", dst,
    ])
    if rc != 0 or not os.path.exists(dst):
        raise DialogueAudioError(f"ffmpeg could not normalize {src}: {out[-300:]}")


async def _silence(seconds: float, dst: str) -> None:
    rc, out = await _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anullsrc=r={_SAMPLE_RATE}:cl=mono",
        "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", dst,
    ])
    if rc != 0:
        raise DialogueAudioError(f"ffmpeg could not make {seconds:.2f}s of silence: {out[-300:]}")


async def _concat(parts: list[str], dst: str, work: Path) -> None:
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    rc, out = await _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
        "-safe", "0", "-i", str(listing), "-c", "copy", dst,
    ])
    if rc != 0 or not os.path.exists(dst):
        raise DialogueAudioError(f"ffmpeg could not join the dialogue track: {out[-300:]}")


async def _reclaim_for_tts() -> None:
    """Best-effort render-GPU reclaim before a line retry. Chatterbox is a
    ladder rung, so it is excluded: restarting the engine about to be called
    would turn a full card into a guaranteed miss."""
    try:
        from poindexter.services.gpu_scheduler import gpu

        await gpu.reclaim_render_vram(include_ollama=True, exclude=("chatterbox",))
    except Exception as exc:  # noqa: BLE001 — the retry runs either way
        logger.warning("[dialogue] VRAM reclaim before retry failed: %s", exc)


def resolve_voice(site_config: Any, speaker: str, narrator_persona: str) -> Any:
    """The persona that voices ``speaker``: the cast member, or for the
    narrator the configured narrator persona. ``None`` = the house voice."""
    from poindexter.services.persona_service import get_persona
    from poindexter.services.screenplay import NARRATOR

    slug = narrator_persona if speaker == NARRATOR else speaker
    if not slug:
        return None
    persona = get_persona(site_config, slug)
    if persona is None or not persona.enabled:
        if speaker == NARRATOR:
            logger.warning(
                "[dialogue] narrator persona %r is not an enabled persona; "
                "the narrator uses the house voice", slug,
            )
            return None
        raise DialogueAudioError(f"speaker {speaker!r} is not an enabled persona")
    return persona


def _intensity_offsets(site_config: Any) -> dict[str, float]:
    """``story_line_intensity_offsets`` ("low=-0.2,medium=0,high=0.2")."""
    from poindexter.services.settings_defaults import DEFAULTS

    raw = str(site_config.get("story_line_intensity_offsets", "") or DEFAULTS["story_line_intensity_offsets"])
    out: dict[str, float] = {}
    for part in raw.split(","):
        name, _, value = part.partition("=")
        try:
            out[name.strip().lower()] = float(value)
        except ValueError:
            continue
    return out


def line_exaggeration(persona: Any, intensity: str, site_config: Any) -> float | None:
    """The Chatterbox emotion strength for one line, or ``None`` to leave it.

    The character's own level (``voice_exaggeration``, else the shared
    ``plugin.tts_provider.chatterbox.exaggeration``) moved by the line's
    intensity, clamped to ``story_line_exaggeration_min`` / ``_max`` (past
    about 1.2 Chatterbox starts to garble). A Kokoro voice has no emotion
    control, so it gets ``None``.
    """
    if persona is None or str(getattr(persona, "voice_provider", "")) != "chatterbox":
        return None
    own = getattr(persona, "voice_exaggeration", None)
    try:
        base = float(own) if own is not None else float(
            site_config.get("plugin.tts_provider.chatterbox.exaggeration", "0.5") or 0.5,
        )
    except (TypeError, ValueError):
        base = 0.5
    offset = _intensity_offsets(site_config).get((intensity or "medium").lower(), 0.0)
    low = site_config.get_float("story_line_exaggeration_min", 0.25)
    high = site_config.get_float("story_line_exaggeration_max", 1.2)
    return round(min(high, max(low, base + offset)), 3)


async def render_dialogue(
    screenplay: dict[str, Any],
    *,
    site_config: Any,
    output_path: str,
    narrator_persona: str | None = None,
    synthesize: Any = None,
) -> dict[str, Any]:
    """Voice every line, join the track, return ``{audio_path, timeline,
    spoken_script, duration_s}``.

    ``synthesize(text, persona, path, *, exaggeration=None) -> path`` is the
    per-line TTS seam; production passes ``PodcastService.synthesize_line``.
    ``exaggeration`` is passed only for a Chatterbox voice: the line's
    intensity moves the speaker's emotion strength (``line_exaggeration``).
    """
    if synthesize is None:
        from poindexter.services.podcast_service import PodcastService

        synthesize = PodcastService(site_config=site_config).synthesize_line
    # Narrator voice: an explicit argument, else the one the screenplay was
    # written for (screenplay_narrator), else story_default_narrator_persona.
    narrator_slug = (
        narrator_persona if narrator_persona is not None
        else str(screenplay.get("narrator_persona") or "")
        or str(site_config.get("story_default_narrator_persona", "") or "")
    ).strip().lower()
    line_gap = site_config.get_float("story_dialogue_gap_seconds", 0.35)
    scene_gap = site_config.get_float("story_scene_gap_seconds", 0.8)
    action_s = site_config.get_float("story_action_beat_seconds", 1.5)

    work = Path(tempfile.mkdtemp(prefix="dialogue-"))
    parts: list[str] = []
    timeline: list[dict[str, Any]] = []
    spoken: list[str] = []
    t = 0.0
    beat_idx = 0
    silences: dict[float, str] = {}

    async def _pad(seconds: float) -> None:
        nonlocal t
        if seconds <= 0:
            return
        key = round(seconds, 3)
        if key not in silences:
            silences[key] = str(work / f"silence_{len(silences)}.wav")
            await _silence(key, silences[key])
        parts.append(silences[key])
        t += await probe_seconds(silences[key])

    previous_was_line = False
    for scene_no, scene in enumerate(screenplay.get("scenes") or []):
        if scene_no:
            await _pad(scene_gap)
            previous_was_line = False
        for beat in scene.get("beats") or []:
            kind = beat.get("kind")
            if kind == "line":
                if previous_was_line:
                    await _pad(line_gap)
                speaker = str(beat.get("speaker") or "")
                persona = resolve_voice(site_config, speaker, narrator_slug)
                raw = str(work / f"line_{beat_idx:03d}_raw.mp3")
                clip = str(work / f"line_{beat_idx:03d}.wav")
                produced = None
                last_error = ""
                intensity = str(beat.get("intensity") or "medium")
                emotion = line_exaggeration(persona, intensity, site_config)
                extra = {"exaggeration": emotion} if emotion is not None else {}
                for attempt in (1, 2):
                    try:
                        produced = await synthesize(beat["text"], persona, raw, **extra)
                    except Exception as exc:  # noqa: BLE001 — retried, then raised below
                        last_error = f"{type(exc).__name__}: {exc}"
                        produced = None
                    if produced and os.path.exists(produced) and os.path.getsize(produced) > 0:
                        break
                    logger.warning(
                        "[dialogue] line %d (%s) attempt %d produced no audio %s",
                        beat_idx, speaker, attempt, last_error,
                    )
                    produced = None
                    if attempt == 1:
                        # The usual cause is a full card (2026-10-12: Chatterbox
                        # OOM'd with 45 MB free while ComfyUI held an edit model).
                        # Run the shared VRAM ladder before the retry, keeping
                        # the TTS engines we are about to call.
                        await _reclaim_for_tts()
                if not produced:
                    raise DialogueAudioError(
                        f"line {beat_idx} in scene {scene.get('scene_id')!r} "
                        f"({speaker}: {beat['text'][:60]!r}) failed to synthesize twice"
                        + (f": {last_error}" if last_error else ""),
                    )
                await _normalize(produced, clip)
                seconds = await probe_seconds(clip)
                parts.append(clip)
                timeline.append({
                    "beat_idx": beat_idx, "scene_id": scene.get("scene_id"),
                    "kind": "line", "speaker": speaker, "text": beat["text"],
                    "intensity": intensity, "exaggeration": emotion,
                    "characters": list(scene.get("characters") or []),
                    "start_s": round(t, 3), "end_s": round(t + seconds, 3),
                })
                t += seconds
                spoken.append(beat["text"])
                previous_was_line = True
            elif kind == "action":
                start = t
                await _pad(action_s)
                timeline.append({
                    "beat_idx": beat_idx, "scene_id": scene.get("scene_id"),
                    "kind": "action", "speaker": None, "text": beat.get("text", ""),
                    "characters": list(beat.get("characters") or []),
                    "start_s": round(start, 3), "end_s": round(t, 3),
                })
                previous_was_line = False
            beat_idx += 1
    if not spoken:
        raise DialogueAudioError("the screenplay has no line beats to voice")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    await _concat(parts, output_path, work)
    duration = await probe_seconds(output_path)
    logger.info(
        "[dialogue] %d lines, %d beats, %.1fs track -> %s",
        len(spoken), len(timeline), duration, output_path,
    )
    return {
        "audio_path": output_path,
        "timeline": timeline,
        "spoken_script": "\n".join(spoken),
        "duration_s": round(duration, 3),
    }


__all__ = [
    "DialogueAudioError",
    "probe_seconds",
    "render_dialogue",
    "resolve_voice",
]
