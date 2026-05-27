"""Contract test for the 2026-05-27 bold-text heading fallback in
``services.stages.replace_inline_images._plan_and_inject_placeholders``.

Pins the fix for the production observation: 12 consecutive published
canonical_blog posts had 0 inline images because the writer emitted
``**Section Title**`` standalone-line pseudo-headings instead of real
``## Section Title`` markdown, and the heading-map regex only matched
the latter.

This test confirms that:
1. When ``plan_images`` returns inline images anchored to a section
   heading, AND
2. The content uses bold-text pseudo-headings for that section,
THEN the placeholder injection still finds the anchor (heading_map is
non-empty) and writes ``[IMAGE-N]`` markers into the content.

A regression here re-introduces the "0 inline images" prod symptom.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _make_plan_images_result(*sections: str):
    """Build a minimal ImagePlanResult clone shaped like the real one
    enough for ``_plan_and_inject_placeholders`` to consume."""
    images = [
        SimpleNamespace(
            section_heading=section,
            source="pexels",
            style="photo",
            prompt=f"image about {section}",
            reasoning="r",
        )
        for section in sections
    ]
    return SimpleNamespace(featured_image=None, images=images)


@pytest.mark.asyncio
async def test_bold_pseudo_headings_anchor_inline_image_placeholders():
    """The exact prod content shape: bold-text pseudo-headings.
    Verify ``_plan_and_inject_placeholders`` injects ``[IMAGE-N]``."""
    from services.stages.replace_inline_images import (
        _plan_and_inject_placeholders,
    )

    content = (
        "Intro paragraph about the topic.\n\n"
        "**First Real Section**\n\n"
        "First section body — multiple sentences explaining the topic.\n\n"
        "**Second Section**\n\n"
        "Second section body.\n"
    )

    fake_plan = _make_plan_images_result("First Real Section", "Second Section")

    with patch(
        "services.image_decision_agent.plan_images",
        new=AsyncMock(return_value=fake_plan),
    ):
        result_content, info = await _plan_and_inject_placeholders(
            content, "the topic", "technology",
        )

    placeholder_count = result_content.count("[IMAGE-")
    assert placeholder_count == 2, (
        f"Expected 2 [IMAGE-N] placeholders anchored to bold-text "
        f"pseudo-headings, got {placeholder_count}. The 2026-05-27 "
        f"fix regressed — production posts will get 0 inline images "
        f"again. Content was:\n{result_content[:500]}"
    )


@pytest.mark.asyncio
async def test_real_h2_headings_still_take_priority():
    """When the writer DOES emit real H2 markdown, the bold-text
    fallback must NOT replace it — real headings win."""
    from services.stages.replace_inline_images import (
        _plan_and_inject_placeholders,
    )

    content = (
        "Intro paragraph.\n\n"
        "## Real Section\n\n"
        "Body for the real section.\n\n"
        "**Bold Pseudo Heading**\n\n"
        "Body for the bold section.\n"
    )

    # plan_images suggests one image for "Real Section" (matches the H2).
    fake_plan = _make_plan_images_result("Real Section")

    with patch(
        "services.image_decision_agent.plan_images",
        new=AsyncMock(return_value=fake_plan),
    ):
        result_content, _info = await _plan_and_inject_placeholders(
            content, "topic", "technology",
        )

    assert result_content.count("[IMAGE-") == 1


@pytest.mark.asyncio
async def test_inline_bold_text_does_not_anchor_placeholders():
    """A bold phrase mid-paragraph is NOT a section heading and must
    NOT serve as an image anchor — otherwise we'd inject images inside
    paragraphs, not between them."""
    from services.stages.replace_inline_images import (
        _plan_and_inject_placeholders,
    )

    content = (
        "Paragraph one with **inline bold word** in the middle of "
        "regular prose. No section dividers anywhere in this content.\n"
    )

    fake_plan = _make_plan_images_result("inline bold word")

    with patch(
        "services.image_decision_agent.plan_images",
        new=AsyncMock(return_value=fake_plan),
    ):
        result_content, _info = await _plan_and_inject_placeholders(
            content, "topic", "technology",
        )

    assert "[IMAGE-" not in result_content, (
        "Inline bold text inside prose was treated as a section "
        "heading anchor — regex over-matched."
    )
