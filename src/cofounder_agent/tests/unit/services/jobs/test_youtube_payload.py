"""Unit tests for the shared YouTube-payload helpers.

The description contract changed 2026-08-31 (operator decision): the default
is **excerpt + tagged back-link only** — the article body ships only when
``youtube_description_body_chars`` opts a snippet in. The fixtures echo the
real failure shapes found on the live channel that day: raw
``[text](/go/…)`` link syntax in the viewer-facing text, ``## heading``
markers mid-paragraph, every paragraph break collapsed into one 4,800-char
wall, and a mid-word truncation ("Every post gets a val").
"""
from __future__ import annotations

from services.jobs.youtube_payload import (
    _build_youtube_description,
    _markdown_to_plain,
    _parse_seo_keywords,
    _strip_markup,
    _trim_at_sentence,
)
from services.site_config import SiteConfig


def _sc(**extra: str) -> SiteConfig:
    return SiteConfig(initial_config={"site_url": "https://www.gladlabs.io", **extra})


BODY_MD = (
    "You can write the best breakdown of "
    "[ASUS ROG Astral RTX 5090](/go/asus-rog-astral) bandwidth. Nobody cares.\n\n"
    "We've covered [automating workflows](/posts/automating-511012cc) elsewhere. "
    "Amplification is the other half.\n"
    "## Why generation without distribution is a dead end\n"
    "We built Poindexter to scale a pipeline. "
    "![diagram](/images/pipeline.png) It works.\n\n"
    "```python\nprint('hi')\n```\n\n"
    "Final **bold** thought with `code` and x > 0."
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_strip_markup_removes_tags_and_collapses_ws():
    assert _strip_markup("<p>hi   <b>there</b></p>") == "hi there"
    assert _strip_markup("") == ""


def test_parse_seo_keywords_caps_and_trims():
    assert _parse_seo_keywords("a, b ,, c") == ["a", "b", "c"]
    assert _parse_seo_keywords("") == []
    many = ",".join(f"k{i}" for i in range(40))
    assert len(_parse_seo_keywords(many)) == 30


def test_markdown_to_plain_keeps_text_drops_machinery():
    out = _markdown_to_plain(BODY_MD)
    # Link text survives; the relative /go/ and /posts/ hrefs — dead as
    # description text, and the affiliate slugs are nobody's business — do not.
    assert "ASUS ROG Astral RTX 5090" in out
    assert "/go/" not in out and "](" not in out
    # Heading marker gone, heading text standing as its own paragraph even
    # though the writer glued it to the previous paragraph with a single \n.
    assert "##" not in out
    assert "\n\nWhy generation without distribution is a dead end" in out
    # Images vanish wholesale; emphasis and fence markers go; paragraphs stay.
    assert "diagram" not in out
    assert "**" not in out and "`" not in out
    assert out.count("\n\n") >= 3


def test_trim_at_sentence_prefers_a_full_stop():
    text = "One sentence here. Another follows it. And a third one."
    out = _trim_at_sentence(text, 30)
    assert out == "One sentence here."


def test_trim_at_sentence_falls_back_to_word_boundary():
    text = "no sentence punctuation just a very long run of words " * 3
    out = _trim_at_sentence(text, 40)
    assert len(out) <= 40
    assert not out.endswith(" ")
    assert out == text[: len(out)]  # a clean prefix, cut between words


def test_trim_at_sentence_short_input_untouched():
    assert _trim_at_sentence("short.", 100) == "short."
    assert _trim_at_sentence("anything", 0) == ""


# ---------------------------------------------------------------------------
# the description contract
# ---------------------------------------------------------------------------


def test_default_description_is_excerpt_plus_tagged_link_only():
    """The 2026-08-31 operator decision: no article body by default."""
    out = _build_youtube_description(
        seo_description="The hook paragraph.",
        body=BODY_MD,
        site_config=_sc(),
        slug="my-post-abc12345",
    )
    assert out == (
        "The hook paragraph.\n\n"
        "Read the full post: https://www.gladlabs.io/posts/my-post-abc12345"
        "?utm_source=youtube&utm_medium=video"
    )
    # Nothing of the article leaked.
    assert "ASUS" not in out and "Poindexter" not in out


def test_body_snippet_is_opt_in_deduped_and_sentence_trimmed():
    excerpt = "You can write the best breakdown of ASUS ROG Astral RTX 5090 bandwidth. Nobody cares."
    out = _build_youtube_description(
        seo_description=excerpt,
        body=BODY_MD,
        site_config=_sc(youtube_description_body_chars="200"),
        slug="my-post-abc12345",
    )
    # The body's first paragraph IS the excerpt (posts.excerpt is the post's
    # opener) — the snippet must continue the story, not restart it.
    assert out.count("Nobody cares.") == 1
    assert "We've covered automating workflows elsewhere." in out
    # Sentence-trimmed within the budget — never a mid-word cut.
    tail = out.rsplit("\n\n", 1)[-1]
    assert len(tail) <= 200
    assert tail.endswith(".")


def test_bad_body_chars_value_degrades_to_no_snippet():
    out = _build_youtube_description(
        seo_description="Hook.",
        body=BODY_MD,
        site_config=_sc(youtube_description_body_chars="not-a-number"),
        slug="s",
    )
    assert "ASUS" not in out
    assert out.startswith("Hook.")


def test_no_site_config_composes_and_strips_angle_brackets():
    out = _build_youtube_description(
        seo_description="A <b>great</b> post",
        body="Body with x > 0 and a <a href='#'>link</a>.",
        site_config=None,  # no site_url → back-link omitted, never raises
        slug="my-post",
    )
    assert "<" not in out and ">" not in out
    assert out.startswith("A great post")
    assert "Read the full post" not in out


def test_empty_excerpt_with_snippet_enabled_still_produces_a_description():
    out = _build_youtube_description(
        seo_description="",
        body=BODY_MD,
        site_config=_sc(youtube_description_body_chars="300"),
        slug="my-post-abc12345",
    )
    assert out.startswith("Read the full post: ")
    assert "Nobody cares." in out  # nothing to dedupe against → body from the top


def test_budget_cap_holds_with_huge_opt_in():
    out = _build_youtube_description(
        seo_description="Hook.",
        body=("A sentence that repeats. " * 600),
        site_config=_sc(youtube_description_body_chars="999999"),
        slug="s",
    )
    assert len(out) <= 4800


# ---------------------------------------------------------------------------
# titles — separating a Short from its long-form twin
# ---------------------------------------------------------------------------


def test_long_form_title_is_the_post_title_verbatim():
    from services.jobs.youtube_payload import _build_youtube_title

    assert _build_youtube_title("The Gap Nobody Names", shorts=False, site_config=_sc()) == (
        "The Gap Nobody Names"
    )


def test_short_gets_a_distinguishing_suffix():
    """A post can produce BOTH renders; taking posts.title verbatim for each
    put two identically-named videos on the channel."""
    from services.jobs.youtube_payload import _build_youtube_title

    long_form = _build_youtube_title("The Gap Nobody Names", shorts=False, site_config=_sc())
    short = _build_youtube_title("The Gap Nobody Names", shorts=True, site_config=_sc())
    assert short != long_form
    assert short == "The Gap Nobody Names #Shorts"


def test_short_suffix_survives_a_title_at_the_cap():
    """Appending blindly would push the suffix past YouTube's 100-char limit
    and the adapter's clamp would cut off the very thing that distinguishes
    it — so the title is trimmed at a word boundary to make room first."""
    from services.jobs.youtube_payload import _build_youtube_title

    long_title = "Why " + "extremely " * 12 + "long titles break naive appending"
    out = _build_youtube_title(long_title, shorts=True, site_config=_sc())
    assert len(out) <= 100
    assert out.endswith(" #Shorts")
    assert not out.replace(" #Shorts", "").endswith(" ")


def test_short_suffix_is_idempotent():
    """A re-sync of an already-suffixed video must not stack a second marker."""
    from services.jobs.youtube_payload import _build_youtube_title

    once = _build_youtube_title("The Gap Nobody Names", shorts=True, site_config=_sc())
    assert _build_youtube_title(once, shorts=True, site_config=_sc()) == once
    # An operator-written title that already says #shorts is left alone too.
    assert _build_youtube_title(
        "Already #shorts here", shorts=True, site_config=_sc()
    ) == "Already #shorts here"


def test_empty_suffix_means_no_distinction():
    from services.jobs.youtube_payload import _build_youtube_title

    sc = _sc(youtube_short_title_suffix="")
    assert _build_youtube_title("T", shorts=True, site_config=sc) == "T"


def test_custom_suffix_is_honoured():
    from services.jobs.youtube_payload import _build_youtube_title

    sc = _sc(youtube_short_title_suffix=" (Short)")
    assert _build_youtube_title("T", shorts=True, site_config=sc) == "T (Short)"


def test_titles_are_clamped_to_the_api_limit():
    from services.jobs.youtube_payload import _build_youtube_title

    assert len(_build_youtube_title("x" * 400, shorts=False, site_config=_sc())) == 100
    assert len(_build_youtube_title("x" * 400, shorts=True, site_config=_sc())) <= 100
