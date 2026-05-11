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
    looks_like_sdxl_prompt,
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


# ---------------------------------------------------------------------------
# looks_like_sdxl_prompt — Glad-Labs/poindexter#469
# ---------------------------------------------------------------------------


class TestLooksLikeSdxlPrompt:
    """Positive cases — these MUST be flagged as SDXL-prompt-shaped."""

    @pytest.mark.parametrize("verb", [
        "Show", "Render", "Depict", "Create", "Generate",
        "Draw", "Illustrate", "Visualize", "Visualise", "Imagine",
    ])
    def test_imperative_opener_at_string_start(self, verb):
        assert looks_like_sdxl_prompt(f"{verb} the key components of SDXL")

    @pytest.mark.parametrize("verb", [
        "show", "render", "depict", "create", "generate",
        "draw", "illustrate", "visualize", "visualise", "imagine",
    ])
    def test_imperative_opener_case_insensitive(self, verb):
        assert looks_like_sdxl_prompt(f"{verb} a serene server room")

    def test_imperative_after_first_sentence(self):
        # The exact bug from issue #469.
        poisoned = (
            "An isometric diagram of a simplified SDXL architecture. "
            "Show the key components (encoder, decoder, refiner)..."
        )
        assert looks_like_sdxl_prompt(poisoned)

    def test_imperative_after_semicolon(self):
        assert looks_like_sdxl_prompt(
            "A serene server room; render with cinematic lighting"
        )

    def test_style_prefix_isometric(self):
        assert looks_like_sdxl_prompt(
            "isometric 3D illustration, clean vector style, soft shadows"
        )

    def test_style_prefix_photorealistic(self):
        assert looks_like_sdxl_prompt(
            "photorealistic scene, cinematic lighting"
        )

    def test_style_prefix_cinematic(self):
        # Bare "cinematic, ..." opener IS a prompt shape.
        assert looks_like_sdxl_prompt(
            "cinematic lighting, dramatic shadows, no text"
        )

    def test_style_prefix_flat_vector(self):
        assert looks_like_sdxl_prompt(
            "flat vector illustration, simple geometric shapes, cyan and dark navy"
        )

    def test_style_prefix_cyberpunk(self):
        assert looks_like_sdxl_prompt(
            "cyberpunk neon style, dark background, glowing cyan purple"
        )

    def test_style_prefix_dark_moody_editorial(self):
        assert looks_like_sdxl_prompt(
            "dark moody editorial photograph, dramatic lighting"
        )

    def test_style_prefix_macro_close_up(self):
        # The full style entry from INLINE_STYLES, not the word "macro" alone.
        assert looks_like_sdxl_prompt(
            "macro close-up photograph, extreme detail, bokeh"
        )

    def test_negative_fragment_no_text(self):
        assert looks_like_sdxl_prompt(
            "A diagram of three layers of cloud infrastructure, no text, no faces"
        )

    def test_negative_fragment_no_faces(self):
        assert looks_like_sdxl_prompt(
            "Server room with monitors, no faces, soft shadows"
        )

    def test_negative_fragment_faceless_silhouettes(self):
        assert looks_like_sdxl_prompt(
            "Engineers working at terminals, faceless silhouettes only"
        )

    def test_negative_fragment_negative_prompt_phrase(self):
        assert looks_like_sdxl_prompt(
            "A serene server room. Negative prompt: people, text, watermark"
        )

    def test_negative_fragment_low_quality(self):
        assert looks_like_sdxl_prompt(
            "A blueprint diagram, low quality, distorted"
        )

    # Real-world poisoned alt — end-to-end check ---------------------------

    def test_exact_issue_469_repro_string(self):
        poisoned = (
            "An isometric diagram of a simplified SDXL architecture. "
            "Show the key components (encoder, decoder, refiner) with "
            "interconnected arrows, no text, no faces."
        )
        assert looks_like_sdxl_prompt(poisoned)


