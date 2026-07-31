"""An inline illustration must never be injected above the article's lede.

RCA 2026-07-31. The visible half of the missing-hero incident: the published
post opened with a full-width inline image and no hero, so the inline image read
as the hero while the real hero slot sat empty.

Mechanism — ``_plan_and_inject_placeholders`` anchors each image at
``content_text.find("\\n\\n", heading.end())``, i.e. the gap between a heading and
its first paragraph. That is the right look for a mid-article section. But when
the writer opens the body with an ``## H2`` instead of an intro paragraph (the
blog-generation prompt asked for both in different places), that gap is the very
top of the article. Measured: 10 of 82 posts published in a 60-day window had an
inline image with zero prose above it.

The fix clamps placement to ``_first_prose_end`` — the image is kept, just moved
below the lede. These tests pin the floor, and that ordinary placement is
untouched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.atoms._image_helpers import (
    _first_prose_end,
    _plan_and_inject_placeholders,
)

pytestmark = pytest.mark.unit


def _plan(*sections: str, featured=None):
    images = [
        SimpleNamespace(
            section_heading=s, source="image_gen", style="dramatic",
            prompt=f"image about {s}", reasoning="r",
        )
        for s in sections
    ]
    return SimpleNamespace(featured_image=featured, images=images)


async def _inject(content: str, plan):
    with patch(
        "services.image_decision_agent.plan_images",
        AsyncMock(return_value=plan),
    ):
        out, _info = await _plan_and_inject_placeholders(
            content, "topic", "technology", site_config=None,
        )
    return out


def _prose_before_first_marker(content: str) -> str:
    """Prose preceding the first ``[IMAGE-N]``, headings/blanks removed."""
    import re

    idx = content.find("[IMAGE-")
    assert idx != -1, "expected an injected marker"
    before = content[:idx]
    return re.sub(r"(?m)^#{1,6}\s+.*$", "", before).strip()


# ---------------------------------------------------------------------------
# _first_prose_end
# ---------------------------------------------------------------------------


def test_first_prose_end_skips_leading_heading() -> None:
    content = "## The 5,000-minutes-a-day problem\n\nWe hit a wall.\n\nMore body.\n"
    assert content[: _first_prose_end(content)].endswith("We hit a wall.")


def test_first_prose_end_skips_bold_pseudo_heading() -> None:
    content = "**Section One**\n\nActual prose here.\n\nMore.\n"
    assert content[: _first_prose_end(content)].endswith("Actual prose here.")


def test_first_prose_end_ignores_existing_markers() -> None:
    content = "## Title\n\n[IMAGE-1: something]\n\nReal prose.\n\nMore.\n"
    assert content[: _first_prose_end(content)].endswith("Real prose.")


def test_first_prose_end_returns_zero_for_headings_only() -> None:
    """No prose at all → no floor invented, placement left unchanged."""
    assert _first_prose_end("## One\n\n### Two\n") == 0


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


async def test_image_not_injected_above_lede_when_body_opens_with_heading() -> None:
    """The incident shape: body opens with an H2, agent picks that section."""
    content = (
        "## The 5,000-minutes-a-day problem\n\n"
        "We hit a wall a few months back. GitHub Actions minutes were burning.\n\n"
        "## What we changed\n\n"
        "We split the suite.\n"
    )
    out = await _inject(content, _plan("The 5,000-minutes-a-day problem"))

    assert "[IMAGE-1:" in out, "the image is kept, not dropped"
    prose = _prose_before_first_marker(out)
    assert "We hit a wall" in prose, (
        "an inline image above the opening sentence reads as the hero"
    )


async def test_normal_intro_paragraph_placement_is_unchanged() -> None:
    """Writer follows the prompt → first heading isn't at the top → no clamp."""
    content = (
        "Intro paragraph that sets up the whole piece.\n\n"
        "## First Real Section\n\n"
        "Section body text.\n\n"
        "## Second Section\n\n"
        "More body.\n"
    )
    out = await _inject(content, _plan("First Real Section"))

    marker_idx = out.find("[IMAGE-1:")
    heading_idx = out.find("## First Real Section")
    assert heading_idx < marker_idx, "still anchored under its own heading"
    assert "Section body text." in out[marker_idx:], (
        "image sits between the heading and its body, the intended look"
    )


async def test_mid_article_sections_still_anchor_under_their_heading() -> None:
    """The floor must not drag later images forward."""
    content = (
        "## Opening\n\n"
        "Opening prose.\n\n"
        "## Middle Section\n\n"
        "Middle prose.\n\n"
        "## Last Section\n\n"
        "Last prose.\n"
    )
    out = await _inject(content, _plan("Middle Section", "Last Section"))

    assert out.find("## Middle Section") < out.find("[IMAGE-1:")
    assert out.find("[IMAGE-1:") < out.find("## Last Section") < out.find("[IMAGE-2:")


async def test_headings_only_body_still_places_the_image() -> None:
    """Degenerate input must not lose the image to a bogus floor."""
    content = "## One\n\n## Two\n"
    out = await _inject(content, _plan("One"))
    assert "[IMAGE-1:" in out
