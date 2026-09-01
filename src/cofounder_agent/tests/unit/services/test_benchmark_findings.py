"""Unit tests for ``services/benchmark_findings.py``.

The significance bars are the whole product here — a claim published on thin
evidence is worse than no post — so each bar is pinned independently, and the
rendered fact block is asserted to carry the numbers a downstream
``qa.numeric_fidelity`` pass will need to reconcile the draft against.
"""

from __future__ import annotations

import pytest

from services.benchmark_findings import (
    KIND_FLEET,
    KIND_NEW_MODEL,
    ModelMeasurement,
    build_findings,
    render_fleet_fact_block,
    render_new_model_fact_block,
)


def _m(model: str, decode: float, wall: float, calls: int = 100) -> ModelMeasurement:
    return ModelMeasurement(
        model=model, calls=calls, decode_tps=decode, wall_tps=wall,
        overhead_ms=1000.0, first_seen="2026-08-26", last_seen="2026-09-01",
    )


# Real prod shape (2026-09-01): a 77-point spread across six models.
_FLEET = [
    _m("phi4:14b", 124.7, 25.3, 243),
    _m("qwen2.5:7b", 235.1, 64.2, 90),
    _m("qwen3-vl:30b", 162.8, 84.8, 940),
    _m("glm-4.7-5090:latest", 177.0, 172.2, 34),
]


class TestTaxCalculation:
    def test_tax_is_the_share_never_delivered(self):
        assert _m("x", 100.0, 25.0).tax_pct == 75.0

    def test_a_warm_model_has_near_zero_tax(self):
        assert _m("x", 177.0, 172.2).tax_pct == pytest.approx(2.7, abs=0.1)

    def test_zero_decode_never_divides(self):
        assert _m("x", 0.0, 0.0).tax_pct == 0.0


class TestSignificanceBars:
    def test_a_real_fleet_clears_every_bar(self):
        out = build_findings(_FLEET, window_days=30, min_models=3, min_spread_pct=25)
        assert [f.kind for f in out] == [KIND_FLEET]

    def test_too_few_models_suppresses_the_fleet_finding(self):
        out = build_findings(_FLEET, window_days=30, min_models=99, min_spread_pct=25)
        assert out == []

    def test_a_flat_fleet_is_not_a_post(self):
        """'Everything behaves the same' is true, dull, and not a finding."""
        flat = [_m("a", 100.0, 90.0), _m("b", 120.0, 108.0), _m("c", 80.0, 72.0)]
        out = build_findings(flat, window_days=30, min_models=3, min_spread_pct=25)
        assert out == []

    def test_no_measurements_proposes_nothing(self):
        assert build_findings([], window_days=30, min_models=3, min_spread_pct=25) == []


class TestNewModelFindings:
    def test_only_named_new_models_produce_a_finding(self):
        out = build_findings(
            _FLEET, window_days=30, min_models=3, min_spread_pct=25,
            new_model_names={"qwen2.5:7b"},
        )
        new = [f for f in out if f.kind == KIND_NEW_MODEL]
        assert [f.subject for f in new] == ["qwen2.5:7b"]

    def test_an_empty_new_set_yields_no_new_model_findings(self):
        out = build_findings(
            _FLEET, window_days=30, min_models=3, min_spread_pct=25,
            new_model_names=set(),
        )
        assert all(f.kind != KIND_NEW_MODEL for f in out)

    def test_new_model_findings_survive_a_suppressed_fleet_finding(self):
        """The two kinds are independent — a flat fleet still permits a
        first-numbers-for-this-model post."""
        out = build_findings(
            _FLEET, window_days=30, min_models=99, min_spread_pct=25,
            new_model_names={"qwen2.5:7b"},
        )
        assert [f.kind for f in out] == [KIND_NEW_MODEL]


class TestFactBlockIsVerifiable:
    def test_every_measured_number_appears_in_the_block(self):
        """The block becomes research_context; qa.numeric_fidelity will require
        the draft's attributed figures to reconcile against exactly these."""
        block = render_fleet_fact_block(_FLEET, window_days=30)
        for m in _FLEET:
            assert m.model in block
            assert f"{m.decode_tps:g}" in block
            assert f"{m.wall_tps:g}" in block
            assert str(m.calls) in block

    def test_block_states_its_provenance_and_sample_size(self):
        block = render_fleet_fact_block(_FLEET, window_days=30)
        assert "cost_logs" in block
        assert "1307" in block  # 243 + 90 + 940 + 34
        assert "30 days" in block

    def test_block_warns_against_the_wrong_causal_claim(self):
        """The finding is residency, not model speed. A writer told otherwise
        would publish a checkable falsehood."""
        block = render_fleet_fact_block(_FLEET, window_days=30)
        assert "CAUSAL NOTE" in block
        assert "residency" in block.lower()

    def test_new_model_block_carries_its_own_numbers(self):
        block = render_new_model_fact_block(_FLEET[0], window_days=30)
        assert "phi4:14b" in block and "124.7" in block and "25.3" in block
        assert "CAUSAL NOTE" in block

    def test_the_spread_sentence_names_both_extremes(self):
        block = render_fleet_fact_block(_FLEET, window_days=30)
        assert "phi4:14b" in block and "glm-4.7-5090:latest" in block
        assert "spread is the finding" in block
