"""Director's notes: a finished cartoon reviewed shot by shot, with fixes the chat can apply.

Chat drafting, phase 8. Matt on the Red Riding Hood render: "We're definitely
making headway but it's still pretty ai slop-like". Until now he found the
slop by watching. A render now gets notes the way a director gives them after
a screening:

1. **Look at every drawn shot** (:func:`look_at_shots`). A contact sheet of
   ``director_notes_frames_per_shot`` moments from the shot's window in the
   FINISHED video goes to the vision judge (``qa_vision_model``) with what the
   shot was meant to show (the picture, who speaks, the line), under the
   ``qa.director_shot`` prompt. The answer is countable checks, not a score:
   hands, face and mouth intact; strangers; garbled writing; does it move;
   does it show what it should. Code turns the failed checks into named
   flaws.
2. **Write the notes** (:func:`write_notes`). The writer reads the story, a
   table of every shot (its time, length, who, the line, the picture) with
   the judge's flaws, and returns a verdict, what works, and up to
   ``director_notes_max_notes`` ranked notes under ``story.director_notes``.
   A ``redo`` note names shots and says how to redraw them, which is exactly
   what a render revision (``render_revision``) takes; ``story``, ``pacing``
   and ``sound`` notes are for the next script. Code checks the shot numbers
   and that a redo names only drawn shots, with one repair.

The review is stored on the video's ``media_assets`` row
(``metadata.director_notes``) with a status, so the render card shows it and
the chat can read it. It runs as the last step of every cartoon render
(``media.director_notes``) and on demand from the chat or the card
(:func:`start_review`). Best-effort throughout: a review never fails a
render, and a shot the judge could not read is marked unread, not clean.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any

from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

#: ``media_assets.metadata`` key the review is stored under (on the ``video`` row).
NOTES_KEY = "director_notes"
NOTE_KINDS = ("redo", "story", "pacing", "sound")
_JOBS: set[asyncio.Task[Any]] = set()

#: How each kind of cut is described to the judge and the writer. A rough
#: cut is still pictures timed to the voices, so "nothing moves" is not a
#: flaw there and is never reported.
CUT_KINDS = {
    "final": "the finished cut",
    "rough": "a rough cut: still pictures timed to the voices and music, before anything is animated",
}

#: Judge check -> the flaw named when it fails.
_CHECKS = {
    "hands_ok": "hands",
    "face_ok": "face changes or warps",
    "mouth_ok": "mouth",
    "text_ok": "garbled writing",
    "moves_ok": "nothing moves",
    "matches": "does not show what it should",
}


class ReviewError(RuntimeError):
    """A render cannot be reviewed as asked. The message says why."""


def _setting(site_config: Any, key: str) -> str:
    from poindexter.services.settings_defaults import DEFAULTS

    value = site_config.get(key, None) if site_config is not None else None
    return str(DEFAULTS[key] if value is None or str(value).strip() == "" else value)


def _on(site_config: Any, key: str) -> bool:
    return _setting(site_config, key).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Looking at the shots
# ---------------------------------------------------------------------------


def parse_shot_look(text: str, *, rough: bool = False) -> dict[str, Any]:
    """The judge's reply as ``{read, flaws, strangers, note}``; ``read=False`` when unusable.

    ``rough``: the shot is a still in a rough cut, so it is meant not to move.
    """
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        parsed = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        return {"read": False, "flaws": [], "strangers": 0, "note": "unreadable judge reply"}
    flaws = [
        name for key, name in _CHECKS.items()
        if parsed.get(key) is False and not (rough and key == "moves_ok")
    ]
    strangers = parsed.get("strangers")
    strangers = strangers if isinstance(strangers, int) and not isinstance(strangers, bool) else 0
    if strangers > 0:
        flaws.append(f"{strangers} stranger(s) in frame")
    return {"read": True, "flaws": flaws, "strangers": strangers, "note": str(parsed.get("note") or "")[:240]}


def shot_window(row: dict[str, Any], scale: float) -> tuple[float, float]:
    """Where a planned shot sits in the finished video."""
    start = row["start_s"] * scale
    return start, start + max(0.1, row["duration_s"] * scale)


#: What a shot's line reads as when the render kept no dialogue timeline.
#: Not "no one speaks": the voices are there, only not written down here.
UNKNOWN_LINE = "(not known: this render kept no dialogue timeline)"


def shot_lines(shot_list: dict[str, Any], timeline: list[dict[str, Any]]) -> dict[int, str]:
    """The spoken line over each shot, from the shot itself or the dialogue timeline.

    With no timeline, a shot without its own line reads :data:`UNKNOWN_LINE`.
    """
    lines: dict[int, str] = {}
    for shot in shot_list.get("shots") or []:
        idx = shot.get("idx")
        if idx is None:
            continue
        text = str(shot.get("line") or shot.get("words") or "")
        if not text and timeline:
            start = float(shot.get("narration_offset_s") or 0.0)
            end = start + float(shot.get("duration_s") or 0.0)
            text = " ".join(
                f"{t.get('speaker')}: {t.get('text')}" for t in timeline
                if t.get("kind") == "line" and float(t.get("start_s") or 0) < end
                and float(t.get("end_s") or 0) > start
            )
        text = " ".join(text.split())[:240]
        lines[int(idx)] = text or ("" if timeline else UNKNOWN_LINE)
    return lines


async def look_at_shots(
    *, video_path: str, rows: list[dict[str, Any]], lines: dict[int, str], planned_s: float,
    site_config: Any, pool: Any, cut: str = "final",
) -> dict[int, dict[str, Any]]:
    """The judge's look at each drawn shot, by shot number."""
    from poindexter.services.prompt_manager import get_prompt_manager
    from poindexter.services.video_renderers import shot_vision_qa as sq

    model = str(site_config.get("qa_vision_model", "") or "").strip()
    if not model:
        raise ReviewError("no vision model is set (qa_vision_model)")
    duration = await sq._probe_seconds(video_path)
    scale = (duration / planned_s) if duration and planned_s > 0 else 1.0
    frames = max(1, int(float(_setting(site_config, "director_notes_frames_per_shot"))))
    crop = min(0.45, max(0.0, float(_setting(site_config, "director_notes_caption_crop"))))
    cap = max(1, int(float(_setting(site_config, "director_notes_max_shots"))))
    looks: dict[int, dict[str, Any]] = {}
    for row in rows[:cap]:
        start, end = shot_window(row, scale)
        times = [start + (end - start) * (i + 0.5) / frames for i in range(frames)]
        sheet = await sq.sheet_at(video_path, times, f"director{row['idx']}", crop_bottom=crop)
        if sheet is None:
            looks[row["idx"]] = {"read": False, "flaws": [], "strangers": 0, "note": "no frames"}
            continue
        prompt = get_prompt_manager().get_prompt(
            "qa.director_shot", frames=frames, who=row["who"], picture=row["picture"],
            line=lines.get(row["idx"]) or "(no one speaks)", cut=CUT_KINDS[cut],
        )
        text, why = await sq._vision_text(sheet, prompt=prompt, model=model, pool=pool, shot_idx=row["idx"])
        sq._remove_quietly(sheet)
        looks[row["idx"]] = parse_shot_look(text, rough=cut == "rough") if text else {
            "read": False, "flaws": [], "strangers": 0, "note": why,
        }
    return looks


