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
changes (``revise_storyboard``) and phase 3 can render from it. A story
revision (phase 10, ``restory_storyboard``) rewrites the script from a note,
re-voices only the changed lines (the line cache keeps the rest) and redraws
only the scenes the rewrite touched (``carry_over``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TALKING = "talking"
STILL = "still"
FOOTAGE = "footage"
_STILL_SOURCES = ("image_gen", "image_kenburns", "generative", "wan21", "keyframes")


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
        if slr._no_fallbacks(ctx, site_config):
            # No frame could be drawn at all: the shot stays undrawn for the
            # render to draw, rather than a bare portrait standing in.
            return None
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


async def _seed_places(ctx: Any, shot_list: Any, indices: set[int], existing: dict[int, str | None], work: Path) -> None:
    """A partial redraw keeps each scene's existing establishing picture as its place."""
    from PIL import Image

    from poindexter.services.creative_drafts import _download

    for shot in shot_list.shots:
        url = existing.get(shot.idx)
        # A scene with an empty plate draws it afresh: seeding the establishing
        # picture would put its characters back into the place reference.
        if not (shot.establishing and shot.scene_id and url) or shot.idx in indices or shot.place_prompt:
            continue
        from poindexter.services.video_renderers.shot_list_renderer import _plate_key

        key = _plate_key(shot)
        try:
            raw = await _download(url, str(work / f"place_src_{shot.idx:02d}"))
            local = str(work / f"place_{shot.idx:02d}.png")
            with Image.open(raw) as img:
                img.convert("RGB").save(local)
            ctx.places[key] = local
        except Exception as exc:  # noqa: BLE001 — without it the scene's place is drawn afresh
            logger.warning("[storyboard] could not fetch the place picture of shot %d: %s", shot.idx, exc)


async def _seed_plates(ctx: Any, plates: dict[str, str], work: Path) -> None:
    """The background plates the storyboard already drew, as the places to draw in."""
    from PIL import Image

    from poindexter.services.creative_drafts import _download

    for key, url in plates.items():
        if not url or key in ctx.places:
            continue
        try:
            raw = await _download(url, str(work / f"plate_src_{len(ctx.places):02d}"))
            local = str(work / f"plate_{len(ctx.places):02d}.png")
            with Image.open(raw) as img:
                img.convert("RGB").save(local)
            ctx.places[key] = local
        except Exception as exc:  # noqa: BLE001 — without it the plate is drawn afresh
            logger.warning("[storyboard] could not fetch the plate for %s: %s", key, exc)


def colour_script(listing: dict[str, Any], plates: dict[str, str]) -> list[dict[str, Any]]:
    """The colour script: each scene's palette and its background plate, in story order.

    A studio paints a strip of small keys, one per scene, to see the colour
    move with the story before anything is animated. Ours is the plates the
    storyboard drew: each scene's location in its palette. A scene with no
    palette (an older script) is left out; an empty list means none.
    """
    from poindexter.schemas.video_shot_list import VideoShotList
    from poindexter.services.video_renderers.shot_list_renderer import _place_key, _plate_key

    strip: list[dict[str, Any]] = []
    seen: set[str] = set()
    for shot in VideoShotList.model_validate(listing).shots:
        scene = str(shot.scene_id or "")
        # A scene's opening landscape is drawn on its own, outside the
        # location's plate (no location_id), so its key is the next shot.
        if not scene or scene in seen or not shot.palette or not shot.location_id:
            continue
        seen.add(scene)
        strip.append({
            "scene_id": scene, "location": shot.location_id or "", "palette": shot.palette,
            "plate_url": plates.get(_plate_key(shot)) or plates.get(_place_key(shot)) or None,
        })
    return strip


async def _draw_shots(
    listing: dict[str, Any], indices: set[int] | None, *, work: Path, site_config: Any, pool: Any,
    upload_prefix: str, version_tag: str, existing: dict[int, str | None] | None = None,
    plates: dict[str, str] | None = None,
) -> dict[int, str | None]:
    """Draw ``indices`` (all when None) of a shot list; returns idx -> uploaded URL.

    ``existing`` is the picture URL of each shot not being redrawn: a scene's
    establishing picture among them stays the scene's place reference.
    ``plates`` maps a location to its background plate's URL: plates drawn
    before are reused, and every plate this pass draws is uploaded and added
    to it, so a revision or a re-storyboard keeps each location's background.
    """
    from poindexter.schemas.video_shot_list import VideoShotList
    from poindexter.services.video_renderers import shot_list_renderer as slr

    shot_list = VideoShotList.model_validate(listing)
    ctx = slr.reference_context_for(shot_list)
    if ctx is not None:
        ctx.pool = pool
        from poindexter.services.model_sheets import ensure_turnarounds

        await ensure_turnarounds(list(ctx.cast), site_config=site_config, pool=pool)
        if plates:
            await _seed_plates(ctx, plates, work)
        if indices is not None and existing:
            await _seed_places(ctx, shot_list, indices, existing, work)
    seeded = set(ctx.places) if ctx is not None else set()
    portraits = dict(listing.get("cast_portraits") or {})
    urls: dict[int, str | None] = {}
    for shot in shot_list.shots:
        if indices is not None and shot.idx not in indices:
            continue
        local = await _draw(shot, work=work, ctx=ctx, site_config=site_config, portraits=portraits)
        urls[shot.idx] = await _upload(
            local, f"{upload_prefix}/shot_{shot.idx:02d}{version_tag}.png", "image/png", site_config,
        ) if local else None
        if shot.source == "keyframes":
            # Its second drawing, edited from the first: approved with the
            # storyboard, so the render joins the two pictures the operator saw.
            entry = next(x for x in listing["shots"] if x.get("idx") == shot.idx)
            entry.pop("after_url", None)
            after = str(work / f"shot_{shot.idx:02d}_after.png")
            if local and await slr.draw_keyframe_after(
                shot.model_copy(update={"after_url": None}), before=local, output_path=after, site_config=site_config,
            ):
                entry["after_url"] = await _upload(
                    after, f"{upload_prefix}/shot_{shot.idx:02d}_after{version_tag}.png", "image/png", site_config,
                )
    if ctx is not None and plates is not None:
        for key, local in ctx.places.items():
            if key in seeded or not os.path.exists(local):
                continue
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", key)
            plates[key] = await _upload(local, f"{upload_prefix}/plate_{safe}{version_tag}.png", "image/png", site_config)
    try:
        from poindexter.services.gpu_scheduler import gpu

        await gpu._unload_comfyui(hard=False)
    except Exception as exc:  # noqa: BLE001  # silent-ok: best effort, logged
        logger.warning("[storyboard] could not free ComfyUI after drawing: %s", exc)
    _require_pictures(shot_list, urls, site_config)
    return urls


def _require_pictures(shot_list: Any, urls: dict[int, str | None], site_config: Any) -> None:
    """Fail when too many of the pictures this pass drew came back empty.

    The Circuit and the Song (2026-10-15): the image server restarted after
    shot 2, every later draw was refused, and the storyboard reported ready
    with 42 of 45 pictures missing. More than story_storyboard_max_missing
    (a share of the shots drawn) empty is a failure, with the count.
    """
    from poindexter.services.settings_defaults import DEFAULTS

    drawable = [shot.idx for shot in shot_list.shots if shot.idx in urls and _kind(shot) in (TALKING, STILL)]
    missing = [idx for idx in drawable if not urls.get(idx)]
    raw = site_config.get("story_storyboard_max_missing", None) if site_config is not None else None
    limit = float(raw if raw not in (None, "") else DEFAULTS["story_storyboard_max_missing"])
    if drawable and len(missing) / len(drawable) > limit:
        raise StoryboardError(
            f"{len(missing)} of {len(drawable)} pictures could not be drawn (shots {missing[:12]}"
            f"{'...' if len(missing) > 12 else ''}); check the image server and storyboard again",
        )


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
            **({"after": shot.after_prompt, "after_url": shot.after_url, "transition": shot.transition}
               if shot.source == "keyframes" else {}),
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
    plates: dict[str, str] = {}
    urls = await _draw_shots(
        listing, None, work=work, site_config=site_config, pool=pool,
        upload_prefix=upload_prefix, version_tag="", plates=plates,
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
        "plates": plates,
        "colour_script": colour_script(listing, plates),
    }


def check_revision(reply: dict[str, Any], known: set[int], cast: set[str] | None = None) -> list[str]:
    """FIX lines for a storyboard revision reply. ``cast``: who a still's ``visible`` may name."""
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
        motion = item.get("motion")
        if motion is not None and not isinstance(motion, str):
            errors.append(f'FIX shot {sid}: "motion" is one sentence of text, or is left out')
        if not str(item.get("prompt") or "").strip() and not str(motion or "").strip():
            errors.append(f'FIX shot {sid}: write the new "prompt", or a new "motion" for an animated shot')
        seen = item.get("visible")
        if seen is not None and (
            not isinstance(seen, list) or any(str(c).strip().lower() not in (cast or set()) for c in seen)
        ):
            errors.append(f'FIX shot {sid}: "visible" lists character ids from {sorted(cast or set())}, or is left out')
    return errors


def revision_table(rows: list[dict[str, Any]], drawable: set[int], listing: dict[str, Any]) -> str:
    """The shot table a revision note is read against; an animated shot shows how it moves."""
    motions = {
        s.get("idx"): str(s.get("motion") or "").strip()
        for s in listing.get("shots") or [] if s.get("source") == "generative"
    }
    return "\n".join(
        f"{r['idx']} | {r['kind']} | {r.get('speaker') or ', '.join(r.get('characters') or []) or '-'} | "
        f"{r.get('words') or '-'} | {r.get('picture') or ''}"
        + (f" | moves: {motions[r['idx']]}" if motions.get(r["idx"]) else "")
        for r in rows if r["idx"] in drawable
    )


def listing_cast(listing: dict[str, Any]) -> set[str]:
    """Everyone a listing draws, by id."""
    return {
        str(c).lower() for shot in listing.get("shots") or []
        for c in [*(shot.get("characters") or []), shot.get("speaker") or ""] if c
    }


def apply_revision(listing: dict[str, Any], reply: dict[str, Any]) -> tuple[set[int], set[int]]:
    """Apply a ``story.storyboard_revise`` reply to ``listing`` in place.

    Returns ``(redrawn, changed)``: the shots whose picture changes, and every
    shot the reply touches. A reply can change only an animated shot's
    ``motion`` and keep its picture: The Battery Drop's render (2026-10-15)
    had right pictures whose movement went wrong ("the camera follows the
    bolt as it falls" ended in two seconds of blur; "lifted quickly" gave
    Cinder another face by the end), and a revision could only redraw them.
    """
    from poindexter.services.screenplay_director import with_props

    props = list(listing.get("props") or [])
    cast = listing_cast(listing)
    by_id = {int(str(item["id"])): item for item in reply.get("shots") or []}
    redrawn: set[int] = set()
    for shot in listing.get("shots") or []:
        item = by_id.get(shot.get("idx"))
        if item is None:
            continue
        talking = shot.get("source") == "presenter" and shot.get("speaker")
        prompt = str(item.get("prompt") or "").strip()
        if prompt:
            # A rewritten picture keeps the props' one design.
            text = prompt if talking else with_props(prompt, props, cast)
            shot["frame_prompt" if talking else "prompt"] = text[:2000]
            redrawn.add(shot["idx"])
        if not talking and isinstance(item.get("visible"), list):
            # Who the new picture shows: their references are drawn in.
            shot["characters"] = [str(c).strip().lower() for c in item["visible"]]
            redrawn.add(shot["idx"])
        motion = str(item.get("motion") or "").strip()
        if motion and shot.get("source") == "generative":
            shot["motion"] = motion[:500]
    return redrawn, set(by_id) & {s.get("idx") for s in listing.get("shots") or []}


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
    table = revision_table(rows, drawable, listing)
    cast = listing_cast(listing)

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return check_revision(reply, drawable, cast), []

    reply, _ = await _ask_validated(
        "story.storyboard_revise",
        {"shot_table": table, "instruction": note[:2000], "style": payload.get("style", "")},
        _check, model=_resolve_model(site_config), site_config=site_config, pool=pool,
        task_id=None, what="storyboard revision", phase="story_revise_storyboard",
    )
    # A storyboard is pictures: a motion-only change keeps the picture and
    # rides on the shot list to the render.
    redrawn, changed = apply_revision(listing, reply)
    version = len(payload.get("revisions") or []) + 2
    before = {r["idx"]: r.get("image_url") for r in rows}
    plates = dict(payload.get("plates") or {})
    urls = await _draw_shots(
        listing, redrawn, work=Path(tempfile.mkdtemp(prefix="storyboard-")),
        site_config=site_config, pool=pool, upload_prefix=upload_prefix, version_tag=f"-v{version}",
        existing=before, plates=plates,
    )
    merged = {idx: (urls[idx] if idx in urls else before.get(idx)) for idx in before}
    return {
        **payload,
        "shot_list": listing,
        "shots": _rows(listing, list(payload.get("timeline") or []), merged),
        "plates": plates,
        "colour_script": colour_script(listing, plates),
        "revisions": [*(payload.get("revisions") or []), {"note": note[:500], "shots": sorted(changed)}],
    }


# --- A story revision (chat drafting, phase 10) --------------------------------


def _scene_sig(scene: dict[str, Any]) -> str:
    """What decides a scene's pictures: its place, who is in it, and its beats."""
    beats = [
        [b.get("kind"), b.get("speaker"), " ".join(str(b.get("text") or "").split()),
         b.get("intensity"), sorted(b.get("characters") or [])]
        for b in scene.get("beats") or []
    ]
    return json.dumps(
        [" ".join(str(scene.get("setting") or "").split()), sorted(scene.get("characters") or []), beats],
        sort_keys=True,
    )


def shot_scenes(listing: dict[str, Any], timeline: list[dict[str, Any]]) -> dict[int, str]:
    """Each shot's scene id: the scene of the beat its first moment falls in."""
    beats = sorted(timeline, key=lambda b: float(b.get("start_s") or 0.0))
    out: dict[int, str] = {}
    for shot in listing.get("shots") or []:
        at = float(shot.get("narration_offset_s") or 0.0) + 0.01
        scene = None
        for beat in beats:
            if float(beat.get("start_s") or 0.0) <= at:
                scene = beat.get("scene_id")
            else:
                break
        if scene is None and beats:
            scene = beats[0].get("scene_id")
        out[int(shot["idx"])] = str(scene)
    return out


_CARRIED = ("prompt", "frame_prompt", "motion", "intent")


def carry_over(
    old: dict[str, Any], new_listing: dict[str, Any], new_timeline: list[dict[str, Any]],
    new_screenplay: dict[str, Any],
) -> tuple[dict[int, str | None], set[int], list[str]]:
    """Keep the drawn pictures of every scene a story revision left alone.

    ``old`` is the previous storyboard payload. A scene is unchanged when its
    place, cast and beats are the same; its new shots then line up one for
    one with its old shots (the director plans a scene from its beats and
    their measured lengths, and unchanged lines keep their takes). Each such
    shot takes its old picture prompt and image. Changes ``new_listing`` in
    place; returns ``(new idx -> image url, shots to draw, changed scene ids)``.
    """
    old_scenes = {str(s.get("scene_id")): _scene_sig(s) for s in (old.get("screenplay") or {}).get("scenes") or []}
    new_scenes = {str(s.get("scene_id")): _scene_sig(s) for s in new_screenplay.get("scenes") or []}
    same = {sid for sid, sig in new_scenes.items() if old_scenes.get(sid) == sig}
    old_listing = old.get("shot_list") or {}
    old_of = shot_scenes(old_listing, list(old.get("timeline") or []))
    new_of = shot_scenes(new_listing, new_timeline)
    old_urls = {int(r["idx"]): r.get("image_url") for r in old.get("shots") or []}
    old_by_idx = {int(sh["idx"]): sh for sh in old_listing.get("shots") or []}
    new_shots = new_listing.get("shots") or []

    def _shape(shot: dict[str, Any]) -> tuple[Any, ...]:
        return shot.get("source"), shot.get("speaker"), tuple(shot.get("characters") or [])

    urls: dict[int, str | None] = {}
    kept: set[int] = set()
    for sid in same:
        olds = [old_by_idx[i] for i in sorted(old_by_idx) if old_of.get(i) == sid]
        news = [sh for sh in new_shots if new_of.get(int(sh["idx"])) == sid]
        if len(olds) != len(news) or any(_shape(a) != _shape(b) for a, b in zip(olds, news, strict=False)):
            continue
        for was, now in zip(olds, news, strict=False):
            for key in _CARRIED:
                if was.get(key) is not None:
                    now[key] = was[key]
            urls[int(now["idx"])] = old_urls.get(int(was["idx"]))
            kept.add(int(now["idx"]))
    redraw = {int(sh["idx"]) for sh in new_shots} - kept
    changed = sorted(sid for sid in new_scenes if sid not in same)
    return urls, redraw, changed


async def restory_storyboard(
    previous: dict[str, Any],
    screenplay: dict[str, Any],
    *,
    site_config: Any,
    pool: Any,
    upload_prefix: str,
    note: str = "",
) -> dict[str, Any]:
    """Storyboard a revised script again, redoing only what the revision changed.

    The lines are voiced through the line cache, so only changed lines are
    synthesized; the shots are planned afresh against the new timing; every
    shot of a scene the revision left alone keeps its picture; only the rest
    is drawn. ``previous`` is the storyboard being revised.
    """
    from poindexter.services.casting import _ensure_voice_headroom
    from poindexter.services.dialogue_audio import render_dialogue
    from poindexter.services.screenplay_director import direct_screenplay

    wanted = [str(s) for s in previous.get("scenes") or []]
    known = {str(s.get("scene_id")) for s in screenplay.get("scenes") or []}
    # A revision that renamed or cut the storyboarded scenes storyboards the
    # whole revised script rather than failing.
    sub = select_scenes(screenplay, [s for s in wanted if s in known] or None)
    look = str(previous.get("style") or "")
    old_listing = previous.get("shot_list") or {}
    work = Path(tempfile.mkdtemp(prefix="storyboard-"))
    await _ensure_voice_headroom(site_config)
    voiced = await render_dialogue(sub, site_config=site_config, output_path=str(work / "dialogue.wav"))
    listing = await direct_screenplay(
        sub, list(voiced["timeline"]), float(voiced["duration_s"]),
        site_config=site_config, style=look, pool=pool,
        cast_portraits=dict(old_listing.get("cast_portraits") or {}),
        # The props keep their design, so a carried-over picture and a redrawn
        # one show the same object. A listing from before props were designed
        # (no key) designs them now.
        props=old_listing.get("props") if "props" in old_listing else None,
    )
    urls, redraw, changed = carry_over(previous, listing, list(voiced["timeline"]), sub)
    mp3 = str(work / "dialogue.mp3")
    version = len(previous.get("revisions") or []) + 2
    audio_url = await _upload(mp3, f"{upload_prefix}/dialogue-v{version}.mp3", "audio/mpeg", site_config) \
        if await _to_mp3(voiced["audio_path"], mp3) else None
    master_url = await _upload(
        voiced["audio_path"], f"{upload_prefix}/dialogue-v{version}.wav", "audio/wav", site_config,
    )
    plates = dict(previous.get("plates") or {})
    drawn = await _draw_shots(
        listing, redraw, work=work, site_config=site_config, pool=pool,
        upload_prefix=upload_prefix, version_tag=f"-v{version}", existing=urls, plates=plates,
    ) if redraw else {}
    merged = {**urls, **drawn}
    lines = sum(1 for b in voiced["timeline"] if b.get("kind") == "line")
    reused = int(voiced.get("lines_reused") or 0)
    logger.info(
        "[storyboard] story revision: %d of %d lines re-voiced, %d of %d shots drawn, scenes changed %s",
        lines - reused, lines, len(redraw), len(listing["shots"]), changed,
    )
    return {
        **{k: v for k, v in previous.items() if k not in ("score_url",)},
        "title": screenplay.get("title", ""),
        "scenes": [s.get("scene_id") for s in sub.get("scenes") or []],
        "audio_url": audio_url,
        "audio_master_url": master_url,
        "spoken_script": voiced.get("spoken_script", ""),
        "duration_s": float(voiced["duration_s"]),
        "shots": _rows(listing, list(voiced["timeline"]), merged),
        "shot_list": listing,
        "timeline": voiced["timeline"],
        "screenplay": sub,
        "plates": plates,
        "colour_script": colour_script(listing, plates),
        "revisions": [*(previous.get("revisions") or []), {
            "note": note[:500], "story": True, "scenes_changed": changed,
            "shots_drawn": sorted(redraw), "lines_reused": voiced.get("lines_reused", 0),
        }],
    }


__all__ = [
    "StoryboardError",
    "build_storyboard",
    "carry_over",
    "check_revision",
    "restory_storyboard",
    "revise_storyboard",
    "select_scenes",
    "shot_scenes",
]
