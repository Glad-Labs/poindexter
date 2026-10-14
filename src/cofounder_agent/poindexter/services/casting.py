"""Casting — a request in, a drawn and voiced cast of personas out.

Creative studio (poindexter#1134). The screenplay writer can only write for
characters that exist as personas (``persona_service``), and before this
module those were made by hand: a fairytale, or any story with its own
people, could not be asked for at all. Matt (2026-10-13): the system "should
be able to draw its own characters and scenes".

``story.cast_characters`` runs first in the creative chain. One LLM call
(``story.casting``) reads the request and the existing characters, reuses
the ones that fit, and designs the rest: a name, a role, an appearance, a
personality, a voice from the catalog, an energy and a sample line. A look
the request names is written out as a phrase a painter could follow; with
none, the niche's house style applies (``story_visual_style``). Code checks every reply
(reused ids exist, voices come from the catalog, every new character has
looks) and sends problems back once. Then, for each new character:

- **Persona.** ``persona.<id>.*`` rows, so the character is reusable in later
  pieces and appears in the architect's roster.
- **Portrait.** The first is drawn from text by the image service. Every one
  after it is drawn by the image-edit model with the cast's existing
  portraits as style-only references (``reference_frames``), so the whole
  cast shares one look. Every draw is checked by ``qa.cast_frame``: exactly
  this one character, nobody else. The portrait is what every frame of the
  piece is later drawn from, so the scenes come out in the same style.
- **Voice.** Kokoro speaks the sample line in the chosen voice, and that clip
  becomes the character's Chatterbox clone reference with an emotion
  strength from its energy, the treatment Kai's voice won in an audition. A
  clip that cannot be written leaves the character on plain Kokoro.

The outputs (``screenplay_cast``, ``screenplay_visual_style``,
``screenplay_narrator``) are PipelineState channels the writer and the
director read, so no node config has to repeat them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENERGIES: tuple[str, ...] = ("low", "medium", "high")
NO_NARRATOR = "none"


class CastingError(RuntimeError):
    """The cast could not be decided. The message names why."""


@dataclass(frozen=True)
class CastingRequest:
    topic: str
    brief: str = ""
    format: str = "story"
    target_seconds: float = 90.0
    required: tuple[str, ...] = ()
    style_hint: str = ""
    # "" = casting decides, "none" = no narrator, a slug = that persona narrates.
    narrator: str = ""
    # Whose house style applies when the request names no look.
    niche_slug: str | None = None


def _setting(site_config: Any, key: str) -> str:
    from poindexter.services.settings_defaults import DEFAULTS

    value = site_config.get(key, None) if site_config is not None else None
    return str(DEFAULTS[key] if value is None or str(value).strip() == "" else value)


def build_casting_request(inputs: dict[str, Any], site_config: Any) -> CastingRequest:
    from poindexter.services.screenplay import _csv_or_list

    topic = str(inputs.get("topic") or "").strip()
    if not topic:
        raise CastingError("story.cast_characters needs a topic")
    try:
        seconds = float(inputs.get("screenplay_target_seconds") or _setting(site_config, "story_default_target_seconds"))
    except (TypeError, ValueError):
        seconds = 90.0
    return CastingRequest(
        topic=topic,
        brief=str(inputs.get("screenplay_brief") or "").strip(),
        format=str(inputs.get("screenplay_format") or _setting(site_config, "story_default_format")).strip(),
        target_seconds=seconds,
        required=tuple(s.lower() for s in _csv_or_list(inputs.get("screenplay_cast"))),
        style_hint=str(inputs.get("screenplay_visual_style") or "").strip(),
        narrator=str(inputs.get("screenplay_narrator") or "").strip().lower(),
        niche_slug=str(inputs.get("niche_slug") or "").strip() or None,
    )


def voice_catalog(site_config: Any) -> dict[str, str]:
    """``story_voice_catalog``: ``id: description`` entries separated by ``;``."""
    out: dict[str, str] = {}
    for part in _setting(site_config, "story_voice_catalog").split(";"):
        vid, _, desc = part.partition(":")
        if vid.strip():
            out[vid.strip()] = desc.strip()
    return out


def _energy_levels(site_config: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in _setting(site_config, "story_cast_energy_exaggeration").split(","):
        name, _, value = part.partition("=")
        try:
            out[name.strip().lower()] = float(value)
        except ValueError:
            continue
    return out


def roster_text(personas: list[Any]) -> str:
    lines = []
    for p in personas:
        face = "has a portrait" if p.has_portrait else "voice only"
        bits = [b for b in (p.visual_description, p.personality) if b]
        lines.append(f"- {p.slug}: {p.display_name or p.slug} ({face}){'; ' + '; '.join(bits) if bits else ''}")
    return "\n".join(lines) or "(none)"


def _new_id(raw: dict[str, Any]) -> str:
    text = str(raw.get("id") or raw.get("name") or "").strip().lower()
    return re.sub(r"[^a-z0-9-]+", "-", text).strip("-")[:40]


def check_cast(
    reply: dict[str, Any],
    *,
    roster: dict[str, Any],
    catalog: dict[str, str],
    required: tuple[str, ...],
    max_new: int,
    style_hint: str = "",
) -> list[str]:
    """Hard problems with a casting reply, as FIX lines."""
    errors: list[str] = []
    chars = reply.get("characters")
    if not isinstance(chars, list) or not chars:
        return ['FIX: add a "characters" list with at least one character']
    if not style_hint and not str(reply.get("style") or "").strip():
        errors.append('FIX: add a "style": the art style for every picture, as a short phrase')
    reused: set[str] = set()
    new = 0
    for i, ch in enumerate(chars, start=1):
        if not isinstance(ch, dict):
            errors.append(f"FIX character {i}: give an object")
            continue
        reuse = str(ch.get("reuse") or "").strip().lower()
        if reuse:
            if reuse not in roster:
                errors.append(f"FIX character {i}: {reuse!r} cannot be reused in this piece; reuse one of {sorted(roster) or 'none'} or design a new character")
            reused.add(reuse)
            continue
        new += 1
        cid = _new_id(ch)
        if not cid:
            errors.append(f'FIX character {i}: add an "id" (lowercase letters and dashes)')
        if not str(ch.get("name") or "").strip():
            errors.append(f'FIX character {i}: add a "name"')
        if not str(ch.get("appearance") or "").strip():
            errors.append(f'FIX character {i}: add an "appearance" a designer could draw the same way every time')
        if str(ch.get("voice") or "").strip() not in catalog:
            errors.append(f'FIX character {i}: "voice" must be one of {sorted(catalog)}')
    for slug in required:
        if slug not in reused:
            errors.append(f'FIX: {slug!r} must be in the cast: add {{"reuse": "{slug}"}}')
    if new > max_new:
        errors.append(f"FIX: at most {max_new} new characters; reuse or drop {new - max_new}")
    narrator = reply.get("narrator")
    if isinstance(narrator, dict) and narrator.get("use") and not str(narrator.get("reuse") or "").strip():
        if str(narrator.get("voice") or "").strip() not in catalog:
            errors.append(f'FIX narrator: "voice" must be one of {sorted(catalog)}')
    elif isinstance(narrator, dict) and narrator.get("use"):
        if str(narrator.get("reuse")).strip().lower() not in roster:
            errors.append("FIX narrator: \"reuse\" must name an existing character, or leave it empty and describe a new voice")
    return errors


def _drawn_in(persona: Any, look: str) -> bool:
    """Was this persona's portrait drawn in ``look``? (its prompt names it)"""
    look = (look or "").strip().lower()
    return bool(look) and look in str(getattr(persona, "portrait_prompt", "") or "").lower()