# ---------------------------------------------------------------------------
# Writing the notes
# ---------------------------------------------------------------------------


def check_notes(
    reply: dict[str, Any], *, shot_ids: set[int], drawn_ids: set[int], max_notes: int,
) -> tuple[list[str], list[str]]:
    """``(hard, soft)`` FIX lines for a notes reply. Normalises ``reply`` in place."""
    hard: list[str] = []
    soft: list[str] = []
    reply["verdict"] = str(reply.get("verdict") or "").strip()
    if not reply["verdict"]:
        hard.append('FIX: add "verdict", the overall take in two or three sentences')
    strengths = reply.get("strengths")
    reply["strengths"] = [str(s).strip() for s in strengths if str(s).strip()] if isinstance(strengths, list) else []
    raw = reply.get("notes")
    if not isinstance(raw, list):
        hard.append('FIX: add "notes", a list (empty when nothing needs fixing)')
        raw = []
    notes: list[dict[str, Any]] = []
    for n, note in enumerate(raw, start=1):
        if not isinstance(note, dict):
            hard.append(f"FIX: note {n} must be an object")
            continue
        kind = str(note.get("kind") or "").strip().lower()
        if kind not in NOTE_KINDS:
            hard.append(f'FIX: note {n} "kind" must be one of {list(NOTE_KINDS)}')
        try:
            shots = sorted({int(str(s).strip()) for s in note.get("shots") or []})
        except ValueError:
            shots = []
            hard.append(f'FIX: note {n} "shots" must be shot numbers')
        unknown = [s for s in shots if s not in shot_ids]
        if unknown:
            hard.append(f"FIX: note {n} names shot(s) {unknown}, which are not in the table")
        if kind == "redo":
            if not shots:
                hard.append(f"FIX: note {n} is a redo; name the shots to redraw")
            undrawn = [s for s in shots if s in shot_ids and s not in drawn_ids]
            if undrawn:
                hard.append(f"FIX: note {n} redoes shot(s) {undrawn}, which are not drawn pictures; only drawn shots can be redrawn")
        problem = str(note.get("problem") or "").strip()
        fix = str(note.get("fix") or "").strip()
        if not problem or not fix:
            hard.append(f'FIX: note {n} needs both "problem" and "fix"')
        notes.append({"n": len(notes) + 1, "kind": kind, "shots": shots, "problem": problem, "fix": fix})
    if len(notes) > max_notes:
        soft.append(f"(kept the first {max_notes} of {len(notes)} notes)")
        notes = notes[:max_notes]
    reply["notes"] = notes
    return hard, soft


