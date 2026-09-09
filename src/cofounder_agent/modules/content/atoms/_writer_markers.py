"""Pure helpers for writer-placed image markers.

The writer (blog-generation SKILL.md) emits ``[IMAGE: subject]`` inline, one
``[HERO-IMAGE: subject]`` first line, and — on posts about Poindexter itself —
``[SCREENSHOT: target-key]``, and ``[CHART: chart-key]`` where a section makes
a claim our own measurements can plot. These functions extract the hero and
number the inline markers into the ``[IMAGE-N: …]`` form the rest of the
pipeline parses.
No I/O — trivially unit-testable.

Screenshot markers share ONE numbering sequence with ordinary image markers,
because ``content.inject_images`` matches ``image_results`` back to
placeholders by number. They are numbered in document order alongside
``[IMAGE:]`` and carry a ``screenshot:`` prefix in the description, which
``content.plan_image_markers`` splits back out into a ``screenshot_target``.
The prefix (rather than a separate channel) is what lets the target survive
the round-trip through the article text, which is the only thing that
persists between those two atoms.

One sequence, TWO budgets. ``[SCREENSHOT:]`` / ``[CHART:]`` are *evidence*
markers — they show the reader a real screen or real measurements — while
``[IMAGE:]`` is an illustration. They used to share the single
``writer_max_inline_images`` cap, so a draft that placed one screenshot had
spent a third of its illustration budget on it, and (worse) the planner atom
read "markers present" as "illustrations planned" and never ran the Image
Decision Agent. Every canonical_blog draft from 2026-09-02 that took the
screenshot offer shipped with that one dashboard capture and no generated
images at all. ``number_inline_markers`` now caps the kinds separately, and
:func:`is_evidence_desc` lets the planner count only the illustrations when
deciding how many slots are left to top up.
"""
from __future__ import annotations

import re

#: Description prefix that marks a numbered placeholder as a screenshot slot.
SCREENSHOT_PREFIX = "screenshot:"
CHART_PREFIX = "chart:"

_HERO_RE = re.compile(
    r"^[ \t]*\[HERO-IMAGE:\s*([^\]]*)\][ \t]*\n?", re.IGNORECASE | re.MULTILINE
)
# Matches [IMAGE: …] and [SCREENSHOT: …] in one pass so the two share a single
# document-order counter. Group 1 is the keyword, group 2 the payload.
_UNNUMBERED_RE = re.compile(
    r"\[(IMAGE|SCREENSHOT|CHART):\s*([^\]]*)\]", re.IGNORECASE,
)


def extract_hero_subject(content: str) -> tuple[str, str | None]:
    """Return ``(content_without_hero_line, hero_subject_or_None)``. First match wins."""
    m = _HERO_RE.search(content)
    if not m:
        return content, None
    subject = (m.group(1) or "").strip()
    stripped = _HERO_RE.sub("", content, count=1)
    return stripped, (subject or None)


#: The numbered form every downstream atom parses. Group 1 is the number,
#: group 2 the description (which may carry an evidence prefix).
PLACEHOLDER_RE = re.compile(r"\[IMAGE-(\d+)(?::\s*([^\]]*))?\]")

_EVIDENCE_PREFIXES = (SCREENSHOT_PREFIX, CHART_PREFIX)


def is_evidence_desc(desc: str) -> bool:
    """True when a numbered placeholder's description names a screenshot or chart slot.

    Evidence slots are filled by the ScreenshotProvider / ChartProvider, never
    by image-gen, so they must not count against the illustration budget.
    """
    stripped = (desc or "").strip().lower()
    return stripped.startswith(_EVIDENCE_PREFIXES)


