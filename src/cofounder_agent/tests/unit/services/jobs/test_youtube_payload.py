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

from poindexter.services.jobs.youtube_payload import (
    _build_youtube_description,
    _build_youtube_title,
    _markdown_to_plain,
    _parse_seo_keywords,
    _strip_markup,
    _trim_at_sentence,
    first_sentences,
    hashtags_for_short,
    short_hook_line,
    short_hook_title,
    shorten_at_word,
    strip_preamble,
    twin_watch_url,
)
from poindexter.services.site_config import SiteConfig


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
    from poindexter.services.jobs.youtube_payload import _build_youtube_title

    assert _build_youtube_title("The Gap Nobody Names", shorts=False, site_config=_sc()) == (
        "The Gap Nobody Names"
    )


def test_short_gets_a_distinguishing_suffix():
    """A post can produce BOTH renders; taking posts.title verbatim for each
    put two identically-named videos on the channel."""
    from poindexter.services.jobs.youtube_payload import _build_youtube_title

    long_form = _build_youtube_title("The Gap Nobody Names", shorts=False, site_config=_sc())
    short = _build_youtube_title("The Gap Nobody Names", shorts=True, site_config=_sc())
    assert short != long_form
    assert short == "The Gap Nobody Names #Shorts"


def test_short_suffix_survives_a_title_at_the_cap():
    """Appending blindly would push the suffix past YouTube's 100-char limit
    and the adapter's clamp would cut off the very thing that distinguishes
    it — so the title is trimmed at a word boundary to make room first."""
    from poindexter.services.jobs.youtube_payload import _build_youtube_title

    long_title = "Why " + "extremely " * 12 + "long titles break naive appending"
    out = _build_youtube_title(long_title, shorts=True, site_config=_sc())
    assert len(out) <= 100
    assert out.endswith(" #Shorts")
    assert not out.replace(" #Shorts", "").endswith(" ")


def test_short_suffix_is_idempotent():
    """A re-sync of an already-suffixed video must not stack a second marker."""
    from poindexter.services.jobs.youtube_payload import _build_youtube_title

    once = _build_youtube_title("The Gap Nobody Names", shorts=True, site_config=_sc())
    assert _build_youtube_title(once, shorts=True, site_config=_sc()) == once
    # An operator-written title that already says #shorts is left alone too.
    assert _build_youtube_title(
        "Already #shorts here", shorts=True, site_config=_sc()
    ) == "Already #shorts here"


def test_empty_suffix_means_no_distinction():
    from poindexter.services.jobs.youtube_payload import _build_youtube_title

    sc = _sc(youtube_short_title_suffix="")
    assert _build_youtube_title("T", shorts=True, site_config=sc) == "T"


def test_custom_suffix_is_honoured():
    from poindexter.services.jobs.youtube_payload import _build_youtube_title

    sc = _sc(youtube_short_title_suffix=" (Short)")
    assert _build_youtube_title("T", shorts=True, site_config=sc) == "T (Short)"


def test_titles_are_clamped_to_the_api_limit():
    from poindexter.services.jobs.youtube_payload import _build_youtube_title

    assert len(_build_youtube_title("x" * 400, shorts=False, site_config=_sc())) == 100
    assert len(_build_youtube_title("x" * 400, shorts=True, site_config=_sc())) <= 100


# ---------------------------------------------------------------------------
# A Short is not a smaller long form (2026-09-22): own title, own opener,
# own utm_medium, hashtags, and the pair links to each other when both are live.
# ---------------------------------------------------------------------------

SHORT_SCRIPT = (
    "Nobody clicks anymore — and that changes what you write. Platforms like "
    "Google and LinkedIn now deliver answers directly. Here is what to do instead."
)


def test_first_sentences_and_shorten_at_word():
    assert first_sentences(SHORT_SCRIPT) == "Nobody clicks anymore — and that changes what you write."
    assert first_sentences(SHORT_SCRIPT, max_sentences=2).endswith("deliver answers directly.")
    assert shorten_at_word("one two three four", 9) == "one two"
    assert shorten_at_word("short", 60) == "short"
    assert shorten_at_word("ends with a comma, then", 18) == "ends with a comma"