def shot_table(rows: list[dict[str, Any]], all_shots: list[dict[str, Any]], lines: dict[int, str],
               looks: dict[int, dict[str, Any]], scale: float) -> str:
    """One row per shot: number | starts | seconds | kind | who | line | picture | what the judge saw."""
    drawn = {r["idx"]: r for r in rows}
    out = []
    for shot in all_shots:
        idx = shot.get("idx")
        if idx is None:
            continue
        idx = int(idx)
        start = float(shot.get("narration_offset_s") or 0.0) * scale
        secs = float(shot.get("duration_s") or 0.0) * scale
        if idx in drawn:
            look = looks.get(idx)
            if look is None:
                seen = "not reviewed"
            elif not look["read"]:
                seen = f"could not be read ({look['note']})"
            else:
                seen = ("flaws: " + ", ".join(look["flaws"])) if look["flaws"] else "clean"
                if look["note"]:
                    seen += f" ({look['note']})"
            out.append(
                f"{idx} | {start:.1f}s | {secs:.1f}s | drawn | {drawn[idx]['who']} | "
                f"{lines.get(idx) or '-'} | {drawn[idx]['picture']} | {seen}"
            )
        else:
            out.append(f"{idx} | {start:.1f}s | {secs:.1f}s | {shot.get('source') or 'other'} | - | "
                       f"{lines.get(idx) or '-'} | - | not a drawn picture")
    return "\n".join(out)


async def write_notes(
    *, screenplay: dict[str, Any], story: str, table: str, style: str, shot_ids: set[int],
    drawn_ids: set[int], total_s: float, site_config: Any, pool: Any, task_id: str | None,
    cut: str = "final",
) -> dict[str, Any]:
    from poindexter.services.screenplay import ScreenplayError, _ask_validated, _resolve_model

    max_notes = max(1, int(float(_setting(site_config, "director_notes_max_notes"))))

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return check_notes(reply, shot_ids=shot_ids, drawn_ids=drawn_ids, max_notes=max_notes)

    try:
        reply, _ = await _ask_validated(
            "story.director_notes",
            {"title": screenplay.get("title") or "", "logline": screenplay.get("logline") or "",
             "style": style or "(as drawn)", "story": story, "shot_table": table,
             "total_seconds": round(total_s), "max_notes": max_notes, "cut": CUT_KINDS[cut]},
            _check, model=_resolve_model(site_config), site_config=site_config, pool=pool,
            task_id=task_id, what="director's notes", phase="story_director_notes",
        )
    except ScreenplayError as exc:
        raise ReviewError(str(exc)) from exc
    return {"verdict": reply["verdict"], "strengths": reply["strengths"], "notes": reply["notes"]}


# ---------------------------------------------------------------------------
# A whole review, stored on the video
# ---------------------------------------------------------------------------


