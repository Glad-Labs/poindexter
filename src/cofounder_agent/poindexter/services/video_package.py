"""A finished cartoon's YouTube package: title, description, tags and thumbnail, from the chat.

Chat drafting, phase 7. A creative-studio video has no post, so until now it
uploaded under its screenplay's title, its logline as the whole description,
no tags and no thumbnail (YouTube picked a frame). Matt wants to finish a
cartoon the way he drafted it: in the chat, see what will go up, change it by
talking, then publish.

:func:`draft_package` asks the writer (``story.youtube_package``, or
``story.youtube_package_revise`` with the operator's note and the previous
version) for one JSON object: the title, the description, the tags, the
thumbnail's words and which shot the thumbnail shows. Code checks it
(:func:`check_package`) and sends problems back once:

- the title fits WHOLE in ``video_package_title_max_chars``; a cut title is a
  defect, never a fallback;
- every number in the title, description or thumbnail words is in the
  script; an invented number in the most visible text a video has is the one
  mistake a viewer quotes back;
- no links in the description (the uploader adds what links it adds);
- tags are short phrases, no commas (YouTube joins them with commas);
- the thumbnail words pass the long-video thumbnail's own checks
  (``video_thumbnail.clean_thumbnail_hook``);
- the thumbnail shot is one the video actually draws.

A title with nothing a viewer would search for (``title_searchability``) is
a soft problem: it gets the retry, then ships with a note.

The thumbnail is composed, never generated: a frame of the rendered video at
the middle of the chosen shot (the caption band cropped off) under the
thumbnail words, by the same chromium composer as every long video
(``video_thumbnail.render_thumbnail_jpeg``), in the ``video_thumbnail_*``
look with ``video_package_thumbnail_layout``.

Each version is a ``creative_drafts`` row of kind ``package`` (the chat's
draft machinery: built in the background, revised by a note, shown on a
card). Nothing touches the video until the operator keeps one
(:func:`apply_package`): then it is stored where the uploader already reads
it, so what the card shows is exactly what goes up: the title and description
on the video's ``media_assets`` row (the standalone dispatch path reads those
columns), the tags in ``metadata.youtube_package`` (read by the same query),
and the image as the task's ``video_thumbnail`` asset. Nothing is public until
the video is approved (the card's Publish button keeps the package and
approves it, or the approval queue): that approval is the gate, as it is for
every video.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Any

from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

#: ``media_assets.metadata`` key the package is stored under (on the ``video`` row).
PACKAGE_KEY = "youtube_package"
#: Where the screenplay's own title and logline are kept the first time a
#: package overwrites the row's columns, so every later version starts from them.
SOURCE_KEY = "youtube_package_source"

TITLE_MAX_DEFAULT = 70
DESCRIPTION_MAX_DEFAULT = 700
MAX_TAGS_DEFAULT = 12
TAG_MAX_CHARS = 30

_NUMBER_RE = re.compile(r"\d[\d,.]*")
_URL_RE = re.compile(r"https?://|www\.", re.I)
#: Shot sources that are drawn pictures (not a text card or a demo clip).
_DRAWN = ("generative", "presenter")


class PackageError(RuntimeError):
    """A video cannot be packaged as asked. The message says why."""


def _sc_int(site_config: Any, key: str, default: int) -> int:
    try:
        return int(str(site_config.get(key, default) if site_config is not None else default).strip())
    except (TypeError, ValueError):
        return default


def _sc_str(site_config: Any, key: str, default: str) -> str:
    raw = site_config.get(key, default) if site_config is not None else default
    return str(raw if raw is not None and str(raw).strip() else default).strip()


# ---------------------------------------------------------------------------
# What the writer sees
# ---------------------------------------------------------------------------


def shot_rows(shot_list: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The drawn shots: ``{idx, who, picture, start_s, duration_s}`` in order."""
    rows = []
    for shot in (shot_list or {}).get("shots") or []:
        if shot.get("source") not in _DRAWN or shot.get("idx") is None:
            continue
        who = shot.get("speaker") or ", ".join(shot.get("characters") or []) or "-"
        picture = shot.get("intent") or shot.get("frame_prompt") or shot.get("prompt") or ""
        rows.append({
            "idx": int(shot["idx"]), "who": str(who), "picture": " ".join(str(picture).split())[:300],
            "start_s": float(shot.get("narration_offset_s") or 0.0),
            "duration_s": float(shot.get("duration_s") or 0.0),
        })
    return rows