def test_short_title_is_the_scripts_hook_plus_suffix():
    hook = short_hook_title(SHORT_SCRIPT, site_config=_sc())
    assert hook == "Nobody clicks anymore — and that changes what you write"  # no trailing stop
    title = _build_youtube_title("Nobody Clicks Anymore: Building Content for Zero-Click Extraction", shorts=True, site_config=_sc(), hook=hook)
    assert title == "Nobody clicks anymore — and that changes what you write #Shorts"
    assert len(title) <= 100


def test_short_title_hook_respects_the_feed_budget():
    hook = short_hook_title(SHORT_SCRIPT, site_config=_sc(youtube_short_title_max_chars="30"))
    assert len(hook) <= 30
    assert hook == "Nobody clicks anymore — and"


def test_short_title_keeps_a_whole_sentence_just_over_the_budget():
    """62 chars against a 60 budget: the whole sentence beats a mid-phrase cut
    ("…answers the"); the 100-char API cap stays the hard limit."""
    script = "Zero-click content is how the open web pays its own bills now. More."
    hook = short_hook_title(script, site_config=_sc(youtube_short_title_max_chars="60"))
    assert hook == "Zero-click content is how the open web pays its own bills now"
    assert short_hook_title(
        script, site_config=_sc(youtube_short_title_max_chars="40"),
    ) == "Zero-click content is how the open web"


def test_description_hook_takes_whole_sentences_only():
    script = ("Zero-click content is the new standard. "
              "As platforms like Google, LinkedIn, TikTok, and Facebook evolve to keep users within their "
              "ecosystems, they surface answers directly, reducing click-through rates intentionally. Third.")
    hook = short_hook_line(script)
    # Two whole sentences fit the 220-char budget, so both are taken — and the
    # hook never ends mid-sentence.
    assert hook.startswith("Zero-click content is the new standard.")
    assert hook.endswith("intentionally.")
    assert len(hook) <= 220
    # A sentence that does NOT fit is dropped whole rather than cut: budget 60
    # takes sentence 1 only.
    assert short_hook_line.__doc__  # (the helper documents the rule it enforces)
    assert first_sentences(script, max_sentences=2, limit=60) == "Zero-click content is the new standard."
    assert first_sentences("A very " + "long " * 60 + "sentence.", max_sentences=2, limit=40).startswith("A very long")


def test_short_title_falls_back_to_the_post_title_without_a_hook():
    title = _build_youtube_title("Post Title", shorts=True, site_config=_sc(), hook="")
    assert title == "Post Title #Shorts"


def test_short_title_source_post_title_keeps_the_old_behaviour():
    title = _build_youtube_title(
        "Post Title", shorts=True, site_config=_sc(youtube_short_title_source="post_title"), hook="a hook",
    )
    assert title == "Post Title #Shorts"


def test_long_form_title_ignores_the_hook():
    assert _build_youtube_title("Post Title", shorts=False, site_config=_sc(), hook="a hook") == "Post Title"


def test_hashtags_start_with_shorts_camelcase_capped_and_deduped():
    tags = hashtags_for_short(
        ["zeroclick content", "content marketing strategy", "Content Marketing Strategy", "click-through rate", "x" * 40, "extra"],
        site_config=_sc(youtube_short_hashtags_max="3"),
    )
    assert tags == ["#Shorts", "#ZeroclickContent", "#ContentMarketingStrategy", "#ClickThroughRate"]
    assert hashtags_for_short([], site_config=_sc()) == ["#Shorts"]
    assert hashtags_for_short(["a", "b"], site_config=_sc(youtube_short_hashtags_max="0")) == ["#Shorts"]


def test_twin_watch_url_forms():
    assert twin_watch_url("abc123", twin_is_short=True) == "https://www.youtube.com/shorts/abc123"
    assert twin_watch_url("abc123", twin_is_short=False) == "https://www.youtube.com/watch?v=abc123"
    assert twin_watch_url("", twin_is_short=True) == ""


