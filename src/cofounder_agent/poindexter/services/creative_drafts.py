"""Creative drafts: a cast or a screenplay drafted and revised in the chat.

Chat drafting, phase 1 (Matt, 2026-10-14: "draft a story, draft character
sheets, and draft and tweak renders with the architect in a chat"). A draft
is not a persona and not a pipeline task: it is something to look at, talk
about and revise, and only "keep" makes it real.

- A **cast** draft runs the casting step without saving anything: it designs
  the characters, draws their portraits into ``drafts/<id>/`` storage and
  voices each sample line, so the card shows faces and plays voices. Keep
  writes the personas.
- A **screenplay** draft runs the screenplay writer for an existing cast.
  Keep marks it kept; phase 3 renders from it.
- A **storyboard** draft (phase 2) voices scenes of a script draft and draws
  a still per shot with the renderer's own functions
  (``services/storyboard.py``). A note redraws only the shots it changes.

Every revision is a new row (``parent_id`` -> the version it revises,
``version`` + 1), so the chat can go back. Drafts are built in the
background, because drawing a cast outlasts a chat turn's deadline. The card
polls the row's ``status``: ``working`` -> ``ready`` | ``failed``. A cast
draft that would draw while a video renders waits for the render to finish
first, with a note saying so.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

KINDS = ("cast", "screenplay", "storyboard")
_JOBS: set[asyncio.Task[Any]] = set()


class DraftError(RuntimeError):
    """A draft request that cannot be acted on. The message says why."""


def _setting(site_config: Any, key: str) -> str:
    from poindexter.services.settings_defaults import DEFAULTS

    value = site_config.get(key, None) if site_config is not None else None
    return str(DEFAULTS[key] if value is None or str(value).strip() == "" else value)


def _row(record: Any) -> dict[str, Any]:
    out = dict(record)
    for key in ("id", "conversation_id", "parent_id"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    for key in ("request", "payload"):
        if isinstance(out.get(key), str):
            out[key] = json.loads(out[key])
    for key in ("created_at", "updated_at"):
        if out.get(key) is not None and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    return out


async def get_draft(pool: Any, draft_id: str, *, site_config: Any = None) -> dict[str, Any] | None:
    """One draft by id or 8+ character prefix; a stale ``working`` one reads as failed."""
    draft_id = (draft_id or "").strip().lower()
    if len(draft_id) < 8:
        return None
    record = await pool.fetchrow(
        "SELECT * FROM creative_drafts WHERE id::text LIKE $1 || '%' ORDER BY created_at DESC LIMIT 1",
        draft_id,
    )
    if record is None:
        return None
    row = _row(record)
    if row["status"] == "working" and site_config is not None:
        stale = int(float(_setting(site_config, "creative_draft_stale_minutes")))
        expired = await pool.fetchval(
            "SELECT updated_at < now() - make_interval(mins => $2) FROM creative_drafts WHERE id = $1::uuid",
            row["id"], stale,
        )
        if expired:
            await _finish(pool, row["id"], status="failed", error="interrupted: the worker restarted mid-draft")
            row.update(status="failed", error="interrupted: the worker restarted mid-draft")
    return row


async def list_drafts(pool: Any, conversation_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM creative_drafts WHERE conversation_id = $1::uuid ORDER BY created_at DESC LIMIT $2",
        conversation_id, limit,
    )
    return [_row(r) for r in rows]


async def _insert(
    pool: Any, *, kind: str, request: dict[str, Any], conversation_id: str | None,
    parent: dict[str, Any] | None,
) -> dict[str, Any]:
    record = await pool.fetchrow(
        """
        INSERT INTO creative_drafts (conversation_id, parent_id, kind, version, request)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb)
        RETURNING *
        """,
        conversation_id or None, parent["id"] if parent else None, kind,
        (int(parent["version"]) + 1) if parent else 1, json.dumps(request, default=str),
    )
    return _row(record)


async def _note(pool: Any, draft_id: str, note: str) -> None:
    await pool.execute(
        "UPDATE creative_drafts SET note = $2, updated_at = now() WHERE id = $1::uuid", draft_id, note,
    )


async def _finish(
    pool: Any, draft_id: str, *, status: str, payload: dict[str, Any] | None = None, error: str = "",
) -> None:
    await pool.execute(
        """
        UPDATE creative_drafts
           SET status = $2, payload = COALESCE($3::jsonb, payload), error = $4, note = '', updated_at = now()
         WHERE id = $1::uuid
        """,
        draft_id, status, json.dumps(payload, default=str) if payload is not None else None, error[:2000],
    )


async def _gpu_busy(pool: Any, site_config: Any) -> bool:
    patterns = [p.strip() for p in _setting(site_config, "creative_draft_gpu_busy_stages").split(",") if p.strip()]
    if not patterns:
        return False
    return bool(await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pipeline_tasks WHERE status = 'in_progress' AND stage LIKE ANY($1::text[]))",
        patterns,
    ))


async def _wait_for_gpu(pool: Any, draft_id: str, site_config: Any) -> None:
    """Hold a drawing draft while a video renders: both want the same card."""
    wait = float(_setting(site_config, "creative_draft_gpu_wait_minutes")) * 60
    poll = 30.0
    waited = 0.0
    while await _gpu_busy(pool, site_config):
        if waited >= wait:
            raise DraftError(f"a video render held the GPU for over {int(wait // 60)} minutes")
        # Refreshed every poll: the stale check reads updated_at.
        await _note(pool, draft_id, "waiting for a video render to finish before drawing")
        await asyncio.sleep(poll)
        waited += poll
    await _note(pool, draft_id, "")


async def _build(pool: Any, draft: dict[str, Any], parent: dict[str, Any] | None, site_config: Any) -> dict[str, Any]:
    request = draft["request"]
    instruction = str(request.get("instruction") or "")
    if draft["kind"] == "storyboard":
        from poindexter.services import storyboard as sb

        await _wait_for_gpu(pool, draft["id"], site_config)
        prefix = f"drafts/{draft['id']}"
        if parent is not None:
            return await sb.revise_storyboard(
                parent["payload"], instruction, site_config=site_config, pool=pool, upload_prefix=prefix,
            )
        script = await get_draft(pool, str(request["screenplay_draft"]), site_config=site_config)
        if script is None or script["kind"] != "screenplay" or script["status"] not in ("ready", "kept"):
            raise DraftError("the storyboard's script draft is gone or not ready")
        cast = None
        if request.get("cast_draft"):
            cast = await get_draft(pool, str(request["cast_draft"]), site_config=site_config)
        cast_payload = (cast or {}).get("payload") or {}
        return await sb.build_storyboard(
            script["payload"], scenes=list(request.get("scenes") or []), site_config=site_config,
            pool=pool, upload_prefix=prefix,
            style=str(request.get("style") or cast_payload.get("style") or ""),
            niche_slug=request.get("niche_slug"),
            cast_portraits=dict(cast_payload.get("cast_portraits") or {}),
        )
    if draft["kind"] == "screenplay":
        from poindexter.services import screenplay as sp

        if parent is not None:
            return await sp.revise_screenplay(
                parent["payload"], instruction, site_config=site_config, pool=pool,
            )
        return await sp.write_screenplay(sp.build_request(request, site_config), site_config=site_config, pool=pool)

    from poindexter.services import casting

    if parent is not None:
        design = await casting.revise_cast_design(
            parent["payload"], instruction, site_config=site_config, pool=pool,
        )
    else:
        design = await casting.design_cast(
            casting.build_casting_request(request, site_config), site_config=site_config, pool=pool,
        )
    await _wait_for_gpu(pool, draft["id"], site_config)
    return await casting.preview_cast(
        design, site_config=site_config, pool=pool, upload_prefix=f"drafts/{draft['id']}",
        previous=parent["payload"] if parent is not None else None,
    )


async def _run(pool: Any, draft: dict[str, Any], parent: dict[str, Any] | None, site_config: Any) -> None:
    try:
        payload = await _build(pool, draft, parent, site_config)
    except Exception as exc:  # noqa: BLE001 — every failure lands on the card, with its reason
        logger.warning("[creative_drafts] %s draft %s failed: %s", draft["kind"], draft["id"][:8], exc)
        await _finish(pool, draft["id"], status="failed", error=f"{type(exc).__name__}: {exc}")
        return
    await _finish(pool, draft["id"], status="ready", payload=payload)
    logger.info("[creative_drafts] %s draft %s v%s ready", draft["kind"], draft["id"][:8], draft["version"])


async def _check_storyboard_request(pool: Any, request: dict[str, Any], site_config: Any) -> None:
    """Fail in the chat turn on a storyboard that cannot be made."""
    from poindexter.services import storyboard as sb

    script = await get_draft(pool, str(request.get("screenplay_draft") or ""), site_config=site_config)
    if script is None or script["kind"] != "screenplay":
        raise DraftError("a storyboard needs a screenplay draft id (from a script draft card)")
    if script["status"] not in ("ready", "kept"):
        raise DraftError(f"script draft {script['id'][:8]} is {script['status']}; storyboard a ready script")
    try:
        sb.select_scenes(script["payload"], list(request.get("scenes") or []))
    except sb.StoryboardError as exc:
        raise DraftError(str(exc)) from exc
    request["screenplay_draft"] = script["id"]
    if request.get("cast_draft"):
        cast = await get_draft(pool, str(request["cast_draft"]), site_config=site_config)
        if cast is None or cast["kind"] != "cast":
            raise DraftError("cast_draft must be a cast draft id")
        request["cast_draft"] = cast["id"]


def _spawn(coro: Any) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _JOBS.add(task)
    task.add_done_callback(_JOBS.discard)


async def start_draft(
    pool: Any, *, kind: str, request: dict[str, Any], site_config: Any,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Insert a ``working`` draft and build it in the background; returns the row."""
    if kind not in KINDS:
        raise DraftError(f"kind must be one of {KINDS}, not {kind!r}")
    if kind == "storyboard":
        await _check_storyboard_request(pool, request, site_config)
    elif not str(request.get("topic") or "").strip():
        raise DraftError("a draft needs a topic: what the piece is about")
    if kind == "screenplay":
        # Fail now, in the chat turn, rather than in the background job.
        from poindexter.services import screenplay as sp

        try:
            sp.resolve_cast(site_config, sp.build_request(request, site_config).cast)
        except sp.ScreenplayError as exc:
            raise DraftError(str(exc)) from exc
    draft = await _insert(pool, kind=kind, request=request, conversation_id=conversation_id, parent=None)
    _spawn(_run(pool, draft, None, site_config))
    return draft


