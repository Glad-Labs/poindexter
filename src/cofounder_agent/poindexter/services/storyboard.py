"""Storyboards: a scene's voiced lines and a still per shot, before any animation.

Chat drafting, phase 2. A script reads differently once it is voiced and
pictured, and a talking shot costs about six minutes of GPU to animate, so
the place to fix a shot is a still. A storyboard runs the same steps the
render runs, stopping short of animation:

1. ``dialogue_audio.render_dialogue`` voices the chosen scenes (one track).
2. ``screenplay_director.direct_screenplay`` plans the shots against it.
3. Each shot is drawn with the renderer's own functions: a talking shot's
   per-scene talking frame (``_draw_talking_frame``, checked for exactly the
   speaker), and each still with ``_render_reference_still`` from the cast's
   portraits. Stock footage and screen captures are listed, not drawn.

So the preview is the picture the video will start from, not a look-alike.
The payload keeps the shot list, so a revision redraws only the shots a note
changes (``revise_storyboard``) and phase 3 can render from it.
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

TALKING = "talking"
STILL = "still"
FOOTAGE = "footage"
_STILL_SOURCES = ("image_gen", "image_kenburns", "generative", "wan21")


class StoryboardError(RuntimeError):
    """A storyboard could not be made. The message says why."""


def select_scenes(screenplay: dict[str, Any], scenes: list[str] | None) -> dict[str, Any]:
    """The screenplay cut down to ``scenes`` (ids or 1-based numbers); all when empty."""
    all_scenes = list(screenplay.get("scenes") or [])
    if not scenes:
        return dict(screenplay)
    wanted: list[dict[str, Any]] = []
    for ref in scenes:
        ref = str(ref).strip().lower()
        hit = next((s for s in all_scenes if str(s.get("scene_id", "")).lower() == ref), None)
        if hit is None and ref.isdigit() and 1 <= int(ref) <= len(all_scenes):
            hit = all_scenes[int(ref) - 1]
        if hit is None:
            ids = [s.get("scene_id") for s in all_scenes]
            raise StoryboardError(f"no scene {ref!r}; the script has {ids} (or numbers 1-{len(all_scenes)})")
        if hit not in wanted:
            wanted.append(hit)
    return {**screenplay, "scenes": wanted}


def _kind(shot: Any) -> str:
    if shot.source == "presenter" and getattr(shot, "speaker", None):
        return TALKING
    if shot.source in _STILL_SOURCES:
        return STILL
    return FOOTAGE


def _words(timeline: list[dict[str, Any]], start: float, end: float) -> str:
    """The lines spoken during ``[start, end)``, as ``speaker: text``."""
    out = []
    for item in timeline:
        if item.get("kind") != "line":
            continue
        if float(item["end_s"]) > start + 0.05 and float(item["start_s"]) < end - 0.05:
            out.append(f"{item.get('speaker')}: {item.get('text')}")
    return " / ".join(out)


async def _upload(local: str, key: str, mime: str, site_config: Any) -> str:
    from poindexter.services.r2_upload_service import R2UploadService

    url = await R2UploadService(site_config=site_config).upload_to_r2(local, key, mime)
    if not url:
        raise StoryboardError(f"upload failed for {key} (check the storage_* settings)")
    return url


async def _to_mp3(src: str, dst: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-y", "-i", src, "-ac", "1", "-b:a", "96k", dst,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0 and os.path.exists(dst)


async def _draw(
    shot: Any, *, work: Path, ctx: Any, site_config: Any, portraits: dict[str, str],
) -> str | None:
    """One shot's picture as the render will start it, or ``None``."""
    from poindexter.services.persona_service import get_persona
    from poindexter.services.settings_defaults import DEFAULTS
    from poindexter.services.video_renderers import shot_list_renderer as slr

    kind = _kind(shot)
    if kind == TALKING:
        persona = get_persona(site_config, shot.speaker or "")
        if persona is None:
            return None
        frame = work / f"talk_{shot.idx:02d}.png"
        if shot.frame_prompt:
            await slr._draw_talking_frame(
                shot, frame=frame, persona=persona, orientation="landscape",
                reference_ctx=ctx, work_dir=work, site_config=site_config, http_client_factory=None,
            )
        if frame.exists():
            return str(frame)
        # No frame passed its check: the render lip-syncs the portrait.
        url = portraits.get(persona.slug) or persona.portrait_url
        got = await slr._fetch_presenter_portrait(url, work / f"portrait_{shot.idx:02d}.png", None) if url else None
        return got
    if kind == STILL and shot.prompt:
        out = str(work / f"shot_{shot.idx:02d}.png")

        def _dim(key: str) -> int:
            raw = site_config.get(key, None) if site_config is not None else None
            return int(float(raw if raw not in (None, "") else DEFAULTS[key]))

        ok = await slr._render_reference_still(
            shot, prompt=shot.prompt, output_path=out,
            width=_dim("video_reference_width"), height=_dim("video_reference_height"),
            ctx=ctx, work_dir=work, site_config=site_config, http_client_factory=None,
        )
        return out if ok and os.path.exists(out) else None
    return None


