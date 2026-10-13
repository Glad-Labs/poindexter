"""Screenplay director — a voiced screenplay in, a renderable shot list out.

Creative studio phase 4 (poindexter#1138; epic poindexter#1134). The
``media.direct_screenplay`` atom delegates here.

Timing is code. The shots tile the measured dialogue track
(``media.render_dialogue``'s timeline) exactly, so every lip-sync window lands
on the words it belongs to:

- A cast member's line becomes a **talking shot** (``presenter`` + ``speaker``)
  when that persona has a portrait. One S2V chunk is the longest seamless
  take (``presenter_window_budget_s``), so a longer line keeps its first part
  on the speaker's face and the rest becomes a **cutaway** of the scene with
  the voice continuing over it.
- Narrator lines, lines by a character without a portrait, and ``action``
  beats become stills of the scene: ``generative`` (animated, within
  ``video_hero_shots_max``) or Ken Burns / static stills, alternated so the
  schema's pacing rule (no source three times running) holds.

Visuals are LLM. One call (``story.shot_visuals``) writes a picture prompt
and a motion line for every non-talking shot from the scene setting and the
beat. If that call fails or skips a shot, the shot falls back to its setting
plus beat text, so a render never stalls on it. Code then puts the visual
style in front, and the renderer adds each character's appearance
(``character_prompt``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

DIRECTOR_PROMPT_VERSION = "screenplay-v1"
_STILL_SOURCES = ("image_kenburns", "image_gen")


class DirectorError(RuntimeError):
    """The shot list could not be built. The message names why."""


def _segments(
    timeline: list[dict[str, Any]], track_seconds: float,
) -> list[dict[str, Any]]:
    """Each beat's span on the track: from its start to the next beat's start
    (absorbing the pause after it), the last running to the end of the track."""
    beats = sorted(timeline, key=lambda b: float(b["start_s"]))
    out: list[dict[str, Any]] = []
    for i, beat in enumerate(beats):
        start = 0.0 if i == 0 else float(beat["start_s"])
        end = float(beats[i + 1]["start_s"]) if i + 1 < len(beats) else track_seconds
        if end - start > 1e-3:
            out.append({**beat, "start_s": start, "end_s": end})
    return out


def _split(start: float, length: float, max_len: float) -> list[tuple[float, float]]:
    """``length`` seconds from ``start`` in pieces no longer than ``max_len``,
    as even as possible."""
    if length <= max_len:
        return [(start, length)]
    n = int(length // max_len) + (1 if length % max_len > 1e-6 else 0)
    piece = length / n
    return [(start + k * piece, piece) for k in range(n)]


def plan_shots(
    screenplay: dict[str, Any],
    timeline: list[dict[str, Any]],
    track_seconds: float,
    site_config: Any,
) -> list[dict[str, Any]]:
    """The shot plan, before visuals: kind, timing, speaker, characters."""
    from poindexter.services.persona_service import get_persona
    from poindexter.services.video_renderers.shot_list_renderer import (
        presenter_window_budget_s,
    )

    if not timeline:
        raise DirectorError("the dialogue timeline is empty — run media.render_dialogue first")
    cast = set(screenplay.get("cast") or [])
    scenes = {s.get("scene_id"): s for s in screenplay.get("scenes") or []}
    talk_budget = presenter_window_budget_s(site_config) or 4.6
    max_shot = site_config.get_float("story_max_shot_seconds", 6.0)
    max_talking = site_config.get_int("story_max_talking_shots", -1)
    talkable = {
        slug for slug in cast
        if (p := get_persona(site_config, slug)) is not None and p.enabled and p.has_portrait
    }

    plan: list[dict[str, Any]] = []
    talking = 0
    for seg in _segments(timeline, track_seconds):
        scene = scenes.get(seg.get("scene_id")) or {}
        setting = str(scene.get("setting") or "")
        speaker = seg.get("speaker")
        base = {
            "scene_id": seg.get("scene_id"), "setting": setting,
            "beat_text": seg.get("text", ""), "beat_kind": seg.get("kind"),
            "delivery": "",
        }
        length = seg["end_s"] - seg["start_s"]
        if seg.get("kind") == "line" and speaker in talkable and (
            max_talking < 0 or talking < max_talking
        ):
            delivery = _delivery_for(screenplay, seg)
            talk_len = min(length, talk_budget)
            plan.append({**base, "kind": "talk", "start_s": seg["start_s"], "length": talk_len,
                         "speaker": speaker, "characters": [speaker], "delivery": delivery})
            talking += 1
            rest = length - talk_len
            for start, piece in _split(seg["start_s"] + talk_len, rest, max_shot) if rest > 1e-3 else []:
                plan.append({**base, "kind": "cutaway", "start_s": start, "length": piece,
                             "speaker": None, "characters": [speaker]})
            continue
        if seg.get("kind") == "action":
            chars = [c for c in seg.get("characters") or [] if c in cast]
        elif speaker in cast:
            chars = [speaker]
        else:
            chars = [c for c in scene.get("characters") or [] if c in cast]
        for start, piece in _split(seg["start_s"], length, max_shot):
            plan.append({**base, "kind": "still", "start_s": start, "length": piece,
                         "speaker": None, "characters": chars})
    return _merge_slivers(plan, site_config)


def _delivery_for(screenplay: dict[str, Any], seg: dict[str, Any]) -> str:
    """The screenplay's delivery note for a timeline line, matched by text."""
    for scene in screenplay.get("scenes") or []:
        if scene.get("scene_id") != seg.get("scene_id"):
            continue
        for beat in scene.get("beats") or []:
            if beat.get("kind") == "line" and beat.get("text") == seg.get("text"):
                return str(beat.get("delivery") or "")
    return ""