async def review_cut(
    *, video_path: str, screenplay: dict[str, Any], shot_list: dict[str, Any],
    timeline: list[dict[str, Any]], site_config: Any, pool: Any, task_id: str | None = None,
    cut: str = "final",
) -> dict[str, Any]:
    """Look at every drawn shot of a cut, then write the notes. Returns the review (not stored)."""
    from poindexter.services import video_package as vp
    from poindexter.services.video_renderers import shot_vision_qa as sq

    rows = vp.shot_rows(shot_list)
    if not rows:
        raise ReviewError("the cut has no drawn shots to review")
    all_shots = [s for s in shot_list.get("shots") or [] if s.get("idx") is not None]
    planned = sum(float(s.get("duration_s") or 0) for s in all_shots)
    lines = shot_lines(shot_list, timeline)
    looks = await look_at_shots(
        video_path=video_path, rows=rows, lines=lines, planned_s=planned, site_config=site_config,
        pool=pool, cut=cut,
    )
    duration = await sq._probe_seconds(video_path) or planned
    scale = (duration / planned) if planned > 0 else 1.0
    story = vp.story_block(screenplay) if screenplay.get("scenes") else "\n".join(f"- {r['picture']}" for r in rows)
    notes = await write_notes(
        screenplay=screenplay, story=story, table=shot_table(rows, all_shots, lines, looks, scale),
        style=str(shot_list.get("style") or ""), shot_ids={int(s["idx"]) for s in all_shots},
        drawn_ids={r["idx"] for r in rows}, total_s=duration, site_config=site_config, pool=pool,
        task_id=task_id, cut=cut,
    )
    flawed = {idx: look["flaws"] for idx, look in looks.items() if look["read"] and look["flaws"]}
    return {
        **notes,
        "cut": cut,
        "title": screenplay.get("title") or "",
        "shots_reviewed": sum(1 for look in looks.values() if look["read"]),
        "shots_unread": sum(1 for look in looks.values() if not look["read"]),
        "flawed_shots": {str(k): v for k, v in sorted(flawed.items())},
    }


async def review_render(pool: Any, *, task_id: str, site_config: Any) -> dict[str, Any]:
    """Review a finished render's video. Returns the review (not stored)."""
    from poindexter.services import video_package as vp

    try:
        video = await vp.load_video(pool, task_id)
    except vp.PackageError as exc:
        raise ReviewError(str(exc)) from exc
    kept = vp._kept_render(task_id, site_config)
    screenplay, shot_list = vp._source(video, kept)
    if not vp.shot_rows(shot_list):
        raise ReviewError("the video kept no drawn shots to review")
    review = await review_cut(
        video_path=str(video.get("storage_path") or ""), screenplay=screenplay, shot_list=shot_list,
        timeline=list((kept or {}).get("timeline") or []), site_config=site_config, pool=pool, task_id=task_id,
    )
    return {**review, "task_id": task_id, "title": review["title"] or video.get("title") or ""}


async def _store(pool: Any, task_id: str, value: dict[str, Any]) -> None:
    """Set ``metadata.director_notes`` on the task's video row, leaving every other key alone."""
    await pool.execute(
        """
        UPDATE media_assets
           SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{director_notes}', $2::jsonb),
               updated_at = now()
         WHERE id = (SELECT id FROM media_assets WHERE task_id = $1 AND type = 'video' AND post_id IS NULL
                     ORDER BY created_at DESC LIMIT 1)
        """,
        task_id, json.dumps(value, default=str),
    )


async def get_review(pool: Any, *, task_id: str, site_config: Any) -> dict[str, Any] | None:
    """The stored review for a task's video; a stale ``working`` one reads as failed."""
    raw = await pool.fetchval(
        "SELECT metadata->'director_notes' FROM media_assets WHERE task_id = $1 AND type = 'video' "
        "AND post_id IS NULL ORDER BY created_at DESC LIMIT 1",
        task_id,
    )
    if raw is None:
        return None
    review = json.loads(raw) if isinstance(raw, str) else dict(raw)
    if review.get("status") == "working":
        stale = float(_setting(site_config, "director_notes_stale_minutes")) * 60
        try:
            started = datetime.fromisoformat(str(review.get("started_at")))
            age = (datetime.now(timezone.utc) - started).total_seconds()
        except ValueError:
            age = stale + 1
        if age > stale:
            review = {**review, "status": "failed", "error": "interrupted: the review stopped mid-way"}
    return review


