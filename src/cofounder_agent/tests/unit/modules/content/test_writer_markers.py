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
    split_chart_target,
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


# --- end-to-end: marker → image_results → <img> ----------------------------
#
# poindexter#1002 follow-up. The original slice wired plan_image_markers and
# generate_images but NOT inject_images, whose source branch is
# image_gen / pexels / else-strip. A screenshot slot fell into else-strip, so
# the capture uploaded, recorded a media_assets row, and was then silently
# discarded from the body. Only a test that spans plan → inject catches that;
# testing the atoms individually never would.


@pytest.mark.asyncio
async def test_screenshot_marker_survives_plan_through_inject():
    from modules.content.atoms import content_inject_images, content_plan_image_markers
    from services.site_config import SiteConfig

    sc = SiteConfig(initial_config={"writer_max_inline_images": "3"})
    planned = await content_plan_image_markers.run({
        "content": "# Draft\n\nText.\n[SCREENSHOT: qa-rails]\nMore.",
        "topic": "quality gates",
        "site_config": sc,
    })
    plan = planned["image_plans"][0]
    assert plan["screenshot_target"] == "qa-rails"

    # What _capture_screenshot returns for a successful capture.
    injected = await content_inject_images.run({
        "content": planned["content"],
        "image_results": [{
            "num": plan["num"], "url": "https://cdn/shot.webp",
            "alt_text": "The QA Rails dashboard", "source": "screenshot",
            "width": 1600, "height": 1150,
        }],
    })
    body = injected["content"]
    assert "https://cdn/shot.webp" in body, "screenshot was stripped from the body"
    assert "[IMAGE-" not in body, "placeholder left unreplaced"
    # Truthful dimensions, not the image_gen 1024x1024 square.
    assert 'width="1600"' in body and 'height="1150"' in body


@pytest.mark.asyncio
async def test_screenshot_slot_with_no_url_strips_placeholder():
    """A failed capture must leave no orphan marker in the published body."""
    from modules.content.atoms import content_inject_images

    injected = await content_inject_images.run({
        "content": "Text.\n\n[IMAGE-1: screenshot:qa-rails]\n\nMore.",
        "image_results": [{
            "num": "1", "url": None, "alt_text": "", "source": "none",
        }],
    })
    assert "[IMAGE-1" not in injected["content"]


class TestChartMarkers:
    """``[CHART: key]`` mirrors ``[SCREENSHOT: key]``: the writer names a
    catalogued key and never the data, because a chart's contents come from
    measurements, not from the model."""

    def test_chart_marker_gets_its_own_prefix(self):
        out = number_inline_markers("[CHART: llm-decode-vs-delivered]", 5)
        assert out == "[IMAGE-1: chart:llm-decode-vs-delivered]"

    def test_all_three_marker_kinds_share_one_numbering_sequence(self):
        """Regression: an early version dropped the chart prefix entirely
        because the keyword→prefix branch only knew about SCREENSHOT."""
        out = number_inline_markers(
            "[IMAGE: a desk]\n[CHART: llm-decode-vs-delivered]\n[SCREENSHOT: qa-rails]",
            5,
        )
        assert "[IMAGE-1: a desk]" in out
        assert "[IMAGE-2: chart:llm-decode-vs-delivered]" in out
        assert "[IMAGE-3: screenshot:qa-rails]" in out

    def test_chart_markers_respect_the_inline_cap(self):
        out = number_inline_markers("[CHART: a]\n[CHART: b]\n[CHART: c]", 2)
        assert "chart:a" in out and "chart:b" in out and "chart:c" not in out

    def test_split_chart_target_round_trips(self):
        assert split_chart_target("chart:llm-decode-vs-delivered") == (
            "llm-decode-vs-delivered", "llm-decode-vs-delivered",
        )

    def test_split_chart_target_ignores_a_plain_description(self):
        assert split_chart_target("a desk by a window") == ("a desk by a window", None)

    def test_split_chart_target_ignores_a_screenshot_payload(self):
        """The two prefixes must not consume each other's markers."""
        assert split_chart_target("screenshot:qa-rails") == ("screenshot:qa-rails", None)

    def test_an_empty_chart_key_yields_no_target(self):
        assert split_chart_target("chart:") == ("", None)
