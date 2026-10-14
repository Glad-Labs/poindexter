"""A rough cut: a storyboard as a video, watched and noted before anything animates.

Chat drafting, phase 9. A full cartoon render takes an hour or two of GPU.
Until now the first time Matt saw the piece play was after that hour: a weak
scene, a dull shot or a flat ending cost a whole render to find. A
storyboard already has everything a cut needs except the motion: the voiced
lines (``audio_master_url``), a still per shot timed to them, and the script.
A rough cut puts them together:

1. **Score.** The cut's music and sound effects (``services/score.py``), the
   same soundtrack step a render runs. The score is kept with the draft
   (``score_url``): a revision keeps it (it only redraws pictures, and the
   voices and timing do not change), and rendering the storyboard uses it,
   so the music approved in the rough cut is the music in the video.
2. **Captions** written straight from the dialogue timeline: the words are
   known, so no transcription is needed.
3. **Compose** with the renderer's own compositor (``FFmpegLocalCompositor``)
   at ``rough_cut_width`` x ``rough_cut_height``: each still held for its
   shot with the compositor's Ken Burns move, the dialogue over the whole
   cut, the score under it, the captions burned in. A shot with no picture
   (a title card, a still that failed to draw) holds the previous picture.
4. **Director's notes** on the cut (``director_notes.review_cut`` with
   ``cut="rough"``) when ``rough_cut_notes_enabled``: the judge screens the
   stills and the writer gives notes; a redo note goes back to the
   storyboard as a revision, so the shots are fixed as pictures, for free,
   before they are animated.

Built inside the storyboard draft job when ``storyboard_rough_cut_enabled``.
Best-effort: a cut that fails leaves the storyboard as it was, with
``rough_cut_error`` saying why.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)


class RoughCutError(RuntimeError):
    """A rough cut could not be made. The message says why."""


def _setting(site_config: Any, key: str) -> str:
    from poindexter.services.settings_defaults import DEFAULTS

    value = site_config.get(key, None) if site_config is not None else None
    return str(DEFAULTS[key] if value is None or str(value).strip() == "" else value)


def _on(site_config: Any, key: str) -> bool:
    return _setting(site_config, key).strip().lower() in ("1", "true", "yes", "on")


def _stamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def captions_srt(timeline: list[dict[str, Any]]) -> str:
    """SRT for every spoken line on the timeline, at its measured time."""
    blocks: list[str] = []
    for beat in timeline:
        text = " ".join(str(beat.get("text") or "").split())
        if beat.get("kind") != "line" or not text:
            continue
        start = float(beat.get("start_s") or 0.0)
        end = max(start + 0.3, float(beat.get("end_s") or start))
        blocks.append(f"{len(blocks) + 1}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n")
    return "\n".join(blocks)


def scene_plan(rows: list[dict[str, Any]], duration_s: float) -> list[tuple[str | None, float]]:
    """``(image_url, seconds)`` per shot in order; a shot with no picture holds the one before.

    The last shot runs to the end of the audio, so the cut is never shorter
    than its voices.
    """
    plan: list[tuple[str | None, float]] = []
    held: str | None = None
    ordered = sorted(rows, key=lambda r: float(r.get("start_s") or 0.0))
    for i, row in enumerate(ordered):
        held = row.get("image_url") or held
        if i + 1 < len(ordered):
            seconds = float(ordered[i + 1].get("start_s") or 0.0) - float(row.get("start_s") or 0.0)
        else:
            seconds = max(float(row.get("duration_s") or 0.0), duration_s - float(row.get("start_s") or 0.0))
        if seconds > 0.04:
            plan.append((held, seconds))
    return plan


async def _download(url: str, dest: str) -> str:
    from poindexter.services.creative_drafts import _download as fetch

    return await fetch(url, dest)


def _blank(path: str, width: int, height: int) -> str:
    from PIL import Image

    Image.new("RGB", (width, height), (8, 10, 14)).save(path)
    return path


async def _score(
    payload: dict[str, Any], *, site_config: Any, pool: Any, work: Path, upload_prefix: str,
    previous: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """``(local_path, url)`` of the cut's score: the previous version's, else a new one."""
    from poindexter.services.storyboard import _upload

    kept = (previous or {}).get("score_url") or payload.get("score_url")
    if kept:
        return await _download(str(kept), str(work / "score.wav")), str(kept)
    from poindexter.services.score import score_screenplay

    built = await score_screenplay(
        payload.get("screenplay") or {}, list(payload.get("timeline") or []),
        duration_s=float(payload.get("duration_s") or 0.0), style=str(payload.get("style") or ""),
        site_config=site_config, pool=pool, output_dir=str(work),
    )
    if not built.get("path"):
        logger.info("[rough_cut] no score: %s", built.get("skipped") or "nothing was made")
        return None, None
    url = await _upload(built["path"], f"{upload_prefix}/score.wav", "audio/wav", site_config)
    return built["path"], url


async def compose_cut(
    payload: dict[str, Any], *, site_config: Any, work: Path, score_path: str | None,
) -> str:
    """The cut as a local MP4. Raises ``RoughCutError`` with the reason."""
    from poindexter.plugins.media_compositor import CompositionRequest, CompositionScene
    from poindexter.services.media_compositors.ffmpeg_local import FFmpegLocalCompositor

    if not payload.get("audio_master_url"):
        raise RoughCutError("this storyboard has no full-quality dialogue track; storyboard the scenes again")
    width = int(float(_setting(site_config, "rough_cut_width")))
    height = int(float(_setting(site_config, "rough_cut_height")))
    audio = await _download(str(payload["audio_master_url"]), str(work / "dialogue.wav"))
    plan = scene_plan(list(payload.get("shots") or []), float(payload.get("duration_s") or 0.0))
    if not plan:
        raise RoughCutError("the storyboard has no shots")
    local: dict[str, str] = {}
    scenes = []
    for n, (url, seconds) in enumerate(plan):
        if url and url not in local:
            try:
                local[url] = await _download(url, str(work / f"shot_{n:03d}{Path(url).suffix or '.png'}"))
            except Exception as exc:  # noqa: BLE001 — a missing still holds a blank, the cut still plays
                logger.warning("[rough_cut] still %s did not download: %s", url, exc)
        path = local.get(url or "") or _blank(str(work / "blank.png"), width, height)
        scenes.append(CompositionScene(clip_path=path, duration_s=seconds))
    captions = work / "captions.srt"
    srt = captions_srt(list(payload.get("timeline") or []))
    if srt:
        captions.write_text(srt, encoding="utf-8")
    out = str(work / "rough_cut.mp4")
    result = await FFmpegLocalCompositor(site_config=site_config).compose(CompositionRequest(
        scenes=scenes, narration_track_path=audio, soundtrack_path=score_path,
        caption_track_path=str(captions) if srt else None, output_path=out,
        width=width, height=height, fps=30, codec="h264", container="mp4",
        metadata={"renderer": "rough_cut", "shot_count": len(scenes)},
    ))
    if not result.success or not result.output_path:
        raise RoughCutError(f"the compositor failed: {result.error or 'no output'}")
    return result.output_path


async def build_rough_cut(
    payload: dict[str, Any], *, site_config: Any, pool: Any, upload_prefix: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score, compose, upload and (optionally) note a storyboard's rough cut.

    Returns the keys to merge into the storyboard payload: ``rough_cut_url``,
    ``rough_cut_seconds``, ``score_url`` and, with notes on, ``rough_cut_notes``.
    """
    from poindexter.services import director_notes as dn
    from poindexter.services.storyboard import _upload
    from poindexter.services.video_renderers import shot_vision_qa as sq

    work = Path(tempfile.mkdtemp(prefix="rough-cut-"))
    score_path, score_url = await _score(
        payload, site_config=site_config, pool=pool, work=work, upload_prefix=upload_prefix, previous=previous,
    )
    video = await compose_cut(payload, site_config=site_config, work=work, score_path=score_path)
    version = len(payload.get("revisions") or []) + 1
    url = await _upload(video, f"{upload_prefix}/rough_cut-v{version}.mp4", "video/mp4", site_config)
    out: dict[str, Any] = {
        "rough_cut_url": url,
        "rough_cut_seconds": round(await sq._probe_seconds(video) or 0.0, 1),
        "score_url": score_url,
    }
    if _on(site_config, "rough_cut_notes_enabled"):
        try:
            review = await dn.review_cut(
                video_path=video, screenplay=payload.get("screenplay") or {},
                shot_list=payload.get("shot_list") or {}, timeline=list(payload.get("timeline") or []),
                site_config=site_config, pool=pool, cut="rough",
            )
            out["rough_cut_notes"] = {**review, "status": "ready"}
        except Exception as exc:  # noqa: BLE001 — notes are a refinement; the cut is the deliverable
            logger.warning("[rough_cut] no notes: %s", exc)
            out["rough_cut_notes"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
    try:
        os.remove(video)
    except OSError:  # silent-ok: temp cleanup, the cut is uploaded
        pass
    return out


async def add_rough_cut(
    payload: dict[str, Any], *, site_config: Any, pool: Any, upload_prefix: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``payload`` with its rough cut, or with ``rough_cut_error`` when the cut failed. Never raises."""
    clean = {k: v for k, v in payload.items()
             if k not in ("rough_cut_url", "rough_cut_seconds", "rough_cut_notes", "rough_cut_error")}
    if not _on(site_config, "storyboard_rough_cut_enabled"):
        return clean
    try:
        return {**clean, **await build_rough_cut(
            clean, site_config=site_config, pool=pool, upload_prefix=upload_prefix, previous=previous,
        )}
    except Exception as exc:  # noqa: BLE001 — the storyboard is still worth having without its cut
        logger.warning("[rough_cut] failed: %s", exc)
        return {**clean, "rough_cut_error": f"{type(exc).__name__}: {exc}"[:500]}


__all__ = [
    "RoughCutError",
    "add_rough_cut",
    "build_rough_cut",
    "captions_srt",
    "compose_cut",
    "scene_plan",
]
