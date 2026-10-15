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

**Line cache** (chat drafting phase 10). Every voiced line is kept under
``story_line_cache_dir``, keyed by what decides how it sounds: the words, the
intensity, the emotion strength, and the speaker's voice (provider, voice id,
reference clip, exaggeration) plus the house voice settings. Voicing a
revised script synthesizes only the lines that changed; every other line is
the exact take the operator already heard. Chatterbox never says a line the
same way twice, so this is about keeping an approved take as much as about
saving GPU. Files untouched for ``story_line_cache_keep_days`` are deleted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
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


_VOICE_FIELDS = ("slug", "voice_provider", "voice_id", "voice_ref_audio_url", "voice_exaggeration")
_HOUSE_VOICE_KEYS = ("podcast_tts_voice", "plugin.tts_provider.chatterbox.exaggeration")


def line_cache_key(persona: Any, text: str, intensity: str, emotion: float | None, site_config: Any) -> str:
    """What decides how a line sounds, hashed: the cache file's name."""
    voice = {f: getattr(persona, f, None) for f in _VOICE_FIELDS} if persona is not None else None
    house = {k: str(site_config.get(k, "") or "") for k in _HOUSE_VOICE_KEYS} if site_config is not None else {}
    blob = json.dumps(
        {"voice": voice, "house": house, "text": text, "intensity": intensity, "emotion": emotion},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]


def line_cache_dir(site_config: Any) -> str | None:
    """The line cache directory, or ``None`` when ``story_line_cache_enabled`` is off."""
    from poindexter.services.settings_defaults import DEFAULTS

    def _get(key: str) -> str:
        raw = site_config.get(key, None) if site_config is not None else None
        return str(DEFAULTS[key] if raw is None or str(raw).strip() == "" else raw)

    if _get("story_line_cache_enabled").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    path = os.path.expanduser(_get("story_line_cache_dir"))
    os.makedirs(path, exist_ok=True)
    return path


def prune_line_cache(path: str, keep_days: float) -> int:
    """Delete cached lines untouched for ``keep_days``. Returns how many."""
    if keep_days <= 0 or not os.path.isdir(path):
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                os.remove(full)
                removed += 1
        except OSError:  # silent-ok: a file another render is using stays
            continue
    return removed


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