async def _draw_shots(
    listing: dict[str, Any], indices: set[int] | None, *, work: Path, site_config: Any, pool: Any,
    upload_prefix: str, version_tag: str,
) -> dict[int, str | None]:
    """Draw ``indices`` (all when None) of a shot list; returns idx -> uploaded URL."""
    from poindexter.schemas.video_shot_list import VideoShotList
    from poindexter.services.video_renderers import shot_list_renderer as slr

    shot_list = VideoShotList.model_validate(listing)
    ctx = slr.reference_context_for(shot_list)
    if ctx is not None:
        ctx.pool = pool
    portraits = dict(listing.get("cast_portraits") or {})
    urls: dict[int, str | None] = {}
    for shot in shot_list.shots:
        if indices is not None and shot.idx not in indices:
            continue
        local = await _draw(shot, work=work, ctx=ctx, site_config=site_config, portraits=portraits)
        urls[shot.idx] = await _upload(
            local, f"{upload_prefix}/shot_{shot.idx:02d}{version_tag}.png", "image/png", site_config,
        ) if local else None
    try:
        from poindexter.services.gpu_scheduler import gpu

        await gpu._unload_comfyui(hard=False)
    except Exception as exc:  # noqa: BLE001  # silent-ok: best effort, logged
        logger.warning("[storyboard] could not free ComfyUI after drawing: %s", exc)
    return urls


def _rows(listing: dict[str, Any], timeline: list[dict[str, Any]], urls: dict[int, str | None]) -> list[dict[str, Any]]:
    from poindexter.schemas.video_shot_list import VideoShotList

    rows = []
    for shot in VideoShotList.model_validate(listing).shots:
        start = float(shot.narration_offset_s or 0.0)
        end = start + float(shot.duration_s)
        rows.append({
            "idx": shot.idx,
            "kind": _kind(shot),
            "source": shot.source,
            "speaker": shot.speaker,
            "characters": list(shot.characters or []),
            "start_s": round(start, 2),
            "duration_s": round(float(shot.duration_s), 2),
            "words": _words(timeline, start, end),
            "picture": (shot.frame_prompt if _kind(shot) == TALKING else shot.prompt) or shot.intent or "",
            "image_url": urls.get(shot.idx),
        })
    return rows


