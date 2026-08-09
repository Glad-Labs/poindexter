"""Unresolved writer image markers must never reach a human-facing surface.

The bug this pins: all three boundaries (preview render, publish, podcast
narration) used ``\\[IMAGE-\\d+\\]``, which matches a bare ``[IMAGE-1]`` and
nothing else. The form the pipeline actually produces is ``[IMAGE-1: a server
rack]``, so a marker that never got an image sailed past every net and printed
verbatim on the live post.
"""
from __future__ import annotations

import pytest

from services.image_markers import strip_unresolved_image_markers


@pytest.mark.parametrize(
    "marker",
    [
        "[IMAGE-1]",                       # bare numbered — the only form ever caught
        "[IMAGE-1: a server rack]",        # numbered + description — the real form
        "[IMAGE-12: a rack]",              # multi-digit
        "[IMAGE: a server rack]",          # unnumbered, pre-planner
        "[HERO-IMAGE: a token tree]",      # hero
        "[SCREENSHOT: qa-rails]",          # poindexter#1002
        "[screenshot: qa-rails]",          # case-insensitive
    ],
)
def test_strips_every_marker_form(marker):
    out = strip_unresolved_image_markers(f"Before.\n\n{marker}\n\nAfter.")
    assert marker not in out
    assert "Before." in out and "After." in out


def test_standalone_marker_leaves_no_blank_gap():
    out = strip_unresolved_image_markers("Para one.\n\n[IMAGE-1: a rack]\n\nPara two.")
    assert out == "Para one.\n\nPara two."


def test_strips_several_markers_in_one_body():
    body = "A\n\n[IMAGE-1: x]\n\nB\n\n[SCREENSHOT: qa-rails]\n\nC"
    out = strip_unresolved_image_markers(body)
    assert "[" not in out
    assert out == "A\n\nB\n\nC"


@pytest.mark.parametrize(
    "text",
    [
        "See [Image processing] for details.",      # prose in brackets
        "A note on [Images: three of them].",       # plural, not a marker
        "Read [the docs](https://example.com/x).",  # markdown link
        "An array index like arr[IMAGES] stays.",
    ],
)
def test_leaves_ordinary_bracketed_text_alone(text):
    assert strip_unresolved_image_markers(text) == text


def test_resolved_images_are_untouched():
    """A marker that DID become an image has no bracket left to match."""
    html = '<img src="https://cdn/x.webp" alt="A rack" width="1600" height="1150" />'
    assert strip_unresolved_image_markers(f"Text.\n\n{html}\n\nMore.") == (
        f"Text.\n\n{html}\n\nMore."
    )


@pytest.mark.parametrize("text", ["", None])
def test_empty_input_is_returned_as_is(text):
    assert strip_unresolved_image_markers(text) == text


def test_clean_text_is_returned_unchanged_object():
    """No marker → no rewrite, so blank-line runs in clean prose survive."""
    text = "Para.\n\n\n\nDeliberate gap."
    assert strip_unresolved_image_markers(text) == text


# --- contract with what the writer/planner actually emit -------------------


def test_strips_everything_number_inline_markers_produces():
    """Whatever the planner rewrites writer markers INTO must be strippable.

    Couples this net to the real producer instead of a hand-copied list, so a
    new marker form added to _writer_markers fails here rather than shipping
    to a published post.
    """
    from modules.content.atoms._writer_markers import number_inline_markers

    body = "A\n[IMAGE: a rack]\nB\n[SCREENSHOT: qa-rails]\nC"
    planned = number_inline_markers(body, max_inline=5)
    assert "[IMAGE-1" in planned and "[IMAGE-2" in planned  # planner ran

    out = strip_unresolved_image_markers(planned)
    assert "[IMAGE" not in out and "[SCREENSHOT" not in out


def test_strips_the_hero_marker_the_writer_places():
    from modules.content.atoms._writer_markers import extract_hero_subject

    body = "[HERO-IMAGE: a token tree]\n\n## Intro\n\nText."
    # The hero is normally lifted out by the planner; when the graph has no
    # planner (dev_diary) it reaches the boundary intact.
    _, hero = extract_hero_subject(body)
    assert hero == "a token tree"
    assert "[HERO-IMAGE" not in strip_unresolved_image_markers(body)
