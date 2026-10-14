"""Screenplay director — a voiced screenplay in, a renderable shot list out.

Creative studio phase 4 (poindexter#1138; epic poindexter#1134). The
``media.direct_screenplay`` atom delegates here.

Timing is code. The shots tile the measured dialogue track
(``media.render_dialogue``'s timeline) exactly, so every lip-sync window lands
on the words it belongs to:

- A cast member's line becomes a **talking shot** (``presenter`` + ``speaker``)
  when that persona has a portrait. One S2V chunk is the longest seamless
  take (``presenter_window_budget_s``), so a longer line keeps its first part
  on the speaker's face and the rest becomes a **reaction shot**: the scene's
  other characters listening, with the voice continuing over it.
- Narrator lines, lines by a character without a portrait, and ``action``
  beats become stills of the scene: ``generative`` (animated, within
  ``story_max_animated_shots``, every one by default) or Ken Burns / static stills, alternated so the
  schema's pacing rule (no source three times running) holds.

**Coverage, not illustration** (2026-10-13). The first two-character cartoon
cut talk, 1.5 s picture, talk, 1.5 s picture: 16 of its 19 non-talking shots
were action-beat pauses, each drawn new, and the illustrator was given the
spoken line as "what is happening". A line that said "think of the model as a
pot of soup" got a picture of soup. Matt: the stills "are kinda off-topic or
not really a continuation of the previous scene". So:

- A non-talking shot shorter than ``story_min_insert_seconds`` is the scene's
  **master shot** (``kind="master"``): one wide picture of the scene's
  characters in its setting (``story_scene_master_template``), drawn once
  per scene and reused, moving differently each time.
- The rest of a long line is a **reaction** shot of whoever is listening.
- Only the remaining inserts and reactions go to the illustrator
  (``story.shot_visuals``), which is told each picture is the next moment of
  the same scene and that analogies stay spoken.

If that call fails or skips a shot, the shot falls back to its setting plus
beat text, so a render never stalls on it. Code then puts the visual style in
front, and the renderer adds each character's appearance
(``character_prompt``).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

DIRECTOR_PROMPT_VERSION = "screenplay-v2"
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
    # The story's locations (screenplay "locations"): a place the story
    # returns to shares one background plate across its scenes.
    places = {str(loc.get("id")): str(loc.get("description") or "") for loc in screenplay.get("locations") or []}
    talk_budget = presenter_window_budget_s(site_config) or 4.6
    max_shot = site_config.get_float("story_max_shot_seconds", 6.0)
    # A line longer than one talking shot leaves a remainder for a cutaway.
    # A remainder shorter than this is a flash (0.1 s in the Midnight Patch
    # rough cut, 2026-10-14), so the talking shot gives up enough of the line
    # to make the cutaway at least this long.
    min_reaction = site_config.get_float("story_min_reaction_seconds", 1.8)
    max_talking = site_config.get_int("story_max_talking_shots", -1)
    talkable = {
        slug for slug in cast
        if (p := get_persona(site_config, slug)) is not None and p.enabled and p.has_portrait
    }
    # Only a character with a portrait can be drawn, so only they are on
    # screen. A voice-only narrator (the cyberpunk Cinderella's Storyteller)
    # put in a picture has nothing to be drawn from: the still fell back to
    # the text-to-image path, that failed, and the slot became stock footage.
    cast = talkable

    plan: list[dict[str, Any]] = []
    talking = 0
    # Each speaker's talking shots rotate through story_talking_framings, so
    # one character never gets the same portrait twice running (Matt,
    # 2026-10-14: "It feels flat just going from portrait to portrait").
    lines_by: dict[str, int] = {}
    segments = _segments(timeline, track_seconds)
    # Who is in the scene right now: a character who enters partway (an
    # action's "enters") is out of the wide shots until then, and one who
    # exits is out after it. Mara stood in every wide of Midnight Patch,
    # from the first second, before her ring summoned her (2026-10-14).
    entering: dict[Any, set[str]] = {}
    for seg in segments:
        if seg.get("kind") == "action":
            entering.setdefault(seg.get("scene_id"), set()).update(seg.get("enters") or [])
    away: dict[Any, set[str]] = {}
    # Each scene opens on a landscape that holds long enough to show where we
    # are (story_establishing_seconds; 0 = off), under the start of its first
    # beat: the wider world the place sits in, with nobody in it, not the
    # background the characters stand in front of (Matt, 2026-10-14).
    # Midnight Patch's wides were 1.5-1.8 s flashes between close-ups, too
    # short to give the scene any geography.
    establish = site_config.get_float("story_establishing_seconds", 3.0)
    established: set[Any] = set()
    for seg in segments:
        scene = scenes.get(seg.get("scene_id")) or {}
        setting = str(scene.get("setting") or "")
        speaker = seg.get("speaker")
        gone = away.setdefault(seg.get("scene_id"), set(entering.get(seg.get("scene_id"), set())))
        if seg.get("kind") == "action":
            gone.difference_update(seg.get("enters") or [])
        scene_cast = [c for c in scene.get("characters") or [] if c in cast and c not in gone]
        base = {
            "scene_id": seg.get("scene_id"), "setting": setting,
            "location": str(scene.get("location") or seg.get("scene_id") or ""),
            "location_desc": places.get(str(scene.get("location") or "")) or setting,
            # The scene's colour-script entry (screenplay ``palette``).
            "palette": str(scene.get("palette") or "").strip(),
            "beat_text": seg.get("text", ""), "beat_kind": seg.get("kind"),
            "delivery": "", "scene_cast": scene_cast, "line_speaker": speaker,
            # An action beat is a moment to see, never folded into the scene's
            # master shot (_mark_masters): Midnight Patch's payoff (Mara's
            # spell turning the jumpsuit into a gown, the hover-coach landing)
            # was a 1.5 s still of the same wide as the rest (2026-10-14).
            "moment": seg.get("kind") == "action",
        }
        length = seg["end_s"] - seg["start_s"]
        # A scene that opens on an action shows the action, not a landscape
        # over it.
        if establish > 0 and seg.get("scene_id") not in established and not base["moment"]:
            established.add(seg.get("scene_id"))
            # A beat too short to share keeps the wide for all of it.
            held = establish if length >= establish + min_reaction else length
            plan.append({**base, "kind": "landscape", "start_s": seg["start_s"], "length": held,
                         "speaker": None, "characters": []})
            if held >= length - 1e-3:
                gone.update(seg.get("exits") or [] if seg.get("kind") == "action" else [])
                continue
            seg = {**seg, "start_s": seg["start_s"] + held}
            length -= held
        established.add(seg.get("scene_id"))
        if seg.get("kind") == "line" and speaker in talkable and (
            max_talking < 0 or talking < max_talking
        ):
            delivery = _delivery_for(screenplay, seg)
            talk_len = min(length, talk_budget)
            if 1e-3 < length - talk_len < min_reaction:
                talk_len = max(min(talk_len, length - min_reaction), length / 2)
            plan.append({**base, "kind": "talk", "start_s": seg["start_s"], "length": talk_len,
                         "speaker": speaker, "characters": [speaker], "delivery": delivery,
                         "framing_idx": lines_by.get(str(speaker), 0)})
            lines_by[str(speaker)] = lines_by.get(str(speaker), 0) + 1
            talking += 1
            rest = length - talk_len
            for start, piece in _split(seg["start_s"] + talk_len, rest, max_shot) if rest > 1e-3 else []:
                plan.append({**base, "kind": "reaction", "start_s": start, "length": piece,
                             "speaker": None, "characters": _listeners(scene_cast, speaker)})
            continue
        if seg.get("kind") == "action":
            chars = [c for c in seg.get("characters") or [] if c in cast]
        elif speaker in cast:
            chars = [speaker]
        else:
            chars = list(scene_cast)
        for start, piece in _split(seg["start_s"], length, max_shot):
            plan.append({**base, "kind": "still", "start_s": start, "length": piece,
                         "speaker": None, "characters": chars})
        if seg.get("kind") == "action":
            # The exit is seen in its own shot; the character is gone after it.
            gone.update(seg.get("exits") or [])
    return _mark_masters(_merge_slivers(plan, site_config), site_config)


def _listeners(scene_cast: list[str], speaker: str | None) -> list[str]:
    """Who a reaction shot shows: the scene's other characters, else the speaker."""
    others = [c for c in scene_cast if c != speaker]
    return others or ([speaker] if speaker else [])


