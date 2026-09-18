"""Anonymous appeals to authority (poindexter#1008).

"Independent benchmarks comparing embedding models from OpenAI, Voyage, and
Cohere have generally found the performance gap … to be smaller than the gap
between different chunking strategies" reached awaiting_approval at Q95. No
link, no named benchmark, no date — and the surrounding sections of that same
post were properly sourced, which is what made it stand out.

It was uncovered by construction: `qa.unlinked_attribution` resolves an
attribution SUBJECT against the corpus and an anonymous appeal has none;
`qa.citations` only dead-link-checks URLs that already exist; and a vague
aggregate claim about "benchmarks generally" is not groundable by
`qa.web_factcheck`.

The GOOD cases below are the false positives an earlier draft produced against
the real corpus. They are kept as regressions because each one is a different
way the pattern family over-reaches.
"""

from __future__ import annotations

import pytest

from poindexter.modules.content.content_validator import detect_anonymous_authority

pytestmark = pytest.mark.unit

_ISSUE_SENTENCE = (
    "Independent benchmarks comparing embedding models from OpenAI, Voyage, "
    "and Cohere have generally found the performance gap between these models "
    "to be smaller than the gap between different chunking strategies."
)


class TestFlagsAnonymousAppeals:
    def test_the_issue_sentence(self):
        issues = detect_anonymous_authority(_ISSUE_SENTENCE)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].category == "anonymous_authority"

    @pytest.mark.parametrize(
        "text",
        [
            "Studies consistently show that users abandon sites that take longer than three seconds to load.",
            "It's widely known that vector search degrades past a certain corpus size.",
            "Most engineers agree that premature abstraction costs more than duplication.",
            "Several analyses have concluded that the effect is smaller than reported.",
        ],
    )
    def test_hedged_authority_without_a_link(self, text):
        assert detect_anonymous_authority(text)


class TestExemptions:
    """Both exemptions were earned against the published corpus."""

    def test_a_link_in_the_same_sentence_is_a_citation(self):
        assert detect_anonymous_authority(
            "Independent benchmarks have found the gap is small "
            "([Zilliz](https://zilliz.com/x))."
        ) == []

    def test_a_link_in_the_NEXT_sentence_still_counts(self):
        """The writer citing a source and linking it one sentence later is
        sourcing, not appealing."""
        assert detect_anonymous_authority(
            "Studies show the gap is small. See [the writeup](https://example.com/a) "
            "for the numbers."
        ) == []

    def test_a_named_source_belongs_to_unlinked_attribution(self):
        """'the Knight Institute research shows …' names its source — that is
        qa.unlinked_attribution's job, and double-reporting it would punish the
        same sentence twice."""
        assert detect_anonymous_authority(
            "The Knight Institute research shows the overlooked catalog is "
            "often where the value sits."
        ) == []

    def test_sentence_initial_capital_is_not_a_named_source(self):
        """Regression on a bug in the first draft: treating the leading capital
        of 'Independent benchmarks…' as a proper noun silently exempted the
        exact sentence this rule exists for."""
        assert detect_anonymous_authority(_ISSUE_SENTENCE)


class TestOverReachRegressions:
    """Each is a real false positive from the first draft, measured against the
    207-post published corpus."""

    def test_independent_tests_meaning_parallel_execution(self):
        assert detect_anonymous_authority(
            "Modern CI/CD systems should be able to run independent tests "
            "simultaneously."
        ) == []

    def test_reports_as_a_grammatical_object(self):
        assert detect_anonymous_authority(
            "It's crucial to look for patterns across multiple reports, "
            "considering factors like niche, product type and time in business."
        ) == []

    def test_plain_prose_without_an_authority_claim(self):
        assert detect_anonymous_authority(
            "We measured the gap ourselves and wrote up what we saw."
        ) == []


def test_empty_and_none_are_safe():
    assert detect_anonymous_authority("") == []
    assert detect_anonymous_authority(None) == []