def number_inline_markers(
    content: str,
    max_inline: int,
    max_evidence_per_kind: int | None = None,
) -> str:
    """Convert ``[IMAGE: x]`` / ``[SCREENSHOT: k]`` / ``[CHART: k]`` → ``[IMAGE-N: …]``.

    Every kind is numbered in ONE document-order sequence (``inject_images``
    matches results back by number). ``[SCREENSHOT: k]`` becomes
    ``[IMAGE-N: screenshot:k]`` and ``[CHART: k]`` becomes
    ``[IMAGE-N: chart:k]`` so the target survives into ``image_plans``.

    Budgets:

    * ``max_inline`` caps ordinary ``[IMAGE:]`` illustrations
      (``writer_max_inline_images``).
    * ``max_evidence_per_kind``, when given, caps ``[SCREENSHOT:]`` and
      ``[CHART:]`` *each* on their own count (``writer_max_evidence_per_kind``)
      — an evidence marker no longer spends an illustration slot. The prompt
      asks for "at most one of each"; this is what actually enforces it.
    * ``None`` keeps the legacy shape where every kind shares ``max_inline``.

    Markers over a budget are stripped so a runaway writer can't flood a post
    with images.
    """
    counts = {"n": 0, "IMAGE": 0, "SCREENSHOT": 0, "CHART": 0}

    def _sub(match: re.Match[str]) -> str:
        keyword = (match.group(1) or "").upper()
        if keyword not in counts:
            keyword = "IMAGE"
        if max_evidence_per_kind is None or keyword == "IMAGE":
            budget_key, budget = ("n" if max_evidence_per_kind is None else "IMAGE"), max_inline
        else:
            budget_key, budget = keyword, max_evidence_per_kind
        counts[budget_key] += 1
        if counts[budget_key] > budget:
            return ""  # strip extras beyond this kind's budget
        if budget_key != "n":
            counts["n"] += 1
        payload = (match.group(2) or "").strip()
        # Each keyword carries its own prefix so the plan builder can route
        # the slot without re-parsing the original marker.
        prefix = {"SCREENSHOT": SCREENSHOT_PREFIX, "CHART": CHART_PREFIX}.get(keyword, "")
        desc = f"{prefix}{payload}"
        return f"[IMAGE-{counts['n']}: {desc}]"

    return _UNNUMBERED_RE.sub(_sub, content)


def renumber_placeholders(content: str) -> str:
    """Renumber every ``[IMAGE-N: …]`` placeholder 1..K in document order.

    The planner atom merges writer-placed markers (numbered first) with the
    Image Decision Agent's top-up (numbered after them, but inserted wherever
    the agent's sections fall), so the merged body can carry ``[IMAGE-2]``
    above ``[IMAGE-1]``. ``inject_images`` matches by number, not position, so
    gaps and disorder are harmless to it — but the numbers are also what
    operators and the vision rail read, and ``image_plans`` is rebuilt from
    the renumbered body so the two can never disagree.
    """
    counter = {"n": 0}

    def _sub(match: re.Match[str]) -> str:
        counter["n"] += 1
        desc = match.group(2)
        if desc is None:
            return f"[IMAGE-{counter['n']}]"
        return f"[IMAGE-{counter['n']}: {desc}]"

    return PLACEHOLDER_RE.sub(_sub, content)


def split_chart_target(desc: str) -> tuple[str, str | None]:
    """Return ``(desc_without_prefix, chart_target_or_None)``.

    ``"chart:llm-decode-vs-delivered"`` → ``("llm-decode-vs-delivered",
    "llm-decode-vs-delivered")``. Mirrors :func:`split_screenshot_target`; the
    description is kept equal to the key so a logged plan still reads.
    """
    stripped = (desc or "").strip()
    if not stripped.lower().startswith(CHART_PREFIX):
        return stripped, None
    target = stripped[len(CHART_PREFIX):].strip()
    return target, (target or None)


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
    "CHART_PREFIX",
    "PLACEHOLDER_RE",
    "SCREENSHOT_PREFIX",
    "extract_hero_subject",
    "is_evidence_desc",
    "number_inline_markers",
    "renumber_placeholders",
    "split_chart_target",
    "split_screenshot_target",
]