def script_text(screenplay: dict[str, Any]) -> str:
    """Everything the video says or shows in words: the ground truth for numbers."""
    parts = [str(screenplay.get(k) or "") for k in ("title", "logline")]
    for scene in screenplay.get("scenes") or []:
        parts += [str(scene.get(k) or "") for k in ("setting", "summary", "turn")]
        parts += [str(b.get("text") or "") for b in scene.get("beats") or []]
    return " ".join(p for p in parts if p)


def story_block(screenplay: dict[str, Any]) -> str:
    """The story in a few lines: scenes and their turns, then the spoken lines."""
    lines = []
    for scene in screenplay.get("scenes") or []:
        lines.append(f"- {scene.get('summary') or ''} Ends with: {scene.get('turn') or '(unstated)'}")
    spoken = [
        f"{b.get('speaker')}: {b.get('text')}"
        for scene in screenplay.get("scenes") or [] for b in scene.get("beats") or []
        if b.get("kind") == "line" and b.get("text")
    ]
    if spoken:
        lines.append("LINES")
        lines += spoken[:80]
    return "\n".join(lines) or "(no script kept)"


def cast_block(screenplay: dict[str, Any], site_config: Any, shot_list: dict[str, Any] | None = None) -> str:
    """``slug: Display Name`` per cast member (names are what people search for).

    The script's cast, else everyone the shots show or give a line to.
    """
    from poindexter.services.persona_service import get_persona

    slugs = list(screenplay.get("cast") or [])
    if not slugs:
        seen: dict[str, None] = {}
        for shot in (shot_list or {}).get("shots") or []:
            for slug in [*(shot.get("characters") or []), shot.get("speaker")]:
                if slug and slug != "narrator":
                    seen.setdefault(str(slug), None)
        slugs = list(seen)
    out = []
    for slug in slugs:
        persona = None
        try:
            persona = get_persona(site_config, str(slug))
        except Exception:  # noqa: BLE001  # silent-ok: an unknown slug is listed bare
            persona = None
        name = str(getattr(persona, "display_name", "") or "") if persona else ""
        out.append(f"{slug}: {name}" if name else str(slug))
    return "\n".join(out) or "(none listed)"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _ungrounded_numbers(text: str, haystack: str) -> list[str]:
    flat = haystack.replace(",", "")
    return [n for n in _NUMBER_RE.findall(text) if n.replace(",", "").rstrip(".") not in flat]


