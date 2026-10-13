"""Screenplay writer — a topic and a cast in, a validated screenplay out.

Creative studio phase 2 (poindexter#1136; epic and design: poindexter#1134). The
``story.write_screenplay`` atom delegates here, so the architect can put a
writer in front of any downstream chain (dialogue audio, a shot list, or
nothing at all).

A screenplay is plain JSON::

    {"title", "logline", "format", "cast": [slug], "narrator": bool,
     "scenes": [{"scene_id", "setting", "summary", "characters": [slug],
                 "beats": [{"kind": "line", "speaker": slug|"narrator",
                            "text", "delivery"},
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
        target = float(
            inputs.get("screenplay_target_seconds")
            or site_config.get_float("story_default_target_seconds", 90.0)
        )
    except (TypeError, ValueError) as exc:
        raise ScreenplayError(
            f"screenplay_target_seconds must be a number, got "
            f"{inputs.get('screenplay_target_seconds')!r}",
        ) from exc
    if target <= 0:
        raise ScreenplayError("screenplay_target_seconds must be positive")
    return ScreenplayRequest(
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
        else:
            item["characters"] = _csv_or_list(beat.get("characters"))
        out.append(item)
    return out


def normalize_outline(raw: dict[str, Any]) -> dict[str, Any]:
    scenes: list[dict[str, Any]] = []
    for i, scene in enumerate(raw.get("scenes") or [], start=1):
        if not isinstance(scene, dict):
            continue
        scenes.append({
            "scene_id": str(scene.get("scene_id") or f"s{i}").strip() or f"s{i}",
            "setting": str(scene.get("setting") or "").strip(),
            "summary": str(scene.get("summary") or "").strip(),
            "characters": _csv_or_list(scene.get("characters")),
        })
    return {
        "title": str(raw.get("title") or "").strip(),
        "logline": str(raw.get("logline") or "").strip(),
        "scenes": scenes,
    }


def check_outline(outline: dict[str, Any], request: ScreenplayRequest) -> list[str]:
    """Hard problems with a normalized outline."""
    errors: list[str] = []
    cast = set(request.cast)
    if not outline["title"]:
        errors.append("FIX: add a non-empty \"title\"")
    if not outline["scenes"]:
        errors.append("FIX: add at least one scene")
    for scene in outline["scenes"]:
        sid = scene["scene_id"]
        if not scene["setting"]:
            errors.append(f"FIX scene {sid!r}: add a \"setting\" (one sentence: place and mood)")
        if not scene["summary"]:
            errors.append(f"FIX scene {sid!r}: add a \"summary\" of what happens")
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
            for name in beat["characters"]:
                if name not in cast:
                    errors.append(
                        f"FIX scene {scene_id!r}: action names {name!r}, who is not in the cast",
                    )
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
    errors = check_outline(screenplay, request)
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
            phase="story_write_screenplay",
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
        f"- {s['scene_id']}: {s['summary']} (setting: {s['setting']}; "
        f"characters: {', '.join(s['characters']) or 'none'})"
        for s in outline["scenes"]
    )


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
    }
    ask = {"model": model, "site_config": site_config, "pool": pool, "task_id": task_id}

    def _check_outline(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return check_outline(normalize_outline(reply), request), []

    raw_outline, _ = await _ask_validated(
        "story.outline", {**common, "scene_count": scene_count(request.target_seconds, site_config)},
        _check_outline, what="outline", **ask,
    )
    outline = normalize_outline(raw_outline)
    per_scene = budget / len(outline["scenes"])
    notes: list[str] = []
    recent: list[str] = []
    scenes: list[dict[str, Any]] = []
    for scene in outline["scenes"]:
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
                "scene_id": sid,
                "scene_setting": scene["setting"],
                "scene_summary": scene["summary"],
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

    screenplay: dict[str, Any] = {
        "title": outline["title"], "logline": outline["logline"], "scenes": scenes,
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
    return screenplay


__all__ = [
    "BEAT_KINDS",
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
    "resolve_cast",
    "scene_count",
    "spoken_words",
    "validate",
    "word_budget",
    "write_screenplay",
]
