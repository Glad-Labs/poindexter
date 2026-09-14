"""Presenter personas — a face bound to a voice, stored as settings.

A persona is one presenter identity the media pipeline can speak and show:
the narration voice (Kokoro voice id or a Chatterbox clone reference) and the
reference portrait the speech-to-video render animates
(``video_providers/comfyui.py`` speech path). The 2026-09-14 talking-head
spike rendered a female narration onto a male portrait because the voice was a
setting and the face was a file chosen separately; binding them in one record
makes that mismatch impossible by construction.

**Storage is the key family ``persona.<slug>.<field>`` in ``app_settings``** —
the same shape as the per-niche media policy (``niche.<slug>.media.*``). That
needs no migration, every field reads synchronously from the cached
``SiteConfig`` where the TTS seam and the prompt renders live, and the existing
settings CLI / API / MCP already edit it from a phone. This module only adds
validation, listing by prefix, niche resolution, and the portrait render.

Selection: ``niche.<slug>.media.persona`` → ``media_default_persona`` → none.
A persona whose ``voice_id`` is empty *inherits* ``podcast_tts_voice``, so a
fresh install keeps the narration voice it already had (no silent behaviour
change from seeding a default persona).

Consumers: ``podcast_service._select_voice`` (narration voice),
``poindexter personas`` (CLI), and — next — the ``presenter`` shot source.
See ``docs/architecture/media-personas.md``.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

PERSONA_PREFIX = "persona."
DEFAULT_PERSONA_SETTING = "media_default_persona"

#: Every field a persona carries, in display order. Adding one = add here,
#: seed it for the default persona in ``settings_defaults.py``, document it.
FIELDS: tuple[str, ...] = (
    "display_name",
    "description",
    "voice_provider",
    "voice_id",
    "voice_ref_audio_url",
    "portrait_url",
    "portrait_prompt",
    "portrait_seed",
    "style_policy",
    "enabled",
    "render_prompt_suffix",
)
VOICE_PROVIDERS: tuple[str, ...] = ("kokoro", "chatterbox")
STYLE_POLICIES: tuple[str, ...] = ("photoreal", "stylized")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off", ""}


class PersonaError(ValueError):
    """Invalid slug or field value — the message is operator-facing."""


@dataclass(frozen=True)
class Persona:
    slug: str
    display_name: str = ""
    description: str = ""
    voice_provider: str = "kokoro"
    voice_id: str = ""  # "" = inherit podcast_tts_voice
    voice_ref_audio_url: str = ""
    portrait_url: str = ""
    portrait_prompt: str = ""
    portrait_seed: int | None = None
    style_policy: str = "photoreal"
    enabled: bool = True
    render_prompt_suffix: str = ""

    @property
    def has_portrait(self) -> bool:
        return bool(self.portrait_url)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            **{f: getattr(self, f) for f in FIELDS},
        }


def persona_key(slug: str, field: str) -> str:
    return f"{PERSONA_PREFIX}{slug}.{field}"


def validate_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not _SLUG_RE.match(slug):
        raise PersonaError(
            f"persona slug {slug!r} must be 1-64 chars of a-z, 0-9 and '-', "
            "starting with a letter or digit",
        )
    return slug


def validate_field(field: str, value: Any) -> str:
    """Normalize one field value to its stored string form, or raise."""
    if field not in FIELDS:
        raise PersonaError(
            f"unknown persona field {field!r}; fields: {', '.join(FIELDS)}",
        )
    text = "" if value is None else str(value).strip()
    if field == "voice_provider":
        if text not in VOICE_PROVIDERS:
            raise PersonaError(
                f"voice_provider must be one of {', '.join(VOICE_PROVIDERS)}, got {text!r}",
            )
    elif field == "style_policy":
        if text not in STYLE_POLICIES:
            raise PersonaError(
                f"style_policy must be one of {', '.join(STYLE_POLICIES)}, got {text!r}",
            )
    elif field == "enabled":
        low = text.lower()
        if low in _TRUE:
            return "true"
        if low in _FALSE:
            return "false"
        raise PersonaError(f"enabled must be true or false, got {text!r}")
    elif field == "portrait_seed":
        if text:
            try:
                int(text)
            except ValueError as e:
                raise PersonaError(f"portrait_seed must be an integer, got {text!r}") from e
    return text


def _parse(slug: str, raw: dict[str, str]) -> Persona:
    seed_text = (raw.get("portrait_seed") or "").strip()
    seed: int | None
    try:
        seed = int(seed_text) if seed_text else None
    except ValueError:
        seed = None
    return Persona(
        slug=slug,
        display_name=raw.get("display_name", "") or "",
        description=raw.get("description", "") or "",
        voice_provider=(raw.get("voice_provider") or "kokoro").strip() or "kokoro",
        voice_id=(raw.get("voice_id") or "").strip(),
        voice_ref_audio_url=(raw.get("voice_ref_audio_url") or "").strip(),
        portrait_url=(raw.get("portrait_url") or "").strip(),
        portrait_prompt=raw.get("portrait_prompt", "") or "",
        portrait_seed=seed,
        style_policy=(raw.get("style_policy") or "photoreal").strip() or "photoreal",
        enabled=(raw.get("enabled") or "true").strip().lower() not in _FALSE - {""},
        render_prompt_suffix=raw.get("render_prompt_suffix", "") or "",
    )


def _all_settings(site_config: Any) -> dict[str, str]:
    """The config's key snapshot, or ``{}`` when it has none (a stub without
    ``all()``); ``get_persona`` then falls back to per-key reads, so a failing
    snapshot is a warning, not a silent empty persona list."""
    getter = getattr(site_config, "all", None)
    if not callable(getter):
        return {}
    try:
        return dict(getter())
    except (AttributeError, TypeError, ValueError) as exc:
        logger.warning(
            "[personas] site_config.all() failed (%s: %s) — falling back to "
            "per-key reads; persona listing may be incomplete",
            type(exc).__name__, exc,
        )
        return {}


def get_persona(site_config: Any, slug: str) -> Persona | None:
    """The persona at ``slug``, or ``None`` when no key of its family exists."""
    if site_config is None or not slug:
        return None
    slug = slug.strip().lower()
    raw: dict[str, str] = {}
    snapshot = _all_settings(site_config)
    prefix = f"{PERSONA_PREFIX}{slug}."
    for key, value in snapshot.items():
        if key.startswith(prefix):
            raw[key[len(prefix):]] = "" if value is None else str(value)
    if not raw:
        # No snapshot (or none of this slug's keys in it) — try direct reads,
        # which is how a test's SiteConfig(initial_config=...) also answers.
        for field in FIELDS:
            try:
                value = site_config.get(persona_key(slug, field), None)
            except TypeError:
                value = site_config.get(persona_key(slug, field))
            if value not in (None, ""):
                raw[field] = str(value)
        if not raw:
            return None
    return _parse(slug, raw)


def list_personas(site_config: Any) -> list[Persona]:
    """Every persona that has at least one key, sorted by slug."""
    slugs: set[str] = set()
    for key in _all_settings(site_config):
        if key.startswith(PERSONA_PREFIX):
            rest = key[len(PERSONA_PREFIX):]
            slug, _, field = rest.partition(".")
            if slug and field in FIELDS:
                slugs.add(slug)
    out = [p for p in (get_persona(site_config, s) for s in sorted(slugs)) if p]
    return out


def resolve_persona_for_niche(site_config: Any, niche_slug: str | None) -> Persona | None:
    """``niche.<slug>.media.persona`` → ``media_default_persona`` → ``None``.

    A disabled persona resolves to ``None`` (the caller falls back to whatever
    it did before personas existed), never to a different persona.
    """
    if site_config is None:
        return None
    chosen = ""
    if niche_slug:
        chosen = str(site_config.get(f"niche.{niche_slug}.media.persona", "") or "").strip()
    if not chosen:
        chosen = str(site_config.get(DEFAULT_PERSONA_SETTING, "") or "").strip()
    if not chosen:
        return None
    persona = get_persona(site_config, chosen)
    if persona is None:
        logger.warning(
            "[personas] %r names persona %r but no persona.%s.* key exists",
            f"niche.{niche_slug}.media.persona" if niche_slug else DEFAULT_PERSONA_SETTING,
            chosen, chosen,
        )
        return None
    return persona if persona.enabled else None


def persona_voice(persona: Persona | None, site_config: Any) -> str:
    """The narration voice for ``persona``: its own ``voice_id``, else the
    install's ``podcast_tts_voice`` (inherit), else ``""``."""
    if persona is not None and persona.voice_id:
        return persona.voice_id
    if site_config is None:
        return ""
    return str(site_config.get("podcast_tts_voice", "") or "").strip()


