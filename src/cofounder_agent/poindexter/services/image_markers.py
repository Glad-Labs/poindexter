"""Strip writer-placed image markers that never got resolved into images.

The writer emits four marker forms (``skills/content/blog-generation/SKILL.md``):

- ``[HERO-IMAGE: subject]`` — one, first line
- ``[IMAGE: subject]`` — inline, unnumbered
- ``[IMAGE-N: subject]`` — the numbered form ``content.plan_image_markers``
  rewrites the above into
- ``[SCREENSHOT: target-key]`` — poindexter#1002, numbered alongside ``[IMAGE:]``

Markers are load-bearing mid-pipeline: ``modules/content/writer_core.py``
deliberately does NOT strip them, because ``content.plan_image_markers`` owns
their lifecycle and ``content.inject_images`` swaps each one for an ``<img>``
(or drops it when its slot came back empty). This module is the **boundary**
net for the ones that still get through, and it belongs only at render/publish
edges — never in the middle of the graph, where stripping would delete the
planner's input.

Two ways a marker survives to a boundary:

1. **A template with no image block.** ``dev_diary`` runs
   ``verify_task → narrate_bundle → generate_seo_metadata →
   source_featured_image → finalize_task`` — no ``plan_image_markers``, no
   ``inject_images``. Nothing in that graph has ever heard of a marker, so a
   writer that places one publishes it verbatim.
2. **A marker with a description.** The three call sites this replaces each
   used ``\\[IMAGE-\\d+\\]``, which matches a bare ``[IMAGE-1]`` and nothing
   else — so ``[IMAGE-1: a server rack]``, the form the pipeline actually
   produces, sailed straight past all of them.

Hence one regex covering every form, used everywhere the content is about to
be shown to a human.
"""

from __future__ import annotations

import re

#: Every writer marker form, with or without a ``: description`` payload.
#:
#: Anchored on the keyword immediately after ``[`` so ordinary prose and
#: markdown links are untouched: ``[Image processing]`` does not match (the
#: char after ``IMAGE`` is neither ``:`` nor ``]``), nor does ``[Images: x]``.
_UNRESOLVED_MARKER_RE = re.compile(
    r"\[(?:HERO-IMAGE|SCREENSHOT|IMAGE(?:-\d+)?)\s*(?::[^\]]*)?\]",
    re.IGNORECASE,
)

#: A marker alone on its line leaves a blank line behind once removed.
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def strip_unresolved_image_markers(text: str) -> str:
    """Remove every unresolved image marker from ``text``.

    Collapses the 3+ newline runs a removed standalone marker leaves behind,
    so the rendered output has no gap where the image would have been.

    Safe on already-clean text and on text whose markers were resolved — a
    rendered ``<img>`` contains no bracket marker to match.
    """
    if not text:
        return text
    stripped = _UNRESOLVED_MARKER_RE.sub("", text)
    if stripped == text:
        return text
    return _BLANK_RUN_RE.sub("\n\n", stripped)


__all__ = ["strip_unresolved_image_markers"]
