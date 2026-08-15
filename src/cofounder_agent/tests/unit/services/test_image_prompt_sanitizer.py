"""Tests for ``services.image_prompt_sanitizer`` (poindexter#3229).

The prompt-writing model (``inline_image_prompt_model``, a local instruct
model) frequently answers the ``image.featured_image`` brief by restating it —
a markdown plan of ``Subject:`` / ``Constraints:`` / ``Output ONLY the prompt``
bullets — which ``image_prompt_max_tokens`` then truncates before the model
ever reaches the actual prompt. That reply used to go to the diffusion model
verbatim: 26 of the 39 posts published in the 60 days before this fix carry a
bullet-list scratchpad in ``posts.featured_image_data->>'image_gen_prompt'``.

The two multi-line fixtures below are REAL production values, copied from
that column, not invented shapes.
"""

from __future__ import annotations

import pytest

from services.image_prompt_sanitizer import (
    clean_image_prompt,
    strip_bullet,
    subject_fallback_prompt,
)

# posts.slug = 'the-echoing-title-bug-f4a7d7eb', published 2026-08-15.
LEAKED_SCAFFOLD_ONLY = """*   Subject: A single polished chrome sphere on a dark mirrored surface with a distorted/shifted reflection (visual dissonance).
    *   Style: Risograph print style (grainy texture, limited 3-color palette, vintage print shop aesthetic, halftone dots).
    *   Constraints: No text, no generic tech backgrounds, no teal/cyan lock, no identifiable faces, no hands.
    *   Output format: Single prompt, 1-2 sentences, nothing else."""

# The other observed shape: a usable prompt, then the brief echoed as a tail.
LEAKED_PROSE_THEN_BULLETS = """Magazine-style editorial cover illustration.
Chasing a silent failure in a GPU image gate: stale readings and phantom memory.
Low poly 3D geometric mesh style, clean triangulated shapes, no text.

        *   Concrete/specific subject or visual metaphor.
        *   No generic glowing circuit boards.
        *   Output: ONLY the prompt, 1-2 sentences."""


class TestCleanImagePrompt:
    def test_scaffold_only_reply_salvages_the_subject_line(self):
        """A pure plan still names a real scene on its Subject: bullet."""
        out = clean_image_prompt(LEAKED_SCAFFOLD_ONLY)
        assert out == (
            "A single polished chrome sphere on a dark mirrored surface "
            "with a distorted/shifted reflection (visual dissonance)."
        )

    def test_prose_then_bullets_keeps_prose_drops_the_tail(self):
        out = clean_image_prompt(LEAKED_PROSE_THEN_BULLETS)
        assert out.startswith("Magazine-style editorial cover illustration.")
        assert "Low poly 3D geometric mesh style" in out

    @pytest.mark.parametrize(
        "leak",
        ["Constraints", "Output", "Subject:", "circuit boards", "*"],
        ids=["constraints", "output", "subject-label", "negative-instruction", "bullet"],
    )
    def test_no_scaffolding_token_survives_into_the_render(self, leak):
        """The whole point: none of the brief may reach the diffusion model."""
        for raw in (LEAKED_SCAFFOLD_ONLY, LEAKED_PROSE_THEN_BULLETS):
            assert leak not in clean_image_prompt(raw)

    def test_clean_reply_passes_through_untouched(self):
        """A model that answers correctly must not be edited."""
        good = (
            "A row of brass taps over a bone-dry basin, retro 16-bit pixel art, "
            "bright colors on a dark background."
        )
        assert clean_image_prompt(good) == good

    def test_prose_hyphen_is_not_mistaken_for_a_bullet(self):
        """Bullet detection is line-leading only — mid-line dashes are prose."""
        good = "A low-poly graphics card - a ghosted duplicate hovering above it."
        assert clean_image_prompt(good) == good

    def test_answer_label_is_stripped_but_the_sentence_kept(self):
        out = clean_image_prompt(
            "Prompt: A tangled ball of yarn resolving into a node-and-edge graph."
        )
        assert out == "A tangled ball of yarn resolving into a node-and-edge graph."

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "*   Constraints: no text\n    *   Output ONLY the image prompt.",
            "Subject: tiny",
        ],
        ids=["empty", "whitespace", "pure-scaffold", "too-short-to-render"],
    )
    def test_unusable_replies_return_empty_for_the_caller_to_fall_back(self, raw):
        """"" is the signal to use a subject-bearing fallback, not to render."""
        assert clean_image_prompt(raw) == ""


class TestStripBullet:
    @pytest.mark.parametrize("bullet", ["*", "-", "•", "–", "+"])
    def test_reports_bulleted_lines(self, bullet):
        assert strip_bullet(f"  {bullet}   Constraints: no text") == (
            "Constraints: no text",
            True,
        )

    def test_reports_plain_prose(self):
        assert strip_bullet("  A glowing server rack  ") == ("A glowing server rack", False)


class TestSubjectFallbackPrompt:
    def test_keeps_the_subject(self):
        """The old fallback was style-only, so a degraded run rendered a
        handsome image of nothing in particular. An off-topic hero is its own
        defect — it must relate to the post."""
        out = subject_fallback_prompt(
            "taps that ingested nothing for 17 days",
            "retro 16-bit pixel art style",
            "bright colors on dark background",
        )
        assert out.startswith("taps that ingested nothing for 17 days")
        assert "retro 16-bit pixel art style" in out
        assert "bright colors on dark background" in out
        assert out.endswith("no text, no faces")

    def test_collapses_whitespace_and_caps_length(self):
        out = subject_fallback_prompt("a  b\n\nc " + "x" * 500, "flat vector", "cyan")
        assert "\n" not in out
        assert out.startswith("a b c ")
        assert len(out.split(",")[0]) <= 300

    def test_survives_a_missing_subject(self):
        """Empty subject must not leave a leading comma."""
        assert subject_fallback_prompt("", "flat vector", "cyan") == (
            "flat vector, cyan, no text, no faces"
        )