def _unique_slug(base: str, taken: set[str]) -> str:
    slug, n = base or "character", 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _energy(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    return text if text in ENERGIES else "medium"


async def _ffmpeg_to_wav(src: str, dst: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-y", "-i", src, "-ac", "1", "-ar", "24000", dst,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("[casting] ffmpeg could not convert %s: %s", src, (err or b"").decode()[:300])
    return proc.returncode == 0 and os.path.exists(dst)


async def build_voice(slug: str, voice_id: str, energy: str, sample_line: str, *, site_config: Any) -> dict[str, str]:
    """Persona voice fields for a new character.

    With ``story_cast_voice_engine=chatterbox`` the Kokoro voice reads the
    sample line, and the clip becomes the Chatterbox clone reference. Any
    failure keeps the character on plain Kokoro: a flat voice beats none.
    """
    kokoro = {"voice_provider": "kokoro", "voice_id": voice_id}
    if _setting(site_config, "story_cast_voice_engine").strip().lower() != "chatterbox" or not sample_line.strip():
        return kokoro
    from poindexter.services.persona_service import Persona
    from poindexter.services.podcast_service import PodcastService

    target_dir = Path(_setting(site_config, "story_cast_voice_dir"))
    speaker = Persona(slug=slug, voice_provider="kokoro", voice_id=voice_id)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cast-voice-") as tmp:
            raw = await PodcastService(site_config=site_config).synthesize_line(
                sample_line, speaker, os.path.join(tmp, f"{slug}.mp3"),
            )
            dst = str(target_dir / f"{slug}.wav")
            if not await _ffmpeg_to_wav(raw, dst):
                logger.warning("[casting] no voice clip for %s; it stays on Kokoro %s", slug, voice_id)
                return kokoro
    except (OSError, RuntimeError) as exc:
        logger.warning("[casting] no voice clip for %s (%s); it stays on Kokoro %s", slug, exc, voice_id)
        return kokoro
    level = _energy_levels(site_config).get(energy, 0.7)
    prefix = _setting(site_config, "story_cast_voice_ref_prefix").rstrip("/")
    return {
        "voice_provider": "chatterbox", "voice_id": voice_id,
        "voice_ref_audio_url": f"{prefix}/{slug}.wav", "voice_exaggeration": str(level),
    }


async def _fetch(url: str, dest: Path) -> str | None:
    from poindexter.services.video_renderers.shot_list_renderer import _fetch_presenter_portrait

    return await _fetch_presenter_portrait(url, dest, None)


async def draw_portrait(
    *,
    slug: str,
    name: str,
    appearance: str,
    style: str,
    anchors: list[str],
    work: Path,
    site_config: Any,
    pool: Any,
    identity: str = "",
) -> tuple[str | None, str]:
    """Draw ``slug``'s portrait; ``(local_path, prompt)``.

    With ``anchors`` (other cast portraits) the image-edit model draws it in
    their style; without, the image service draws it from text. With
    ``identity`` (the character's name), ``anchors`` is that character's own
    portrait and the edit model redraws them in ``style``. Each draw is
    checked for exactly this one character and redrawn up to
    ``story_portrait_attempts`` times; the last draw stands if none passes.
    """
    from poindexter.services import reference_frames as rf
    from poindexter.services.image_service import get_image_service
    from poindexter.services.video_renderers.shot_vision_qa import check_cast_frame

    prompt = (
        _setting(site_config, "story_portrait_template")
        .replace("{style}", style).replace("{appearance}", appearance)
    ).strip(" ,")
    size = int(float(_setting(site_config, "story_portrait_size")))
    attempts = max(1, int(float(_setting(site_config, "story_portrait_attempts"))))
    local = str(work / f"{slug}.png")
    drawn = False
    for attempt in range(1, attempts + 1):
        if anchors:
            ok, why = await rf.render_reference_frame(
                prompt=rf.compose_reference_prompt(
                    prompt, [identity] if identity else [], style_only=not identity,
                    site_config=site_config,
                ),
                reference_paths=anchors, output_path=local, width=size, height=size,
                site_config=site_config,
            )
            if not ok:
                logger.warning("[casting] %s portrait draw %d failed: %s", slug, attempt, why)
                continue
        else:
            outcome = await get_image_service(site_config).generate_image_result(prompt, local)
            if not getattr(outcome, "ok", False) or not os.path.exists(local):
                logger.warning("[casting] %s portrait render %d failed: %s", slug, attempt,
                               getattr(outcome, "reason", "no file"))
                continue
        drawn = True
        verdict = await check_cast_frame(
            frame_path=local, required=[(name, appearance)], cast=[(name, appearance)],
            site_config=site_config, pool=pool,
        )
        if verdict.ok is not False:
            break
        logger.warning("[casting] %s portrait draw %d rejected: %s people, %s",
                       slug, attempt, verdict.people, verdict.reason)
    return (local if drawn else None), prompt


async def cast_characters(
    request: CastingRequest,
    *,
    site_config: Any,
    pool: Any,
    task_id: str | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    """Decide, draw and voice the cast. Raises CastingError on a hard failure."""
    from poindexter.services.persona_service import (
        Persona,
        list_personas,
        upload_portrait,
        upsert_persona,
    )
    from poindexter.services.screenplay import _ask_validated, _resolve_model

    if settings is None:
        from poindexter.services.settings_service import SettingsService

        settings = SettingsService(pool)
    personas = [p for p in list_personas(site_config) if p.enabled]
    everyone = {p.slug: p for p in personas}
    catalog = voice_catalog(site_config)
    max_new = int(float(_setting(site_config, "story_cast_max_new")))
    missing = [s for s in request.required if s not in everyone]
    if missing:
        raise CastingError(f"required cast {missing} are not enabled personas")
    from poindexter.services.media_subject_policy import story_visual_style

    # A look the request asked for, or the niche's house style. Never a free
    # pick: an unasked-for piece is drawn in the company's style.
    asked_style, asked = story_visual_style(site_config, request.niche_slug, request.style_hint)
    # Who may be reused: a voice-only character, one whose portrait was drawn
    # in this look, and anyone the request names. Matt (2026-10-14), on a
    # neon retelling that restyled the watercolour Ruby, Wolf and Marta: the
    # three came out in three different styles, "they should really be new
    # characters". Everyone else is designed fresh, and the new faces are
    # drawn as one set, each anchored on the ones before it.
    personas = [
        p for p in personas
        if not p.has_portrait or _drawn_in(p, asked_style) or p.slug in request.required
    ]
    roster = {p.slug: p for p in personas}

    def _check(reply: dict[str, Any]) -> tuple[list[str], list[str]]:
        return check_cast(
            reply, roster=roster, catalog=catalog, required=request.required,
            max_new=max_new, style_hint=asked_style,
        ), []

    try:
        reply, _ = await _ask_validated(
            "story.casting",
            {
                "site_name": str(site_config.get("site_name", "") or "the studio"),
                "topic": request.topic,
                "brief": request.brief or "(none)",
                "format_name": request.format,
                "target_seconds": int(round(request.target_seconds)),
                "roster_block": roster_text(personas),
                "required": ", ".join(request.required) or "(nobody in particular)",
                "style_hint": asked_style,
                "voice_catalog": "\n".join(f"- {k}: {v}" for k, v in catalog.items()),
                "max_new": max_new,
            },
            _check, model=_resolve_model(site_config), site_config=site_config, pool=pool,
            task_id=task_id, what="cast", phase="story_cast_characters",
        )
    except Exception as exc:  # noqa: BLE001 — the library's own error names the FIX lines
        raise CastingError(str(exc)) from exc

    # A requested look comes back written out in words a painter can follow,
    # and that phrase wins. The house style is used exactly as configured.
    style = (str(reply.get("style") or "").strip() or asked_style) if asked else asked_style
    taken = set(everyone)
    cast: list[str] = []
    new: list[dict[str, Any]] = []
    for ch in reply.get("characters") or []:
        reuse = str(ch.get("reuse") or "").strip().lower()
        if reuse:
            if reuse not in cast:
                cast.append(reuse)
            continue
        slug = _unique_slug(_new_id(ch), taken)
        taken.add(slug)
        new.append({**ch, "slug": slug, "energy": _energy(ch.get("energy"))})
        cast.append(slug)

    for ch in new:
        await upsert_persona(settings, ch["slug"], {
            "display_name": str(ch.get("name") or ch["slug"]).strip(),
            "description": str(ch.get("role") or "").strip(),
            "appearance": str(ch.get("appearance") or "").strip(),
            "personality": str(ch.get("personality") or "").strip(),
            "style_policy": "stylized", "enabled": "true",
            "voice_provider": "kokoro", "voice_id": str(ch.get("voice") or "").strip(),
        })

    narrator_slug = request.narrator
    narrator_new: dict[str, Any] | None = None
    got = reply.get("narrator")
    raw_narrator: dict[str, Any] = got if isinstance(got, dict) else {}
    if not narrator_slug and raw_narrator.get("use"):
        reuse = str(raw_narrator.get("reuse") or "").strip().lower()
        if reuse in roster:
            narrator_slug = reuse
        else:
            narrator_slug = _unique_slug(_new_id({"name": raw_narrator.get("name") or "storyteller"}), taken)
            taken.add(narrator_slug)
            narrator_new = {**raw_narrator, "slug": narrator_slug, "energy": _energy(raw_narrator.get("energy"))}
            await upsert_persona(settings, narrator_slug, {
                "display_name": str(raw_narrator.get("name") or "Narrator").strip(),
                "description": "the narrator, heard and never seen",
                "style_policy": "stylized", "enabled": "true",
                "voice_provider": "kokoro", "voice_id": str(raw_narrator.get("voice") or "").strip(),
            })
    elif not narrator_slug:
        narrator_slug = NO_NARRATOR

    # Portraits. Reused faces already drawn in this look anchor the new ones,
    # so the cast is one set.
    work = Path(tempfile.mkdtemp(prefix="casting-"))
    anchors: list[str] = []
    for slug in cast:
        persona = roster.get(slug)
        if persona is not None and persona.has_portrait and (
            _drawn_in(persona, asked_style) or _drawn_in(persona, style)
        ):
            got = await _fetch(persona.portrait_url, work / f"anchor_{slug}.png")
            if got:
                anchors.append(got)
    from poindexter.services import reference_frames as rf
    from poindexter.services.persona_service import PersonaError

    use_refs = rf.enabled(site_config)
    cleared = False

    async def _free_card() -> None:
        # The image-edit model needs the card the image service holds.
        nonlocal cleared
        if cleared:
            return
        from poindexter.services.video_renderers.shot_list_renderer import (
            _clear_image_gen_for_hero,
        )

        await _clear_image_gen_for_hero(site_config)
        cleared = True

    # A character the request names whose portrait is in another look is
    # redrawn in this one, for this piece only (the shot list carries it as
    # cast_portraits): every frame is drawn from the portraits. The persona
    # keeps its own portrait. Unnamed characters in another look are never
    # offered for reuse in the first place (see ``personas`` above).
    restyled: dict[str, str] = {}
    for slug in cast if use_refs else []:
        persona = roster.get(slug)
        if persona is None or not persona.has_portrait:
            continue
        if _drawn_in(persona, asked_style) or _drawn_in(persona, style):
            continue
        own = await _fetch(persona.portrait_url, work / f"own_{slug}.png")
        if not own:
            continue
        await _free_card()
        local, _prompt = await draw_portrait(
            slug=f"{slug}-restyled", name=persona.display_name or slug,
            appearance=persona.visual_description, style=style, anchors=[own],
            identity=persona.display_name or slug, work=work, site_config=site_config, pool=pool,
        )
        try:
            if not local:
                raise PersonaError("no restyled portrait could be drawn")
            seeded = Persona(slug=slug, portrait_seed=zlib.crc32(style.encode("utf-8")))
            restyled[slug] = await upload_portrait(seeded, local, site_config=site_config)
        except PersonaError as exc:
            logger.warning("[casting] %s keeps its own portrait (%s)", slug, exc)
            continue
        anchors.append(local)

    drawn: dict[str, str] = {}
    for ch in new:
        refs = anchors[-rf.MAX_REFERENCES:] if use_refs else []
        if refs:
            await _free_card()
        local, prompt = await draw_portrait(
            slug=ch["slug"], name=str(ch.get("name") or ch["slug"]),
            appearance=str(ch.get("appearance") or ""), style=style,
            anchors=refs, work=work, site_config=site_config, pool=pool,
        )
        try:
            if not local:
                raise PersonaError("no portrait could be drawn")
            url = await upload_portrait(Persona(slug=ch["slug"]), local, site_config=site_config)
        except PersonaError as exc:
            logger.warning("[casting] %s has no portrait (%s); it can speak only over stills", ch["slug"], exc)
            continue
        await upsert_persona(settings, ch["slug"], {"portrait_url": url, "portrait_prompt": prompt})
        drawn[ch["slug"]] = url
        anchors.append(local)

    voices: dict[str, str] = {}
    for ch in [*new, *([narrator_new] if narrator_new else [])]:
        fields = await build_voice(
            ch["slug"], str(ch.get("voice") or ""), ch["energy"],
            str(ch.get("sample_line") or ""), site_config=site_config,
        )
        await upsert_persona(settings, ch["slug"], fields)
        voices[ch["slug"]] = fields.get("voice_provider", "kokoro")

    reload = getattr(site_config, "reload", None)
    if reload is not None and pool is not None:
        await reload(pool)
    sheet = {
        "style": style,
        "cast": cast,
        "reused": [s for s in cast if s in roster],
        "created": [ch["slug"] for ch in new],
        "portraits": drawn,
        "restyled": restyled,
        "voices": voices,
        "narrator": narrator_slug,
    }
    logger.info("[casting] %s", sheet)
    return {
        "screenplay_cast": cast,
        "screenplay_visual_style": style,
        "screenplay_narrator": narrator_slug,
        "cast_sheet": sheet,
        "cast_portraits": restyled,
    }


__all__ = [
    "CastingError",
    "CastingRequest",
    "build_casting_request",
    "build_voice",
    "cast_characters",
    "check_cast",
    "draw_portrait",
    "roster_text",
    "voice_catalog",
]
