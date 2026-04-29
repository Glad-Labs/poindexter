"""Tests for services/markdown_normalizer.py — gh#191 fix."""

from __future__ import annotations

import markdown as md
import pytest

from services.markdown_normalizer import normalize_markdown


# ---------------------------------------------------------------------------
# Repro from the issue body
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRepro:
    def test_issue_body_repro_now_renders_as_list(self):
        """The exact repro from gh#191: bullets glued to intro line render
        as <p>...*   bullet</p> before the fix, <p>...</p><ul>... after."""
        bad = (
            "By the end of this guide, you will understand:\n"
            "*   First bullet\n"
            "*   Second bullet"
        )

        # Before the fix: python-markdown produces a <p> with literal asterisks
        before = md.markdown(
            bad, extensions=["extra", "codehilite", "sane_lists", "smarty"],
        )
        assert "<ul>" not in before  # confirms the bug repro

        # After: normalizer inserts blank line, then python-markdown sees a list
        normalized = normalize_markdown(bad)
        after = md.markdown(
            normalized,
            extensions=["extra", "codehilite", "sane_lists", "smarty"],
        )
        assert "<ul>" in after
        assert "<li>First bullet</li>" in after
        assert "<li>Second bullet</li>" in after


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdempotent:
    def test_already_correct_unchanged(self):
        good = (
            "Intro line:\n\n"
            "*   First\n"
            "*   Second\n"
        )
        assert normalize_markdown(good) == good

    def test_running_twice_is_stable(self):
        bad = (
            "Intro:\n"
            "- one\n"
            "- two\n"
        )
        once = normalize_markdown(bad)
        twice = normalize_markdown(once)
        assert once == twice


# ---------------------------------------------------------------------------
# List markers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListMarkers:
    @pytest.mark.parametrize("marker", ["* item", "- item", "+ item"])
    def test_unordered_markers(self, marker: str):
        bad = f"Intro:\n{marker}\nmore"
        out = normalize_markdown(bad)
        assert out == f"Intro:\n\n{marker}\nmore"

    @pytest.mark.parametrize("marker", ["1. item", "10. item", "1) item"])
    def test_numbered_markers(self, marker: str):
        bad = f"Intro:\n{marker}\nmore"
        out = normalize_markdown(bad)
        assert out == f"Intro:\n\n{marker}\nmore"


# ---------------------------------------------------------------------------
# Don't false-positive on bold / hr / inline asterisks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFalsePositives:
    def test_bold_not_treated_as_bullet(self):
        text = "Intro line.\n**bold here**\nmore"
        # **bold** has no space after the second *, so the bullet regex
        # shouldn't match. No blank line should be inserted.
        assert normalize_markdown(text) == text

    def test_horizontal_rule_not_treated_as_bullet(self):
        text = "Intro line.\n---\nmore"
        # `---` on its own (no trailing space + content) is an HR, not a
        # bullet. No blank line should be inserted.
        assert normalize_markdown(text) == text

    def test_asterisk_in_word_not_treated_as_bullet(self):
        text = "footnote*marker is fine."
        assert normalize_markdown(text) == text


# ---------------------------------------------------------------------------
# Code fences are sacred
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCodeFences:
    def test_bullet_inside_code_fence_untouched(self):
        text = (
            "Example shell session:\n"
            "```\n"
            "intro\n"
            "* not a bullet\n"
            "```\n"
        )
        # Bullet inside fence must NOT get a blank line inserted —
        # changing whitespace inside a code block changes meaning.
        assert normalize_markdown(text) == text

    def test_tilde_fence_also_protected(self):
        text = (
            "Example:\n"
            "~~~python\n"
            "data = [\n"
            "* maybe a bullet but inside code\n"
            "~~~\n"
        )
        assert normalize_markdown(text) == text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEdgeCases:
    def test_empty_string(self):
        assert normalize_markdown("") == ""

    def test_none_short_circuits(self):
        # Per the impl: ``if not text: return text``
        assert normalize_markdown(None) is None  # type: ignore[arg-type]

    def test_list_at_start_no_extra_newline(self):
        # No "previous line" to separate from — leave alone.
        text = "* first item\n* second"
        assert normalize_markdown(text) == text

    def test_consecutive_list_items_no_inserts_between(self):
        bad = "Intro:\n- a\n- b\n- c"
        out = normalize_markdown(bad)
        # Exactly ONE blank line, between intro and first bullet.
        assert out == "Intro:\n\n- a\n- b\n- c"

    def test_trailing_newline_preserved(self):
        bad = "Intro:\n- a\n"
        assert normalize_markdown(bad).endswith("\n")

    def test_no_trailing_newline_preserved(self):
        bad = "Intro:\n- a"
        assert not normalize_markdown(bad).endswith("\n")
