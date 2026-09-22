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
    strip_scaffold,
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

    @pytest.mark.parametrize(
        "sentence,expected",
        [
            # "In a <adj> <noun>," — a framing device announcing that a fact
            # is coming, in place of the fact. 6 of 489 stored short scripts.
            ("In a surprising twist, Llama.cpp isn't just competing with vLLM",
             "Llama.cpp isn't just competing with vLLM"),
            ("In a groundbreaking study, small models matched a 70B on reasoning",
             "Small models matched a 70B on reasoning"),
            # the adjective is optional
            ("In a twist, the cheapest model won the benchmark outright",
             "The cheapest model won the benchmark outright"),
            # "where", not only the "of" the pattern already had
            ("In a world where AI writes code, the bottleneck moved to review",
             "The bottleneck moved to review"),
            # bare stance adverbs editorialise instead of claiming
            ("Surprisingly, a 1.5-hour model beats a 70B on this task",
             "A 1.5-hour model beats a 70B on this task"),
            ("Finally, someone measured what a GPU lock actually costs",
             "Someone measured what a GPU lock actually costs"),
        ],
    )
    def test_run_up_families_measured_on_stored_scripts(self, sentence, expected):
        assert strip_preamble(sentence) == expected

    @pytest.mark.parametrize(
        "sentence",
        [
            # A DATE is usually the most concrete thing in the hook. Eating it
            # would be a regression, so these are pinned as must-survive.
            "In 2026, JPMorgan's report confirmed what small models proved",
            "On June 19th, our autocomplete tap produced its first topic",
            "In December 2025, the first Wan 2.2 clip rendered on the 5090",
            # A real qualifier scopes the claim; it is not a run-up.
            "In production environments, the GPU lock is the real bottleneck",
            "In our development stack, Redis quietly became the bottleneck",
            "For indie developers, a judge model is the whole budget",
            # "In a <noun>," only goes when the noun is a framing device —
            # a concrete noun keeps its clause.
            "In a single afternoon, the RAG sweep dropped 87% of every chunk",
        ],
    )
    def test_a_real_opening_clause_survives(self, sentence):
        assert strip_preamble(sentence) == sentence

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
            ("llama.cpp and vLLM and SGLang each serve a different job in a modern local inference stack today", "too_many_words"),
            ("JPMorgan Chase highlights six pivotal shifts for banks and their clients", "too_long"),
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
        s = "JPMorgan Chase highlights six pivotal shifts"   # 43 chars
        assert "too_long" in hook_defects(s, max_chars=40)   # over, not runaway
        assert "too_long" not in hook_defects(s, max_chars=80)
        # Far enough past the budget and it stops being "long" and becomes
        # "not a hook" — a different defect with a different remedy.
        assert "runaway" in hook_defects(s, max_chars=20)