async def run_review(pool: Any, *, task_id: str, site_config: Any) -> dict[str, Any]:
    """Review and store the result (``ready`` or ``failed``); never raises."""
    started = datetime.now(timezone.utc).isoformat()
    await _store(pool, task_id, {"status": "working", "started_at": started})
    try:
        review = await review_render(pool, task_id=task_id, site_config=site_config)
    except Exception as exc:  # noqa: BLE001 — every failure lands on the card, with its reason
        logger.warning("[director_notes] review of %s failed: %s", task_id, exc)
        failed = {"status": "failed", "started_at": started, "error": f"{type(exc).__name__}: {exc}"[:2000]}
        await _store(pool, task_id, failed)
        return failed
    done = {**review, "status": "ready", "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat()}
    await _store(pool, task_id, done)
    logger.info("[director_notes] %s: %d note(s), %d flawed shot(s)", task_id, len(review["notes"]),
                len(review["flawed_shots"]))
    return done


async def start_review(pool: Any, *, task_id: str, site_config: Any) -> dict[str, Any]:
    """Start a review in the background unless one is running; returns the stored state."""
    from poindexter.services import video_package as vp

    try:
        await vp.load_video(pool, task_id)
    except vp.PackageError as exc:
        raise ReviewError(str(exc)) from exc
    current = await get_review(pool, task_id=task_id, site_config=site_config)
    if current and current.get("status") == "working":
        return current
    state = {"status": "working", "started_at": datetime.now(timezone.utc).isoformat()}
    await _store(pool, task_id, state)
    job = asyncio.get_running_loop().create_task(run_review(pool, task_id=task_id, site_config=site_config))
    _JOBS.add(job)
    job.add_done_callback(_JOBS.discard)
    return state


def revision_instruction(review: dict[str, Any], picks: list[int]) -> str:
    """The note text a render revision takes, from the picked redo notes."""
    by_n = {int(n["n"]): n for n in review.get("notes") or []}
    missing = [p for p in picks if p not in by_n]
    if missing:
        raise ReviewError(f"no note(s) {missing}; the review has notes 1 to {len(by_n)}")
    chosen = [by_n[p] for p in picks]
    not_redo = [n["n"] for n in chosen if n["kind"] != "redo"]
    if not_redo:
        raise ReviewError(
            f"note(s) {not_redo} are about the story, pacing or sound, which a shot redo cannot fix; "
            "pick redo notes, or rework the script"
        )
    return "\n".join(
        f"Shot{'s' if len(n['shots']) > 1 else ''} {', '.join(str(s) for s in n['shots'])}: "
        f"{n['problem']} Fix: {n['fix']}"
        for n in chosen
    )


def describe(review: dict[str, Any] | None) -> str:
    """A review as text, for the chat model to read and discuss."""
    if not review:
        return "No director's notes yet."
    status = review.get("status")
    if status == "working":
        return "Director's notes are being written (the judge looks at every shot; a few minutes)."
    if status == "failed":
        return f"The review failed: {review.get('error') or 'unknown'}"
    lines = [f"Director's notes for {review.get('title') or review.get('task_id')}: {review.get('verdict')}"]
    if review.get("strengths"):
        lines.append("Works: " + "; ".join(review["strengths"]))
    for note in review.get("notes") or []:
        shots = ", ".join(str(s) for s in note["shots"]) or "-"
        lines.append(f"{note['n']}. [{note['kind']}] shots {shots}: {note['problem']} Fix: {note['fix']}")
    lines.append(f"({review.get('shots_reviewed', 0)} shots reviewed, {review.get('shots_unread', 0)} unread)")
    return "\n".join(lines)


__all__ = [
    "CUT_KINDS",
    "NOTES_KEY",
    "NOTE_KINDS",
    "ReviewError",
    "check_notes",
    "describe",
    "get_review",
    "look_at_shots",
    "parse_shot_look",
    "review_cut",
    "review_render",
    "revision_instruction",
    "run_review",
    "shot_lines",
    "shot_table",
    "shot_window",
    "start_review",
    "write_notes",
]
