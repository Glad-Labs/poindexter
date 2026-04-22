"""Unit tests for services.alt_text — GitHub issue Glad-Labs/poindexter#84.

Covers the four acceptance criteria from the bug:

1. Regex strip on representative fixtures (mixed ``||sdxl:||`` and
   ``||pexels:||`` tokens).
2. At-budget alt text is accepted.
3. Mid-word-truncated alt text is rejected (loud-fail).
4. Alt text with a trailing pipe is rejected.

Plus round-trip sanitization and ``<img>`` tag scrubbing.
"""

from __future__ import annotations

import pytest

from services.alt_text import (
    assert_alt_text_clean,
    iter_img_alts,
    sanitize_alt_text,
    strip_pipeline_tokens,
    strip_tokens_from_img_tags,
)

# ---------------------------------------------------------------------------
# strip_pipeline_tokens
# ---------------------------------------------------------------------------


class TestStripPipelineTokens:
    def test_removes_sdxl_token(self):
        out = strip_pipeline_tokens("A blueprint diagram ||sdxl:blueprint||")
        assert out == "A blueprint diagram"

    def test_removes_pexels_token(self):
        out = strip_pipeline_tokens(
            "Modern server room ||pexels:real-world objects||"
        )
        assert out == "Modern server room"

    def test_removes_token_mid_string(self):
        out = strip_pipeline_tokens(
            "Screens ||pexels:screens with code|| showing Python"
        )
        assert out == "Screens showing Python"

    def test_removes_multiple_tokens(self):
        out = strip_pipeline_tokens(
            "foo ||sdxl:x|| bar ||pexels:y|| baz"
        )
        assert out == "foo bar baz"

    def test_no_token_is_noop(self):
        assert strip_pipeline_tokens("plain text") == "plain text"

    def test_empty_string_returns_empty(self):
        assert strip_pipeline_tokens("") == ""

    def test_none_returns_none_safely(self):
        assert strip_pipeline_tokens(None) is None  # type: ignore[arg-type]

    def test_trailing_period_stays_attached(self):
        out = strip_pipeline_tokens("Complete sentence. ||sdxl:blueprint||")
        assert out == "Complete sentence."

    def test_single_pipe_is_not_stripped(self):
        # A lone ``|`` is not a token — shouldn't be touched.
        out = strip_pipeline_tokens("foo | bar")
        assert out == "foo | bar"

    def test_uppercase_provider_not_stripped(self):
        # Pipeline emits lowercase providers; something else is going on
        # if we see ``||SDXL:x||`` and we should leave it for the
        # assertion to loud-fail on.
        out = strip_pipeline_tokens("foo ||SDXL:x||")
        assert "||SDXL:x||" in out


# ---------------------------------------------------------------------------
# sanitize_alt_text
# ---------------------------------------------------------------------------


class TestSanitizeAltText:
    def test_short_clean_draft_passes_through(self):
        assert (
            sanitize_alt_text("Cats nap on a sunny windowsill.", budget=120)
            == "Cats nap on a sunny windowsill."
        )

    def test_strips_pipeline_tokens(self):
        out = sanitize_alt_text(
            "A diagram of Kubernetes ||sdxl:blueprint||",
            budget=120,
        )
        assert "||" not in out
        assert "sdxl" not in out
        assert out.startswith("A diagram of Kubernetes")

    def test_strips_image_prefix(self):
        out = sanitize_alt_text("IMAGE: a cat", budget=120)
        assert out == "a cat"

    def test_strips_figure_prefix(self):
        out = sanitize_alt_text("Figure - a server room", budget=120)
        assert out == "a server room"

    def test_long_draft_truncates_on_sentence_boundary(self):
        draft = (
            "This first sentence is under the budget. "
            "But this second trailing sentence would push us way over the "
            "character limit that we have set."
        )
        out = sanitize_alt_text(draft, budget=50)
        assert out == "This first sentence is under the budget."
        assert len(out) <= 50

    def test_long_draft_without_sentence_breaks_uses_word_boundary(self):
        # No sentence-ending punctuation within budget → word-boundary
        # cut with ``...`` suffix, never mid-word.
        draft = "Kubernetes pod scaling across multiple availability zones"
        out = sanitize_alt_text(draft, budget=30)
        # No mid-word chop.
        assert not out.endswith("acro") and not out.endswith("multi")
        # Ends with ellipsis or a word boundary.
        assert out.endswith("...") or out.endswith(" ")  # pragma: no cover
        assert len(out) <= 30

    def test_empty_draft_falls_back_to_topic(self):
        out = sanitize_alt_text("", budget=120, topic="Kubernetes security")
        assert "Kubernetes security" in out
        assert len(out) <= 120

    def test_none_draft_falls_back_to_topic(self):
        out = sanitize_alt_text(None, budget=120, topic="async Python")
        assert "async Python" in out

    def test_collapses_newlines_and_whitespace(self):
        out = sanitize_alt_text("Line one\n\nLine\ttwo", budget=120)
        assert out == "Line one Line two"

    def test_strips_square_brackets(self):
        out = sanitize_alt_text("[IMAGE-1]: something", budget=120)
        assert "[" not in out and "]" not in out

    def test_never_produces_trailing_pipe(self):
        # Worst case: token arrives mid-draft and the token-pipe reduction
        # still leaves content clean.
        draft = "Scaling ||pexels:screens with code|| operations"
        out = sanitize_alt_text(draft, budget=120)
        assert not out.endswith("|")
        assert "||" not in out

    def test_over_budget_never_mid_word_chopped(self):
        # Even pathological single-word drafts get fallback, not chopped.
        draft = "Supercalifragilisticexpialidocious" * 10
        out = sanitize_alt_text(draft, budget=40, topic="word games")
        assert len(out) <= 40
        # Must not end inside a run of letters.
        assert out.endswith(("...", ".", "!", "?")) or " " in out