def _mark_masters(plan: list[dict[str, Any]], site_config: Any) -> list[dict[str, Any]]:
    """A still too short to establish anything of its own becomes the scene's
    master shot (``kind="master"``), which shows the scene's whole cast. A
    reaction stays a cutaway to whoever is listening: as masters, Midnight
    Patch's reactions were the same wide five times over (2026-10-14). An
    entrance or exit (``moment``) stays its own shot, pictured from its
    action. Lengths are final here (after ``_merge_slivers``)."""
    floor = site_config.get_float("story_min_insert_seconds", 2.5)
    out: list[dict[str, Any]] = []
    for shot in plan:
        if shot["kind"] == "still" and shot["length"] < floor and not shot.get("moment"):
            shot = {**shot, "kind": "master",
                    "characters": list(shot.get("scene_cast") or shot["characters"])}
        out.append(shot)
    return out


def _scene_master_prompt(shot: dict[str, Any], site_config: Any) -> str:
    """The one picture every master shot of a scene shows."""
    from poindexter.services.persona_service import get_persona
    from poindexter.services.settings_defaults import DEFAULTS

    names = []
    for slug in shot.get("characters") or []:
        persona = get_persona(site_config, slug) if site_config is not None else None
        names.append((getattr(persona, "display_name", "") or slug) if persona else slug)
    who = " and ".join(names) if names else "the scene, with nobody in it"
    template = str(
        (site_config.get("story_scene_master_template", None) if site_config is not None else None)
        or DEFAULTS["story_scene_master_template"]
    )
    return (
        template.replace("{names}", who).replace("{setting}", shot.get("setting") or "the scene")
    ).strip(" ,")[:1500]


