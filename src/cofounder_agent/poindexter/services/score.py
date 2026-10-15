"""Score a cartoon: music per scene and sound effects on its actions.

Chat drafting, phase 6 (Matt chose "music and sound effects", 2026-10-14).
Cartoons were voices only. The renderer already lays one soundtrack under
the dialogue (``CompositionRequest.soundtrack_path``, at ``soundtrack_dbfs``,
looped), so this builds that soundtrack to the dialogue's own timeline:

1. ``plan_score``: one writer call (``story.score``) reads the scenes, their
   turns and the art style and returns a music brief per scene (instruments,
   tempo, mood) plus the action beats that should make a sound and what it is.
   Code checks scene ids and beat numbers against the timeline.
2. ``build_score``: Stable Audio (``audio_gen_service``) makes each scene's
   music and each sound effect; ffmpeg places every piece at its moment on the
   timeline (music across its scene with fades, a sound at its action's start)
   and mixes them into one track the length of the dialogue. Sound effects sit
   ``story_score_sfx_gain_db`` above the music so they read through the
   soundtrack's attenuation.

Everything is best-effort: a piece that fails to generate is left out, and no
score at all just renders voices-only, as before.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ScoreError(RuntimeError):
    """A score could not be planned. The message says why."""


def _setting(site_config: Any, key: str) -> str:
    from poindexter.services.settings_defaults import DEFAULTS

    value = site_config.get(key, None) if site_config is not None else None
    return str(DEFAULTS[key] if value is None or str(value).strip() == "" else value)


def scene_spans(timeline: list[dict[str, Any]], duration_s: float) -> list[tuple[str, float, float]]:
    """``(scene_id, start, end)`` covering the whole track, gaps given to the scene before."""
    order: list[str] = []
    starts: dict[str, float] = {}
    for item in timeline:
        sid = str(item.get("scene_id") or "")
        if not sid:
            continue
        if sid not in starts:
            order.append(sid)
            starts[sid] = float(item.get("start_s") or 0.0)
    spans = []
    for i, sid in enumerate(order):
        start = 0.0 if i == 0 else starts[sid]
        end = starts[order[i + 1]] if i + 1 < len(order) else duration_s
        if end > start:
            spans.append((sid, start, end))
    return spans


def action_beats(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in timeline if t.get("kind") == "action"]


def check_plan(reply: dict[str, Any], *, scene_ids: set[str], beat_ids: set[int]) -> list[str]:
    errors: list[str] = []
    music = reply.get("music")
    if not isinstance(music, list) or not music:
        return ['FIX: add "music": one entry per scene, each {"scene_id", "prompt"}']
    seen = set()
    for i, item in enumerate(music, start=1):
        if not isinstance(item, dict):
            errors.append(f"FIX music {i}: give an object")
            continue
        sid = str(item.get("scene_id") or "")
        if sid not in scene_ids:
            errors.append(f"FIX music {i}: scene_id {sid!r} is not one of {sorted(scene_ids)}")
        if not str(item.get("prompt") or "").strip():
            errors.append(f'FIX music {i}: write a "prompt" for the music')
        seen.add(sid)
    missing = scene_ids - seen
    if missing:
        errors.append(f"FIX: give music for every scene; missing {sorted(missing)}")
    for i, item in enumerate(reply.get("sounds") or [], start=1):
        if not isinstance(item, dict):
            errors.append(f"FIX sound {i}: give an object")
            continue
        try:
            beat = int(str(item.get("beat")))
        except (TypeError, ValueError):
            errors.append(f'FIX sound {i}: "beat" must be an action number from the list')
            continue
        if beat not in beat_ids:
            errors.append(f"FIX sound {i}: there is no action {beat}; use one of {sorted(beat_ids)}")
        if not str(item.get("prompt") or "").strip():
            errors.append(f'FIX sound {i}: describe the "prompt" sound')
    return errors


async def plan_score(
    screenplay: dict[str, Any], timeline: list[dict[str, Any]], *, style: str, site_config: Any,
    pool: Any = None, task_id: str | None = None,
) -> dict[str, Any]:
    """Music per scene and sounds per action, as ``{"music": [...], "sounds": [...]}``."""
    from poindexter.services.screenplay import _ask_validated, _resolve_model

    scenes = screenplay.get("scenes") or []
    total = max((float(t.get("end_s") or 0.0) for t in timeline), default=0.0)
    lengths = {sid: end - start for sid, start, end in scene_spans(timeline, total)}
    scene_rows = "\n".join(
        f"{s.get('scene_id')} | {round(lengths.get(str(s.get('scene_id')), 0.0))}s | "
        f"{s.get('setting', '')} | {s.get('summary', '')} | turn: {s.get('turn', '')}"
        for s in scenes
    )
    actions = action_beats(timeline)
    action_rows = "\n".join(f"{a['beat_idx']} | {a.get('scene_id')} | {a.get('text', '')}" for a in actions) or "(none)"

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return check_plan(
            reply, scene_ids={str(s.get("scene_id")) for s in scenes},
            beat_ids={int(a["beat_idx"]) for a in actions},
        ), []

    reply, _ = await _ask_validated(
        "story.score",
        {
            "title": str(screenplay.get("title") or ""),
            "logline": str(screenplay.get("logline") or ""),
            "style": style or "(none)",
            "scene_rows": scene_rows,
            "action_rows": action_rows,
            "max_sounds": int(float(_setting(site_config, "story_score_max_sounds"))),
        },
        _check, model=_resolve_model(site_config), site_config=site_config, pool=pool,
        task_id=task_id, what="score", phase="story_score",
    )
    cap = int(float(_setting(site_config, "story_score_max_sounds")))
    # One sound per action beat: several on one moment pile into noise.
    sounds: list[dict[str, Any]] = []
    for item in reply.get("sounds") or []:
        if str(item.get("beat")) not in {str(x.get("beat")) for x in sounds}:
            sounds.append(item)
    return {"music": reply["music"], "sounds": sounds[:cap]}


async def _ffmpeg(*args: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-y", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("[score] ffmpeg failed: %s", (err or b"").decode()[:400])
    return proc.returncode == 0


async def _generate(prompt: str, kind: str, seconds: float, stem: str, out_dir: str, site_config: Any) -> str | None:
    from poindexter.services.audio_gen_service import generate_audio

    result = await generate_audio(
        prompt, kind,  # type: ignore[arg-type]  # kind is one of AudioKind's literals by construction
        site_config=site_config, output_dir=out_dir, output_stem=stem, duration_s=seconds,
    )
    path = getattr(result, "file_path", None) if result is not None else None
    return path if path and os.path.exists(path) else None


def _music_chain(
    inputs: list[int], length: float, clip_s: float, out: str, *, fade_s: float, keep: float, lufs: float,
) -> tuple[list[str], int]:
    """One music piece filling ``length`` seconds from the steady part of its clip.

    Generated music fades out over its last stretch (the Midnight Patch score
    fell from -23 to -56 dB RMS in its last 15 s, 2026-10-14), so only the
    first ``keep`` of the clip is used, crossfaded into itself to fill a long
    scene. Each repeat is its own input (``inputs``, the input index for every
    repeat the caller provides): ``asplit`` cannot feed both sides of an
    ``acrossfade``, whose second side then came out empty and left the music
    silent from the first seam. ``loudnorm`` holds the piece at ``lufs``.
    Returns ``(chains, repeats needed)``; with fewer ``inputs`` than repeats
    the caller asks again.
    """
    clip = max(0.1, clip_s or length)
    steady = min(clip, max(6.0, clip * keep)) if clip > 6.0 else clip
    xf = min(3.0, steady / 3)
    reps = 1 if steady >= length else min(30, math.ceil((length - xf) / max(0.1, steady - xf)))
    if len(inputs) < reps:
        return [], reps
    fade = min(fade_s, length / 3)
    tail = (
        f"atrim=0:{length:.3f},asetpts=PTS-STARTPTS,loudnorm=I={lufs:.1f}:TP=-3:LRA=7,aresample=44100,"
        f"afade=t=in:d={fade:.2f},afade=t=out:st={max(0.0, length - fade):.3f}:d={fade:.2f}"
    )
    chains = [f"[{n}:a]atrim=0:{steady:.3f},asetpts=PTS-STARTPTS[{out}_r{i}]" for i, n in enumerate(inputs[:reps])]
    current = f"{out}_r0"
    for i in range(1, reps):
        chains.append(f"[{current}][{out}_r{i}]acrossfade=d={xf:.2f}:c1=tri:c2=tri[{out}_x{i}]")
        current = f"{out}_x{i}"
    chains.append(f"[{current}]{tail}[{out}]")
    return chains, reps


def mix_graph(
    pieces: list[dict[str, Any]], *, duration_s: float, fade_s: float, sfx_gain_db: float,
    music_lufs: float = -24.0, keep_fraction: float = 0.6, limit: float = 0.7,
) -> tuple[str, list[str]]:
    """``(filter graph, input paths)`` placing each piece at its moment and mixing them.

    Every piece is loudness-normalized first: music to ``music_lufs``, a sound
    effect ``sfx_gain_db`` above it. A raw Stable Audio effect could be far
    louder than the music around it (the Midnight Patch score hit -1.4 dBFS on
    one action beat, "a weird cacophony"). A limiter holds the mix's peaks at
    ``limit``. A looped music piece is listed once per repeat in the inputs.
    """
    chains: list[str] = []
    labels: list[str] = []
    inputs: list[str] = []
    for k, p in enumerate(pieces):
        length = max(0.1, float(p["end"]) - float(p["start"]))
        delay = int(round(float(p["start"]) * 1000))
        if p["kind"] == "music":
            clip_s = float(p.get("clip_s") or 0.0)
            _, reps = _music_chain([], length, clip_s, f"q{k}", fade_s=fade_s, keep=keep_fraction, lufs=music_lufs)
            first = len(inputs)
            inputs += [p["path"]] * reps
            music, _ = _music_chain(
                list(range(first, first + reps)), length, clip_s, f"q{k}",
                fade_s=fade_s, keep=keep_fraction, lufs=music_lufs,
            )
            chains += music
            chains.append(f"[q{k}]adelay={delay}|{delay}[p{k}]")
        else:
            n = len(inputs)
            inputs.append(p["path"])
            # The effect's own lead-in silence is trimmed, so the sound lands
            # on its action's cut: generated effects often open quiet, and the
            # Battery Drop's sounds came in after the picture they belonged to.
            chains.append(
                f"[{n}:a]silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02,"
                f"asetpts=PTS-STARTPTS,atrim=0:{length:.3f},asetpts=PTS-STARTPTS,"
                f"loudnorm=I={music_lufs + sfx_gain_db:.1f}:TP=-3:LRA=7,aresample=44100,"
                f"afade=t=out:st={max(0.0, length - 0.3):.3f}:d=0.3,"
                f"adelay={delay}|{delay}[p{k}]"
            )
        labels.append(f"[p{k}]")
    mixed = (
        f"{''.join(labels)}amix=inputs={len(pieces)}:normalize=0:duration=longest,"
        f"alimiter=limit={limit:.3f}:level=disabled,atrim=0:{duration_s:.3f}[out]"
    )
    return ";".join([*chains, mixed]), inputs


async def build_score(
    plan: dict[str, Any], timeline: list[dict[str, Any]], *, duration_s: float, site_config: Any,
    output_path: str,
) -> dict[str, Any]:
    """Generate and lay out every piece; returns ``{path, music, sounds}`` (path ``None`` if nothing made)."""
    spans = {sid: (a, b) for sid, a, b in scene_spans(timeline, duration_s)}
    beats = {int(a["beat_idx"]): a for a in action_beats(timeline)}
    max_clip = float(_setting(site_config, "story_score_max_clip_seconds"))
    out_dir = str(Path(output_path).parent)
    stem = Path(output_path).stem
    pieces: list[dict[str, Any]] = []
    made_music = made_sounds = 0
    for item in plan.get("music") or []:
        sid = str(item.get("scene_id"))
        if sid not in spans:
            continue
        start, end = spans[sid]
        # Only the steady first part of a clip is used (see _music_chain), so
        # a clip as long as the engine makes leaves the fewest loop seams.
        path = await _generate(
            _setting(site_config, "story_score_music_template").replace("{prompt}", str(item["prompt"])),
            "ambient", max_clip, f"{stem}_music_{sid}", out_dir, site_config,
        )
        if path:
            from poindexter.services.dialogue_audio import probe_seconds

            clip_s = await probe_seconds(path)
            pieces.append({"path": path, "kind": "music", "start": start, "end": end, "clip_s": clip_s})
            made_music += 1
    for item in plan.get("sounds") or []:
        beat = beats.get(int(str(item.get("beat"))))
        if beat is None:
            continue
        start = float(beat.get("start_s") or 0.0)
        length = min(float(_setting(site_config, "story_score_max_sound_seconds")),
                     max(1.0, float(beat.get("end_s") or start) - start + 1.0))
        path = await _generate(
            _setting(site_config, "story_score_sound_template").replace("{prompt}", str(item["prompt"])),
            "sfx", length, f"{stem}_sfx_{beat['beat_idx']}", out_dir, site_config,
        )
        if path:
            pieces.append({"path": path, "kind": "sfx", "start": start, "end": min(duration_s, start + length)})
            made_sounds += 1
    if not pieces:
        return {"path": None, "music": 0, "sounds": 0}
    graph, sources = mix_graph(
        pieces, duration_s=duration_s, fade_s=float(_setting(site_config, "story_score_fade_seconds")),
        sfx_gain_db=float(_setting(site_config, "story_score_sfx_gain_db")),
        music_lufs=float(_setting(site_config, "story_score_music_lufs")),
        keep_fraction=float(_setting(site_config, "story_score_music_keep_fraction")),
        limit=float(_setting(site_config, "story_score_limit")),
    )
    inputs: list[str] = []
    for source in sources:
        inputs += ["-i", source]
    ok = await _ffmpeg(*inputs, "-filter_complex", graph, "-map", "[out]", "-ar", "44100", "-ac", "2", output_path)
    return {"path": output_path if ok and os.path.exists(output_path) else None,
            "music": made_music, "sounds": made_sounds}


async def score_screenplay(
    screenplay: dict[str, Any], timeline: list[dict[str, Any]], *, duration_s: float, style: str,
    site_config: Any, pool: Any = None, task_id: str | None = None, output_dir: str | None = None,
) -> dict[str, Any]:
    """Plan and build a cartoon's soundtrack. Returns ``{path, plan, music, sounds}``."""
    from poindexter.services.audio_gen_service import is_audio_gen_enabled

    if _setting(site_config, "story_score_enabled").strip().lower() not in ("true", "1", "yes", "on"):
        return {"path": None, "plan": None, "music": 0, "sounds": 0, "skipped": "story_score_enabled is off"}
    if not is_audio_gen_enabled(site_config):
        return {"path": None, "plan": None, "music": 0, "sounds": 0, "skipped": "no audio_gen_engine"}
    if not timeline:
        return {"path": None, "plan": None, "music": 0, "sounds": 0, "skipped": "no dialogue timeline"}
    plan = await plan_score(screenplay, timeline, style=style, site_config=site_config, pool=pool, task_id=task_id)
    out_dir = output_dir or os.path.expanduser(_setting(site_config, "story_score_dir"))
    os.makedirs(out_dir, exist_ok=True)
    name = f"{task_id or 'score'}_{uuid.uuid4().hex[:8]}_score.wav"
    from poindexter.services.gpu_scheduler import gpu

    # Stable Audio shares the render card: the same lock the blog's ambient
    # bed takes, so the score never lands mid-render.
    async with gpu.lock("video", model="stable_audio", task_id=task_id, phase="video_score"):
        built = await build_score(
            plan, timeline, duration_s=duration_s, site_config=site_config,
            output_path=os.path.join(out_dir, name),
        )
    logger.info("[score] %s: %d music, %d sounds -> %s", task_id or "-", built["music"], built["sounds"], built["path"])
    return {**built, "plan": plan}


__all__ = [
    "ScoreError", "action_beats", "build_score", "check_plan", "mix_graph",
    "plan_score", "scene_spans", "score_screenplay",
]
