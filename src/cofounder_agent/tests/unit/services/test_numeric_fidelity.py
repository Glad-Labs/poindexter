"""Unit tests for ``services/numeric_fidelity.py``.

The library is pure, so the extraction rules, the rounding contract, the
attribution gate and the bounded derivation search are all pinned here without
a DB, a browser or an LLM. Several cases are regressions from the 40-post
corpus run that shaped the design — they are labelled as such.
"""

from __future__ import annotations

import pytest

from services.numeric_fidelity import (
    DEFAULT_ATTRIBUTION_MARKERS,
    extract_claims,
    extract_corpus_numbers,
    is_attributed,
    strip_uncheckable,
    verify,
)


def _raws(text: str) -> list[str]:
    return [c.raw for c in extract_claims(text)]


class TestIdentifiersAreNotQuantities:
    def test_model_names_containing_digits_are_excluded(self):
        """A digit run fused to a letter is a name, not a measurement."""
        assert _raws("qwen2.5:7b and phi4:14b were compared") == []

    def test_a_real_quantity_beside_a_model_name_still_extracts(self):
        assert _raws("qwen2.5:7b hit 235 tok/s") == ["235 tok/s"]

    def test_hardware_names_are_excluded(self):
        assert _raws("the RTX 5090 and Wan2.2 pipeline") == []


class TestUncheckableRegions:
    @pytest.mark.parametrize(
        "text",
        [
            "```\nrate = 99 tokens\n```",
            "`--budget 42 tokens`",
            "see [docs](https://example.com/v2/99-tokens)",
            "https://example.com/1234-words",
            '<img src="https://cdn/1234.png" width="800">',
        ],
    )
    def test_code_links_and_urls_contribute_no_claims(self, text):
        assert extract_claims(text) == []

    def test_stripping_preserves_offsets(self):
        """Regions are blanked, not deleted, so context windows stay aligned."""
        src = "before `9 tokens` after"
        assert len(strip_uncheckable(src)) == len(src)


class TestDatesAreNotMeasurements:
    @pytest.mark.parametrize(
        "text",
        [
            "the 2024 edition landed",        # regression: bare year in prose
            "published in 1569",
            "this matters more in 2026 than 2020",
            "on 2026-08-26 the capture deployed",
        ],
    )
    def test_years_and_dates_are_excluded(self, text):
        assert extract_claims(text) == []

    def test_a_comma_grouped_figure_is_not_mistaken_for_a_year(self):
        assert _raws("responses from 23,262 developers") == ["23,262"]


class TestMagnitudeSuffixes:
    def test_currency_magnitude_is_consumed_with_the_number(self):
        """Regression: "$10K" parsed as "$10" is a different claim, and it
        read as an unsupported fabrication in the first corpus run."""
        claims = extract_claims("targeting $10K MRR by Q3")
        assert [(c.raw, c.value) for c in claims] == [("$10K", 10000.0)]

    def test_millions_scale_correctly(self):
        assert extract_claims("a $1.5M round")[0].value == 1_500_000.0


class TestAttributionGate:
    def test_marker_makes_a_claim_scorable(self):
        claims = extract_claims("According to the survey, 53.7% preferred it")
        assert claims[0].attributed is True

    def test_plain_authorial_framing_is_not_scored(self):
        claims = extract_claims("We shipped 500 posts last year")
        assert claims[0].attributed is False

    def test_a_link_in_the_sentence_counts_as_attribution(self):
        claims = extract_claims("The [report](https://x.test) puts it at 42%")
        assert claims[0].attributed is True

    def test_attribution_is_per_sentence_not_per_document(self):
        text = "According to the study, 90% agreed. Separately we shipped 12 posts."
        claims = extract_claims(text)
        by_raw = {c.raw: c.attributed for c in claims}
        assert by_raw["90%"] is True
        assert by_raw["12 posts"] is False

    def test_is_attributed_is_case_insensitive(self):
        assert is_attributed("ACCORDING TO x", DEFAULT_ATTRIBUTION_MARKERS)


class TestRoundingContract:
    def test_source_is_rounded_to_the_written_precision(self):
        """Rounding a source figure for prose is honest writing."""
        res = verify("The study measured 235 tok/s", ["decode 235.1 tok/s"])
        assert res.unsupported == []

    def test_precision_beyond_the_source_is_not_invented_support(self):
        res = verify("The study measured 235.14 tok/s", ["decode 235.1 tok/s"])
        assert len(res.unsupported) == 1

    def test_a_percentage_matches_a_source_fraction(self):
        res = verify("the survey found 80%", ["ratio 0.8 of runs"])
        assert res.verdicts[0].status == "exact"


class TestDerivation:
    def test_a_percentage_derived_from_two_source_numbers_is_supported(self):
        res = verify("the benchmark shows an 80% tax", ["124.7 and 25.3"])
        assert res.verdicts[0].status == "derived"
        # The relation is RECORDED so an operator can judge a coincidence.
        assert "/" in res.verdicts[0].explanation

    def test_derivation_can_be_disabled(self):
        res = verify(
            "the benchmark shows an 80% tax", ["124.7 and 25.3"],
            allow_derived=False,
        )
        assert res.verdicts[0].status == "unsupported"

    def test_derivation_never_applies_to_plain_quantities(self):
        """Only percentages and multipliers may be derived — a raw quantity
        must be stated, or any number would match some pair."""
        res = verify("the report lists 3 posts", ["12 and 4 things"])
        assert res.verdicts[0].status == "unsupported"


class TestVerifyScoring:
    def test_only_attributed_claims_can_fail(self):
        res = verify("We handled 999 requests", ["nothing numeric here 1 2 3"])
        assert res.extracted == 1
        assert res.checkable == 0
        assert res.unsupported == []

    def test_an_unsourced_attributed_figure_is_flagged(self):
        """The real catch from the corpus run: a headline statistic that
        appears nowhere in the research it was written from."""
        res = verify(
            "The report covers 110,000 papers",
            ["the index holds 47 entries and 98 datasets"],
        )
        assert [v.claim.raw for v in res.unsupported] == ["110,000"]

    def test_strict_mode_scores_every_number(self):
        """For a post built on a fact set we generated, the corpus IS the
        complete ground truth, so unattributed numbers count too."""
        res = verify("We measured 999 tok/s", ["1 2 3"], score_unattributed=True)
        assert res.checkable == 1
        assert len(res.unsupported) == 1

    def test_empty_inputs_are_safe(self):
        res = verify("", [])
        assert res.verdicts == [] and res.corpus_numbers == 0


class TestCorpusExtraction:
    def test_corpus_extraction_is_deliberately_loose(self):
        """A false negative here would flag a TRUE claim as fabricated."""
        nums = extract_corpus_numbers("about 42 things, 1,000 more, and 0.5 again")
        assert {42.0, 1000.0, 0.5} <= set(nums)

    def test_corpus_numbers_are_deduplicated_and_capped(self):
        nums = extract_corpus_numbers(" ".join(str(i) for i in range(50)), max_numbers=10)
        assert len(nums) == 10

    def test_zero_in_the_corpus_never_divides(self):
        """Guards the derivation search against a divide-by-zero."""
        res = verify("the study reports 50%", ["0 and 0 and 0"])
        assert res.verdicts[0].status == "unsupported"