def check_package(
    reply: dict[str, Any],
    *,
    shot_ids: set[int],
    source_text: str,
    title_max: int,
    description_max: int,
    max_tags: int,
    hook_max: int,
) -> tuple[list[str], list[str]]:
    """``(hard, soft)`` FIX lines for a package reply. Normalises ``reply`` in place."""
    from poindexter.services.title_searchability import has_searchable_entity
    from poindexter.services.video_thumbnail import clean_thumbnail_hook

    hard: list[str] = []
    soft: list[str] = []
    title = " ".join(str(reply.get("title") or "").split()).strip().strip("\"'")
    reply["title"] = title
    if not title:
        hard.append('FIX: add "title", the video\'s title')
    elif len(title) > title_max:
        hard.append(f'FIX: "title" is {len(title)} characters; write one of at most {title_max} (it is never cut)')
    description = str(reply.get("description") or "").strip()
    reply["description"] = description
    if not description:
        hard.append('FIX: add "description", what a viewer reads under the video')
    elif len(description) > description_max:
        hard.append(f'FIX: "description" is {len(description)} characters; keep it under {description_max}')
    if _URL_RE.search(description):
        hard.append('FIX: take the links out of "description"; the uploader adds its own')
    for field, text in (("title", title), ("description", description)):
        for number in _ungrounded_numbers(text, source_text):
            hard.append(f'FIX: "{field}" says {number!r}, which the script never says; use only numbers from the script')

    raw_tags = reply.get("tags")
    if not isinstance(raw_tags, list) or not raw_tags:
        hard.append('FIX: add "tags", a list of short search phrases')
        tags: list[str] = []
    else:
        tags = []
        for tag in raw_tags:
            tag = " ".join(str(tag or "").split()).strip().lstrip("#")
            if not tag:
                continue
            if "," in tag or len(tag) > TAG_MAX_CHARS:
                hard.append(f'FIX: tag {tag[:40]!r} must be one short phrase of at most {TAG_MAX_CHARS} characters, no commas')
                continue
            if tag.lower() not in {t.lower() for t in tags}:
                tags.append(tag)
        if len(tags) > max_tags:
            soft.append(f"(kept the first {max_tags} of {len(tags)} tags)")
            tags = tags[:max_tags]
    reply["tags"] = tags

    hook_raw = str(reply.get("thumbnail_text") or "").strip()
    if hook_raw:
        hook, why = clean_thumbnail_hook(hook_raw, title=title, source_text=source_text, max_chars=hook_max)
        if why:
            hard.append(f'FIX: "thumbnail_text" {hook_raw[:60]!r} does not work: {why}')
        reply["thumbnail_text"] = hook
    else:
        reply["thumbnail_text"] = ""

    try:
        shot = int(str(reply.get("thumbnail_shot")).strip())
    except (TypeError, ValueError):
        shot = -1
    if shot not in shot_ids:
        hard.append(f'FIX: "thumbnail_shot" must be one of the shot numbers {sorted(shot_ids)[:40]}')
    reply["thumbnail_shot"] = shot

    if title and not hard:
        report = has_searchable_entity(title, tags=tags, topic=source_text[:200])
        if not report.ok:
            soft.append(
                'FIX: the title names nothing a viewer would search for; name the tale, a character or the subject'
            )
    return hard, soft


# ---------------------------------------------------------------------------
# Loading and storing
# ---------------------------------------------------------------------------


async def load_video(pool: Any, task_id: str) -> dict[str, Any]:
    """The task's standalone ``video`` asset and its approval state."""
    row = await pool.fetchrow(
        """
        SELECT mas.id::text AS id, mas.title, mas.description, mas.storage_path, mas.metadata,
               (SELECT ma.status FROM media_approvals ma
                 WHERE ma.task_id = mas.task_id AND ma.medium = 'video'
                 ORDER BY ma.created_at DESC LIMIT 1) AS approval
          FROM media_assets mas
         WHERE mas.task_id = $1 AND mas.type = 'video' AND mas.post_id IS NULL
         ORDER BY mas.created_at DESC LIMIT 1
        """,
        task_id,
    )
    if row is None:
        raise PackageError(
            f"task {task_id[:8]} has no finished cartoon video to package (still rendering, or not a cartoon)"
        )
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta or "{}")
    return {**dict(row), "metadata": dict(meta or {})}


def _kept_render(task_id: str, site_config: Any) -> dict[str, Any] | None:
    from poindexter.services import render_revision as rr
    from poindexter.services.video_renderers import clip_cache

    try:
        key = rr.cache_key_for(task_id, site_config)
        return clip_cache.load_inputs(str(rr._cache_root(site_config) / key))
    except (rr.RevisionError, FileNotFoundError, ValueError) as exc:
        logger.info("[video_package] no kept render for %s (%s); using the asset row", task_id, exc)
        return None


