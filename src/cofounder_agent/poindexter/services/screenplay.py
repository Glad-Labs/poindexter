"""Screenplay writer — a topic and a cast in, a validated screenplay out.

Creative studio phase 2 (poindexter#1136; epic and design: poindexter#1134). The
``story.write_screenplay`` atom delegates here, so the architect can put a
writer in front of any downstream chain (dialogue audio, a shot list, or
nothing at all).

A screenplay is plain JSON::

    {"title", "logline", "format", "cast": [slug], "narrator": bool,
     "spine": {"want", "obstacle", "turn", "payoff"},
     "scenes": [{"scene_id", "setting", "summary", "turn", "characters": [slug],
                 "beats": [{"kind": "line", "speaker": slug|"narrator",
                            "text", "delivery", "intensity"},
                           {"kind": "action", "text", "characters": [slug]}]}],
     "notes": [str]}

It is written the way a writers' room works: one call outlines the scenes
(``story.outline``), then one call per scene writes its beats
(``story.scene``) against that scene's share of the word budget, with the last
lines before it for continuity. Measured 2026-10-12 on gemma-4-31B: asked for
a 300-word dialogue in ONE call, it wrote 169 words in one scene, and the
repair pass shrank it to 106. Small calls hit their targets, and the shape
scales to multi-minute pieces.

Code checks every reply. A wrong speaker, a missing setting, or an unknown
beat kind is a HARD problem: the reply goes back once with FIX lines (the
architect's repair pattern), and a second failure raises, because a line no
one can voice breaks every step after it. Length is SOFT: a short scene gets
the same one retry, then is accepted and noted in ``notes``. A shorter video
is a weaker video, not a broken one.

The cast is personas (``persona_service``). The writer reads each one's
``display_name``, ``personality`` and ``appearance``.

**A story, not a lecture with pictures** (2026-10-13). Matt on the first
two-character cartoon: he wants "coherent stories and not just random shots
stitched together without emotion or meaning". So the outline now names a
**spine** (who wants what, what is in the way, the turn, the payoff) and each
scene its **turn** (what is different by its end). Missing either is a HARD
problem. The scene writer is handed the spine, its turn and its place in the
piece (what the next scene needs, or the opening to pay off), and gives every
line an **intensity** (low / medium / high) that ``dialogue_audio`` turns
into the speaker's Chatterbox emotion for that line.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The reserved speaker for narration. Not a persona slug: the dialogue step
#: maps it to the narrator persona, or the house voice when there is none.
NARRATOR = "narrator"
#: Screenplay shapes the writer knows. The rules for each live in the
#: ``story.screenplay`` prompt; this list only validates the requested one.
FORMATS: tuple[str, ...] = ("explainer", "story", "dialogue", "sketch")
BEAT_KINDS: tuple[str, ...] = ("line", "action")
_WORD_RE = re.compile(r"[A-Za-z0-9']+")
#: The story spine every outline names (see the module docstring).
SPINE_PARTS: tuple[str, ...] = ("want", "obstacle", "turn", "payoff")
#: How strongly a line's feeling is played; ``dialogue_audio`` maps it to the
#: speaker's Chatterbox emotion strength.
INTENSITIES: tuple[str, ...] = ("low", "medium", "high")


class ScreenplayError(RuntimeError):
    """The writer could not produce a valid screenplay. The message names why."""


@dataclass(frozen=True)
class ScreenplayRequest:
    topic: str
    brief: str = ""
    format: str = "explainer"
    cast: tuple[str, ...] = ()
    narrator: bool = True
    #: Persona slug that voices the narrator; "" = the dialogue step's default.
    narrator_persona: str = ""
    target_seconds: float = 90.0
    tone: str = ""
    #: A public-domain tale to adapt (one of ``story_source_tales``); "" = original.
    source: str = ""


def _csv_or_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        items = str(value).split(",")
    out: list[str] = []
    for item in items:
        slug = item.strip().lower()
        if slug and slug not in out:
            out.append(slug)
    return out


def build_request(inputs: dict[str, Any], site_config: Any) -> ScreenplayRequest:
    """Assemble a request from atom inputs (state + node config), filling gaps
    from ``story_default_*`` settings. Raises :class:`ScreenplayError` on a
    value the writer cannot act on, with the fix in the message."""
    topic = str(inputs.get("topic") or "").strip()
    if not topic:
        raise ScreenplayError("no topic on state — the screenplay writer needs one")
    fmt = str(
        inputs.get("screenplay_format") or site_config.get("story_default_format", "explainer")
        or "explainer"
    ).strip().lower()
    if fmt not in FORMATS:
        raise ScreenplayError(
            f"screenplay_format {fmt!r} is not one of {', '.join(FORMATS)}",
        )
    cast = _csv_or_list(inputs.get("screenplay_cast"))
    if not cast:
        cast = _csv_or_list(site_config.get("story_default_cast", ""))
    # screenplay_narrator: "none" turns narration off; a persona slug makes
    # that persona the narrator's voice; empty / "true" / "narrator" keeps a
    # narrator in the default voice. (The architect writes a slug here when
    # an intent says who narrates, so the key carries both meanings.)
    narrator_raw = str(inputs.get("screenplay_narrator", "") or "").strip().lower()
    narrator = narrator_raw not in ("none", "false", "no", "off")
    narrator_persona = (
        narrator_raw if narrator and narrator_raw not in ("", "true", "yes", "on", NARRATOR) else ""
    )
    if fmt == "dialogue" and len(cast) < 2:
        raise ScreenplayError(
            "a dialogue needs at least two cast members in screenplay_cast "
            f"(got {cast or 'none'})",
        )
    if not cast and not narrator:
        raise ScreenplayError(
            "nobody can speak: screenplay_cast is empty and screenplay_narrator is 'none'",
        )
    try:
        asked = inputs.get("screenplay_target_seconds")
        target = float(asked or site_config.get_float("story_default_target_seconds", 90.0))
    except (TypeError, ValueError) as exc:
        raise ScreenplayError(
            f"screenplay_target_seconds must be a number, got "
            f"{inputs.get('screenplay_target_seconds')!r}",
        ) from exc
    if target <= 0:
        raise ScreenplayError("screenplay_target_seconds must be positive")
    # A story needs room to set up, struggle and pay off. Midnight Patch was a
    # whole Cinderella in 40 s, so its characters announced the premise
    # instead of living it (Matt, 2026-10-14: "the script really is garbage").
    floor = site_config.get_float("story_min_story_seconds", 120.0)
    if fmt == "story" and floor > 0 and target < floor:
        if asked:
            raise ScreenplayError(
                f"a story needs at least {floor:.0f} s to set up, struggle and pay off "
                f"(asked for {target:.0f} s): ask for {floor:.0f} s or more, or the "
                "'sketch' format for a single gag or moment",
            )
        target = floor  # the default length is the format's to choose
    return ScreenplayRequest(
        source=_source_tale(inputs.get("screenplay_source"), site_config),
        topic=topic,
        brief=str(inputs.get("screenplay_brief") or "").strip(),
        format=fmt,
        cast=tuple(cast),
        narrator=narrator,
        narrator_persona=narrator_persona,
        target_seconds=target,
        tone=str(inputs.get("screenplay_tone") or "").strip(),
    )


def resolve_cast(site_config: Any, slugs: tuple[str, ...]) -> list[Any]:
    """Personas for ``slugs``, in order. An unknown or disabled slug raises:
    a line written for a character nobody can voice or draw is unusable."""
    from poindexter.services.persona_service import get_persona

    out = []
    for slug in slugs:
        persona = get_persona(site_config, slug)
        if persona is None or not persona.enabled:
            raise ScreenplayError(
                f"cast member {slug!r} is not an enabled persona — create it with "
                f"`poindexter personas create {slug} --display-name ...`",
            )
        out.append(persona)
    return out


def _source_tale(raw: Any, site_config: Any) -> str:
    """The public-domain tale to adapt, as ``story_source_tales`` spells it; "" for an original.

    Matt, 2026-10-14: borrow an existing storyline, so the plot already makes
    sense. Only a tale on the list may be adapted: the list is the guarantee
    that it is free to retell. Raises :class:`ScreenplayError` for any other.
    """
    from poindexter.services.settings_defaults import DEFAULTS

    wanted = str(raw or "").strip()
    if not wanted:
        return ""
    value = site_config.get("story_source_tales", None) if site_config is not None else None
    try:
        tales = json.loads(value if value not in (None, "") else DEFAULTS["story_source_tales"])
    except (TypeError, ValueError):
        tales = json.loads(DEFAULTS["story_source_tales"])
    key = re.sub(r"[^a-z0-9]+", " ", wanted.lower()).strip()
    for tale in tales:
        if re.sub(r"[^a-z0-9]+", " ", str(tale).lower()).strip() == key:
            return str(tale)
    raise ScreenplayError(
        f"{wanted!r} is not on story_source_tales, the public-domain tales a screenplay may "
        f"adapt: {', '.join(map(str, tales))}",
    )


def _source_block(request: ScreenplayRequest) -> str:
    if not request.source:
        return "SOURCE: none. This is an original story."
    return (
        f"SOURCE: adapt the traditional tale \"{request.source}\" (the public-domain tale, "
        "never a film or other modern version). Keep its plot: the same want, the same "
        "obstacles in the same order, the same turn and the same ending, with its cause and "
        "effect intact. Tell it in this piece's world with this cast: translate each of "
        "the tale's elements into this world's equivalent (see TRANSLATE AN ADAPTED TALE), "
        "so nothing from the tale's own setting appears unless it already belongs here. "
        "When the length cannot hold every beat, keep the beats the ending depends on."
    )


def word_budget(target_seconds: float, site_config: Any) -> int:
    rate = site_config.get_float("story_speech_words_per_second", 2.5)
    return max(10, int(round(target_seconds * rate)))


def scene_count(target_seconds: float, site_config: Any) -> int:
    """How many scenes to ask for: one per ``story_seconds_per_scene``."""
    per = site_config.get_float("story_seconds_per_scene", 40.0)
    return max(1, int(round(target_seconds / per))) if per > 0 else 1


def cast_block(personas: list[Any]) -> str:
    """The cast as prompt data: one line per character, slug first so the
    model copies the exact speaker id."""
    if not personas:
        return "(no cast — only the narrator speaks)"
    lines = []
    for p in personas:
        parts = [f"- {p.slug}: {p.display_name or p.slug}"]
        if p.personality:
            parts.append(f"personality: {p.personality}")
        looks = p.visual_description
        if looks:
            parts.append(f"looks: {looks}")
        lines.append("; ".join(parts))
    return "\n".join(lines)


def spoken_words(screenplay: dict[str, Any]) -> int:
    return sum(
        len(_WORD_RE.findall(str(b.get("text") or "")))
        for s in screenplay.get("scenes") or []
        for b in s.get("beats") or []
        if b.get("kind") == "line"
    )


def normalize_beats(raw_beats: Any) -> list[dict[str, Any]]:
    """Lower-case ids, drop empty beats. Never invents content."""
    out: list[dict[str, Any]] = []
    for beat in raw_beats or []:
        if not isinstance(beat, dict):
            continue
        text = str(beat.get("text") or "").strip()
        if not text:
            continue
        kind = str(beat.get("kind") or "").strip().lower()
        item: dict[str, Any] = {"kind": kind, "text": text}
        if kind == "line":
            item["speaker"] = str(beat.get("speaker") or "").strip().lower()
            item["delivery"] = str(beat.get("delivery") or "").strip()
            intensity = str(beat.get("intensity") or "").strip().lower()
            item["intensity"] = intensity if intensity in INTENSITIES else "medium"
            # Heard, not seen (over a radio, through a wall): the line is
            # voiced, the picture stays on whoever is in the scene.
            if beat.get("offscreen") is True or str(beat.get("offscreen")).strip().lower() == "true":
                item["offscreen"] = True
        else:
            item["characters"] = _csv_or_list(beat.get("characters"))
            # Who arrives or leaves in this action: the director keeps a
            # character out of the scene's wide shots before they enter and
            # after they leave. Mara stood in every wide of Midnight Patch
            # before her ring summoned her (2026-10-14).
            for key in ("enters", "exits"):
                names = _csv_or_list(beat.get(key))
                if names:
                    item[key] = names
        out.append(item)
    return out


def _location_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")[:48]


def normalize_outline(raw: dict[str, Any]) -> dict[str, Any]:
    """The outline reply in a fixed shape.

    ``locations`` are the story's places, each drawn once and reused by every
    scene set there (a studio's background design, 2026-10-14). A reply with
    no locations, like every script written before them, gets one per scene
    from its setting, so older scripts keep working.

    Each scene's ``palette`` is its entry in the colour script: the colours
    and light the scene is painted in, planned across the whole story so the
    colour follows its feeling. Empty in older scripts, which keep the style's
    own colours.
    """
    locations: list[dict[str, str]] = []
    for item in raw.get("locations") or []:
        if not isinstance(item, dict):
            continue
        loc_id = _location_id(item.get("id") or item.get("name") or "")
        description = str(item.get("description") or "").strip()
        if loc_id and loc_id not in {loc["id"] for loc in locations}:
            locations.append({"id": loc_id, "description": description})
    scenes: list[dict[str, Any]] = []
    for i, scene in enumerate(raw.get("scenes") or [], start=1):
        if not isinstance(scene, dict):
            continue
        scenes.append({
            "scene_id": str(scene.get("scene_id") or f"s{i}").strip() or f"s{i}",
            "location": _location_id(scene.get("location") or ""),
            "setting": str(scene.get("setting") or "").strip(),
            "palette": str(scene.get("palette") or "").strip(),
            "summary": str(scene.get("summary") or "").strip(),
            "turn": str(scene.get("turn") or "").strip(),
            "characters": _csv_or_list(scene.get("characters")),
        })
    if not locations:
        for scene in scenes:
            scene["location"] = _location_id(scene["scene_id"]) or scene["scene_id"]
            locations.append({"id": scene["location"], "description": scene["setting"]})
    spine_value = raw.get("spine")
    spine_raw: dict[str, Any] = spine_value if isinstance(spine_value, dict) else {}
    translation = [
        {"tale": str(t.get("tale") or "").strip(), "here": str(t.get("here") or "").strip()}
        for t in raw.get("translation") or [] if isinstance(t, dict)
    ]
    return {
        "title": str(raw.get("title") or "").strip(),
        "logline": str(raw.get("logline") or "").strip(),
        "spine": {k: str(spine_raw.get(k) or "").strip() for k in SPINE_PARTS},
        # The event the piece is about, shown on screen; and, for an adapted
        # tale, how each of its elements translates into this world (Matt,
        # 2026-10-14: a rooftop pasture is no cyberpunk wolf story).
        "main_event": str(raw.get("main_event") or "").strip(),
        "translation": [t for t in translation if t["tale"] and t["here"]],
        "locations": locations,
        "scenes": scenes,
    }


def _cast_names(request: ScreenplayRequest, site_config: Any) -> list[str]:
    """Every name a cast member goes by: their id and, with settings, their display name."""
    from poindexter.services.persona_service import get_persona

    names: dict[str, str] = {}  # lowercased -> as written; a display name wins over its id
    for slug in request.cast:
        if slug:
            names.setdefault(slug.lower(), slug)
        persona = get_persona(site_config, slug) if site_config is not None and slug else None
        if persona is not None and persona.display_name:
            names[persona.display_name.lower()] = persona.display_name
    return sorted(names.values(), key=len, reverse=True)


def _palette_problems(sid: str, palette: str, names: list[str], site_config: Any) -> list[str]:
    """A palette is painted over every picture of its scene, the characters included.

    Naming a character in it put that character into every picture: the
    Midnight Patch palette ("to match Cinder's tired, wistful mood and Bolt's
    jitters. When Mara appears...") drew Bolt beside Cinder in each of her
    talking frames, and its "slate greys" turned Bolt grey (2026-10-14). A long
    palette told the scene's story and was cut mid-sentence.
    """
    from poindexter.services.settings_defaults import DEFAULTS

    errors: list[str] = []
    named = [n for n in names if re.search(rf"\b{re.escape(n)}\b", palette, re.IGNORECASE)]
    if named:
        errors.append(
            f"FIX scene {sid!r}: the \"palette\" names {', '.join(named)}; describe only the "
            "colours and light of the place, and keep the characters in the summary",
        )
    raw = site_config.get("story_palette_max_chars", None) if site_config is not None else None
    limit = int(float(raw if raw not in (None, "") else DEFAULTS["story_palette_max_chars"]))
    if limit > 0 and len(palette) > limit:
        errors.append(
            f"FIX scene {sid!r}: shorten the \"palette\" to one phrase of at most {limit} characters: "
            "the main hues, the time of day and where the light comes from",
        )
    return errors


def check_outline(
    outline: dict[str, Any], request: ScreenplayRequest, site_config: Any = None, *, new: bool = False,
) -> list[str]:
    """Hard problems with a normalized outline.

    ``new``: a freshly written outline must also name its ``main_event`` (a
    story) and, adapting a tale, its ``translation``; a revision of a script
    written before them is not held to it.
    """
    errors: list[str] = []
    if new and request.format == "story" and not outline.get("main_event"):
        errors.append('FIX: add "main_event": the one event the piece is about, shown on screen')
    if new and request.source and len(outline.get("translation") or []) < 3:
        errors.append(
            'FIX: add "translation": each of the tale\'s key elements (hero, what they guard or want, '
            'the threat, the people around them, key objects and acts) with its equivalent in this world',
        )
    cast = set(request.cast)
    names = _cast_names(request, site_config)
    if not outline["title"]:
        errors.append("FIX: add a non-empty \"title\"")
    if not outline["scenes"]:
        errors.append("FIX: add at least one scene")
    spine = outline.get("spine") or {}
    missing = [k for k in SPINE_PARTS if not spine.get(k)]
    if missing:
        errors.append(
            "FIX: add a \"spine\" with " + ", ".join(f'"{k}"' for k in missing)
            + " (who wants what, what is in the way, the turn, the payoff)",
        )
    known = {loc["id"] for loc in outline.get("locations") or []}
    for loc in outline.get("locations") or []:
        if not loc.get("description"):
            errors.append(f"FIX location {loc['id']!r}: add a \"description\" of the place a background artist can paint")
    # The colour script is all or nothing: a scene left out of it would be
    # painted in whatever colours the model picks, breaking the arc.
    painted = any(scene.get("palette") for scene in outline["scenes"])
    for scene in outline["scenes"]:
        sid = scene["scene_id"]
        if painted and not scene.get("palette"):
            errors.append(f"FIX scene {sid!r}: add a \"palette\": its colours and light in the colour script")
        elif scene.get("palette"):
            errors.extend(_palette_problems(sid, scene["palette"], names, site_config))
        if known and scene.get("location") not in known:
            errors.append(
                f"FIX scene {sid!r}: set \"location\" to one of the location ids {sorted(known)}",
            )
        if not scene["setting"]:
            errors.append(f"FIX scene {sid!r}: add a \"setting\" (one sentence: place and mood)")
        if not scene["summary"]:
            errors.append(f"FIX scene {sid!r}: add a \"summary\" of what happens")
        if not scene.get("turn"):
            errors.append(f"FIX scene {sid!r}: add a \"turn\": what is different by the end of the scene")
        for name in scene["characters"]:
            if name not in cast:
                errors.append(
                    f"FIX scene {sid!r}: {name!r} is not in the cast; use only "
                    f"{sorted(cast) or 'no characters'}",
                )
    return errors


def check_beats(
    beats: list[dict[str, Any]], scene_id: str, request: ScreenplayRequest,
) -> list[str]:
    """Hard problems with one scene's normalized beats."""
    errors: list[str] = []
    cast = set(request.cast)
    speakers = cast | ({NARRATOR} if request.narrator else set())
    if not beats:
        errors.append(f"FIX scene {scene_id!r}: add beats — the scene is empty")
    for beat in beats:
        if beat["kind"] not in BEAT_KINDS:
            errors.append(
                f"FIX scene {scene_id!r}: beat kind {beat['kind']!r} must be \"line\" or \"action\"",
            )
        elif beat["kind"] == "line":
            if beat["speaker"] not in speakers:
                errors.append(
                    f"FIX scene {scene_id!r}: speaker {beat['speaker']!r} cannot speak; "
                    f"speaker must be one of {sorted(speakers)}",
                )
        else:
            for name in [*beat["characters"], *beat.get("enters", []), *beat.get("exits", [])]:
                if name not in cast:
                    errors.append(
                        f"FIX scene {scene_id!r}: action names {name!r}, who is not in the cast",
                    )
    errors.extend(_check_entrances(beats, scene_id))
    return errors


def _check_entrances(beats: list[dict[str, Any]], scene_id: str) -> list[str]:
    """A character who enters is not in the scene before it, nor after they exit.

    So they cannot speak before their entrance or after their exit, and they
    cannot enter twice without leaving in between.
    """
    errors: list[str] = []
    entering = {n for b in beats if b["kind"] == "action" for n in b.get("enters", [])}
    away = set(entering)  # those who enter are away until they do
    for beat in beats:
        if beat["kind"] == "line" and beat.get("speaker") in away and not beat.get("offscreen"):
            when = "before entering" if beat["speaker"] in entering else "after leaving"
            errors.append(
                f"FIX scene {scene_id!r}: {beat['speaker']!r} speaks {when}; move the line, "
                "or the action where they enter or leave",
            )
        if beat["kind"] != "action":
            continue
        for name in beat.get("enters", []):
            if name not in away:
                errors.append(f"FIX scene {scene_id!r}: {name!r} enters while already in the scene")
            away.discard(name)
        away.update(beat.get("exits", []))
    return errors


def length_problem(words: int, budget: float, site_config: Any) -> str | None:
    """A FIX line when ``words`` is outside the accepted window, else None."""
    lo = site_config.get_float("story_length_min_ratio", 0.65)
    hi = site_config.get_float("story_length_max_ratio", 1.75)
    target = int(round(budget))
    if words < budget * lo:
        return f"FIX: the spoken lines total {words} words; write about {target} (add lines)"
    if words > budget * hi:
        return f"FIX: the spoken lines total {words} words; write about {target} (cut lines)"
    return None


def _scene_words(beats: list[dict[str, Any]]) -> int:
    return sum(len(_WORD_RE.findall(b["text"])) for b in beats if b.get("kind") == "line")


def validate(
    screenplay: dict[str, Any], request: ScreenplayRequest, site_config: Any,
) -> list[str]:
    """Hard problems with a complete screenplay; empty when it is usable.

    Length is not checked here — it is a soft problem, recorded in ``notes``.
    """
    errors = check_outline(screenplay, request, site_config)
    lines = 0
    for scene in screenplay["scenes"]:
        errors.extend(check_beats(scene.get("beats") or [], scene["scene_id"], request))
        lines += sum(1 for b in scene.get("beats") or [] if b.get("kind") == "line")
    if screenplay["scenes"] and not lines:
        errors.append("FIX: add spoken \"line\" beats — the screenplay has no dialogue or narration")
    return errors


def _parse(raw: str) -> dict[str, Any] | None:
    from poindexter.utils.json_extract import extract_json_object

    blob = extract_json_object(raw or "")
    return blob if isinstance(blob, dict) else None


def _prompt(key: str, **kwargs: Any) -> str:
    from poindexter.services.prompt_manager import get_prompt_manager

    return get_prompt_manager().get_prompt(key, **kwargs)


def _resolve_model(site_config: Any) -> str:
    pinned = str(site_config.get("story_writer_model", "") or "").strip()
    if pinned:
        return pinned
    from poindexter.services.llm_text import resolve_local_writer_model

    return resolve_local_writer_model(site_config=site_config)


async def _ask_validated(
    key: str,
    kwargs: dict[str, Any],
    check: Any,
    *,
    model: str,
    site_config: Any,
    pool: Any,
    task_id: str | None,
    what: str,
    phase: str = "story_write_screenplay",
) -> tuple[dict[str, Any], list[str]]:
    """One prompt, checked; one FIX retry. Returns ``(reply, soft_notes)``.

    ``check(reply) -> (hard, soft)`` lists FIX lines. Hard problems that
    survive the retry raise; soft ones are returned as notes. If the retry
    reply is unusable but the first was only soft-flawed, the first is kept.
    """
    from poindexter.services.llm_text import ollama_chat_text

    base = _prompt(key, **kwargs)
    first: tuple[dict[str, Any], list[str]] | None = None
    errors: list[str] = []
    previous = ""
    for attempt in (1, 2):
        text = base if attempt == 1 else base + "\n\n" + _prompt(
            "story.screenplay_fix", previous=previous[:6000], errors="\n".join(errors[:20]),
        )
        raw = await ollama_chat_text(
            text,
            model=model,
            site_config=site_config,
            pool=pool,
            task_id=task_id,
            phase=phase,
            timeout_setting="story_writer_timeout_seconds",
            timeout_default=300.0,
            think=False,
            response_format={"type": "json_object"},
        )
        reply = _parse(raw)
        if reply is None:
            errors = ["FIX: reply with one JSON object only — no prose or code fences"]
            previous = (raw or "")[:6000]
            logger.info("[screenplay] %s attempt %d: reply was not a JSON object", what, attempt)
            continue
        hard, soft = check(reply)
        if not hard and not soft:
            return reply, []
        if not hard and first is None:
            first = (reply, soft)
        if attempt == 2 and not hard:
            return reply, soft
        errors = [*hard, *soft]
        previous = json.dumps(reply, ensure_ascii=False)
        logger.info(
            "[screenplay] %s attempt %d: %d problem(s): %s",
            what, attempt, len(errors), errors[0][:160],
        )
    if first is not None:
        return first
    raise ScreenplayError(
        f"the writer did not produce a valid {what} after one repair: " + "; ".join(errors[:5]),
    )


def _outline_text(outline: dict[str, Any]) -> str:
    return "\n".join(
        f"- {s['scene_id']}: {s['summary']} Ends with: {s.get('turn') or '(unstated)'} "
        f"(setting: {s['setting']}; characters: {', '.join(s['characters']) or 'none'})"
        for s in outline["scenes"]
    )


def _spine_text(outline: dict[str, Any]) -> str:
    spine = outline.get("spine") or {}
    lines = [f"{k.capitalize()}: {spine.get(k) or '(unstated)'}" for k in SPINE_PARTS]
    if outline.get("main_event"):
        lines.append(f"Main event (shown on screen): {outline['main_event']}")
    for item in outline.get("translation") or []:
        lines.append(f"In the tale {item['tale']}; here {item['here']}")
    return "\n".join(lines)


def _scene_position(outline: dict[str, Any], index: int, opening: list[str]) -> str:
    """What this scene owes the rest of the piece: set up the next one, or
    (the last) pay off the opening."""
    scenes = outline["scenes"]
    if index == len(scenes) - 1:
        payoff = (outline.get("spine") or {}).get("payoff") or "land the point"
        first = "\n".join(opening[:2]) or "(this is also the opening scene)"
        return (
            f"This is the LAST scene. Deliver the payoff: {payoff}\n"
            "End on the main character changed: show what they understand, decide or do now, "
            "in an action or a line, after the main event.\n"
            f"The piece opened with:\n{first}"
        )
    nxt = scenes[index + 1]
    return f"The next scene: {nxt['summary']} Leave the characters ready for it."


async def write_screenplay(
    request: ScreenplayRequest,
    *,
    site_config: Any,
    pool: Any = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Outline, then write each scene. Raises ScreenplayError on hard failure."""
    personas = resolve_cast(site_config, request.cast)
    budget = word_budget(request.target_seconds, site_config)
    model = _resolve_model(site_config)
    common = {
        "site_name": str(site_config.get("site_name", "") or "the studio"),
        "topic": request.topic,
        "brief": request.brief or "(none — choose the angle yourself)",
        "format_name": request.format,
        "tone": request.tone or "(your choice — fit it to the topic)",
        "word_budget": budget,
        "target_seconds": int(round(request.target_seconds)),
        "cast_block": cast_block(personas),
        "narrator_available": "yes" if request.narrator else "no",
        "source_block": _source_block(request),
    }
    ask = {"model": model, "site_config": site_config, "pool": pool, "task_id": task_id}

    def _check_outline(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return check_outline(normalize_outline(reply), request, site_config, new=True), []

    raw_outline, _ = await _ask_validated(
        "story.outline", {**common, "scene_count": scene_count(request.target_seconds, site_config)},
        _check_outline, what="outline", **ask,
    )
    outline = normalize_outline(raw_outline)
    per_scene = budget / len(outline["scenes"])
    notes: list[str] = []
    recent: list[str] = []
    scenes: list[dict[str, Any]] = []
    opening: list[str] = []
    for index, scene in enumerate(outline["scenes"]):
        sid = scene["scene_id"]

        def _check_scene(reply: dict[str, Any], sid: str = sid) -> tuple[list[str], list[str]]:
            beats = normalize_beats(reply.get("beats"))
            hard = check_beats(beats, sid, request)
            if hard:
                return hard, []
            if not any(b["kind"] == "line" for b in beats):
                return [], [f"FIX scene {sid!r}: add at least one spoken line"]
            soft = length_problem(_scene_words(beats), per_scene, site_config)
            return [], [f"scene {sid!r}: {soft}"] if soft else []

        reply, soft = await _ask_validated(
            "story.scene",
            {
                **common,
                "outline_block": _outline_text(outline),
                "spine_block": _spine_text(outline),
                "scene_id": sid,
                "scene_setting": scene["setting"],
                "scene_summary": scene["summary"],
                "scene_turn": scene.get("turn") or "(unstated)",
                "scene_position": _scene_position(outline, index, opening),
                "scene_characters": ", ".join(scene["characters"]) or "none",
                "scene_word_budget": int(round(per_scene)),
                "previous_lines": "\n".join(recent[-6:]) or "(this is the opening scene)",
            },
            _check_scene, what=f"scene {sid}", **ask,
        )
        notes.extend(soft)
        beats = normalize_beats(reply.get("beats"))
        scenes.append({**scene, "beats": beats})
        recent.extend(f"{b['speaker']}: {b['text']}" for b in beats if b["kind"] == "line")
        if not opening:
            opening = list(recent[:2])

    screenplay: dict[str, Any] = {
        "title": outline["title"], "logline": outline["logline"],
        "spine": outline["spine"], "main_event": outline.get("main_event") or "",
        "translation": outline.get("translation") or [],
        "locations": outline.get("locations") or [], "scenes": scenes,
    }
    hard = validate(screenplay, request, site_config)
    if hard:
        raise ScreenplayError("the assembled screenplay is unusable: " + "; ".join(hard[:5]))
    words = spoken_words(screenplay)
    total = length_problem(words, budget, site_config)
    if total:
        notes.append(f"whole piece: {total}")
    screenplay.update({
        "format": request.format,
        "cast": list(request.cast),
        "narrator": request.narrator,
        "narrator_persona": request.narrator_persona,
        "topic": request.topic,
        "source": request.source,
        "target_seconds": request.target_seconds,
        "spoken_words": words,
        "writer_model": model,
        "notes": notes,
    })
    logger.info(
        "[screenplay] %r: %d scenes, %d spoken words (budget %d), model %s%s",
        screenplay["title"], len(scenes), words, budget, model,
        f", notes: {notes}" if notes else "",
    )
    return await edit_screenplay(screenplay, site_config=site_config, pool=pool, task_id=task_id)


async def edit_screenplay(
    screenplay: dict[str, Any],
    *,
    site_config: Any,
    pool: Any = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """The story editor's rounds on a drafted screenplay; returns the edited one.

    A studio script goes through notes and rewrites before anyone draws it;
    ours went straight from the writer's first draft to the storyboard, and
    Midnight Patch read like it (Matt, 2026-10-14). Each round the editor
    (``story.edit_notes``, ``story_editor_model`` or the writer's model)
    reads the whole screenplay, with a first-time viewer's account of it
    (:func:`cold_read`) beside the story the writer meant, and either calls it
    ready or gives up to ``story_edit_max_notes`` notes, which
    :func:`revise_screenplay` applies. Rounds continue until the reader
    follows it and the editor calls it ready, up to ``story_edit_rounds``
    (0 = off); confusions left after the last round go on the draft's notes. Each round is kept under ``edit_rounds``. An editor or a
    revision that fails ends the rounds with the last good draft, logged.
    """
    rounds = site_config.get_int("story_edit_rounds", 2)
    if rounds <= 0:
        return screenplay
    request = _request_from(screenplay)
    personas = resolve_cast(site_config, request.cast)
    model = str(site_config.get("story_editor_model", "") or "").strip() or _resolve_model(site_config)
    max_notes = max(1, site_config.get_int("story_edit_max_notes", 6))
    history: list[dict[str, Any]] = list(screenplay.get("edit_rounds") or [])

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        verdict = str(reply.get("verdict") or "").strip().lower()
        notes = reply.get("notes")
        if verdict not in ("ready", "revise"):
            return ['FIX: set "verdict" to "ready" or "revise"'], []
        if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
            return ['FIX: "notes" is a list of strings'], []
        if verdict == "revise" and not [n for n in notes if n.strip()]:
            return ['FIX: a "revise" verdict needs at least one note'], []
        return [], []

    for n in range(1, rounds + 1):
        read = await cold_read(screenplay, site_config=site_config, pool=pool, task_id=task_id, model=model)
        clashes = await continuity(screenplay, site_config=site_config, pool=pool, task_id=task_id, model=model)
        try:
            reply, _ = await _ask_validated(
                "story.edit_notes",
                {
                    "site_name": str(site_config.get("site_name", "") or "the studio"),
                    "format_name": request.format,
                    "target_seconds": int(round(request.target_seconds)),
                    "cast_block": cast_block(personas),
                    "current": json.dumps(_core(screenplay), ensure_ascii=False, indent=1)[:24000],
                    "spine_block": _spine_text(screenplay),
                    "source_block": _source_block(request),
                    "cold_read": _cold_read_text(read),
                    "continuity": "\n".join(f"- beat {c['beat']}: {c['problem']}" for c in clashes or []) or "none found",
                    "max_notes": max_notes,
                },
                _check, model=model, site_config=site_config, pool=pool, task_id=task_id,
                what=f"edit round {n}", phase="story_edit_screenplay",
            )
        except ScreenplayError as exc:
            logger.warning("[screenplay] editor round %d failed (%s); keeping the draft", n, exc)
            history.append({"round": n, "error": str(exc)[:300]})
            break
        notes = [str(x).strip() for x in reply.get("notes") or [] if str(x).strip()][:max_notes]
        lost = list((read or {}).get("lost") or [])
        if read is not None and read.get("change_seen") is False:
            # "Signal Jammer" stopped at its blackout: no beat where Cinder
            # sees what she did, so the lesson never landed (2026-10-14).
            lost.append(
                "the ending: the film never shows the main character changed; end on what they "
                "understand, decide or do now",
            )
        clash_notes = [f"Continuity: beat {c['beat']}: {c['problem']}" for c in clashes or []]
        if (str(reply.get("verdict")).strip().lower() == "ready" or not notes) and (lost or clash_notes):
            # The editor may not call a script ready while a first-time viewer
            # is lost in it ("The Last Bus Home", 2026-10-14) or a beat
            # contradicts an earlier one ("Cinder Cries Wolf": "we came as
            # fast as we could" after we watched them not come).
            notes = [*clash_notes,
                     *(f"A first-time viewer could not follow this; make it clear on screen: {x}" for x in lost)
                     ][:max_notes]
            lost = lost or clash_notes
        if (str(reply.get("verdict")).strip().lower() == "ready" and not lost) or not notes:
            history.append({"round": n, "verdict": "ready", "cold_read": read, "continuity": clashes})
            logger.info("[screenplay] editor round %d: ready", n)
            break
        logger.info("[screenplay] editor round %d: %d note(s): %s", n, len(notes), notes)
        try:
            revised = await revise_screenplay(
                screenplay, "\n".join(f"{i}. {note}" for i, note in enumerate(notes, 1)),
                site_config=site_config, pool=pool, task_id=task_id,
            )
        except ScreenplayError as exc:
            # One bad revision ends nothing: the next round reads the draft
            # again (the wolf adaptation's round 1 stopped the whole edit).
            logger.warning("[screenplay] revision for editor round %d failed (%s); keeping the draft", n, exc)
            history.append({"round": n, "notes": notes, "error": str(exc)[:300]})
            continue
        history.append({"round": n, "notes": notes, "cold_read": read, "continuity": clashes})
        screenplay = revised
    # The dialogue pass, then what a first-time viewer and the script
    # supervisor make of the finished script; anything still wrong goes on
    # the draft's notes, so it is seen, not buried in a log.
    screenplay = await dialogue_pass(screenplay, site_config=site_config, pool=pool, task_id=task_id, model=model)
    final = await cold_read(screenplay, site_config=site_config, pool=pool, task_id=task_id, model=model)
    final_clashes = await continuity(screenplay, site_config=site_config, pool=pool, task_id=task_id, model=model)
    history.append({"final_cold_read": final, "final_continuity": final_clashes})
    still = [*(f"continuity: beat {c['beat']}: {c['problem']}" for c in final_clashes or []),
             *((final or {}).get("lost") or [])]
    if final is not None and final.get("change_seen") is False:
        still.append("the ending never shows the main character changed")
    if still:
        screenplay["notes"] = [
            *(screenplay.get("notes") or []),
            *(f"a first-time viewer still could not follow: {x}" for x in still),
        ]
    screenplay["edit_rounds"] = history
    return screenplay


def _audience_view(screenplay: dict[str, Any]) -> str:
    """The film as the audience gets it: each line with its speaker, each action, in order.

    No title, outline, spine or summaries: a reader given those reads the
    writer's intent into the scenes instead of what is on screen.
    """
    rows: list[str] = []
    for n, scene in enumerate(screenplay.get("scenes") or [], 1):
        rows.append(f"Scene {n}: {scene.get('setting') or ''}".rstrip())
        for beat in scene.get("beats") or []:
            if beat.get("kind") == "line":
                rows.append(f"  {str(beat.get('speaker') or '').upper()}: {beat.get('text', '')}")
            else:
                rows.append(f"  ({beat.get('text', '')})")
    return "\n".join(rows)


def _cold_read_text(read: dict[str, Any] | None) -> str:
    if not read:
        return "(no account: the cold read failed)"
    wants = read.get("wants") or {}
    lines = [f"Story: {read.get('story', '')}"]
    if isinstance(wants, dict):
        lines += [f"{name} wants: {want}" for name, want in wants.items()]
    lines.append(f"Why it ends that way: {read.get('cause', '')}")
    lost = read.get("lost") or []
    lines.append("Could not follow:" + ("".join(f"\n- {x}" for x in lost) if lost else " nothing"))
    return "\n".join(lines)


async def dialogue_pass(
    screenplay: dict[str, Any], *, site_config: Any, pool: Any = None,
    task_id: str | None = None, model: str | None = None,
) -> dict[str, Any]:
    """Rewrite the lines of an edited screenplay for voice and plain speech (``story.dialogue_pass``).

    "Signal Jammer" (2026-10-14) made sense after the editor and still spoke
    in jargon ("The Architect of Order has descended to scrub this digital
    disaster from existence") with no joke in it. The pass returns only the
    lines it rewrites, by beat number; code applies them, so actions, order
    and speakers cannot move, and a pass that would grow the script past
    ``story_dialogue_max_growth`` is refused. ``story_dialogue_pass_enabled``;
    ``story_dialogue_model`` (empty = ``model``). A failed pass keeps the
    lines, logged, and is recorded under ``dialogue_pass``.
    """
    if str(site_config.get("story_dialogue_pass_enabled", "true") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return screenplay
    writer = str(site_config.get("story_dialogue_model", "") or "").strip() or model or _resolve_model(site_config)
    request = _request_from(screenplay)
    beats = [b for scene in screenplay.get("scenes") or [] for b in scene.get("beats") or []]
    lines = {n: b for n, b in enumerate(beats, 1) if b.get("kind") == "line"}
    growth = site_config.get_float("story_dialogue_max_growth", 1.25)
    before = spoken_words(screenplay)

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        items = reply.get("lines")
        if not isinstance(items, list):
            return ['FIX: "lines" is a list of {"n": beat number, "text": "..."}'], []
        problems = []
        added = 0
        for item in items:
            n = item.get("n") if isinstance(item, dict) else None
            text = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
            if not isinstance(n, int) or n not in lines:
                problems.append(f"FIX: beat {n!r} is not a spoken line; rewrite only line beats")
            elif not text:
                problems.append(f"FIX: beat {n} has no text")
            else:
                added += len(_WORD_RE.findall(text)) - len(_WORD_RE.findall(lines[n]["text"]))
        if before and growth > 0 and (before + added) > before * growth:
            problems.append(f"FIX: keep the length about the same: the rewrite grows {before} words to {before + added}")
        return problems, []

    try:
        reply, _ = await _ask_validated(
            "story.dialogue_pass",
            {
                "site_name": str(site_config.get("site_name", "") or "the studio"),
                "cast_block": cast_block(resolve_cast(site_config, request.cast)),
                "spine_block": _spine_text(screenplay),
                "numbered_view": _numbered_view(screenplay)[:20000],
            },
            _check, model=writer, site_config=site_config, pool=pool, task_id=task_id,
            what="dialogue pass", phase="story_dialogue_pass",
        )
    except ScreenplayError as exc:
        logger.warning("[screenplay] dialogue pass failed (%s); keeping the lines", exc)
        screenplay["dialogue_pass"] = {"error": str(exc)[:300]}
        return screenplay
    changed = 0
    for item in reply["lines"]:
        lines[item["n"]]["text"] = str(item["text"]).strip()
        changed += 1
    screenplay["spoken_words"] = spoken_words(screenplay)
    screenplay["dialogue_pass"] = {"changed": changed}
    logger.info("[screenplay] dialogue pass rewrote %d of %d lines", changed, len(lines))
    return screenplay


def _numbered_view(screenplay: dict[str, Any]) -> str:
    """The beats in order, numbered across the piece, scene headings between."""
    rows: list[str] = []
    n = 0
    for k, scene in enumerate(screenplay.get("scenes") or [], 1):
        rows.append(f"Scene {k}: {scene.get('setting') or ''}".rstrip())
        for beat in scene.get("beats") or []:
            n += 1
            if beat.get("kind") == "line":
                heard = " (heard, not seen)" if beat.get("offscreen") else ""
                rows.append(f"  {n}. {str(beat.get('speaker') or '').upper()}{heard}: {beat.get('text', '')}")
            else:
                rows.append(f"  {n}. ({beat.get('text', '')})")
    return "\n".join(rows)


async def continuity(
    screenplay: dict[str, Any], *, site_config: Any, pool: Any = None,
    task_id: str | None = None, model: str | None = None,
) -> list[dict[str, Any]] | None:
    """Beats that contradict what came before them (``story.continuity``), or ``None``.

    "Cinder Cries Wolf" (2026-10-14) passed the cold read with Bolt saying
    "we came as fast as we could" after the film showed him sit back down,
    and Cinder sitting "alone" beside both of them: a reader can follow a
    story that contradicts itself. ``story_continuity_model`` (empty =
    ``model``). ``None`` when it cannot answer, logged.
    """
    checker = str(site_config.get("story_continuity_model", "") or "").strip() or model or _resolve_model(site_config)

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        items = reply.get("contradictions")
        if not isinstance(items, list) or not all(isinstance(i, dict) and i.get("problem") for i in items):
            return ['FIX: "contradictions" is a list of {"beat": number, "problem": "..."} (empty when none)'], []
        return [], []

    try:
        reply, _ = await _ask_validated(
            "story.continuity", {"numbered_view": _numbered_view(screenplay)[:20000]}, _check,
            model=checker, site_config=site_config, pool=pool, task_id=task_id,
            what="continuity", phase="story_continuity",
        )
    except ScreenplayError as exc:
        logger.warning("[screenplay] continuity check failed: %s", exc)
        return None
    clashes = [{"beat": c.get("beat"), "problem": str(c["problem"]).strip()} for c in reply["contradictions"]]
    logger.info("[screenplay] continuity: %d contradiction(s): %s", len(clashes), clashes)
    return clashes


async def cold_read(
    screenplay: dict[str, Any], *, site_config: Any, pool: Any = None,
    task_id: str | None = None, model: str | None = None,
) -> dict[str, Any] | None:
    """A first-time viewer's account of ``screenplay`` (``story.cold_read``), or ``None``.

    The reader gets only what the audience gets (:func:`_audience_view`) and
    tells the story back: what happens, who wants what, why it ends that way,
    and every moment it could not follow. ``story_cold_read_model`` (empty =
    ``model``, the editor's). ``None`` when it cannot answer, logged.
    """
    reader = str(site_config.get("story_cold_read_model", "") or "").strip() or model or _resolve_model(site_config)

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        problems = []
        if not str(reply.get("story") or "").strip():
            problems.append('FIX: tell the story back in "story"')
        if not isinstance(reply.get("lost", []), list):
            problems.append('FIX: "lost" is a list of strings (empty when you followed everything)')
        return problems, []

    try:
        reply, _ = await _ask_validated(
            "story.cold_read", {"audience_view": _audience_view(screenplay)[:20000]}, _check,
            model=reader, site_config=site_config, pool=pool, task_id=task_id,
            what="cold read", phase="story_cold_read",
        )
    except ScreenplayError as exc:
        logger.warning("[screenplay] cold read failed: %s", exc)
        return None
    reply["lost"] = [str(x).strip() for x in reply.get("lost") or [] if str(x).strip()]
    logger.info("[screenplay] cold read: %d moment(s) lost: %s", len(reply["lost"]), reply["lost"])
    return reply


def _request_from(screenplay: dict[str, Any]) -> ScreenplayRequest:
    """The request a finished screenplay was written for, from its own fields."""
    return ScreenplayRequest(
        topic=str(screenplay.get("topic") or screenplay.get("title") or ""),
        format=str(screenplay.get("format") or "story"),
        cast=tuple(_csv_or_list(screenplay.get("cast"))),
        narrator=bool(screenplay.get("narrator", True)),
        narrator_persona=str(screenplay.get("narrator_persona") or ""),
        target_seconds=float(screenplay.get("target_seconds") or 90.0),
        source=str(screenplay.get("source") or ""),
    )


def normalize_screenplay(raw: dict[str, Any]) -> dict[str, Any]:
    """A whole screenplay reply: the outline fields plus each scene's beats."""
    outline = normalize_outline(raw)
    raw_scenes = [s for s in raw.get("scenes") or [] if isinstance(s, dict)]
    for scene, raw_scene in zip(outline["scenes"], raw_scenes, strict=False):
        scene["beats"] = normalize_beats(raw_scene.get("beats"))
    return outline


def _core(screenplay: dict[str, Any]) -> dict[str, Any]:
    """The parts of a screenplay the writer edits, without bookkeeping fields."""
    return {
        "title": screenplay.get("title", ""),
        "logline": screenplay.get("logline", ""),
        "spine": screenplay.get("spine") or {},
        "main_event": screenplay.get("main_event") or "",
        "translation": screenplay.get("translation") or [],
        "locations": screenplay.get("locations") or [],
        "scenes": [
            {k: scene.get(k) for k in ("scene_id", "location", "setting", "palette", "summary", "turn", "characters", "beats")}
            for scene in screenplay.get("scenes") or []
        ],
    }


async def revise_screenplay(
    screenplay: dict[str, Any],
    instruction: str,
    *,
    site_config: Any,
    pool: Any = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Rewrite ``screenplay`` to address one note (chat drafting).

    One call returns the whole revised screenplay, checked exactly like a new
    one (outline, cast-only speakers, beats), with one FIX repair. Fields the
    writer does not own (cast, format, narrator, length) carry over.
    Raises ScreenplayError when the revision cannot be made valid.
    """
    note = (instruction or "").strip()
    if not note:
        raise ScreenplayError("a revision needs a note saying what to change")
    request = _request_from(screenplay)
    personas = resolve_cast(site_config, request.cast)
    budget = word_budget(request.target_seconds, site_config)
    model = _resolve_model(site_config)

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return validate(normalize_screenplay(reply), request, site_config), []

    reply, _ = await _ask_validated(
        "story.screenplay_revise",
        {
            "site_name": str(site_config.get("site_name", "") or "the studio"),
            "current": json.dumps(_core(screenplay), ensure_ascii=False, indent=1)[:24000],
            "instruction": note[:2000],
            "cast_block": cast_block(personas),
            "narrator_available": "yes" if request.narrator else "no",
            "word_budget": budget,
        },
        _check, model=model, site_config=site_config, pool=pool, task_id=task_id,
        what="revision", phase="story_revise_screenplay",
    )
    revised = normalize_screenplay(reply)
    words = spoken_words(revised)
    total = length_problem(words, budget, site_config)
    carried = {k: v for k, v in screenplay.items()
               if k not in ("title", "logline", "spine", "main_event", "translation", "locations", "scenes")}
    revised.update({
        **carried,
        "spoken_words": words,
        "writer_model": model,
        "notes": [f"whole piece: {total}"] if total else [],
        "revised_with": note[:500],
    })
    logger.info("[screenplay] revised %r for note %r: %d spoken words", revised["title"], note[:80], words)
    return revised


__all__ = [
    "BEAT_KINDS",
    "INTENSITIES",
    "SPINE_PARTS",
    "FORMATS",
    "NARRATOR",
    "ScreenplayError",
    "ScreenplayRequest",
    "build_request",
    "cast_block",
    "check_beats",
    "check_outline",
    "length_problem",
    "normalize_beats",
    "normalize_outline",
    "normalize_screenplay",
    "revise_screenplay",
    "resolve_cast",
    "scene_count",
    "spoken_words",
    "validate",
    "word_budget",
    "write_screenplay",
]
