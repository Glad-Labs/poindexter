"""Writer-placed image marker normalization (image-direction rework).

The writer emits `[IMAGE: subject]` inline + one `[HERO-IMAGE: subject]` first
line. These pure helpers extract the hero and number the inline markers into
the `[IMAGE-N: …]` form the rest of the pipeline parses.
"""
from __future__ import annotations

import pytest

from modules.content.atoms._writer_markers import (
    extract_hero_subject,
    number_inline_markers,
)


def test_extract_hero_pulls_subject_and_strips_line():
    content = "[HERO-IMAGE: a branching token tree]\n\n# Intro\n\nBody."
    new, hero = extract_hero_subject(content)
    assert hero == "a branching token tree"
    assert "[HERO-IMAGE:" not in new
    assert new.lstrip().startswith("# Intro")


def test_extract_hero_none_when_absent():
    new, hero = extract_hero_subject("# Intro\n\nBody.")
    assert hero is None
    assert new == "# Intro\n\nBody."


def test_number_inline_markers_sequential():
    content = "A\n[IMAGE: draft model]\nB\n[IMAGE: verify step]\n"
    out = number_inline_markers(content, max_inline=3)
    assert "[IMAGE-1: draft model]" in out
    assert "[IMAGE-2: verify step]" in out


def test_number_inline_markers_caps_and_strips_extras():
    content = "[IMAGE: a]\n[IMAGE: b]\n[IMAGE: c]\n[IMAGE: d]\n"
    out = number_inline_markers(content, max_inline=2)
    assert "[IMAGE-1: a]" in out and "[IMAGE-2: b]" in out
    assert "[IMAGE-3:" not in out
    assert "[IMAGE: c]" not in out and "[IMAGE: d]" not in out


@pytest.mark.asyncio
async def test_plan_image_markers_surfaces_hero_and_uses_writer_markers():
    from modules.content.atoms import content_plan_image_markers
    from services.site_config import SiteConfig

    sc = SiteConfig(initial_config={"writer_max_inline_images": "3"})
    state = {
        "content": "[HERO-IMAGE: a token tree]\n\n# Draft\n\nText.\n[IMAGE: a draft model]\nMore.",
        "topic": "speculative decoding",
        "site_config": sc,
    }
    out = await content_plan_image_markers.run(state)
    assert out["featured_image_subject"] == "a token tree"
    assert "[HERO-IMAGE:" not in out["content"]
    # writer-primary: the marker was numbered + parsed; decision agent NOT called.
    assert any(p["desc"] == "a draft model" for p in out["image_plans"])
    assert "[IMAGE-1: a draft model]" in out["content"]

# --- [SCREENSHOT: target] markers (poindexter#1002) -------------------------
#
# Screenshot markers share ONE numbering sequence with [IMAGE:] markers,
# because content.inject_images matches image_results back to placeholders by
# number. A separate sequence would collide.


def test_screenshot_marker_numbered_with_prefix():
    content = "A\n[SCREENSHOT: qa-rails]\nB\n"
    out = number_inline_markers(content, max_inline=3)
    assert "[IMAGE-1: screenshot:qa-rails]" in out


def test_screenshot_and_image_markers_share_one_sequence():
    content = "[IMAGE: a rack]\n[SCREENSHOT: qa-rails]\n[IMAGE: a cable]\n"
    out = number_inline_markers(content, max_inline=5)
    assert "[IMAGE-1: a rack]" in out
    assert "[IMAGE-2: screenshot:qa-rails]" in out
    assert "[IMAGE-3: a cable]" in out


def test_screenshot_markers_count_against_the_cap():
    content = "[SCREENSHOT: a]\n[SCREENSHOT: b]\n[IMAGE: c]\n"
    out = number_inline_markers(content, max_inline=2)
    assert "[IMAGE-1: screenshot:a]" in out
    assert "[IMAGE-2: screenshot:b]" in out
    assert "[IMAGE: c]" not in out and "IMAGE-3" not in out


def test_split_screenshot_target_roundtrip():
    from modules.content.atoms._writer_markers import split_screenshot_target

    assert split_screenshot_target("screenshot:qa-rails") == ("qa-rails", "qa-rails")
    assert split_screenshot_target("SCREENSHOT: qa-rails") == ("qa-rails", "qa-rails")
    # An ordinary description is returned untouched with no target.
    assert split_screenshot_target("a server rack") == ("a server rack", None)


@pytest.mark.asyncio
async def test_plan_image_markers_surfaces_screenshot_target():
    from modules.content.atoms import content_plan_image_markers
    from services.site_config import SiteConfig

    sc = SiteConfig(initial_config={"writer_max_inline_images": "3"})
    state = {
        "content": "# Draft\n\nText.\n[SCREENSHOT: qa-rails]\nMore.\n[IMAGE: a rack]",
        "topic": "quality gates",
        "site_config": sc,
    }
    out = await content_plan_image_markers.run(state)
    plans = {p["num"]: p for p in out["image_plans"]}
    assert plans["1"]["screenshot_target"] == "qa-rails"
    # Ordinary markers carry no target, so generate_images still routes them
    # to image-gen.
    assert "screenshot_target" not in plans["2"]
    assert plans["2"]["desc"] == "a rack"