def _merge_slivers(plan: list[dict[str, Any]], site_config: Any) -> list[dict[str, Any]]:
    """Fold any non-talking shot shorter than ``story_min_shot_seconds`` into a
    non-talking neighbour (the previous one, else the next). A talking shot is
    never lengthened: it is already at most one seamless S2V chunk. Shots stay
    contiguous on the track either way."""
    floor = site_config.get_float("story_min_shot_seconds", 0.8)
    out: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None  # a sliver waiting for a next shot
    for shot in plan:
        if shot["kind"] != "talk" and shot["length"] < floor:
            if out and out[-1]["kind"] != "talk" and pending is None:
                out[-1] = {**out[-1], "length": out[-1]["length"] + shot["length"]}
            elif pending is None:
                pending = dict(shot)
            else:
                pending["length"] += shot["length"]
            continue
        if pending is not None:
            if shot["kind"] != "talk":
                shot = {**shot, "start_s": pending["start_s"], "length": shot["length"] + pending["length"]}
            else:
                out.append(pending)  # between two talking shots: it stays a short shot
            pending = None
        out.append(shot)
    if pending is not None:
        if out and out[-1]["kind"] != "talk":
            out[-1] = {**out[-1], "length": out[-1]["length"] + pending["length"]}
        else:
            out.append(pending)
    return out


def assign_sources(plan: list[dict[str, Any]], site_config: Any) -> list[dict[str, Any]]:
    """Pick each shot's source. Talking shots are ``presenter``; a third
    consecutive talking shot by one speaker becomes a cutaway (the schema
    allows two). Stills animate (``generative``) within the hero budget,
    longest first; the rest alternate Ken Burns / static so no source runs
    three times."""
    hero_left = site_config.get_int("video_hero_shots_max", 3)
    hero_floor = site_config.get_float("story_min_animated_shot_seconds", 2.5)
    candidates = sorted(
        (i for i, s in enumerate(plan) if s["kind"] in ("still", "cutaway") and s["length"] >= hero_floor),
        key=lambda i: (-plan[i]["length"], i),
    )
    animate = set(candidates[: max(0, hero_left)]) if hero_left >= 0 else set(candidates)
    out: list[dict[str, Any]] = []
    history: list[tuple[str, str | None]] = []

    def _streak_ok(key: tuple[str, str | None]) -> bool:
        return not (len(history) >= 2 and history[-1] == key and history[-2] == key)

    for i, shot in enumerate(plan):
        shot = dict(shot)
        if shot["kind"] == "talk":
            key = ("presenter", shot["speaker"])
            if _streak_ok(key):
                shot["source"] = "presenter"
                out.append(shot)
                history.append(key)
                continue
            shot.update(kind="cutaway", speaker=None)
        options = (["generative"] if i in animate else []) + list(_STILL_SOURCES)
        for source in options:
            if _streak_ok((source, None)):
                shot["source"] = source
                break
        out.append(shot)
        history.append((shot["source"], None))
    return out