def default_portrait_prompt(persona: Persona) -> str:
    """A portrait prompt from the persona's description and style — used
    when ``portrait_prompt`` is empty, and stored back once a render lands."""
    who = persona.description.strip().rstrip(".") or "a friendly technology presenter"
    if persona.style_policy == "stylized":
        return (
            f"Flat vector illustration of {who}, head and shoulders centred, "
            "facing the viewer, simple geometric shapes, clean outlines, limited "
            "palette, plain dark background, friendly expression, mouth closed, "
            "no text, no letters, no watermark"
        )
    return (
        f"Editorial photograph of {who}, medium shot, head and shoulders centred, "
        "looking straight into the camera and about to speak, mouth relaxed, soft "
        "key light, dark neutral studio background, 85mm lens, natural skin "
        "texture, calm confident expression, no text, no letters, no watermark"
    )


async def upsert_persona(
    settings_service: Any, slug: str, fields: dict[str, Any],
) -> dict[str, str]:
    """Validate and write ``fields`` for ``slug``; returns the stored values.

    ``settings_service`` is anything with ``async set(key, value, category=,
    description=)`` — ``SettingsService`` in production.
    """
    slug = validate_slug(slug)
    stored: dict[str, str] = {}
    for field, value in fields.items():
        text = validate_field(field, value)
        await settings_service.set(
            persona_key(slug, field), text, category="media",
            description=f"Presenter persona {slug}: {field}",
        )
        stored[field] = text
    return stored