def test_short_description_layout_with_a_live_long_form():
    desc = _build_youtube_description(
        seo_description="The excerpt.", body=BODY_MD, site_config=_sc(), slug="nobody-clicks-0bce0e39",
        shorts=True, hook=short_hook_line(SHORT_SCRIPT),
        twin_url=twin_watch_url("LONG1", twin_is_short=False),
        hashtags=["#Shorts", "#ZeroclickContent"],
    )
    paragraphs = desc.split("\n\n")
    assert paragraphs[0].startswith("Nobody clicks anymore")
    assert "The excerpt." not in desc  # the Short opens with ITS hook, not the article's
    assert paragraphs[1] == "Watch the full breakdown: https://www.youtube.com/watch?v=LONG1"
    assert paragraphs[2].startswith("Read the full post: https://www.gladlabs.io/posts/nobody-clicks-0bce0e39?")
    assert "utm_source=youtube" in paragraphs[2] and "utm_medium=shorts" in paragraphs[2]
    assert paragraphs[3] == "#Shorts #ZeroclickContent"
    assert "Nobody cares" not in desc  # no body snippet on a Short, ever


def test_short_description_without_a_live_long_form_has_no_dangling_line():
    desc = _build_youtube_description(
        seo_description="The excerpt.", body="", site_config=_sc(), slug="s",
        shorts=True, hook="Hook line.", twin_url="", hashtags=["#Shorts"],
    )
    assert "Watch the full breakdown" not in desc
    assert desc.split("\n\n") == ["Hook line.", "Read the full post: https://www.gladlabs.io/posts/s?utm_source=youtube&utm_medium=shorts", "#Shorts"]


def test_short_description_falls_back_to_the_excerpt_without_a_hook():
    desc = _build_youtube_description(
        seo_description="The excerpt.", body="", site_config=_sc(), slug="s", shorts=True, hook="", hashtags=["#Shorts"],
    )
    assert desc.startswith("The excerpt.")


def test_long_description_links_the_short_only_when_it_is_live():
    with_twin = _build_youtube_description(
        seo_description="The excerpt.", body="", site_config=_sc(), slug="s",
        twin_url=twin_watch_url("SHORT1", twin_is_short=True),
    )
    assert with_twin.split("\n\n") == [
        "The excerpt.",
        "Read the full post: https://www.gladlabs.io/posts/s?utm_source=youtube&utm_medium=video",
        "Watch the Short: https://www.youtube.com/shorts/SHORT1",
    ]
    without = _build_youtube_description(seo_description="The excerpt.", body="", site_config=_sc(), slug="s")
    assert "Watch the Short" not in without
    assert "utm_medium=video" in without  # the long form keeps its medium


def test_cross_links_can_be_switched_off():
    desc = _build_youtube_description(
        seo_description="The excerpt.", body="", site_config=_sc(youtube_pair_cross_links="false"), slug="s",
        shorts=True, hook="Hook.", twin_url="https://www.youtube.com/watch?v=LONG1", hashtags=["#Shorts"],
    )
    assert "Watch the full breakdown" not in desc


def test_body_snippet_still_follows_the_long_forms_cross_link():
    desc = _build_youtube_description(
        seo_description="You can write the best breakdown of ASUS ROG Astral RTX 5090 bandwidth. Nobody cares.",
        body=BODY_MD, site_config=_sc(youtube_description_body_chars="400"), slug="s",
        twin_url="https://www.youtube.com/shorts/SHORT1",
    )
    parts = desc.split("\n\n")
    assert parts[2] == "Watch the Short: https://www.youtube.com/shorts/SHORT1"
    assert "Amplification is the other half." in desc


# ---------------------------------------------------------------------------
# Sharper hook (2026-09-22, operator): the Short's own narration opened
# "In today's digital age, zero-click content is the new standard." — the
# title inherited six words of throat-clearing, and a Shorts feed shows only
# the first ~40 characters. The script prompt now asks for a flat claim; this
# strip fixes the scripts already frozen into pipeline_versions.
# ---------------------------------------------------------------------------