async def revise_draft(
    pool: Any, draft_id: str, instruction: str, *, site_config: Any,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Start the next version of a ready draft, addressing ``instruction``."""
    parent = await get_draft(pool, draft_id, site_config=site_config)
    if parent is None:
        raise DraftError(f"no draft {draft_id!r}")
    if parent["status"] not in ("ready", "kept"):
        raise DraftError(f"draft {parent['id'][:8]} is {parent['status']}; revise a ready draft")
    if not (instruction or "").strip():
        raise DraftError("say what to change")
    request = {**parent["request"], "instruction": instruction.strip()}
    draft = await _insert(
        pool, kind=parent["kind"], request=request,
        conversation_id=conversation_id or parent.get("conversation_id"), parent=parent,
    )
    _spawn(_run(pool, draft, parent, site_config))
    return draft


async def keep_draft(pool: Any, draft_id: str, *, site_config: Any) -> dict[str, Any]:
    """Make a ready draft real: a cast's personas are saved; a screenplay is marked kept."""
    draft = await get_draft(pool, draft_id, site_config=site_config)
    if draft is None:
        raise DraftError(f"no draft {draft_id!r}")
    if draft["status"] == "kept":
        return draft
    if draft["status"] != "ready":
        raise DraftError(f"draft {draft['id'][:8]} is {draft['status']}; only a ready draft can be kept")
    payload = draft["payload"]
    if draft["kind"] == "cast":
        from poindexter.services import casting

        payload = {**payload, "kept": await casting.keep_cast(payload, site_config=site_config, pool=pool)}
    await _finish(pool, draft["id"], status="kept", payload=payload)
    return {**draft, "status": "kept", "payload": payload}


async def discard_draft(pool: Any, draft_id: str, *, site_config: Any = None) -> dict[str, Any]:
    draft = await get_draft(pool, draft_id, site_config=site_config)
    if draft is None:
        raise DraftError(f"no draft {draft_id!r}")
    if draft["status"] == "kept":
        raise DraftError(f"draft {draft['id'][:8]} was already kept")
    await _finish(pool, draft["id"], status="discarded")
    return {**draft, "status": "discarded"}


# --- Rendering a draft (chat drafting, phase 3) --------------------------------

RENDERABLE = ("screenplay", "storyboard")


async def _download(url: str, dest: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code != 200 or not resp.content:
        raise DraftError(f"could not fetch the storyboard's dialogue track ({resp.status_code})")
    with open(dest, "wb") as fh:
        fh.write(resp.content)
    return dest


async def load_for_render(
    pool: Any, draft_id: str, *, site_config: Any, cast_draft: str | None = None,
) -> dict[str, Any]:
    """The pipeline state a draft hands the render (``story.load_draft``).

    A script draft supplies the screenplay; the render voices and directs it.
    A storyboard supplies all of that already done (screenplay, timeline,
    shot list and its full-quality dialogue track), so the video is the
    storyboard the operator approved, shot for shot. A cast draft, when
    named, supplies the art style and any per-piece portraits.
    """
    import tempfile

    draft = await get_draft(pool, draft_id, site_config=site_config)
    if draft is None or draft["kind"] not in RENDERABLE:
        raise DraftError(f"draft {draft_id!r} is not a script or storyboard draft")
    if draft["status"] not in ("ready", "kept"):
        raise DraftError(f"draft {draft['id'][:8]} is {draft['status']}; render a ready draft")
    payload = draft["payload"]
    cast = await get_draft(pool, cast_draft, site_config=site_config) if cast_draft else None
    cast_payload = (cast or {}).get("payload") or {}
    out: dict[str, Any] = {}
    if draft["kind"] == "screenplay":
        out["screenplay"] = payload
        if cast_payload.get("style"):
            out["screenplay_visual_style"] = cast_payload["style"]
        out["cast_portraits"] = dict(cast_payload.get("cast_portraits") or {})
        return out
    listing = payload["shot_list"]
    timeline = list(payload.get("timeline") or [])
    if not payload.get("audio_master_url"):
        raise DraftError("this storyboard predates its full-quality track; storyboard the scenes again")
    work = tempfile.mkdtemp(prefix="render-draft-")
    out.update({
        "screenplay": payload["screenplay"],
        "video_shot_list": listing,
        "dialogue_timeline": timeline,
        "video_long_script": payload.get("spoken_script") or "\n".join(
            str(t.get("text") or "") for t in timeline if t.get("kind") == "line"
        ),
        "screenplay_visual_style": str(payload.get("style") or ""),
        "cast_portraits": dict(listing.get("cast_portraits") or {}),
        "long_narration_audio_path": await _download(payload["audio_master_url"], os.path.join(work, "dialogue.wav")),
    })
    return out


_RENDER_TAIL = (
    ("transcribe", "media.transcribe_narration"),
    ("render", "media.render_long_video"),
    ("qa", "media.qa"),
    ("persist", "media.persist"),
)


def render_spec(
    kind: str, draft_id: str, *, cast_draft: str | None = None,
    setting_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A fixed plan from a draft to a video in the approval queue.

    A script draft is voiced and directed first; a storyboard already was.
    No architect is involved: the draft is the plan.
    """
    config: dict[str, Any] = {"creative_draft": draft_id}
    if cast_draft:
        config["cast_draft"] = cast_draft
    steps: list[tuple[str, str]] = [("load", "story.load_draft")]
    if kind == "screenplay":
        steps += [("voice", "media.render_dialogue"), ("direct", "media.direct_screenplay")]
    steps += list(_RENDER_TAIL)
    nodes = [{"id": nid, "atom": atom, **({"config": config} if nid == "load" else {})} for nid, atom in steps]
    edges = [{"from": a[0], "to": b[0]} for a, b in zip(steps, steps[1:], strict=False)]
    edges.append({"from": steps[-1][0], "to": "END"})
    spec: dict[str, Any] = {
        "name": f"render_draft_{draft_id[:8]}",
        "description": f"Render {kind} draft {draft_id[:8]} into a video for approval",
        "entry": "load",
        "nodes": nodes,
        "edges": edges,
    }
    if setting_overrides:
        spec["setting_overrides"] = dict(setting_overrides)
    return spec


async def start_render(
    pool: Any,
    db_service: Any,
    *,
    draft_id: str,
    site_config: Any,
    conversation_id: str,
    message_id: str,
    cast_draft: str | None = None,
    setting_overrides: dict[str, Any] | None = None,
    niche_slug: str | None = None,
) -> dict[str, Any]:
    """Queue the render of a ready script or storyboard draft; returns ``{task_id, plan_id, title}``."""
    from poindexter.services import chat_plans
    from poindexter.services.pipeline_architect import _validate_spec
    from poindexter.services.setting_overrides import check_overrides

    draft = await get_draft(pool, draft_id, site_config=site_config)
    if draft is None or draft["kind"] not in RENDERABLE:
        raise DraftError(f"draft {draft_id!r} is not a script or storyboard draft")
    if draft["status"] not in ("ready", "kept"):
        raise DraftError(f"draft {draft['id'][:8]} is {draft['status']}; render a ready draft")
    if draft["kind"] == "storyboard" and not (draft["payload"] or {}).get("audio_master_url"):
        raise DraftError("this storyboard predates its full-quality track; storyboard the scenes again")
    cast_id = None
    if cast_draft:
        cast = await get_draft(pool, cast_draft, site_config=site_config)
        if cast is None or cast["kind"] != "cast":
            raise DraftError("cast_draft must be a cast draft id")
        cast_id = cast["id"]
    problems = check_overrides(setting_overrides, site_config, where="render")
    if problems:
        raise DraftError("; ".join(problems))
    spec = render_spec(draft["kind"], draft["id"], cast_draft=cast_id, setting_overrides=setting_overrides)
    ok, errors = _validate_spec(spec)
    if not ok:
        raise DraftError("the render plan is invalid: " + "; ".join(errors[:3]))
    payload = draft["payload"] or {}
    title = str(payload.get("title") or (payload.get("screenplay") or {}).get("title") or "creative draft")
    card = await chat_plans.create_plan(
        pool, conversation_id=conversation_id, message_id=message_id,
        intent=f"Render {draft['kind']} draft {draft['id'][:8]}", topic=title, spec=spec,
    )
    run = await chat_plans.run_plan(
        pool=pool, db_service=db_service, plan_id=card["plan_id"],
        params={"niche_slug": niche_slug} if niche_slug else None,
    )
    return {"task_id": str(run.get("task_id") or ""), "plan_id": card["plan_id"], "title": title,
            "overrides": card.get("overrides") or []}


def describe(draft: dict[str, Any], *, max_chars: int = 6000) -> str:
    """A draft as text, for the chat model to read and discuss."""
    head = f"{draft['kind']} draft {draft['id'][:8]} v{draft['version']} ({draft['status']})"
    if draft["status"] == "working":
        return head + (f": {draft['note']}" if draft.get("note") else ": still being made")
    if draft["status"] == "failed":
        return f"{head}: {draft.get('error') or 'failed'}"
    payload = draft.get("payload") or {}
    lines = [head]
    if draft["kind"] == "storyboard":
        lines.append(f"storyboard of {payload.get('title', '')}, scenes {payload.get('scenes')}, style: {payload.get('style', '')}")
        for row in payload.get("shots") or []:
            drawn = "drawn" if row.get("image_url") else "not drawn"
            lines.append(
                f"  shot {row['idx']} ({row['kind']}, {row.get('duration_s')}s, {drawn}): "
                f"{row.get('words') or '-'} | picture: {str(row.get('picture') or '')[:160]}"
            )
    elif draft["kind"] == "cast":
        lines.append(f"style: {payload.get('style', '')}")
        for ch in payload.get("characters") or []:
            tag = "reused" if ch.get("reused") else "new"
            lines.append(
                f"- {ch.get('slug')} ({tag}): {ch.get('name')}, {ch.get('role') or ''}; looks: "
                f"{ch.get('appearance') or ''}; voice {ch.get('voice') or ''} ({ch.get('energy') or ''}); "
                f"says: {ch.get('sample_line') or ''}"
            )
        narrator = payload.get("narrator") or {}
        lines.append(f"narrator: {narrator.get('slug') or 'none'}")
    else:
        lines.append(f"{payload.get('title', '')}: {payload.get('logline', '')}")
        for scene in payload.get("scenes") or []:
            lines.append(f"[{scene.get('scene_id')}] {scene.get('setting', '')}")
            for beat in scene.get("beats") or []:
                if beat.get("kind") == "line":
                    lines.append(f"  {beat.get('speaker')}: {beat.get('text')}")
                else:
                    lines.append(f"  ({beat.get('text')})")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 20] + "\n... (truncated)"


def card(draft: dict[str, Any]) -> dict[str, Any]:
    """The chat card part for a draft; the console fetches the rest by id."""
    return {
        "kind": "draft",
        "draft_id": draft["id"],
        "draft_kind": draft["kind"],
        "version": draft["version"],
        "status": draft["status"],
    }


__all__ = [
    "DraftError",
    "KINDS",
    "card",
    "describe",
    "discard_draft",
    "get_draft",
    "keep_draft",
    "list_drafts",
    "load_for_render",
    "render_spec",
    "revise_draft",
    "start_render",
    "start_draft",
]