class TestLooksLikeSdxlPromptNegatives:
    """Negative cases — real human-written alts must pass through unchanged.

    These are the false-positive guards. The heuristic is deliberately
    conservative; if any of these flip to True the regex needs to be
    tightened, NOT loosened with carve-outs.
    """

    def test_macro_photo_of_circuit_board(self):
        # "macro" appears, but as a noun modifier, not a style prefix.
        assert not looks_like_sdxl_prompt(
            "A close-up macro photo of a circuit board"
        )

    def test_isometric_tile_maps_in_retro_games(self):
        # "isometric" appears as an adjective in normal prose.
        assert not looks_like_sdxl_prompt(
            "Isometric tile maps in classic retro RPGs"
        )

    def test_cinematic_still_from_trailer(self):
        # "Cinematic" as an adjective, no comma-delimited style chain.
        assert not looks_like_sdxl_prompt(
            "Cinematic still from the 1982 Blade Runner trailer"
        )

    def test_server_room_with_blue_lighting(self):
        assert not looks_like_sdxl_prompt(
            "A modern server room with cool blue accent lighting"
        )

    def test_diagram_of_kubernetes_architecture(self):
        # No imperative verb, no style prefix, no negative fragment.
        assert not looks_like_sdxl_prompt(
            "A diagram showing the Kubernetes control-plane architecture"
        )

    def test_developer_workspace_with_dual_monitors(self):
        assert not looks_like_sdxl_prompt(
            "A developer workspace with dual monitors and a mechanical keyboard"
        )

    def test_chart_comparing_training_time(self):
        assert not looks_like_sdxl_prompt(
            "A line chart comparing training time across three GPU generations"
        )

    def test_show_inside_noun_phrase(self):
        # "show" not at sentence boundary — "trade show booth" type usage.
        assert not looks_like_sdxl_prompt(
            "Photograph of a busy trade show booth at SIGGRAPH 2024"
        )

    def test_empty_and_whitespace_return_false(self):
        assert not looks_like_sdxl_prompt("")
        assert not looks_like_sdxl_prompt("   ")
        assert not looks_like_sdxl_prompt(None)  # type: ignore[arg-type]

    def test_short_real_alt(self):
        assert not looks_like_sdxl_prompt("A cat napping on a windowsill")


# ---------------------------------------------------------------------------
# sanitize_alt_text + the SDXL heuristic — falls back to topic alt
# ---------------------------------------------------------------------------


class TestSanitizeAltTextDropsSdxlPrompts:
    """Integration of looks_like_sdxl_prompt into sanitize_alt_text."""

    def test_imperative_prompt_returns_topic_fallback(self):
        out = sanitize_alt_text(
            "Show the key components of SDXL with arrows",
            budget=120,
            topic="Stable Diffusion XL",
        )
        # Topic-derived fallback, not the raw prompt.
        assert "Stable Diffusion XL" in out
        assert "Show" not in out

    def test_style_prefix_prompt_returns_topic_fallback(self):
        out = sanitize_alt_text(
            "isometric 3D illustration, clean vector style, soft shadows",
            budget=120,
            topic="Cloud architecture",
        )
        assert "Cloud architecture" in out
        assert "isometric" not in out.lower()

    def test_negative_fragment_prompt_returns_topic_fallback(self):
        out = sanitize_alt_text(
            "A diagram of three cloud layers, no text, no faces",
            budget=120,
            topic="Multi-cloud strategy",
        )
        assert "Multi-cloud strategy" in out
        assert "no text" not in out
        assert "no faces" not in out

    def test_issue_469_exact_repro_lands_clean(self):
        # End-to-end through sanitize_alt_text with the exact poisoned
        # string from the bug.
        poisoned = (
            "An isometric diagram of a simplified SDXL architecture. "
            "Show the key components (encoder, decoder, refiner)..."
        )
        out = sanitize_alt_text(
            poisoned, budget=120, topic="Stable Diffusion XL on RTX 5090",
        )
        assert "Show the key" not in out
        assert "Stable Diffusion XL on RTX 5090" in out

    def test_real_alt_passes_through_unchanged(self):
        # The negative test that matters most — real alts must survive.
        alt = "A close-up macro photo of a circuit board with red LEDs"
        out = sanitize_alt_text(alt, budget=120, topic="electronics")
        assert out == alt

    def test_no_topic_falls_back_to_article_illustration(self):
        # When the caller forgets to pass topic, we still drop the
        # prompt rather than ship it — fallback is the generic phrase.
        out = sanitize_alt_text(
            "Render a serene server room with cinematic lighting",
            budget=120,
        )
        assert "Render" not in out
        # _fallback_alt's no-topic default.
        assert "article illustration" in out
