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

def test_canonical_title_beats_the_body_heading():
    """INVERTED 2026-08-15. The body heading used to win, which discarded the
    pipeline's own title decision for 71.3% of canonical_blog posts — the
    variety block and internal-duplicate check both govern the canonical
    title, so preferring the heading meant they governed a string no reader
    ever saw. The heading is still stripped from the body either way.
    """
    title, content, slug = derive_publish_identity(
        draft_content="# The Writer H1\n\nBody text.",
        canonical_title="Solving Retrieval Mismatch in RAG",
        topic="Ignored Topic",
        task_id="abcd1234ef567890",
    )
    assert title == "Solving Retrieval Mismatch in RAG"
    assert content == "Body text."  # heading still stripped — no double title
    assert slug == "solving-retrieval-mismatch-in-rag-abcd1234"


def test_body_heading_wins_when_canonical_title_is_absent():
    """No canonical title (LLM returned None and nothing was persisted) — the
    writer's heading is still far better than the raw topic label.
    """
    title, content, _slug = derive_publish_identity(
        draft_content="# The Writer H1\n\nBody text.",
        canonical_title="",
        topic="Some Topic",
        task_id="abcd1234ef567890",
    )
    assert title == "The Writer H1"
    assert content == "Body text."


def test_topic_echo_is_skipped_in_favour_of_a_real_candidate():
    """24.8% of canonical_blog posts published under their own topic label.
    A candidate that merely echoes the topic loses to the next real one, in
    either direction.
    """
    # Canonical echoes the topic -> the heading should win.
    title, _content, _slug = derive_publish_identity(
        draft_content="# A Real Headline\n\nBody.",
        canonical_title="The Brand Guide as Fact-Checker",
        topic="The Brand Guide as Fact-Checker",
        task_id="abcd1234ef567890",
    )
    assert title == "A Real Headline"

    # Heading echoes the topic -> the canonical title should win.
    title2, _c2, _s2 = derive_publish_identity(
        draft_content="# The Brand Guide as Fact-Checker\n\nBody.",
        canonical_title="AI Writes the Truth and Gets Your Brand Wrong",
        topic="The Brand Guide as Fact-Checker",
        task_id="abcd1234ef567890",
    )
    assert title2 == "AI Writes the Truth and Gets Your Brand Wrong"


def test_topic_is_last_resort_when_every_candidate_echoes_it():
    """Degenerate case: nothing but the topic exists. Ship it rather than
    returning an empty title.
    """
    title, _content, slug = derive_publish_identity(
        draft_content="# Same Topic\n\nBody.",
        canonical_title="Same Topic",
        topic="Same Topic",
        task_id="abcd1234ef567890",
    )
    assert title == "Same Topic"
    assert slug == "same-topic-abcd1234"


def test_fallback_title_keyword_still_works():
    """Backcompat: ``fallback_title`` was the pre-2026-08-15 parameter name and
    is still accepted as a keyword (it was accurate when the value was only
    ever a fallback).
    """
    title, _content, _slug = derive_publish_identity(
        draft_content="# The Writer H1\n\nBody.",
        fallback_title="Canonical Via Old Kwarg",
        topic="Topic",
        task_id="abcd1234ef567890",
    )
    assert title == "Canonical Via Old Kwarg"


def test_body_heading_source_setting_restores_legacy_precedence():
    """The escape hatch: publish_title_source='body_heading' reverts to the
    old order without a deploy.
    """
    from services.site_config import SiteConfig

    legacy = SiteConfig(initial_config={"publish_title_source": "body_heading"})
    title, _content, _slug = derive_publish_identity(
        draft_content="# The Writer H1\n\nBody.",
        canonical_title="Canonical Title",
        topic="Topic",
        task_id="abcd1234ef567890",
        site_config=legacy,
    )
    assert title == "The Writer H1"

    # Default (no site_config) and an explicit 'canonical' both prefer canonical.
    canonical_sc = SiteConfig(initial_config={"publish_title_source": "canonical"})
    for sc in (None, canonical_sc):
        t, _c, _s = derive_publish_identity(
            draft_content="# The Writer H1\n\nBody.",
            canonical_title="Canonical Title",
            topic="Topic",
            task_id="abcd1234ef567890",
            site_config=sc,
        )
        assert t == "Canonical Title"


def test_canonical_title_comes_from_the_column_not_stage_data():
    """The other half of the 2026-08-15 fix. ``merged["title"]`` (stage_data
    JSON) is absent for 90% of finished canonical_blog tasks, so publish read
    an empty string and the body heading won by default. The column
    (``task["title"]`` = pipeline_versions.title) is the real source.
    """
    from services.publish_service import resolve_canonical_title

    # The 90% case: column populated, stage_data copy missing entirely.
    assert resolve_canonical_title(
        {"title": "The Canonical Title"}, {},
    ) == "The Canonical Title"

    # Column wins when both exist and disagree (9 of the 10 that had both).
    assert resolve_canonical_title(
        {"title": "From Column"}, {"title": "Stale From Stage Data"},
    ) == "From Column"

    # stage_data is still a real fallback when the column is empty/whitespace.
    assert resolve_canonical_title({"title": "   "}, {"title": "From Merged"}) == "From Merged"
    assert resolve_canonical_title({}, {"title": "From Merged"}) == "From Merged"

    # Nothing anywhere -> empty, so derive_publish_identity falls through to
    # the heading/topic rather than publishing a blank title.
    assert resolve_canonical_title({}, {}) == ""
    assert resolve_canonical_title(None, None) == ""


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
