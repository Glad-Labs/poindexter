"""Writer-authored data charts: drawn only from figures the research states.

The chart catalog held one chart on 2026-10-05, so every post that wanted a
chart got the same one (ten posts in thirty days). ``[DATA-CHART:]`` lets the
writer plot the post's own figures, but a chart reads as measured fact, so
every value must be found in the research BESIDE ITS OWN LABEL or nothing is
drawn.
"""

from __future__ import annotations

import pytest

from poindexter.services import data_chart as dc
from poindexter.services.site_config import SiteConfig

RESEARCH = (
    "NVIDIA's RTX 5090 ships with 32GB of GDDR7 memory, a step up from the "
    "previous generation. "
    + "Filler sentence about cooling and power delivery. " * 12
    + "The RTX 4090 carries 24GB of GDDR6X. The older RTX 3090 also has 24GB, "
    "and the budget RTX 3060 is sold with 12GB."
)


def _sc(**extra):
    return SiteConfig(initial_config=extra)


class TestParse:
    def test_a_well_formed_marker_parses(self):
        d = dc.parse("bar | VRAM by card | GB | RTX 5090 = 32; RTX 4090 = 24")
        assert d is not None
        assert (d.form, d.title, d.unit) == ("bar", "VRAM by card", "GB")
        assert [(p.label, p.token) for p in d.points] == [("RTX 5090", "32"), ("RTX 4090", "24")]

    @pytest.mark.parametrize("payload", [
        "pie | T | GB | a = 1; b = 2",            # unknown form
        "bar |  | GB | a = 1; b = 2",             # no title
        "bar | T | GB | a = 1",                   # one point
        "bar | T | GB | a = 32GB; b = 2",         # value is not a plain number
        "bar | T | GB | a = 1; A = 2",            # duplicate label
        "bar | T | a = 1; b = 2",                 # missing a field
        "bar | T | GB | a 1; b = 2",              # no '='
    ])
    def test_malformed_markers_draw_nothing(self, payload):
        assert dc.parse(payload) is None

    def test_too_many_points_draw_nothing(self):
        body = "; ".join(f"c{i} = {i}" for i in range(11))
        assert dc.parse(f"bar | T | u | {body}", max_points=10) is None
        assert dc.parse(f"bar | T | u | {body}", max_points=11) is not None

    def test_thousands_separators_are_values(self):
        d = dc.parse("bar | Price | USD | A = 1,399; B = 1,800")
        assert d is not None and [p.value for p in d.points] == [1399.0, 1800.0]


class TestVerify:
    def test_every_value_beside_its_label_passes(self):
        d = dc.parse("bar | VRAM | GB | RTX 5090 = 32; RTX 4090 = 24; RTX 3060 = 12")
        assert dc.verify(d, RESEARCH).ok

    def test_a_value_stated_only_for_ANOTHER_label_fails(self):
        # 32 is in the research — but it belongs to the 5090, ~700 chars away.
        d = dc.parse("bar | VRAM | GB | RTX 5090 = 32; RTX 4090 = 32")
        v = dc.verify(d, RESEARCH, window_chars=240)
        assert not v.ok
        assert any("RTX 4090 = 32" in r for r in v.reasons)

    def test_swapped_figures_in_one_dense_sentence_fail(self):
        # Real CNBC phrasing (2026-10-05): both numbers sit within a few words
        # of both labels, so only nearest-number binding tells them apart.
        research = (
            "It spiked on 24 and 25 July, with 16,000 requests from more than "
            "4,000 users. OpenAI then found related activity across more than "
            "15,000 users."
        )
        honest = dc.parse("bar | Scale | count | requests = 16,000; users = 4,000")
        swapped = dc.parse("bar | Scale | count | requests = 4,000; users = 16,000")
        assert dc.verify(honest, research).ok
        assert not dc.verify(swapped, research).ok

    def test_digits_inside_a_label_are_not_its_value(self):
        # "RTX 4090" must not vouch for a value of 4090.
        d = dc.parse("bar | VRAM | GB | RTX 4090 = 4090; RTX 3060 = 12")
        assert not dc.verify(d, RESEARCH).ok

    def test_a_number_nowhere_in_the_research_fails(self):
        d = dc.parse("bar | VRAM | GB | RTX 5090 = 32; RTX 4090 = 20")
        assert not dc.verify(d, RESEARCH).ok

    def test_a_label_not_in_the_research_fails(self):
        d = dc.parse("bar | VRAM | GB | RTX 5090 = 32; RTX 5080 = 16")
        v = dc.verify(d, RESEARCH)
        assert not v.ok and any("RTX 5080" in r for r in v.reasons)

    def test_rounding_follows_the_writers_precision(self):
        research = "Model A decodes at 235.1 tok/s while Model B manages 104.6 tok/s."
        assert dc.verify(dc.parse("bar | Speed | tok/s | Model A = 235; Model B = 105"), research).ok
        assert not dc.verify(dc.parse("bar | Speed | tok/s | Model A = 235.4; Model B = 104.6"), research).ok

    def test_no_research_means_no_chart(self):
        d = dc.parse("bar | VRAM | GB | RTX 5090 = 32; RTX 4090 = 24")
        assert not dc.verify(d, "").ok

    def test_label_match_ignores_case_and_spacing(self):
        d = dc.parse("bar | VRAM | GB | rtx  5090 = 32; RTX 4090 = 24")
        assert dc.verify(d, RESEARCH).ok


class TestBuildVerifiedSpec:
    def test_a_verified_chart_becomes_a_renderable_spec(self):
        spec, reasons = dc.build_verified_spec(
            "bar | VRAM by card | GB | RTX 5090 = 32; RTX 4090 = 24", RESEARCH,
        )
        assert reasons == []
        assert spec is not None
        assert spec.categories == ["RTX 5090", "RTX 4090"]
        assert spec.series[0].values == [32.0, 24.0]
        assert spec.source  # a published chart always says where it came from

    def test_an_unverified_chart_returns_the_reasons(self):
        spec, reasons = dc.build_verified_spec("bar | VRAM | GB | RTX 4090 = 48; RTX 3090 = 24", RESEARCH)
        assert spec is None and reasons

    def test_max_points_and_window_come_from_settings(self):
        sc = _sc(data_chart_max_points="2")
        spec, reasons = dc.build_verified_spec(
            "bar | VRAM | GB | RTX 5090 = 32; RTX 4090 = 24; RTX 3060 = 12", RESEARCH, site_config=sc,
        )
        assert spec is None and "2-2 points" in reasons[0]


class TestEnabled:
    def test_on_by_default(self):
        assert dc.enabled(_sc())
        assert "[DATA-CHART:" in dc.prompt_block(_sc())

    def test_global_switch(self):
        sc = _sc(writer_data_charts_enabled="false")
        assert not dc.enabled(sc)
        assert dc.prompt_block(sc) == ""

    def test_the_niche_overrides_the_global_switch(self):
        sc = _sc(**{"writer_data_charts_enabled": "true", "niche.dev_diary.writer_data_charts_enabled": "false"})
        assert not dc.enabled(sc, "dev_diary")
        assert dc.enabled(sc, "glad-labs")

    def test_the_prompt_states_the_configured_point_limit(self):
        assert "2 to 6 points" in dc.prompt_block(_sc(data_chart_max_points="6"))
