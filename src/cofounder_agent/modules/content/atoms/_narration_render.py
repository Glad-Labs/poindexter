"""Shared narration-TTS helper for the Stage-2 media render atoms (#689).

Underscore-prefixed so the atom-registry filesystem scan SKIPS it
(``services/atom_registry.py``: files starting with ``_`` are not discovered as
atoms). This is plumbing, not an atom — mirrors ``_media_render.py``.

Synthesizes a narration script into an MP3 via the existing ``PodcastService``
TTS, after appending the per-medium CTA outro. Used by ``media.render_narration``
(long + short video narration) AND ``podcast.render`` (podcast narration), so the
CTA-append + synth + fail-soft contract lives in ONE place.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Structural section labels the script generator emits as spoken-looking
# text. They are stage directions, not narration — TTS read "Hook" aloud at
# the top of a video (#media-render-fixes). The real writer output wraps them
# in square brackets on their own line ("[Opening Hook]") and often prefixes a
# qualifier word ("Opening Hook", "Final CTA"), so the matchers cover four
# shapes: a whole-line ``[...]`` annotation, a leading ``[...]`` prefix on a
# real line, a bare/marked-up label-only line, and a ``Label:`` prefix — while
# leaving prose that merely STARTS with a label word ("Body cameras changed…")
# untouched.
_SECTION_LABELS = (
    r"hook|teaser|intro(?:duction)?|body|segment|section|scene|part|"
    r"outro|conclusion|wrap[\s-]?up|cta|call[\s-]?to[\s-]?action|"
    r"narrator|voice[\s-]?over|vo"
)
# Qualifier words that commonly precede a section label in a stage direction
# ("Opening Hook", "Closing Outro", "Main Body", "Final CTA").
_LABEL_QUALIFIERS = r"opening|closing|main|final"

# A line that is wholly a square-bracket annotation ("[Opening Hook]",
# "[pause]") — always a stage direction in a narration script, drop outright.
_BRACKET_ONLY_RE = re.compile(r"^\s*\[[^\]]*\]\s*$")
# A leading "[...]" annotation on an otherwise-real line
# ("[Opening Hook] In today's world…") — drop the bracket, keep the prose.
_BRACKET_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]\s*")

_LABEL_LINE_RE = re.compile(
    rf"^[\s>#*_\(\-]*(?:(?:{_LABEL_QUALIFIERS})\s+)?(?:{_SECTION_LABELS})"
    rf"\s*\d*\s*[:.\)\]\-–—]+\s*",
    re.IGNORECASE,
)
_LABEL_ONLY_RE = re.compile(
    rf"^[\s>#*_\(\-]*(?:(?:{_LABEL_QUALIFIERS})\s+)?(?:{_SECTION_LABELS})"
    rf"\s*\d*\s*[\]\)\*_>.]*\s*$",
    re.IGNORECASE,
)


def _strip_script_labels(text: str) -> str:
    """Drop structural section labels so TTS doesn't read them aloud."""
    out: list[str] = []
    for line in text.splitlines():
        if _BRACKET_ONLY_RE.match(line.strip()):
            # Whole line is a bracketed stage direction ("[Opening Hook]").
            continue
        # A leading "[...]" annotation on a real line — drop the bracket,
        # keep the sentence.
        line = _BRACKET_PREFIX_RE.sub("", line, count=1)
        if _LABEL_ONLY_RE.match(line):
            # The (de-marked) line is just a label ("Hook", "Opening Hook",
            # "**Outro**") — drop it.
            continue
        # A label prefix on an otherwise-real line ("Hook: VRAM is…") — strip
        # the prefix, keep the sentence.
        out.append(_LABEL_LINE_RE.sub("", line, count=1))
    return "\n".join(out).strip()


async def render_narration(
    *,
    script: str,
    cta_key: str,
    site_config: Any,
    task_id: Any,
    key: str,
) -> str:
    """Synthesize ``script`` (+ the CTA at ``cta_key``) to an MP3.

    Returns the temp render path, or ``""`` on any fail-soft condition (empty
    script, no ``site_config``, or a TTS exception). NEVER raises — a narration
    failure must not halt the media graph.
    """
    text = _strip_script_labels((script or "").strip())
    if not text or site_config is None:
        return ""

    cta = (site_config.get(cta_key, "") or "").strip()
    if cta:
        text = f"{text}\n\n{cta}"

    from services.podcast_service import PodcastService

    try:
        path, _duration = await PodcastService(site_config=site_config).synthesize(
            text, key=key,
        )
    except Exception as exc:  # noqa: BLE001 — TTS failure must not halt the graph
        logger.warning(
            "[_narration_render] synthesis failed (key=%s, task=%s): %s",
            key, task_id, exc,
        )
        return ""
    return path or ""


__all__ = ["render_narration"]