def _words(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9']+", text.lower().replace("\u2019", "'"))


def word_recall(expected: str, heard: str) -> float:
    """The share of the line's words that were heard, each counted as often as it is written."""
    from collections import Counter

    want, got = Counter(_words(expected)), Counter(_words(heard))
    total = sum(want.values())
    if not total:
        return 1.0
    return sum(min(n, got[w]) for w, n in want.items()) / total


def line_check_settings(site_config: Any) -> tuple[Any, float, int] | None:
    """``(transcribe, min_recall, retries)`` for the per-line check, or ``None`` when it is off."""
    from poindexter.services.settings_defaults import DEFAULTS

    def _get(key: str) -> str:
        raw = site_config.get(key, None) if site_config is not None else None
        return str(DEFAULTS[key] if raw is None or str(raw).strip() == "" else raw)

    if _get("story_line_check_enabled").strip().lower() not in ("true", "1", "yes", "on"):
        return None

    async def _transcribe(path: str) -> str | None:
        from poindexter.services.caption_providers.speaches import SpeachesCaptionProvider

        result = await SpeachesCaptionProvider(site_config=site_config).transcribe(
            audio_path=path, language_hint="en", phase="story_line_check",
        )
        if not result.success:
            return None
        return " ".join(seg.text for seg in result.segments)

    return _transcribe, float(_get("story_line_min_recall")), int(float(_get("story_line_check_retries")))


async def _keep_the_clearest_take(
    clip: str, text: str, check: tuple[Any, float, int], synthesize: Any, *, work: Path, label: str,
) -> bool:
    """Hear ``clip`` back; while it lost words, voice it again. True when a new take replaced it.

    The Battery Drop's render (2026-10-15): Bolt's "The last one! Cinder,
    that is literally the very last one!" came out with its first three words
    squeezed into 0.4 s, heard back as "Hust, Cinder...". Matt: "his first
    line starts late". Nothing listened to a line after voicing it, and the
    line cache handed the same take to every later render. A take whose
    words heard back cover less than ``story_line_min_recall`` of the line
    is voiced again, up to ``story_line_check_retries`` times, and the take
    heard best is kept. A line that cannot be heard (STT down) is kept as is.
    """
    transcribe, min_recall, retries = check
    heard = await transcribe(clip)
    if heard is None:
        logger.warning("[dialogue] %s could not be heard back; kept unchecked", label)
        return False
    best = word_recall(text, heard)
    replaced = False
    for attempt in range(1, retries + 1):
        if best >= min_recall:
            break
        logger.warning("[dialogue] %s heard back as %r (%.0f%% of its words); voicing it again (%d/%d)",
                       label, heard[:120], best * 100, attempt, retries)
        raw = str(work / f"retake_{os.path.basename(clip)}_{attempt}.mp3")
        take = str(work / f"retake_{os.path.basename(clip)}_{attempt}.wav")
        try:
            produced = await synthesize(raw)
        except Exception as exc:  # noqa: BLE001 — the take we have stands
            logger.warning("[dialogue] %s retake %d failed: %s", label, attempt, exc)
            continue
        if not produced or not os.path.exists(produced) or os.path.getsize(produced) == 0:
            continue
        await _normalize(produced, take)
        again = await transcribe(take)
        recall = word_recall(text, again or "")
        if recall > best:
            shutil.copyfile(take, clip)
            best, heard, replaced = recall, again or "", True
    if best < min_recall:
        logger.warning("[dialogue] %s still heard as %r after %d retakes; keeping the clearest", label, heard[:120], retries)
    return replaced


async def render_dialogue(
    screenplay: dict[str, Any],
    *,
    site_config: Any,
    output_path: str,
    narrator_persona: str | None = None,
    synthesize: Any = None,
    line_cache: str | None = None,
    transcribe: Any = None,
) -> dict[str, Any]:
    """Voice every line, join the track, return ``{audio_path, timeline,
    spoken_script, duration_s}``.

    Each line is heard back (``transcribe(path) -> text | None``; production
    uses the Speaches captioner) and voiced again when it lost words: see
    ``_keep_the_clearest_take``. ``story_line_check_enabled`` turns it off.

    ``synthesize(text, persona, path, *, exaggeration=None) -> path`` is the
    per-line TTS seam; production passes ``PodcastService.synthesize_line``.
    ``exaggeration`` is passed only for a Chatterbox voice: the line's
    intensity moves the speaker's emotion strength (``line_exaggeration``).

    ``line_cache`` is the line cache directory; production (no
    ``synthesize`` seam) uses ``story_line_cache_dir`` when the cache is on.
    The result's ``lines_reused`` counts lines taken from it.
    """
    injected = synthesize is not None
    if synthesize is None:
        from poindexter.services.podcast_service import PodcastService

        synthesize = PodcastService(site_config=site_config).synthesize_line
        if line_cache is None:
            line_cache = line_cache_dir(site_config)
    if line_cache:
        try:
            keep = float(site_config.get("story_line_cache_keep_days", 30) or 30)
        except (TypeError, ValueError):
            keep = 30.0
        prune_line_cache(line_cache, keep)
    reused = 0
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

    check = line_check_settings(site_config)
    if check is not None and transcribe is not None:
        check = (transcribe, check[1], check[2])
    elif check is not None and injected:
        # A caller with its own voice and no ear (tests) is not heard back.
        check = None

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
                cached = (
                    os.path.join(line_cache, line_cache_key(persona, beat["text"], intensity, emotion, site_config) + ".wav")
                    if line_cache else None
                )
                if cached and os.path.isfile(cached) and os.path.getsize(cached) > 0:
                    shutil.copyfile(cached, clip)
                    os.utime(cached)
                    produced = clip
                    reused += 1
                for attempt in (1, 2) if produced is None else ():
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
                fresh = produced != clip
                if fresh:
                    await _normalize(produced, clip)
                if check is not None:
                    # A take that dropped or mumbled words is voiced again;
                    # a cached take is checked too, since a bad one would
                    # otherwise come back on every render.
                    redone = await _keep_the_clearest_take(
                        clip, beat["text"], check,
                        lambda path, _t=beat["text"], _p=persona, _x=extra: synthesize(_t, _p, path, **_x),
                        work=work, label=f"line {beat_idx} ({speaker})",
                    )
                    fresh = fresh or redone
                if fresh and cached:
                    try:
                        shutil.copyfile(clip, cached)
                    except OSError as exc:
                        logger.warning("[dialogue] line cache write failed (%s): %s", cached, exc)
                seconds = await probe_seconds(clip)
                parts.append(clip)
                timeline.append({
                    "beat_idx": beat_idx, "scene_id": scene.get("scene_id"),
                    "kind": "line", "speaker": speaker, "text": beat["text"],
                    "intensity": intensity, "exaggeration": emotion,
                    "characters": list(scene.get("characters") or []),
                    "start_s": round(t, 3), "end_s": round(t + seconds, 3),
                    **({"offscreen": True} if beat.get("offscreen") else {}),
                })
                t += seconds
                spoken.append(beat["text"])
                previous_was_line = True
            elif kind == "action":
                start = t
                # The writer's own length for this action (a held moment runs
                # long), else story_action_beat_seconds.
                await _pad(float(beat.get("seconds") or action_s))
                timeline.append({
                    "beat_idx": beat_idx, "scene_id": scene.get("scene_id"),
                    "kind": "action", "speaker": None, "text": beat.get("text", ""),
                    "characters": list(beat.get("characters") or []),
                    "start_s": round(start, 3), "end_s": round(t, 3),
                    **{k: list(beat[k]) for k in ("enters", "exits") if beat.get(k)},
                })
                previous_was_line = False
            beat_idx += 1
    if not spoken:
        raise DialogueAudioError("the screenplay has no line beats to voice")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    await _concat(parts, output_path, work)
    duration = await probe_seconds(output_path)
    logger.info(
        "[dialogue] %d lines (%d from the line cache), %d beats, %.1fs track -> %s",
        len(spoken), reused, len(timeline), duration, output_path,
    )
    return {
        "audio_path": output_path,
        "timeline": timeline,
        "spoken_script": "\n".join(spoken),
        "duration_s": round(duration, 3),
        "lines_reused": reused,
    }


__all__ = [
    "DialogueAudioError",
    "line_check_settings",
    "line_cache_dir",
    "line_cache_key",
    "prune_line_cache",
    "probe_seconds",
    "render_dialogue",
    "resolve_voice",
    "word_recall",
]