def _portrait_key(slug: str, seed: int | None) -> str:
    tag = f"-{seed}" if seed is not None else ""
    return f"personas/{slug}{tag}.png"


async def upload_portrait(persona: Persona, local_path: str, *, site_config: Any) -> str:
    """Upload an existing portrait file for ``persona``; returns its public URL."""
    if not os.path.exists(local_path):
        raise PersonaError(f"portrait file not found: {local_path}")
    from poindexter.services.r2_upload_service import R2UploadService

    url = await R2UploadService(site_config=site_config).upload_to_r2(
        local_path, _portrait_key(persona.slug, persona.portrait_seed), "image/png",
    )
    if not url:
        raise PersonaError(
            "portrait upload failed — check the storage_* settings (R2UploadService "
            "returned no URL)",
        )
    return url


async def render_portrait(
    persona: Persona,
    *,
    site_config: Any,
    seed: int | None = None,
    prompt: str | None = None,
    output_dir: str | None = None,
) -> tuple[str, str, str]:
    """Render a reference portrait through the install's image service and
    upload it. Returns ``(public_url, prompt_used, local_path)``.

    Goes through ``ImageService.generate_image_result`` so the render takes the
    GPU lock like every other operator-surface render (poindexter#1005).
    """
    from poindexter.services.image_service import get_image_service

    text = (prompt or persona.portrait_prompt or default_portrait_prompt(persona)).strip()
    out_dir = output_dir or tempfile.mkdtemp(prefix="persona-portrait-")
    os.makedirs(out_dir, exist_ok=True)
    local = os.path.join(out_dir, f"{persona.slug}-{seed if seed is not None else 'portrait'}.png")
    outcome = await get_image_service(site_config).generate_image_result(text, local)
    if not getattr(outcome, "ok", False) or not os.path.exists(local):
        reason = getattr(outcome, "reason", None) or "image service returned no file"
        detail = getattr(outcome, "detail", None)
        raise PersonaError(f"portrait render failed: {reason}{f' ({detail})' if detail else ''}")
    seeded = Persona(**{**persona.to_dict(), "portrait_seed": seed}) if seed is not None else persona
    # dataclass to_dict includes 'slug' — Persona(**) accepts it
    url = await upload_portrait(seeded, local, site_config=site_config)
    return url, text, local


__all__ = [
    "DEFAULT_PERSONA_SETTING",
    "FIELDS",
    "PERSONA_PREFIX",
    "STYLE_POLICIES",
    "VOICE_PROVIDERS",
    "Persona",
    "PersonaError",
    "default_portrait_prompt",
    "get_persona",
    "list_personas",
    "persona_key",
    "persona_voice",
    "render_portrait",
    "resolve_persona_for_niche",
    "upload_portrait",
    "upsert_persona",
    "validate_field",
    "validate_slug",
]
