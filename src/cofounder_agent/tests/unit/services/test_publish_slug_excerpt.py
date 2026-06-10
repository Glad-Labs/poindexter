"""Unit tests for publish_service slug + excerpt helpers (#728)."""

from services.publish_service import (
    build_post_slug,
    choose_excerpt,
    sanitize_published_title,
)


def test_build_post_slug_collapses_double_hyphen():
    # "What we shipped -- 2026-05-14" historically produced
    # "what-we-shipped----2026-05-14-...". The collapse fixes that.
    assert (
        build_post_slug("What we shipped -- 2026-05-14", "3ed10e25abcd")
        == "what-we-shipped-2026-05-14-3ed10e25"
    )


def test_build_post_slug_em_dash_no_double_hyphen():
    slug = build_post_slug("Context windows — why 128k is not free", "deadbeefcafe")
    assert "--" not in slug
    assert slug.endswith("-deadbeef")


def test_build_post_slug_all_punctuation_falls_back():
    assert build_post_slug("###", "abcd1234ef") == "post-abcd1234"


def test_choose_excerpt_prefers_pipeline_excerpt():
    assert (
        choose_excerpt(
            task_metadata={"excerpt": "Real summary."},
            merged={},
            seo_description="seo fallback",
            title="A Title",
        )
        == "Real summary."
    )


def test_choose_excerpt_never_equals_title():
    assert (
        choose_excerpt(
            task_metadata={"excerpt": "My Title"},
            merged={},
            seo_description="",
            title="My Title",
        )
        == ""
    )


def test_choose_excerpt_falls_back_to_seo_description():
    assert (
        choose_excerpt(
            task_metadata={},
            merged={},
            seo_description="SEO desc",
            title="A Title",
        )
        == "SEO desc"
    )


def test_sanitize_published_title_strips_label():
    assert sanitize_published_title("Title: Best Eats") == "Best Eats"