async def write_visuals(
    plan: list[dict[str, Any]],
    screenplay: dict[str, Any],
    *,
    site_config: Any,
    pool: Any = None,
    task_id: str | None = None,
) -> tuple[dict[int, dict[str, str]], str]:
    """One LLM call: picture + motion for every non-talking shot.

    Returns ``({shot_index: {"prompt", "motion"}}, model)``. Any failure
    returns what it has (possibly nothing): the caller falls back per shot.
    """
    from poindexter.services.llm_text import ollama_chat_text
    from poindexter.services.prompt_manager import get_prompt_manager
    from poindexter.utils.json_extract import extract_json_object

    wanted = [i for i, s in enumerate(plan) if s["source"] != "presenter"]
    model = str(site_config.get("story_visuals_model", "") or "").strip()
    if not model:
        from poindexter.services.llm_text import resolve_local_writer_model

        model = resolve_local_writer_model(site_config=site_config)
    if not wanted:
        return {}, model
    rows = "\n".join(
        f"- id {i}: scene: {plan[i]['setting']} | happening: {plan[i]['beat_text']} | "
        f"visible: {', '.join(plan[i]['characters']) or 'no characters'} | "
        f"{'animated' if plan[i]['source'] == 'generative' else 'still'}"
        for i in wanted
    )
    prompt = get_prompt_manager().get_prompt(
        "story.shot_visuals",
        title=str(screenplay.get("title") or ""),
        logline=str(screenplay.get("logline") or ""),
        shot_rows=rows,
    )
    try:
        raw = await ollama_chat_text(
            prompt, model=model, site_config=site_config, pool=pool, task_id=task_id,
            phase="story_shot_visuals", timeout_setting="story_writer_timeout_seconds",
            timeout_default=300.0, think=False, response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 — fallback prompts cover every shot
        logger.warning(
            "[director] shot-visuals call failed (%s: %s); every shot uses its "
            "scene setting", type(exc).__name__, exc,
        )
        return {}, model
    blob = extract_json_object(raw or "") or {}
    out: dict[int, dict[str, str]] = {}
    for item in blob.get("shots") or [] if isinstance(blob, dict) else []:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        try:
            idx = int(raw_id) if raw_id is not None else -1
        except (TypeError, ValueError):
            continue
        picture = str(item.get("prompt") or "").strip()
        if idx in wanted and picture:
            out[idx] = {"prompt": picture, "motion": str(item.get("motion") or "").strip()}
    missing = len(wanted) - len(out)
    if missing:
        logger.info("[director] shot-visuals skipped %d of %d shots; they use the scene setting",
                    missing, len(wanted))
    return out, model


def _talking_frame_prompt(shot: dict[str, Any], style: str, site_config: Any) -> str:
    """The cast shot's first frame: the speaker in this scene, in this style.

    The renderer draws it from the speaker's portrait (reference-conditioned)
    and lip-syncs it, so the talking shot matches the stills around it
    (poindexter#1141). Wording is ``story_talking_frame_template``.
    """
    from poindexter.services.persona_service import get_persona

    persona = get_persona(site_config, shot["speaker"]) if site_config is not None else None
    name = (getattr(persona, "display_name", "") or shot["speaker"]) if persona else shot["speaker"]
    from poindexter.services.settings_defaults import DEFAULTS

    template = str(
        (site_config.get("story_talking_frame_template", None) if site_config is not None else None)
        or DEFAULTS["story_talking_frame_template"]
    )
    text = (
        template.replace("{style}", style or "")
        .replace("{name}", name)
        .replace("{delivery}", shot.get("delivery") or "expressive")
        .replace("{setting}", shot.get("setting") or "the scene")
    )
    return text.strip(" ,")[:1500]


def _talking_intent(shot: dict[str, Any], site_config: Any) -> str:
    """What a talking shot should show: who (with their looks) and where."""
    from poindexter.services.persona_service import get_persona

    persona = get_persona(site_config, shot["speaker"]) if site_config is not None else None
    who = shot["speaker"]
    if persona is not None:
        name = persona.display_name or shot["speaker"]
        looks = persona.visual_description
        who = f"{name}, {looks}," if looks else name
    where = f" in {shot['setting']}" if shot.get("setting") else ""
    return f"{who} talking{where}"[:200]


def build_shot_list(
    plan: list[dict[str, Any]],
    visuals: dict[int, dict[str, str]],
    *,
    style: str,
    director_model: str,
    site_config: Any = None,
) -> dict[str, Any]:
    """Assemble and validate the ``VideoShotList``. Raises DirectorError."""
    from pydantic import ValidationError

    from poindexter.schemas.video_shot_list import MAX_SHOTS, VideoShotList

    if len(plan) > MAX_SHOTS:
        raise DirectorError(
            f"{len(plan)} shots exceed the {MAX_SHOTS}-shot cap — shorten the "
            "screenplay or raise story_max_shot_seconds",
        )
    shots: list[dict[str, Any]] = []
    for i, s in enumerate(plan):
        duration = round(s["length"], 3)
        shot: dict[str, Any] = {
            "idx": i, "duration_s": duration, "source": s["source"],
            "narration_offset_s": round(s["start_s"], 3), "characters": list(s["characters"]),
        }
        # ``intent`` is what the per-shot vision QA grades the frame against,
        # so it describes the PICTURE, never the spoken line: graded against
        # dialogue text, a correct frame of a character talking scored under
        # threshold and was swapped for a held frame (2026-10-12 test, shot 12).
        if s["source"] == "presenter":
            shot["speaker"] = s["speaker"]
            # The judge must know what the speaker LOOKS like to catch a wrong
            # face: S2V handed a smeared frame of a robot owl invented a human
            # woman, and "nova talking" alone cannot flag that (2026-10-13).
            shot["intent"] = _talking_intent(s, site_config)
            note = "; ".join(p for p in (s.get("delivery"), s.get("setting")) if p)
            if note:
                shot["prompt"] = note[:2000]
            shot["frame_prompt"] = _talking_frame_prompt(s, style, site_config)
        else:
            seen = visuals.get(i) or {}
            picture = seen.get("prompt") or f"{s['setting']}. {s['beat_text']}".strip(". ")
            shot["intent"] = picture[:200] or "scene"
            shot["prompt"] = (f"{style}, {picture}" if style else picture)[:1500]
            if s["source"] == "generative":
                shot["motion"] = (seen.get("motion") or "gentle camera push-in, subtle character motion")[:500]
        shots.append(shot)
    try:
        listing = VideoShotList.model_validate({
            "aspect": "16:9",
            # The shots tile the measured dialogue track: the renderer must not
            # re-fit them (see VideoShotList.timing).
            "timing": "exact",
            "total_duration_s": round(sum(s["duration_s"] for s in shots), 3),
            "shots": shots,
            "director_model": director_model,
            "director_prompt_version": DIRECTOR_PROMPT_VERSION,
            "director_decided_at": datetime.now(timezone.utc),
        })
    except ValidationError as exc:
        raise DirectorError(f"the planned shot list does not validate: {exc}") from exc
    return listing.model_dump(mode="json")


async def direct_screenplay(
    screenplay: dict[str, Any],
    timeline: list[dict[str, Any]],
    track_seconds: float,
    *,
    site_config: Any,
    style: str,
    pool: Any = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    plan = assign_sources(plan_shots(screenplay, timeline, track_seconds, site_config), site_config)
    visuals, model = await write_visuals(plan, screenplay, site_config=site_config, pool=pool, task_id=task_id)
    listing = build_shot_list(plan, visuals, style=style, director_model=model, site_config=site_config)
    counts: dict[str, int] = {}
    for s in listing["shots"]:
        counts[s["source"]] = counts.get(s["source"], 0) + 1
    logger.info(
        "[director] %d shots over %.1fs: %s", len(listing["shots"]),
        listing["total_duration_s"], counts,
    )
    return listing


__all__ = [
    "DIRECTOR_PROMPT_VERSION",
    "DirectorError",
    "assign_sources",
    "build_shot_list",
    "direct_screenplay",
    "plan_shots",
    "write_visuals",
]
