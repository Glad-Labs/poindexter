"""The deterministic hook rules (services/short_hook.py).

Every case here is a shape MEASURED on 2026-09-22 over the ten most recent
published posts with the production scene model (phi4:14b), not an invented
one. The prompt bans all of them explicitly and the model produced them
anyway, which is the whole argument for judging the output instead of
trusting the instruction.
"""

from __future__ import annotations

import pytest

from poindexter.services.short_hook import (
    CONTENT_DEFECTS,
    HOOK_MAX_CHARS_DEFAULT,
    HOOK_MAX_WORDS_DEFAULT,
    content_defects,
    first_sentence,
    hook_defects,
    hook_limits,
    is_acceptable_hook,
    sentences,
    strip_preamble,
)
from poindexter.services.site_config import SiteConfig


class TestStrip:
    def test_run_up_clause_needs_its_comma(self):
        assert strip_preamble(
            "In today's digital age, zero-click content is the new standard."
        ) == "Zero-click content is the new standard."
        # No comma: the phrase is the sentence's own subject.
        assert strip_preamble(
            "Let's talk about zero-click content and what it costs"
        ) == "Let's talk about zero-click content and what it costs"

    def test_describes_article_prefix_needs_no_comma(self):
        assert strip_preamble(
            "Discover how a GPU lock bug was quietly wrecking our RAG sweep"
        ) == "A GPU lock bug was quietly wrecking our RAG sweep"

    def test_curly_apostrophe(self):
        assert strip_preamble(
            "In today’s digital age, ensuring the accuracy of AI matters"
        ) == "Ensuring the accuracy of AI matters"

    def test_three_word_floor_stops_it_eating_the_sentence(self):
        assert strip_preamble("In today's digital age, clicks died.") == "In today's digital age, clicks died."
        assert strip_preamble("Discover it.") == "Discover it."

    def test_a_real_claim_is_untouched(self):
        for s in ("Zero-click content is the new standard.",
                  "A 4-bit model just beat its full-precision original."):
            assert strip_preamble(s) == s


class TestDefects:
    def test_a_clean_claim_has_none(self):
        assert hook_defects("Zero-click content is the new standard.") == ()
        assert is_acceptable_hook("Zero-click content is the new standard.")

    def test_defects_are_judged_after_the_strip(self):
        """A sentence the strip rescues must not buy an LLM call."""
        assert hook_defects("Discover how clicks died on the open web") == ()

    @pytest.mark.parametrize(
        ("sentence", "expected"),
        [
            ("llama.cpp vs vLLM vs SGLang: The Right Tool for Your Needs", "too_many_words"),
            ("JPMorgan Chase highlights six pivotal shifts for banks", "too_long"),
            ("Ever wondered why nobody clicks anymore?", "question"),
            ("Zero-click content and the open web", "not_a_claim"),
            ("Clicks died", "fragment"),
            ("", "empty"),
        ],
    )
    def test_measured_defect_shapes(self, sentence, expected):
        assert expected in hook_defects(sentence)

    def test_restating_the_article_title_is_a_defect(self):
        title = "How a GPU Lock Bug Was Quietly Wrecking Our RAG Sweep"
        assert "restates_title" in hook_defects(
            "A GPU lock bug was quietly wrecking our RAG sweep", article_title=title,
        )
        # Sharing the subject is not restating it.
        assert "restates_title" not in hook_defects(
            "The lock was held for nine hours", article_title=title, max_chars=60,
        )

    def test_budgets_are_caller_supplied(self):
        s = "JPMorgan Chase highlights six pivotal shifts"
        assert "too_long" in hook_defects(s, max_chars=20)
        assert "too_long" not in hook_defects(s, max_chars=80)


class TestLimits:
    def test_defaults_match_the_feed_window(self):
        assert hook_limits(None) == (HOOK_MAX_CHARS_DEFAULT, HOOK_MAX_WORDS_DEFAULT)
        assert HOOK_MAX_CHARS_DEFAULT == 42

    def test_db_overrides(self):
        sc = SiteConfig(initial_config={
            "media.short_hook.max_chars": "30", "media.short_hook.max_words": "6",
        })
        assert hook_limits(sc) == (30, 6)

    def test_a_junk_override_falls_back(self):
        sc = SiteConfig(initial_config={"media.short_hook.max_chars": "wide"})
        assert hook_limits(sc)[0] == HOOK_MAX_CHARS_DEFAULT


def test_sentence_helpers():
    assert sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]
    assert first_sentence("One. Two.") == "One."
    assert first_sentence("") == ""


class TestContentVsLength:
    """Length is a shortening problem, content is a regeneration problem.

    Measured 2026-09-22: asked for <=42 chars, phi4:14b returned 59-92 — but
    those sentences were good claims. Calling an LLM again to shorten them
    would spend a call to make them no better, so only content defects do.
    """

    def test_a_long_good_claim_has_no_content_defect(self):
        s = "Our GPU lock bug was quietly wrecking our RAG sweep for weeks"
        assert "too_long" in hook_defects(s)
        assert content_defects(s) == ()

    def test_a_short_bad_claim_does(self):
        assert "question" in content_defects("Ever wondered why?", max_chars=99)

    def test_the_split_is_exhaustive(self):
        """Every defect hook_defects can return is either content or length —
        a new one must be classified deliberately, not default to 'length'."""
        length_only = {"too_long", "too_many_words"}
        seen = set()
        for s, t in [("", ""), ("Clicks died", ""), ("Ever wondered why clicks died now?", ""),
                     ("Discover how it", ""), ("Zero-click content and the open web", ""),
                     ("In today's age, it", ""),
                     ("A GPU lock bug wrecked our RAG sweep",
                      "How a GPU Lock Bug Wrecked Our RAG Sweep"),
                     ("JPMorgan Chase highlights six pivotal shifts for banks everywhere", "")]:
            seen |= set(hook_defects(s, article_title=t))
        assert seen, "fixtures produced no defects"
        assert seen <= (CONTENT_DEFECTS | length_only), seen - (CONTENT_DEFECTS | length_only)