def _landscape_prompt(shot: dict[str, Any], site_config: Any) -> str:
    """A scene's opening landscape: the wider world its place sits in, nobody in it."""
    from poindexter.services.settings_defaults import DEFAULTS

    template = str(
        (site_config.get("story_landscape_template", None) if site_config is not None else None)
        or DEFAULTS["story_landscape_template"]
    )
    place = shot.get("location_desc") or shot.get("setting") or "the scene"
    return template.replace("{place}", place).strip(" ,")[:1200]


def _place_prompt(setting: str, style: str, site_config: Any) -> str:
    """The scene's empty background plate: its place with nobody in it."""
    from poindexter.services.settings_defaults import DEFAULTS

    template = str(
        (site_config.get("story_place_plate_template", None) if site_config is not None else None)
        or DEFAULTS["story_place_plate_template"]
    )
    return (
        template.replace("{style}", style or "").replace("{setting}", setting or "the scene")
    ).strip(" ,")[:1500]


def _with_palette(text: str, palette: str, site_config: Any, limit: int) -> str:
    """``text`` painted in the scene's colour-script ``palette`` (``story_palette_template``).

    The palette goes last and always fits: the picture is cut to make room.
    """
    from poindexter.services.settings_defaults import DEFAULTS

    if not palette or not text:
        return text[:limit]
    template = str(
        (site_config.get("story_palette_template", None) if site_config is not None else None)
        or DEFAULTS["story_palette_template"]
    )
    line = template.replace("{palette}", palette.strip().rstrip("."))[: limit // 3].strip()
    return f"{text[: limit - len(line) - 1].rstrip()} {line}"


def _mark_establishing(
    shots: list[dict[str, Any]], plan: list[dict[str, Any]], place_prompts: Any = None,
) -> None:
    """One establishing picture per location: its first master shot, else its first still.

    The renderer draws every other picture of the scene with the establishing
    one as a place reference, so a scene keeps one background. Matt on scene 2
    of the first cartoon: the talking shots each had their own swirl backdrop.
    The establishing shot also carries ``place_prompt``, the scene's empty
    background plate: drawn with nobody in it, it is the place reference
    instead, so no character's features ride along on it (Mara's wings were
    stuck on the buildings behind Cinder and Bolt, 2026-10-14).
    """
    chosen: dict[str, int] = {}
    for i, (shot, s) in enumerate(zip(shots, plan, strict=False)):
        # One plate per LOCATION: a place the story returns to in a later
        # scene keeps its background (a studio's background design).
        place = shot.get("location_id") or shot.get("scene_id")
        if not place or shot["source"] == "presenter" or s["kind"] == "landscape":
            continue
        if s["kind"] == "master" and (place not in chosen or plan[chosen[place]]["kind"] != "master"):
            chosen[place] = i
        elif place not in chosen:
            chosen[place] = i
    for i in chosen.values():
        shots[i]["establishing"] = True
        described = plan[i].get("location_desc") or plan[i].get("setting")
        if place_prompts is not None and described:
            shots[i]["place_prompt"] = place_prompts(described)


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


#: The motion an animated shot gets when the illustrator wrote none.
DEFAULT_MOTION = "gentle camera push-in, subtle character motion"


def prepare_storyboard_render(
    listing: dict[str, Any], rows: list[dict[str, Any]], site_config: Any,
) -> dict[str, Any]:
    """A storyboard's shot list, ready to render as the cartoon the operator approved.

    Every shot the storyboard drew carries that picture as ``frame_url``, so
    the render starts from it (the talking face lip-syncs it, an animated shot
    moves from it) instead of drawing the shot again. Matt, 2026-10-14: "I
    wouldn't want the background to change scene to scene". And the stills a
    storyboard planned before cartoons were animated throughout become
    animated shots under the cartoon budget (story_max_animated_shots,
    story_min_animated_shot_seconds), so an older storyboard renders without
    still pans too. Returns a new listing; ``listing`` is left alone.
    """
    out = json.loads(json.dumps(listing))
    frames = {int(r["idx"]): r.get("image_url") for r in rows if r.get("idx") is not None}
    budget = site_config.get_int("story_max_animated_shots", -1) if site_config is not None else -1
    floor = site_config.get_float("story_min_animated_shot_seconds", 0.0) if site_config is not None else 0.0
    animated = sum(1 for sh in out.get("shots") or [] if sh.get("source") == "generative")
    for shot in out.get("shots") or []:
        url = frames.get(int(shot["idx"]))
        if url:
            shot["frame_url"] = url
        if (
            shot.get("source") in _STILL_SOURCES and shot.get("prompt")
            and float(shot.get("duration_s") or 0.0) >= floor and (budget < 0 or animated < budget)
        ):
            shot["source"] = "generative"
            shot["motion"] = (shot.get("motion") or DEFAULT_MOTION)[:500]
            animated += 1
    return out


def assign_sources(plan: list[dict[str, Any]], site_config: Any) -> list[dict[str, Any]]:
    """Pick each shot's source. Talking shots are ``presenter``; a third
    consecutive talking shot by one speaker becomes a cutaway (the schema
    allows two). Stills animate (``generative``) within the hero budget,
    longest first; the rest alternate Ken Burns / static so no source runs
    three times."""
    # A cartoon is animated throughout (Matt, 2026-10-14: "a fully automated
    # cartoon start to finish", not still pans). story_max_animated_shots
    # (-1 = every non-talking shot) and story_min_animated_shot_seconds (0 =
    # any length) are the cartoon's own budget, apart from the blog videos'
    # video_hero_shots_max. A master shot animates too.
    hero_left = site_config.get_int("story_max_animated_shots", -1)
    hero_floor = site_config.get_float("story_min_animated_shot_seconds", 0.0)
    # A wide is a painting the camera moves across (a slow push-in under the
    # music), as in a lot of animation, unless story_wide_motion is "animate".
    # Animated, Midnight Patch's trio wides drifted: Mara turned into a mouse
    # and Cinder's hair went purple (2026-10-14).
    camera_wides = str(site_config.get("story_wide_motion", "camera") or "camera").strip().lower() != "animate"
    animated_kinds = ("still", "cutaway", "reaction") if camera_wides else ("still", "cutaway", "reaction", "master")
    candidates = sorted(
        (i for i, s in enumerate(plan)
         if s["kind"] in animated_kinds and s["length"] >= hero_floor),
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
            shot.update(kind="reaction", speaker=None,
                        characters=_listeners(shot.get("scene_cast") or [], shot["speaker"]))
        if shot["kind"] == "landscape" or (shot["kind"] == "master" and camera_wides):
            options = ["image_kenburns", "image_gen"]
        else:
            options = (["generative"] if i in animate else []) + list(_STILL_SOURCES)
        # An animated shot is its own picture (the schema keys it by prompt),
        # so animated shots in a row are never a streak.
        for source in options:
            key = (source, str(i)) if source == "generative" else (source, None)
            if _streak_ok(key):
                shot["source"] = source
                break
        out.append(shot)
        history.append((shot["source"], str(i)) if shot["source"] == "generative" else (shot["source"], None))
    return out


def _visual_row(i: int, shot: dict[str, Any]) -> str:
    """One shot as the illustrator reads it. A reaction row says who is
    talking and who listens, so the picture stays on the listener in the
    scene instead of illustrating what is being said."""
    kind = "animated" if shot["source"] == "generative" else "still"
    visible = ", ".join(shot["characters"]) or "no characters"
    head = f"- id {i}: scene {shot.get('scene_id') or '?'}: {shot['setting']}"
    if shot["kind"] in ("reaction", "cutaway") and shot.get("line_speaker"):
        return (
            f"{head} | reaction while {shot['line_speaker']} says: \"{shot['beat_text']}\" | "
            f"show: {visible} listening and reacting | {kind}"
        )
    return f"{head} | happening: {shot['beat_text']} | visible: {visible} | {kind}"


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

    # Master shots are a fixed picture of the scene; only inserts and
    # reactions need the illustrator.
    wanted = [i for i, s in enumerate(plan) if s["source"] != "presenter" and s["kind"] not in ("master", "landscape")]
    model = str(site_config.get("story_visuals_model", "") or "").strip()
    if not model:
        from poindexter.services.llm_text import resolve_local_writer_model

        model = resolve_local_writer_model(site_config=site_config)
    if not wanted:
        return {}, model
    rows = "\n".join(_visual_row(i, plan[i]) for i in wanted)
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


def _talking_framings(site_config: Any) -> list[str]:
    """``story_talking_framings``: the camera setups a speaker's lines rotate through."""
    from poindexter.services.settings_defaults import DEFAULTS

    raw = site_config.get("story_talking_framings", None) if site_config is not None else None
    for value in (raw, DEFAULTS["story_talking_framings"]):
        try:
            items = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            continue
        if isinstance(items, list) and (framings := [str(f).strip() for f in items if str(f).strip()]):
            return framings
    return ["{name}"]


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
    framings = _talking_framings(site_config)
    framing = framings[int(shot.get("framing_idx") or 0) % len(framings)].replace("{name}", name)
    text = (
        template.replace("{style}", style or "")
        .replace("{framing}", framing)
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
    cast_portraits: dict[str, str] | None = None,
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
        # Every picture of a scene is painted in its colour-script palette;
        # the renderer relights a returning location's plate to match it.
        palette = str(s.get("palette") or "").strip()
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
            shot["frame_prompt"] = _with_palette(
                _talking_frame_prompt(s, style, site_config), palette, site_config, 1500)
        elif s["kind"] == "landscape":
            picture = _landscape_prompt(s, site_config)
            shot["intent"] = picture[:200]
            shot["prompt"] = _with_palette(f"{style}, {picture}" if style else picture, palette, site_config, 1500)
        elif s["kind"] == "master":
            picture = _scene_master_prompt(s, site_config)
            shot["intent"] = picture[:200]
            shot["prompt"] = _with_palette(f"{style}, {picture}" if style else picture, palette, site_config, 1500)
        else:
            seen = visuals.get(i) or {}
            picture = seen.get("prompt") or f"{s['setting']}. {s['beat_text']}".strip(". ")
            shot["intent"] = picture[:200] or "scene"
            shot["prompt"] = _with_palette(f"{style}, {picture}" if style else picture, palette, site_config, 1500)
            if s["source"] == "generative":
                shot["motion"] = (seen.get("motion") or DEFAULT_MOTION)[:500]
        if s.get("scene_id"):
            shot["scene_id"] = str(s["scene_id"])[:64]
        # A landscape is a wider view than the place's plate, so it is drawn
        # on its own, not in the plate.
        if s.get("location") and s["kind"] != "landscape":
            shot["location_id"] = str(s["location"])[:64]
        if palette:
            shot["palette"] = palette[:300]
        shots.append(shot)
    _mark_establishing(shots, plan, lambda setting: _place_prompt(setting, style, site_config))
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
            "cast_portraits": dict(cast_portraits or {}),
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
    cast_portraits: dict[str, str] | None = None,
) -> dict[str, Any]:
    plan = assign_sources(plan_shots(screenplay, timeline, track_seconds, site_config), site_config)
    visuals, model = await write_visuals(plan, screenplay, site_config=site_config, pool=pool, task_id=task_id)
    listing = build_shot_list(
        plan, visuals, style=style, director_model=model, site_config=site_config,
        cast_portraits=cast_portraits,
    )
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
