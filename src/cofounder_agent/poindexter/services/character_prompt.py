"""Join a cast's appearance into shot prompts, in code (poindexter#1135).

A recurring character stays on-model only if every still that shows it is
generated from the same description of what it looks like. Asking the director
LLM to repeat that description in each prompt fails quietly: it paraphrases,
drops the goggles in shot 9, and the fox changes colour. So the description
lives once, on the persona (``persona.<slug>.appearance``), and this module
puts it into each prompt that names the character in ``Shot.characters``.

Measured 2026-10-12 (``/data/comfyui-spike/cartoon-demo``): the same
appearance text plus a fixed seed kept one character consistent across three
art styles and two scenes in Qwen-Image.

The appearance goes FIRST: diffusion models weight the start of a prompt most,
and the scene text that follows still sets composition and action.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: ``Shot.prompt`` max_length — the composed prompt must still validate.
MAX_PROMPT_CHARS = 2000

#: Sources whose render reads ``Shot.prompt`` as an image prompt.
PROMPTED_SOURCES = frozenset({"image_gen", "image_kenburns", "generative"})


def character_descriptions(site_config: Any, slugs: list[str]) -> list[str]:
    """``"<display name>: <appearance>"`` for each slug that resolves.

    An unknown, disabled, or description-less persona is skipped with a
    warning rather than failing the render: a still without its character is
    a weaker shot, but no shot at all sinks the whole video.
    """
    from poindexter.services.persona_service import get_persona

    out: list[str] = []
    for slug in slugs:
        persona = get_persona(site_config, slug)
        if persona is None or not persona.enabled:
            logger.warning(
                "[character_prompt] shot names character %r but no enabled "
                "persona.%s.* exists; the still renders without it",
                slug, slug,
            )
            continue
        looks = persona.visual_description
        if not looks:
            logger.warning(
                "[character_prompt] persona %r has no appearance or description; "
                "set one with `poindexter personas set %s appearance \"...\"`",
                slug, slug,
            )
            continue
        name = (persona.display_name or slug).strip()
        out.append(f"{name}: {looks.rstrip('.')}")
    return out


def compose_character_prompt(prompt: str, descriptions: list[str]) -> str:
    """``prompt`` with the character descriptions put in front of it.

    A description already present in the prompt is not repeated, so composing
    twice (a re-render of a stored list) is a no-op.
    """
    base = (prompt or "").strip()
    missing = [d for d in descriptions if d.split(": ", 1)[-1].lower() not in base.lower()]
    if not missing:
        return base
    cast = "; ".join(missing)
    composed = f"{cast}. {base}" if base else cast
    return composed[:MAX_PROMPT_CHARS]


def apply_character_sheets(shots: list[Any], site_config: Any) -> list[Any]:
    """Return ``shots`` with every prompted shot's characters joined into its
    prompt. Shots without characters, and sources that do not read a prompt,
    pass through unchanged (same object)."""
    if site_config is None:
        return shots
    out: list[Any] = []
    cache: dict[tuple[str, ...], list[str]] = {}
    for shot in shots:
        chars = list(getattr(shot, "characters", None) or [])
        if not chars or shot.source not in PROMPTED_SOURCES:
            out.append(shot)
            continue
        key = tuple(chars)
        if key not in cache:
            cache[key] = character_descriptions(site_config, chars)
        composed = compose_character_prompt(shot.prompt or "", cache[key])
        out.append(shot if composed == (shot.prompt or "") else shot.model_copy(update={"prompt": composed}))
    return out


__all__ = [
    "MAX_PROMPT_CHARS",
    "PROMPTED_SOURCES",
    "apply_character_sheets",
    "character_descriptions",
    "compose_character_prompt",
]
