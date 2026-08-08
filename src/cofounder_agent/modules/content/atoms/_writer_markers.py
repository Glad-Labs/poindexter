"""Pure helpers for writer-placed image markers.

The writer (blog-generation SKILL.md) emits ``[IMAGE: subject]`` inline, one
``[HERO-IMAGE: subject]`` first line, and — on posts about Poindexter itself —
``[SCREENSHOT: target-key]``. These functions extract the hero and number the
inline markers into the ``[IMAGE-N: …]`` form the rest of the pipeline parses.
No I/O — trivially unit-testable.

Screenshot markers share ONE numbering sequence with ordinary image markers,
because ``content.inject_images`` matches ``image_results`` back to
placeholders by number. They are numbered in document order alongside
``[IMAGE:]`` and carry a ``screenshot:`` prefix in the description, which
``content.plan_image_markers`` splits back out into a ``screenshot_target``.
The prefix (rather than a separate channel) is what lets the target survive
the round-trip through the article text, which is the only thing that
persists between those two atoms.
"""
from __future__ import annotations

import re

#: Description prefix that marks a numbered placeholder as a screenshot slot.
SCREENSHOT_PREFIX = "screenshot:"

_HERO_RE = re.compile(
    r"^[ \t]*\[HERO-IMAGE:\s*([^\]]*)\][ \t]*\n?", re.IGNORECASE | re.MULTILINE
)
# Matches [IMAGE: …] and [SCREENSHOT: …] in one pass so the two share a single
# document-order counter. Group 1 is the keyword, group 2 the payload.
_UNNUMBERED_RE = re.compile(
    r"\[(IMAGE|SCREENSHOT):\s*([^\]]*)\]", re.IGNORECASE,
)


def extract_hero_subject(content: str) -> tuple[str, str | None]:
    """Return ``(content_without_hero_line, hero_subject_or_None)``. First match wins."""
    m = _HERO_RE.search(content)
    if not m:
        return content, None
    subject = (m.group(1) or "").strip()
    stripped = _HERO_RE.sub("", content, count=1)
    return stripped, (subject or None)


def number_inline_markers(content: str, max_inline: int) -> str:
    """Convert ``[IMAGE: x]`` / ``[SCREENSHOT: k]`` → ``[IMAGE-N: …]``.

    Both forms are numbered in a single document-order sequence.
    ``[SCREENSHOT: k]`` becomes ``[IMAGE-N: screenshot:k]`` so the target
    survives into ``image_plans``.

    Markers beyond ``max_inline`` are stripped so a runaway writer can't flood
    a post with images (the cap lives in ``writer_max_inline_images``).
    """
    counter = {"n": 0}

    def _sub(match: re.Match[str]) -> str:
        counter["n"] += 1
        if counter["n"] > max_inline:
            return ""  # strip extras beyond the cap
        keyword = (match.group(1) or "").upper()
        payload = (match.group(2) or "").strip()
        desc = (
            f"{SCREENSHOT_PREFIX}{payload}"
            if keyword == "SCREENSHOT"
            else payload
        )
        return f"[IMAGE-{counter['n']}: {desc}]"

    return _UNNUMBERED_RE.sub(_sub, content)


def split_screenshot_target(desc: str) -> tuple[str, str | None]:
    """Return ``(desc_without_prefix, screenshot_target_or_None)``.

    ``"screenshot:qa-rails"`` → ``("qa-rails", "qa-rails")``. The description
    is kept equal to the target so anything that logs or displays the plan
    still shows something meaningful.
    """
    stripped = (desc or "").strip()
    if not stripped.lower().startswith(SCREENSHOT_PREFIX):
        return stripped, None
    target = stripped[len(SCREENSHOT_PREFIX):].strip()
    return target, (target or None)


__all__ = [
    "SCREENSHOT_PREFIX",
    "extract_hero_subject",
    "number_inline_markers",
    "split_screenshot_target",
]