def _source(video: dict[str, Any], kept: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(screenplay, shot_list)`` for a video: the kept render's, else the asset row's."""
    original = video["metadata"].get(SOURCE_KEY) or {}
    screenplay = dict((kept or {}).get("screenplay") or {})
    screenplay.setdefault("title", original.get("title") or video.get("title") or "")
    screenplay.setdefault("logline", original.get("logline") or video.get("description") or "")
    shot_list = (kept or {}).get("shot_list") or video["metadata"].get("video_shot_list") or {}
    return screenplay, shot_list


async def save_package(
    pool: Any, *, video: dict[str, Any], package: dict[str, Any],
) -> None:
    """Write the package where the uploader reads it (the video's own row)."""
    meta = dict(video["metadata"])
    meta.setdefault(SOURCE_KEY, {"title": video.get("title") or "", "logline": video.get("description") or ""})
    meta[PACKAGE_KEY] = package
    await pool.execute(
        "UPDATE media_assets SET title = $2, description = $3, metadata = $4::jsonb, updated_at = now() "
        "WHERE id = $1::uuid",
        video["id"], package["title"][:500], package["description"], json.dumps(meta, default=str),
    )


async def _video_seconds(path: str) -> float | None:
    from poindexter.services.video_thumbnail import _video_duration

    return await _video_duration(path)


def _package_dir(site_config: Any) -> str:
    from poindexter.services.video_service import VIDEO_DIR

    path = os.path.join(str(VIDEO_DIR), "packages")
    os.makedirs(path, exist_ok=True)
    return path


async def compose_thumbnail(
    *, video_path: str, shot: dict[str, Any], planned_s: float, hook: str, site_config: Any, out_path: str,
) -> str:
    """A frame from the middle of ``shot`` under ``hook``, written to ``out_path`` as a JPEG."""
    from poindexter.services import video_thumbnail as vt

    if not video_path or not os.path.exists(video_path):
        raise PackageError("the rendered video is not on disk, so there is no frame to put on a thumbnail")
    duration = await _video_seconds(video_path)
    at = shot["start_s"] + shot["duration_s"] * 0.5
    if duration and planned_s > 0:
        at *= duration / planned_s
    try:
        crop = float(site_config.get("video_thumbnail_frame_crop_bottom", 0.22) or 0.22)
    except (TypeError, ValueError):
        crop = 0.22
    style = vt.style_from_settings(site_config)
    layout = _sc_str(site_config, "video_package_thumbnail_layout", "right")
    with tempfile.TemporaryDirectory(prefix="yt-package-") as work:
        frame = await vt._frame(video_path, at, min(0.45, max(0.0, crop)), work, f"shot{shot['idx']}")
        if not frame:
            raise PackageError(f"could not take a frame of shot {shot['idx']} from the video")
        data = await vt.render_thumbnail_jpeg(
            hook=hook, background_path=frame, style=style,
            image_layout=layout if layout in ("right", "cover") else "right",
        )
    if not data:
        raise PackageError("the thumbnail renderer (chromium) returned no image")
    with open(out_path, "wb") as fh:
        fh.write(data)
    os.chmod(out_path, 0o644)
    return out_path


# ---------------------------------------------------------------------------
# Drafting (a creative_drafts kind) and applying
# ---------------------------------------------------------------------------


async def check_packageable(pool: Any, task_id: str) -> dict[str, Any]:
    """The video, if it can take a package now; raises ``PackageError`` with why not."""
    video = await load_video(pool, task_id)
    if video.get("approval") == "approved":
        raise PackageError("this video is already approved for upload; its package can no longer change")
    return video


async def draft_package(
    pool: Any,
    *,
    task_id: str,
    site_config: Any,
    upload_prefix: str,
    previous: dict[str, Any] | None = None,
    instruction: str = "",
) -> dict[str, Any]:
    """Write (or, from ``previous`` and ``instruction``, revise) a package; draws its thumbnail.

    Nothing is stored on the video: the returned payload is a draft version.
    :func:`apply_package` makes it what uploads.
    """
    from poindexter.services.screenplay import ScreenplayError, _ask_validated, _resolve_model
    from poindexter.services.storyboard import _upload

    video = await check_packageable(pool, task_id)
    kept = _kept_render(task_id, site_config)
    screenplay, shot_list = _source(video, kept)
    rows = shot_rows(shot_list)
    if not rows:
        raise PackageError("the video kept no drawn shots to take a thumbnail from")
    by_idx = {r["idx"]: r for r in rows}
    if screenplay.get("scenes"):
        story, source = story_block(screenplay), script_text(screenplay)
    else:
        # A render from before scripts were kept: its shots are the story.
        story = "\n".join(f"- {r['picture']}" for r in rows)
        source = " ".join([script_text(screenplay), *(r["picture"] for r in rows)])
    limits = {
        "title_max": max(20, _sc_int(site_config, "video_package_title_max_chars", TITLE_MAX_DEFAULT)),
        "description_max": max(100, _sc_int(site_config, "video_package_description_max_chars", DESCRIPTION_MAX_DEFAULT)),
        "max_tags": max(1, _sc_int(site_config, "video_package_max_tags", MAX_TAGS_DEFAULT)),
        "hook_max": max(8, _sc_int(site_config, "video_thumbnail_hook_max_chars", 32)),
    }
    prompt_vars: dict[str, Any] = {
        "title": screenplay.get("title") or "", "logline": screenplay.get("logline") or "",
        "style": str(shot_list.get("style") or screenplay.get("visual_style") or "(as drawn)"),
        "cast_block": cast_block(screenplay, site_config, shot_list), "story": story,
        "shot_table": "\n".join(f"{r['idx']} | {r['who']} | {r['picture']}" for r in rows),
        **limits,
    }
    note = (instruction or "").strip()
    if previous is not None:
        if not note:
            raise PackageError("say what to change")
        key = "story.youtube_package_revise"
        prompt_vars["current"] = json.dumps(
            {k: previous.get(k) for k in ("title", "description", "tags", "thumbnail_text", "thumbnail_shot")},
            ensure_ascii=False,
        )
        prompt_vars["instruction"] = note[:2000]
    else:
        key = "story.youtube_package"

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return check_package(reply, shot_ids=set(by_idx), source_text=source, **limits)

    try:
        reply, notes = await _ask_validated(
            key, prompt_vars, _check, model=_resolve_model(site_config), site_config=site_config,
            pool=pool, task_id=task_id, what="YouTube package", phase="story_youtube_package",
        )
    except ScreenplayError as exc:
        raise PackageError(str(exc)) from exc

    shot = by_idx[reply["thumbnail_shot"]]
    planned = sum(float(s.get("duration_s") or 0) for s in shot_list.get("shots") or [])
    local = os.path.join(_package_dir(site_config), upload_prefix.replace("/", "_") + ".jpg")
    await compose_thumbnail(
        video_path=str(video.get("storage_path") or ""), shot=shot, planned_s=planned,
        hook=reply["thumbnail_text"], site_config=site_config, out_path=local,
    )
    url = await _upload(local, f"{upload_prefix}/thumbnail.jpg", "image/jpeg", site_config)
    return {
        "task_id": task_id, "title": reply["title"], "description": reply["description"],
        "tags": reply["tags"], "thumbnail_text": reply["thumbnail_text"], "thumbnail_shot": shot["idx"],
        "thumbnail_picture": shot["picture"], "thumbnail_url": url, "thumbnail_path": local,
        "source_title": screenplay.get("title") or "",
        "notes": [n.removeprefix("FIX: ") for n in notes],
    }


async def apply_package(pool: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Make a package draft what uploads: the video row's words and the task's thumbnail."""
    from poindexter.services import video_thumbnail as vt

    task_id = str(payload.get("task_id") or "")
    video = await check_packageable(pool, task_id)
    src = str(payload.get("thumbnail_path") or "")
    if not src or not os.path.exists(src):
        raise PackageError("the draft's thumbnail file is gone; revise the package to draw it again")
    package = {
        k: payload.get(k) for k in ("title", "description", "tags", "thumbnail_text", "thumbnail_shot")
    } | {"applied_at": datetime.now(timezone.utc).isoformat()}
    await save_package(pool, video=video, package=package)
    tmp = os.path.join(tempfile.gettempdir(), f"media_{task_id}_package_thumbnail.jpg")
    shutil.copyfile(src, tmp)
    asset_id = await vt.store_thumbnail_asset(
        pool, task_id=task_id, src_path=tmp, post_id=None, video_recorded_now=True,
        meta={"hook": package["thumbnail_text"], "background": f"shot {package['thumbnail_shot']}",
              "source": "chat_package"},
    )
    if not asset_id:
        raise PackageError("the package was saved but its thumbnail could not be stored")
    logger.info("[video_package] task %s: package applied, %r", task_id, package["title"])
    return {**package, "thumbnail_asset_id": asset_id}


__all__ = [
    "PACKAGE_KEY",
    "SOURCE_KEY",
    "PackageError",
    "apply_package",
    "cast_block",
    "check_package",
    "check_packageable",
    "compose_thumbnail",
    "draft_package",
    "load_video",
    "save_package",
    "script_text",
    "shot_rows",
    "story_block",
]