def test_strip_preamble_cuts_the_run_up_and_recapitalises():
    assert strip_preamble(
        "In today's digital age, zero-click content is the new standard."
    ) == "Zero-click content is the new standard."
    assert strip_preamble("These days, nobody clicks through.") == "Nobody clicks through."
    assert strip_preamble("As we all know, the funnel is dead.") == "The funnel is dead."
    assert strip_preamble(
        "In the world of B2B search, nobody clicks through anymore."
    ) == "Nobody clicks through anymore."


def test_strip_preamble_leaves_a_real_claim_alone():
    for sentence in (
        "Zero-click content is the new standard.",
        "Nobody clicks anymore, and that changes what you write.",
        # No comma: the phrase is the sentence's own subject, not a run-up.
        "Let's talk about zero-click content and what it costs you.",
        # Stripping would leave two words — it cut the sentence, not its run-up.
        "In today's digital age, clicks died.",
    ):
        assert strip_preamble(sentence) == sentence


def test_short_title_drops_the_preamble_the_feed_would_have_shown():
    script = "In today's digital age, zero-click content is the new standard. More."
    assert short_hook_title(script, site_config=_sc()) == "Zero-click content is the new standard"
    title = _build_youtube_title(
        "Nobody Clicks Anymore: Building Content for Zero-Click Extraction",
        shorts=True, site_config=_sc(),
        hook=short_hook_title(script, site_config=_sc()),
    )
    assert title == "Zero-click content is the new standard #Shorts"


def test_short_description_hook_drops_it_too():
    script = (
        "In today's digital age, zero-click content is the new standard. "
        "Platforms surface answers directly."
    )
    hook = short_hook_line(script)
    assert hook.startswith("Zero-click content is the new standard.")
    assert "In today's digital age" not in hook


# ---------------------------------------------------------------------------
# Measured 2026-09-22, phi4:14b over 10 published posts.
#
# The hook rule shipped in #3951 carried a verbatim example sentence
# ("Zero-click content is the new standard.") and the model COPIED it onto
# unrelated articles — 4 of 10, including a JPMorgan trends report and a GPU
# lock post-mortem. A/B/C on the same article settled it: example present ->
# parroted; example removed or swapped -> correct, on-topic opener. The
# example is gone.
#
# With it gone the model writes on-topic openers but ignores the ban on
# describing the article: 4 of 10 came back "Discover how ...". Instructions
# alone do not hold that line, so the strip does.
# ---------------------------------------------------------------------------


def test_strip_cuts_an_opener_that_describes_the_article():
    """Real openers from the 10-post sweep."""
    assert strip_preamble(
        "Discover how a GPU lock bug was quietly wrecking our RAG sweep"
    ) == "A GPU lock bug was quietly wrecking our RAG sweep"
    assert strip_preamble(
        "Discover how JPMorgan Chase highlights six pivotal shifts"
    ) == "JPMorgan Chase highlights six pivotal shifts"
    assert strip_preamble(
        "This article reveals how a tiny transformer model trained fast"
    ) == "A tiny transformer model trained fast"
    assert strip_preamble("Here's why the lock was held for nine hours") == "The lock was held for nine hours"


def test_describe_strip_needs_no_comma_but_still_needs_a_claim_left():
    """Unlike a run-up clause it is a bare prefix, so no comma is required —
    but the three-word floor still applies."""
    assert strip_preamble("Discover it.") == "Discover it."
    assert strip_preamble("Learn how we did") == "Learn how we did"  # 2 words survive


def test_a_real_claim_is_never_touched_by_either_strip():
    for sentence in (
        "Zero-click content is the new standard.",
        "A 4-bit model just beat its full-precision original.",
        "The page-view cursor outran the data.",
    ):
        assert strip_preamble(sentence) == sentence


def test_both_strips_accept_a_curly_apostrophe():
    """The writer emits U+2019. A class of only ' let the exact shape these
    exist to catch walk through — 1 of 10 in the 2026-09-22 sweep."""
    assert strip_preamble(
        "In today’s digital age, ensuring the accuracy of AI matters"
    ) == "Ensuring the accuracy of AI matters"
    assert strip_preamble(
        "Here’s why the lock was held for nine hours"
    ) == "The lock was held for nine hours"
    assert strip_preamble(
        "It’s no secret, the funnel is dead now"
    ) == "The funnel is dead now"
