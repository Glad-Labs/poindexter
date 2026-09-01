"""Unit tests for ``services/chart_render.py``.

``build_chart_html`` is pure, so geometry, ticks, escaping and the palette are
all testable without launching chromium; only ``render_chart`` needs a browser
and that path is exercised through its documented fail-open behaviour.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from services.chart_render import (
    ChartSpec,
    Series,
    build_chart_html,
    chart_alt_text,
    format_value,
    nice_ticks,
    render_chart,
)


def _bar(**over) -> ChartSpec:
    base = dict(
        form="bar",
        title="Decode vs delivered",
        categories=["qwen2.5:7b", "phi4:14b"],
        series=[Series("Raw decode", [235.1, 124.7]), Series("Delivered", [55.6, 25.3])],
    )
    base.update(over)
    return ChartSpec(**base)  # type: ignore[arg-type]


class TestNiceTicks:
    @pytest.mark.parametrize("max_value", [235.1, 1.0, 7.0, 99.9, 100.0, 0.42, 1_250_000.0])
    def test_last_tick_always_covers_the_max(self, max_value):
        """Regression: a short tick list rescales marks past the last gridline.

        A 235 tok/s bar drew off the end of a 200 axis and lost its value
        label — the axis maximum IS the scale, so it must never sit below the
        largest value being drawn.
        """
        assert nice_ticks(max_value)[-1] >= max_value

    def test_steps_are_round_numbers(self):
        ticks = nice_ticks(235.1)
        assert ticks == [0.0, 50.0, 100.0, 150.0, 200.0, 250.0]

    def test_starts_at_zero_baseline(self):
        # A truncated bar axis misstates ratios, which is the whole point of a
        # measurement chart.
        assert nice_ticks(500.0)[0] == 0.0

    @pytest.mark.parametrize("bad", [0.0, -5.0, float("inf"), float("nan")])
    def test_degenerate_input_still_yields_an_axis(self, bad):
        ticks = nice_ticks(bad)
        assert len(ticks) >= 2 and ticks[0] == 0.0


class TestFormatValue:
    @pytest.mark.parametrize(
        "value,expected",
        [(1234.0, "1,234"), (235.1, "235"), (55.61, "55.6"), (1.5, "1.5"), (0.25, "0.25")],
    )
    def test_decimal_places_scale_with_magnitude(self, value, expected):
        assert format_value(value) == expected

    def test_suffix_is_appended(self):
        assert format_value(42.0, " tok/s") == "42 tok/s"


class TestValidation:
    def test_rejects_unknown_form(self):
        with pytest.raises(ValueError, match="unsupported chart form"):
            _bar(form="pie").validate()

    def test_rejects_empty_categories(self):
        with pytest.raises(ValueError, match="no categories"):
            _bar(categories=[]).validate()

    def test_rejects_empty_series(self):
        with pytest.raises(ValueError, match="no series"):
            _bar(series=[]).validate()

    def test_rejects_ragged_series(self):
        spec = _bar(series=[Series("a", [1.0])])  # 1 value, 2 categories
        with pytest.raises(ValueError, match="has 1 values but there are 2"):
            spec.validate()

    def test_rejects_more_series_than_palette_slots(self):
        """Cycling hues would put two indistinguishable colors on one chart."""
        spec = _bar(series=[Series(f"s{i}", [1.0, 2.0]) for i in range(5)])
        with pytest.raises(ValueError, match="exceeds the 4-slot"):
            spec.validate()


class TestBuildChartHtml:
    def test_is_self_contained(self):
        """No network at render time: nothing may be FETCHED.

        The SVG ``xmlns`` is a namespace identifier, not a resource, so this
        checks the attributes that actually cause a load.
        """
        doc = build_chart_html(_bar())
        assert not re.search(r"(src|href)\s*=", doc)
        assert "url(" not in doc
        assert "@import" not in doc
        assert "<script" not in doc.lower()
        # The only absolute URI present is the SVG namespace.
        assert re.findall(r"https?://[^\"\s]+", doc) == ["http://www.w3.org/2000/svg"]

    def test_every_value_is_labelled_on_a_bar_chart(self):
        doc = build_chart_html(_bar())
        for text in ("235", "125", "55.6", "25.3"):
            assert text in doc

    def test_uses_the_validated_palette_in_fixed_order(self):
        doc = build_chart_html(_bar())
        assert "#2a78d6" in doc  # slot 1
        assert "#eb6834" in doc  # slot 2

    def test_legend_present_for_two_series(self):
        assert "Raw decode" in build_chart_html(_bar())

    def test_no_legend_for_a_single_series(self):
        """A one-swatch legend restates the title and costs space."""
        spec = _bar(series=[Series("only", [1.0, 2.0])], title="Solo")
        doc = build_chart_html(spec)
        # The series label appears nowhere: no legend row was emitted.
        assert "only" not in doc

    def test_text_never_wears_the_series_color(self):
        """Values/labels use ink tokens; identity comes from the mark beside them."""
        doc = build_chart_html(_bar())
        for match in re.findall(r'<text[^>]*fill="([^"]+)"', doc):
            assert match in {"#0b0b0b", "#52514e", "#84837d"}, match

    def test_escapes_markup_in_user_supplied_text(self):
        spec = _bar(title='<script>alert("x")</script>', categories=["a<b", "c&d"])
        doc = build_chart_html(spec)
        assert "<script>alert" not in doc
        assert "&lt;script&gt;" in doc
        assert "a&lt;b" in doc

    def test_source_line_is_rendered_when_present(self):
        doc = build_chart_html(_bar(source="cost_logs — 9,110 calls"))
        assert "9,110 calls" in doc

    def test_line_form_renders(self):
        spec = ChartSpec(
            form="line",
            title="Trend",
            categories=["2026-08-26", "2026-08-27", "2026-08-28"],
            series=[Series("all", [72.0, 126.0, 156.0])],
        )
        doc = build_chart_html(spec)
        assert "<polyline" in doc
        assert "<circle" in doc  # end marker

    def test_single_category_line_does_not_divide_by_zero(self):
        spec = ChartSpec(
            form="line", title="One", categories=["a"], series=[Series("s", [5.0])],
        )
        assert "<polyline" in build_chart_html(spec)

    def test_all_zero_values_still_render(self):
        spec = _bar(series=[Series("z", [0.0, 0.0])])
        assert "<svg" in build_chart_html(spec)

    def test_long_category_labels_widen_the_gutter(self):
        """Labels must not be clipped by a fixed gutter."""
        short = build_chart_html(_bar(categories=["a", "b"]))
        long = build_chart_html(
            _bar(categories=["a-very-long-model-name-indeed:latest", "b"]),
        )
        assert "a-very-long-model-name-indeed:latest" in long
        # Wider gutter pushes the plot's left edge right.
        assert len(long) != len(short)


class TestChartAltText:
    def test_carries_every_data_point(self):
        """A PNG has no table view — alt text is where the numbers live."""
        alt = chart_alt_text(_bar())
        for token in ("qwen2.5:7b", "phi4:14b", "235", "55.6", "25.3"):
            assert token in alt

    def test_names_the_form_and_source(self):
        alt = chart_alt_text(_bar(source="cost_logs"))
        assert alt.startswith("Bar chart:")
        assert "Source: cost_logs." in alt


class TestRenderChart:
    async def test_missing_playwright_returns_none_not_raises(self):
        """Mirrors preview_screenshot: a missing browser is 'no image', not a crash."""
        with patch.dict("sys.modules", {"playwright.async_api": None}):
            assert await render_chart(_bar()) is None

    async def test_a_malformed_spec_raises_rather_than_silently_skipping(self):
        """Environment failures are None; programming errors are loud."""
        with pytest.raises(ValueError):
            await render_chart(_bar(categories=[]))
