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


# Structural section labels the script generator sometimes emits as
# spoken-looking text ("Hook: ...", "Outro", "Segment 2:"). They are
# stage directions, not narration — TTS read "Hook" aloud at the top of
# a video (#media-render-fixes). Stripped line-by-line: a label is removed
# only when it is the WHOLE (de-marked) line, or a prefix followed by a
# ``:`` / ``-`` / ``)`` separator — so prose like "Body cameras changed…"
# is never touched.
_SECTION_LABELS = (
    r"hook|teaser|intro(?:duction)?|body|segment|section|scene|part|"
    r"outro|conclusion|wrap[\s-]?up|cta|call[\s-]?to[\s-]?action|"
    r"narrator|voice[\s-]?over|vo"
)
_LABEL_LINE_RE = re.compile(
    rf"^[\s>#*_\[\(\-]*(?:{_SECTION_LABELS})\s*\d*\s*[:.\)\]\-–—]+\s*",
    re.IGNORECASE,
)
_LABEL_ONLY_RE = re.compile(
    rf"^[\s>#*_\[\(\-]*(?:{_SECTION_LABELS})\s*\d*\s*[\]\)\*_>.]*\s*$",
    re.IGNORECASE,
)


def _strip_script_labels(text: str) -> str:
    """Drop leading structural section labels so TTS doesn't read them aloud."""
    out: list[str] = []
    for line in text.splitlines():
        if _LABEL_ONLY_RE.match(line):
            # The entire line is just a label ("Hook", "**Outro**") — drop it.
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
