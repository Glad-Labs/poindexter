"""Unit tests for ``services/chart_render.py``.

``build_chart_html`` is pure, so geometry, ticks, escaping and the palette are
all testable without launching chromium; only ``render_chart`` needs a browser
and that path is exercised through its documented fail-open behaviour.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from poindexter.services.chart_render import (
    ChartSpec,
    Series,
    build_chart_html,
    chart_alt_text,
    format_value,
    nice_ticks,
    render_chart,
    text_width,
)
from tests.unit._nonempty import nonempty


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
        for match in nonempty(re.findall(r'<text[^>]*fill="([^"]+)"', doc), "re.findall('<text[^>]*fill='([^']+)'', doc)"):

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


# --- Label clipping ----------------------------------------------------------

# Anchored, end-aligned category labels: x is the label's RIGHT edge, so the
# left edge is x - width and that is what has to stay on canvas.
_END_LABEL = re.compile(
    r'<text x="([0-9.]+)"[^>]*font-size="(\d+)"[^>]*text-anchor="end"[^>]*>'
    r"([^<]*)</text>"
)
_MID_LABEL = re.compile(
    r'<text x="([0-9.]+)"[^>]*font-size="(\d+)"[^>]*text-anchor="middle"[^>]*>'
    r"([^<]*)</text>"
)


def _left_edges(doc: str, pattern: re.Pattern) -> list[tuple[str, float]]:
    """(text, leftmost x) for every anchored label in ``doc``."""
    out = []
    for x, size, text in nonempty(
        pattern.findall(doc), "anchored <text> labels in the rendered SVG"
    ):
        w = text_width(text, float(size))
        out.append((text, float(x) - w if pattern is _END_LABEL else float(x) - w / 2))
    return out


class TestTextWidth:
    """The estimate must never under-read, or the gutter under-sizes."""

    def test_is_blind_to_neither_length_nor_which_characters(self):
        # The bug: sizing from len() alone. 'M' and 'i' are the same count.
        assert text_width("M" * 10, 14) > text_width("i" * 10, 14)

    def test_meets_the_monospace_floor_the_worker_image_falls_back_to(self):
        """Measured in `poindexter-worker` 2026-09-23: 8.40px/char at 14px.

        The image ships JetBrains Mono and Liberation only — none of the
        families `_FONT_STACK` names — so `sans-serif` resolves to a
        monospace face at a flat 0.60em. An estimate below that floor is what
        clipped `qwen3-vl:30b-a3b-instruct` in production.
        """
        for label in ("qwen3-vl:30b-a3b-instruct", "gemma-4-31B-it-qat:latest"):
            assert text_width(label, 14) >= 8.40 * len(label)

    def test_scales_linearly_with_font_size(self):
        assert text_width("abc", 28) == pytest.approx(2 * text_width("abc", 14))


class TestCategoryLabelsAreNeverClipped:
    """Regression: two published charts lost the first glyph of a model name.

    R2 `images/charts/bcc985ab.webp` + `34b0a7f7.webp`, 2026-09-23 — the gutter
    was sized from `len(label) * 7.9` and did not subtract the 12px the label
    is drawn back from the baseline, so a 210px label got a 201px budget and
    `qwen3-vl:30b-a3b-instruct` rendered as `wen3-vl:30b-a3b-instruct`.
    """

    # The real production categories, including the two that clipped.
    CATALOG = [
        "qwen3-vl:30b-a3b-instruct",
        "gemma-4-31B-it-qat:latest",
        "glm-4.7-5090:latest",
        "qwen3-vl:30b",
        "phi4:14b",
    ]

    def test_the_labels_that_clipped_in_production_now_fit(self):
        spec = _bar(
            categories=self.CATALOG,
            series=[Series("Raw decode", [float(i) for i in range(5)])],
        )
        for text, left in nonempty(
            _left_edges(build_chart_html(spec), _END_LABEL),
            "category labels in the rendered bar chart",
        ):
            assert left >= 0, f"{text!r} starts at x={left:.1f}"

    @pytest.mark.parametrize(
        "label",
        [
            # Longer than anything in the catalog, in each direction the
            # estimate could be wrong about.
            "qwen3-vl:30b-a3b-instruct-2511-extended-context:latest",
            "M" * 60,                      # widest glyphs in every font
            "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWW@@@@@@@@@@",
            "a" * 400,                     # past even the maximum gutter
            "模型名称-非常长的中文标签-测试用例",   # full-width, non-ASCII
        ],
    )
    def test_a_label_longer_than_the_catalog_still_starts_on_canvas(self, label):
        spec = _bar(
            categories=[label, "b"],
            series=[Series("Raw decode", [1.0, 2.0])],
        )
        doc = build_chart_html(spec)
        for text, left in nonempty(
            _left_edges(doc, _END_LABEL),
            "category labels in the rendered bar chart",
        ):
            assert left >= 0, f"{text!r} starts at x={left:.1f}"

    def test_a_label_too_long_for_the_gutter_is_ellipsized_not_clipped(self):
        """The gutter is capped so labels cannot eat the plot area.

        Past that cap the only honest options are an ellipsis or a glyph
        sliced off at the canvas edge; a sliced glyph reads as a DIFFERENT
        model name, so it truncates. The full name survives in the alt text.
        """
        label = "a" * 400
        spec = _bar(categories=[label, "b"], series=[Series("s", [1.0, 2.0])])
        doc = build_chart_html(spec)
        assert "\u2026" in doc
        assert label not in doc
        assert label in chart_alt_text(spec)   # nothing is actually lost

    def test_the_gutter_never_swallows_the_plot_area(self):
        spec = _bar(categories=["a" * 400, "b"], series=[Series("s", [1.0, 2.0])])
        doc = build_chart_html(spec)
        # Category labels (font-size 14) are drawn back from the bars'
        # baseline, so their x is the gutter's right edge; it must leave room.
        baseline = max(
            float(x) for x, size, _ in _END_LABEL.findall(doc) if size == "14"
        )
        assert baseline < spec.width * 0.5

    def test_line_chart_edge_labels_stay_on_canvas(self):
        """Same defect one function over: x labels are centered on the point,

        so the first and last hang half their run past the plot edge.
        """
        spec = ChartSpec(
            form="line",
            title="Trend",
            categories=["qwen3-vl:30b-a3b-instruct-extended:latest", "b", "c"],
            series=[Series("all", [1.0, 2.0, 3.0])],
        )
        doc = build_chart_html(spec)
        for text, left in nonempty(
            _left_edges(doc, _MID_LABEL),
            "x-axis labels in the rendered line chart",
        ):
            assert left >= 0, f"{text!r} starts at x={left:.1f}"


class TestFontStack:
    """A concrete family must lead, or the whole stack is decoration.

    `system-ui` is a CSS *generic*: it always resolves, so every family listed
    after it is unreachable. The worker image resolves it to JetBrains Mono
    (it ships JetBrains Mono + Liberation and none of the named desktop
    faces), which is why charts published monospace for months while the
    stack read as though it asked for a proportional face.
    """

    def test_a_concrete_family_precedes_the_system_ui_generic(self):
        doc = build_chart_html(_bar())
        stack = list(
            nonempty(
                re.findall(r"font-family:([^;}]+)", doc),
                "font-family in chart CSS",
            )
        )[0]
        assert "Liberation Sans" in stack, stack
        assert stack.index('"Liberation Sans"') < stack.index("system-ui"), (
            "Liberation Sans must precede system-ui. Behind the generic it is "
            "INERT — measured in poindexter-worker, the probe string stayed at "
            "210.00px (monospace) with it listed after system-ui, and dropped "
            f"to 161.09px in front. Got: {stack}"
        )

    def test_the_generic_is_still_there_as_a_fallback(self):
        """An install without Liberation must still get *a* font."""
        doc = build_chart_html(_bar())
        assert "system-ui" in doc and "sans-serif" in doc


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