class TestLimits:
    def test_defaults_are_punchy_not_clipped(self):
        """42 was measured too tight — stripped hooks land at 33-82 chars
        (median 70), so it cut 9 of 10 mid-phrase. 70 keeps 6 of 10 whole."""
        assert hook_limits(None) == (HOOK_MAX_CHARS_DEFAULT, HOOK_MAX_WORDS_DEFAULT)
        assert HOOK_MAX_CHARS_DEFAULT == 70

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

    Measured 2026-09-22: asked for <=42 chars, phi4:14b returned 33-82 — but
    those sentences were good claims. Calling an LLM again to shorten them
    would spend a call to make them no better, so only content defects do.
    """

    def test_a_long_good_claim_has_no_content_defect(self):
        # 73 chars — over the 70-char budget and still a good, finished claim.
        s = "Our GPU lock bug was quietly wrecking the nightly RAG sweep for six weeks"
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


class TestRunaway:
    """A first sentence far past the budget is not a hook — shortening it
    leaves a stump, so it buys the corrective call instead. Measured
    2026-09-22: the two worst were 114 and 149 characters, both unfinished,
    while the longest real claim was 82 — the threshold sits between them.
    """

    RUNON = ("A small transformer model trained from scratch in just 1.5 hours challenges "
             "everything the field assumed about scale and data and compute budgets")

    def test_a_run_on_is_a_content_defect(self):
        assert "runaway" in hook_defects(self.RUNON)
        assert "runaway" in content_defects(self.RUNON)
        assert "too_long" not in hook_defects(self.RUNON), "runaway supersedes too_long"

    def test_a_merely_long_claim_is_not(self):
        s = "JPMorgan's 2026 report confirms the tech trends small models already proved"
        assert "too_long" in hook_defects(s)
        assert content_defects(s) == (), "a good long claim is shortened, not regenerated"

    def test_the_factor_is_configurable(self):
        from poindexter.services.short_hook import HOOK_RUNAWAY_FACTOR_DEFAULT, runaway_factor

        assert runaway_factor(None) == HOOK_RUNAWAY_FACTOR_DEFAULT
        assert runaway_factor(SiteConfig(initial_config={"media.short_hook.runaway_factor": "3"})) == 3.0
        assert runaway_factor(SiteConfig(initial_config={"media.short_hook.runaway_factor": "junk"})) == HOOK_RUNAWAY_FACTOR_DEFAULT
        # never below 1.0 — that would make every over-budget hook a runaway
        assert runaway_factor(SiteConfig(initial_config={"media.short_hook.runaway_factor": "0.2"})) == 1.0


class TestStripScaffold:
    """Wrapper the model added around the hook, not the hook.

    Measured 2026-09-22 over 489 stored short scripts: 144 first sentences
    open with a quote character, 7 with a code fence and 6 with a stage
    direction or a label. Every one became a YouTube title verbatim.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('"Imagine an AI assistant that never goes down',
             "Imagine an AI assistant that never goes down"),
            ('"Zero-click content is the new standard"',
             "Zero-click content is the new standard"),
            ("\u201cOur RAG sweep dropped 87% of every chunk\u201d",
             "Our RAG sweep dropped 87% of every chunk"),
            ("[ Hook ] Imagine a world where AI is not just a tool",
             "Imagine a world where AI is not just a tool"),
            ("[0:00] Small models matched a 70B on reasoning",
             "Small models matched a 70B on reasoning"),
            ("HOOK: The cheapest model won every benchmark",
             "The cheapest model won every benchmark"),
            # the colon sits INSIDE the emphasis in one spelling and outside
            # in the other, so both are accepted
            ("**Narration:** The GPU lock was the real bottleneck",
             "The GPU lock was the real bottleneck"),
            ("**VO**: The GPU lock was the real bottleneck",
             "The GPU lock was the real bottleneck"),
            ('```' + "text The RAG sweep dropped 87% of every chunk",
             "The RAG sweep dropped 87% of every chunk"),
        ],
    )
    def test_wrapper_comes_off(self, raw, expected):
        assert strip_scaffold(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            # An INTERNAL quote is part of the claim, not a wrapper.
            'He said "no" to the merge and shipped it anyway',
            'The model calls it a "twist" and means it',
            # A bracketed token the sentence is ABOUT is not a stage direction.
            "Shipping [skip-public-sync] keeps a commit private",
        ],
    )
    def test_the_claim_survives(self, raw):
        assert strip_scaffold(raw) == raw

    @pytest.mark.parametrize("raw", ["", "   ", '"', "[Intro music plays]"])
    def test_scaffolding_only_comes_back_empty(self, raw):
        """Empty is the `empty` defect, which regenerates — better than a
        title reading "[Intro music plays]"."""
        assert strip_scaffold(raw) == ""
        assert "empty" in hook_defects(strip_preamble(raw))

    def test_an_empty_slice_is_in_every_string(self):
        """Regression: `""[:1] in _QUOTE_CHARS` is True, so the empty case
        used to reach `clean[0]` and raise IndexError."""
        assert strip_scaffold("") == ""

    def test_strip_preamble_unwraps_first(self):
        """The run-up patterns anchor at ^, so a quote in front of them hides
        the run-up entirely unless the wrapper comes off first."""
        assert strip_preamble(
            '"In a surprising twist, the cheapest model won the benchmark"'
        ) == "The cheapest model won the benchmark"