# ---------------------------------------------------------------------------
# assert_alt_text_clean — the pipeline gate
# ---------------------------------------------------------------------------


class TestAssertAltTextClean:
    def test_accepts_clean_at_budget(self):
        # Exactly at budget and ends with punctuation — fine.
        alt = "Cats nap." + "." * 0
        alt = alt + "." * (120 - len(alt))  # pad to budget with dots
        # The last char is now ".", which is ending punctuation → accept.
        assert_alt_text_clean(alt, budget=120)  # must not raise

    def test_accepts_under_budget_complete_sentence(self):
        assert_alt_text_clean("A photo of a server room.", budget=120)

    def test_accepts_empty_alt(self):
        # Decorative images can have empty alt — not our problem to
        # populate, but we shouldn't reject it.
        assert_alt_text_clean("", budget=120)
        assert_alt_text_clean(None, budget=120)  # type: ignore[arg-type]

    def test_rejects_mid_word_truncation(self):
        # Alt whose length equals the budget and ends on a letter — the
        # exact pattern GH-84 observed in DB (``...around a central Kube``).
        alt = "An abstract diagram illustrating three layers of security "
        alt += "standards around a central Kube"
        # Force length to be >= budget so the assertion triggers.
        budget = len(alt)
        with pytest.raises(ValueError, match="mid-word"):
            assert_alt_text_clean(alt, budget=budget)

    def test_rejects_trailing_pipe(self):
        alt = "Scaling operations |"
        with pytest.raises(ValueError, match="pipe"):
            assert_alt_text_clean(alt, budget=120)

    def test_rejects_unstripped_sdxl_token(self):
        with pytest.raises(ValueError, match="pipeline token"):
            assert_alt_text_clean(
                "A blueprint ||sdxl:blueprint||", budget=120
            )

    def test_rejects_unstripped_pexels_token(self):
        with pytest.raises(ValueError, match="pipeline token"):
            assert_alt_text_clean(
                "Servers ||pexels:real-world objects||", budget=120
            )

    def test_accepts_alt_well_under_budget_ending_with_word_char(self):
        # "ends with word char AND length < budget" is legal — it's only
        # the at-budget case that's suspect.
        assert_alt_text_clean("A cat", budget=120)


# ---------------------------------------------------------------------------
# strip_tokens_from_img_tags — post-stage scrub
# ---------------------------------------------------------------------------


class TestStripTokensFromImgTags:
    def test_strips_from_single_img(self):
        html = '<img src="x.png" alt="A cat ||sdxl:blueprint||" />'
        out = strip_tokens_from_img_tags(html)
        assert '||' not in out
        assert 'alt="A cat"' in out

    def test_strips_from_multiple_imgs(self):
        html = (
            '<img src="a.png" alt="X ||sdxl:a||" />\n'
            '<img src="b.png" alt="Y ||pexels:b||" />\n'
        )
        out = strip_tokens_from_img_tags(html)
        assert '||' not in out
        assert 'alt="X"' in out
        assert 'alt="Y"' in out

    def test_preserves_other_attributes(self):
        html = '<img src="x.png" alt="A ||sdxl:b||" width="1024" height="1024" />'
        out = strip_tokens_from_img_tags(html)
        assert 'width="1024"' in out
        assert 'height="1024"' in out
        assert 'src="x.png"' in out

    def test_idempotent(self):
        html = '<img src="x.png" alt="A cat ||sdxl:blueprint||" />'
        once = strip_tokens_from_img_tags(html)
        twice = strip_tokens_from_img_tags(once)
        assert once == twice

    def test_noop_when_no_tokens(self):
        html = '<img src="x.png" alt="A clean alt sentence." />'
        assert strip_tokens_from_img_tags(html) == html

    def test_handles_empty_content(self):
        assert strip_tokens_from_img_tags("") == ""
        assert strip_tokens_from_img_tags(None) is None  # type: ignore[arg-type]

    def test_case_insensitive_img_tag(self):
        html = '<IMG src="x.png" alt="A ||sdxl:b||" />'
        out = strip_tokens_from_img_tags(html)
        assert '||' not in out


# ---------------------------------------------------------------------------
# iter_img_alts
# ---------------------------------------------------------------------------


class TestIterImgAlts:
    def test_yields_each_alt(self):
        html = (
            '<img src="a.png" alt="first" />'
            '<img src="b.png" alt="second" />'
        )
        alts = list(iter_img_alts(html))
        assert alts == ["first", "second"]

    def test_empty_content_yields_nothing(self):
        assert list(iter_img_alts("")) == []

    def test_no_img_yields_nothing(self):
        assert list(iter_img_alts("<p>hello</p>")) == []
