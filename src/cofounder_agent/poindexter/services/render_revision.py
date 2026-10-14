"""Revise a finished render: redo only the shots a note is about.

Chat drafting, phase 4. "Redo the shot where Ruby talks to the wolf, hands
lower" should cost one shot of GPU, not a whole render. A screenplay render
keeps its shot clips and its inputs (``video_renderers.clip_cache``); a
revision:

1. reads the kept shot list and timeline and asks the writer which shots the
   note is about and their new pictures (``story.storyboard_revise``, the
   storyboard's revision prompt: a render's shots are a storyboard's shots);
2. writes the revised shot list beside the cache;
3. queues a fixed plan (``media.load_render_revision`` →
   ``media.render_long_video`` → ``media.qa`` → ``media.persist``) that
   re-renders under the source's cache key, so every unchanged shot's clip is
   reused and only the changed ones render. The same dialogue track and
   captions are reused; the new video lands in the approval queue.

Setting overrides (a calmer emotion, a different talking-shot prompt) apply
to the revision's render only.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RevisionError(RuntimeError):
    """A render cannot be revised as asked. The message says why."""


def _cache_root(site_config: Any) -> Path:
    from poindexter.services.settings_defaults import DEFAULTS

    raw = site_config.get("video_clip_cache_dir", None) if site_config is not None else None
    value = str(DEFAULTS["video_clip_cache_dir"] if raw is None or str(raw).strip() == "" else raw)
    return Path(os.path.expanduser(value))


def cache_key_for(task_id: str, site_config: Any) -> str:
    """The clip cache a render's revisions build on: its own, or its source's."""
    root = _cache_root(site_config)
    ref = root / "refs" / f"{task_id}.txt"
    if ref.is_file():
        return ref.read_text().strip()
    if (root / task_id).is_dir():
        return task_id
    raise RevisionError(
        f"render {task_id[:8]} kept no shot clips (rendered before revisions existed, "
        "or its cache was pruned); render it again from its draft"
    )


async def plan_revision(
    pool: Any, *, task_id: str, instruction: str, site_config: Any,
) -> dict[str, Any]:
    """Which shots change and how; writes the revised shot list. No rendering."""
    from poindexter.services import storyboard as sb
    from poindexter.services.screenplay import _ask_validated, _resolve_model
    from poindexter.services.video_renderers import clip_cache

    note = (instruction or "").strip()
    if not note:
        raise RevisionError("say what to change")
    key = cache_key_for(task_id, site_config)
    cache_dir = _cache_root(site_config) / key
    try:
        kept = clip_cache.load_inputs(str(cache_dir))
    except FileNotFoundError as exc:
        raise RevisionError(str(exc)) from exc
    listing = kept["shot_list"]
    rows = sb._rows(listing, kept["timeline"], {})
    drawable = {r["idx"] for r in rows if r["kind"] in (sb.TALKING, sb.STILL)}
    table = "\n".join(
        f"{r['idx']} | {r['kind']} | {r.get('speaker') or ', '.join(r.get('characters') or []) or '-'} | "
        f"{r.get('words') or '-'} | {r.get('picture') or ''}"
        for r in rows if r["idx"] in drawable
    )

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return sb.check_revision(reply, drawable), []

    reply, _ = await _ask_validated(
        "story.storyboard_revise",
        {"shot_table": table, "instruction": note[:2000], "style": str(listing.get("style") or "")},
        _check, model=_resolve_model(site_config), site_config=site_config, pool=pool,
        task_id=task_id, what="render revision", phase="story_revise_render",
    )
    changed = {int(str(s["id"])): str(s["prompt"]).strip() for s in reply["shots"]}
    for shot in listing.get("shots") or []:
        if shot.get("idx") in changed:
            field = "frame_prompt" if shot.get("source") == "presenter" and shot.get("speaker") else "prompt"
            shot[field] = changed[shot["idx"]][:2000]
    name = f"revision-{uuid.uuid4().hex[:8]}.json"
    (cache_dir / "revisions").mkdir(parents=True, exist_ok=True)
    (cache_dir / "revisions" / name).write_text(json.dumps(listing, default=str))
    title = str((kept.get("screenplay") or {}).get("title") or "video")
    return {"key": key, "shot_list_file": f"revisions/{name}", "changed": sorted(changed), "title": title}


def revision_spec(key: str, shot_list_file: str, *, setting_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    steps = [
        ("load", "media.load_render_revision"), ("render", "media.render_long_video"),
        ("qa", "media.qa"), ("persist", "media.persist"),
    ]
    nodes = [
        {"id": nid, "atom": atom,
         **({"config": {"render_source": key, "shot_list_file": shot_list_file}} if nid == "load" else {})}
        for nid, atom in steps
    ]
    edges = [{"from": a[0], "to": b[0]} for a, b in zip(steps, steps[1:], strict=False)]
    edges.append({"from": steps[-1][0], "to": "END"})
    spec: dict[str, Any] = {
        "name": f"revise_render_{key[:8]}",
        "description": "Re-render the shots a note changes, reusing every other shot",
        "entry": "load", "nodes": nodes, "edges": edges,
    }
    if setting_overrides:
        spec["setting_overrides"] = dict(setting_overrides)
    return spec


def load_revision_state(render_source: str, shot_list_file: str, *, site_config: Any) -> dict[str, Any]:
    """The state ``media.load_render_revision`` hands the render."""
    from poindexter.services.video_renderers import clip_cache

    cache_dir = _cache_root(site_config) / render_source
    try:
        kept = clip_cache.load_inputs(str(cache_dir))
    except FileNotFoundError as exc:
        raise RevisionError(str(exc)) from exc
    revised = cache_dir / shot_list_file
    if not revised.resolve().is_relative_to(cache_dir.resolve()) or not revised.is_file():
        raise RevisionError(f"no revised shot list {shot_list_file!r} in the cache")
    if not kept["audio_path"]:
        raise RevisionError("the kept render has no dialogue track")
    screenplay = dict(kept.get("screenplay") or {})
    if screenplay.get("title"):
        screenplay["title"] = f"{screenplay['title']} (revised)"
    return {
        "video_shot_list": json.loads(revised.read_text()),
        "long_narration_audio_path": kept["audio_path"],
        "long_caption_srt_path": kept["caption_path"] or "",
        "dialogue_timeline": kept["timeline"],
        "screenplay": screenplay,
        "video_clip_cache_key": render_source,
        # The source's soundtrack: the dialogue track is the same, so its
        # music and sound effects still fit.
        **({"video_ambient_audio_path": kept["score_path"]} if kept.get("score_path") else {}),
    }


async def start_render_revision(
    pool: Any,
    db_service: Any,
    *,
    task_id: str,
    instruction: str,
    site_config: Any,
    conversation_id: str,
    message_id: str,
    setting_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan and queue a revision; returns ``{task_id, changed, title, overrides}``."""
    from poindexter.services import chat_plans
    from poindexter.services.pipeline_architect import _validate_spec
    from poindexter.services.setting_overrides import check_overrides

    problems = check_overrides(setting_overrides, site_config, where="revision")
    if problems:
        raise RevisionError("; ".join(problems))
    plan = await plan_revision(pool, task_id=task_id, instruction=instruction, site_config=site_config)
    spec = revision_spec(plan["key"], plan["shot_list_file"], setting_overrides=setting_overrides)
    ok, errors = _validate_spec(spec)
    if not ok:
        raise RevisionError("the revision plan is invalid: " + "; ".join(errors[:3]))
    card = await chat_plans.create_plan(
        pool, conversation_id=conversation_id, message_id=message_id,
        intent=f"Revise render {task_id[:8]}: {instruction[:200]}", topic=f"{plan['title']} (revised)", spec=spec,
    )
    run = await chat_plans.run_plan(pool=pool, db_service=db_service, plan_id=card["plan_id"])
    new_task = str(run.get("task_id") or "")
    refs = _cache_root(site_config) / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / f"{new_task}.txt").write_text(plan["key"])
    return {"task_id": new_task, "changed": plan["changed"], "title": f"{plan['title']} (revised)",
            "overrides": card.get("overrides") or []}


__all__ = [
    "RevisionError",
    "cache_key_for",
    "load_revision_state",
    "plan_revision",
    "revision_spec",
    "start_render_revision",
]