async def build_storyboard(
    screenplay: dict[str, Any],
    *,
    scenes: list[str] | None,
    site_config: Any,
    pool: Any,
    upload_prefix: str,
    style: str = "",
    niche_slug: str | None = None,
    cast_portraits: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Voice, direct and draw ``scenes`` of a screenplay. Returns the storyboard payload."""
    from poindexter.services.casting import _ensure_voice_headroom
    from poindexter.services.dialogue_audio import render_dialogue
    from poindexter.services.media_subject_policy import story_visual_style
    from poindexter.services.screenplay_director import direct_screenplay

    sub = select_scenes(screenplay, scenes)
    look, _asked = story_visual_style(site_config, niche_slug, style)
    work = Path(tempfile.mkdtemp(prefix="storyboard-"))
    # Voices before pictures, for the same reason as a cast draft: drawing
    # leaves the image-edit model resident and Chatterbox needs the room.
    await _ensure_voice_headroom(site_config)
    voiced = await render_dialogue(sub, site_config=site_config, output_path=str(work / "dialogue.wav"))
    listing = await direct_screenplay(
        sub, list(voiced["timeline"]), float(voiced["duration_s"]),
        site_config=site_config, style=look, pool=pool, cast_portraits=cast_portraits,
    )
    mp3 = str(work / "dialogue.mp3")
    audio_url = await _upload(mp3, f"{upload_prefix}/dialogue.mp3", "audio/mpeg", site_config) \
        if await _to_mp3(voiced["audio_path"], mp3) else None
    # The full-quality track too: rendering from this storyboard (phase 3)
    # uses it as the video's sound, so the shots stay timed to it.
    master_url = await _upload(voiced["audio_path"], f"{upload_prefix}/dialogue.wav", "audio/wav", site_config)
    urls = await _draw_shots(
        listing, None, work=work, site_config=site_config, pool=pool,
        upload_prefix=upload_prefix, version_tag="",
    )
    return {
        "title": screenplay.get("title", ""),
        "scenes": [s.get("scene_id") for s in sub.get("scenes") or []],
        "style": look,
        "audio_url": audio_url,
        "audio_master_url": master_url,
        "spoken_script": voiced.get("spoken_script", ""),
        "duration_s": float(voiced["duration_s"]),
        "shots": _rows(listing, list(voiced["timeline"]), urls),
        "shot_list": listing,
        "timeline": voiced["timeline"],
        "screenplay": sub,
    }


def check_revision(reply: dict[str, Any], known: set[int]) -> list[str]:
    """FIX lines for a storyboard revision reply."""
    shots = reply.get("shots")
    if not isinstance(shots, list) or not shots:
        return ['FIX: list the shots you change under "shots", at least one']
    errors = []
    for i, item in enumerate(shots, start=1):
        if not isinstance(item, dict):
            errors.append(f"FIX shot {i}: give an object with \"id\" and \"prompt\"")
            continue
        try:
            sid = int(str(item.get("id")))
        except (TypeError, ValueError):
            errors.append(f'FIX shot {i}: "id" must be a shot number from the list')
            continue
        if sid not in known:
            errors.append(f"FIX shot {i}: there is no shot {sid}; use one of {sorted(known)}")
        if not str(item.get("prompt") or "").strip():
            errors.append(f'FIX shot {sid}: write the new "prompt"')
    return errors


async def revise_storyboard(
    payload: dict[str, Any],
    instruction: str,
    *,
    site_config: Any,
    pool: Any,
    upload_prefix: str,
) -> dict[str, Any]:
    """Redraw the shots a note changes; every other shot keeps its picture."""
    from poindexter.services.screenplay import _ask_validated, _resolve_model

    note = (instruction or "").strip()
    if not note:
        raise StoryboardError("a revision needs a note saying what to change")
    listing = json.loads(json.dumps(payload["shot_list"]))
    rows = payload.get("shots") or []
    drawable = {r["idx"] for r in rows if r.get("kind") in (TALKING, STILL)}
    table = "\n".join(
        f"{r['idx']} | {r['kind']} | {r.get('speaker') or ', '.join(r.get('characters') or []) or '-'} | "
        f"{r.get('words') or '-'} | {r.get('picture') or ''}"
        for r in rows if r["idx"] in drawable
    )

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return check_revision(reply, drawable), []

    reply, _ = await _ask_validated(
        "story.storyboard_revise",
        {"shot_table": table, "instruction": note[:2000], "style": payload.get("style", "")},
        _check, model=_resolve_model(site_config), site_config=site_config, pool=pool,
        task_id=None, what="storyboard revision", phase="story_revise_storyboard",
    )
    changed: dict[int, str] = {int(s["id"]): str(s["prompt"]).strip() for s in reply["shots"]}
    for shot in listing.get("shots") or []:
        if shot.get("idx") in changed:
            key = "frame_prompt" if shot.get("source") == "presenter" and shot.get("speaker") else "prompt"
            shot[key] = changed[shot["idx"]][:2000]
    version = len(payload.get("revisions") or []) + 2
    urls = await _draw_shots(
        listing, set(changed), work=Path(tempfile.mkdtemp(prefix="storyboard-")),
        site_config=site_config, pool=pool, upload_prefix=upload_prefix, version_tag=f"-v{version}",
    )
    before = {r["idx"]: r.get("image_url") for r in rows}
    merged = {idx: (urls[idx] if idx in urls else before.get(idx)) for idx in before}
    return {
        **payload,
        "shot_list": listing,
        "shots": _rows(listing, list(payload.get("timeline") or []), merged),
        "revisions": [*(payload.get("revisions") or []), {"note": note[:500], "shots": sorted(changed)}],
    }


__all__ = [
    "StoryboardError",
    "build_storyboard",
    "check_revision",
    "revise_storyboard",
    "select_scenes",
]
