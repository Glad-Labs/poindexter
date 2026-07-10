"""Pure helpers for writer-placed image markers.

The writer (blog-generation SKILL.md) emits ``[IMAGE: subject]`` inline and one
``[HERO-IMAGE: subject]`` first line. These functions extract the hero and
number the inline markers into the ``[IMAGE-N: …]`` form the rest of the
pipeline parses. No I/O — trivially unit-testable.
"""
from __future__ import annotations

import re

_HERO_RE = re.compile(
    r"^[ \t]*\[HERO-IMAGE:\s*([^\]]*)\][ \t]*\n?", re.IGNORECASE | re.MULTILINE
)
_UNNUMBERED_RE = re.compile(r"\[IMAGE:\s*([^\]]*)\]", re.IGNORECASE)


def extract_hero_subject(content: str) -> tuple[str, str | None]:
    """Return ``(content_without_hero_line, hero_subject_or_None)``. First match wins."""
    m = _HERO_RE.search(content)
    if not m:
        return content, None
    subject = (m.group(1) or "").strip()
    stripped = _HERO_RE.sub("", content, count=1)
    return stripped, (subject or None)


def number_inline_markers(content: str, max_inline: int) -> str:
    """Convert ``[IMAGE: x]`` → ``[IMAGE-N: x]`` in document order.

    Markers beyond ``max_inline`` are stripped so a runaway writer can't flood
    a post with images (the cap lives in ``writer_max_inline_images``).
    """
    counter = {"n": 0}

    def _sub(match: re.Match[str]) -> str:
        counter["n"] += 1
        if counter["n"] > max_inline:
            return ""  # strip extras beyond the cap
        desc = (match.group(1) or "").strip()
        return f"[IMAGE-{counter['n']}: {desc}]"

    return _UNNUMBERED_RE.sub(_sub, content)


__all__ = ["extract_hero_subject", "number_inline_markers"]
