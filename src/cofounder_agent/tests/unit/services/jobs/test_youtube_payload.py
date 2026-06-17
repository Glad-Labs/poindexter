"""Unit tests for the shared YouTube-payload helpers (extracted from
backfill_videos in the #1460 PR1 prep so media_distribute stops importing
from a job that PR2 deletes)."""
from __future__ import annotations

from services.jobs.youtube_payload import (
    _build_youtube_description,
    _parse_seo_keywords,
    _strip_markup,
)


def test_strip_markup_removes_tags_and_collapses_ws():
    assert _strip_markup("<p>hi   <b>there</b></p>") == "hi there"
    assert _strip_markup("") == ""


def test_parse_seo_keywords_caps_and_trims():
    assert _parse_seo_keywords("a, b ,, c") == ["a", "b", "c"]
    assert _parse_seo_keywords("") == []
    # >30 keywords are capped at 30.
    many = ",".join(f"k{i}" for i in range(40))
    assert len(_parse_seo_keywords(many)) == 30


def test_build_youtube_description_composes_and_strips_angle_brackets():
    out = _build_youtube_description(
        seo_description="A <b>great</b> post",
        body="Body with x > 0 and a <a href='#'>link</a>.",
        site_config=None,            # no site_url → back-link omitted, never raises
        slug="my-post",
    )
    assert "<" not in out and ">" not in out
    assert out.startswith("A great post")
