"""Unit tests for publish_service slug + excerpt helpers (#728)."""

from services.publish_service import (
    build_post_slug,
    choose_excerpt,
    derive_publish_identity,
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


# ---------------------------------------------------------------------------
# derive_publish_identity — the single source of truth for the publish-time
# (title, content, slug) triple. publish_post_from_task AND the
# social.generate_drafts atom both call it, so the slug a social draft bakes
# into its copy can never drift from the slug the post actually publishes
# under (social-drafts linking bug).
# ---------------------------------------------------------------------------

def test_derive_publish_identity_extracts_h1():
    title, content, slug = derive_publish_identity(
        draft_content="# My Title\n\nBody text.",
        fallback_title="Ignored Fallback",
        topic="Ignored Topic",
        task_id="abcd1234ef567890",
    )
    assert title == "My Title"
    assert content == "Body text."
    assert slug == "my-title-abcd1234"


def test_derive_publish_identity_falls_back_to_title_then_topic():
    title, content, slug = derive_publish_identity(
        draft_content="Plain body, no heading.",
        fallback_title="Fallback Title",
        topic="Topic",
        task_id="abcd1234ef567890",
    )
    assert title == "Fallback Title"
    assert content == "Plain body, no heading."
    assert slug == "fallback-title-abcd1234"

    title2, _content2, slug2 = derive_publish_identity(
        draft_content="",
        fallback_title="",
        topic="Topic Only",
        task_id="abcd1234ef567890",
    )
    assert title2 == "Topic Only"
    assert slug2 == "topic-only-abcd1234"


def test_derive_publish_identity_sanitizes_harness_suffix():
    title, _content, slug = derive_publish_identity(
        draft_content="# My Title (2026-05-11 17:48 batch C #5)\n\nBody.",
        fallback_title="",
        topic="",
        task_id="abcd1234ef567890",
    )
    assert title == "My Title"
    assert slug == "my-title-abcd1234"
