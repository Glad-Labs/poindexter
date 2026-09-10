"""Deterministic chart rendering — structured data in, PNG bytes out.

Why this exists: ``blog-generation/SKILL.md`` tells the writer never to ask for
a chart or diagram, because a diffusion model renders axis labels as garbled
glyphs. That instruction is correct and it closed off the single most useful
illustration a measurement post can carry — the measurements. This module is
the honest alternative: a chart is *drawn* from the numbers, never imagined
from a prompt, so every label, tick and bar length is a fact.

**The rendering path is chromium, not a plotting library.** ``playwright`` +
its bundled chromium are already a production dependency (the vision QA rail
and the ``screenshot`` image provider both drive it), so an SVG-in-HTML page
screenshotted at ``device_scale_factor=2`` costs no new wheel, no new system
package, and gives real text layout — font metrics and all — which is exactly
what a plotting library's headless backends are worst at.

``build_chart_html`` is deliberately **pure**: spec in, self-contained HTML
string out, no I/O. Geometry, ticks, label placement and palette are therefore
unit-testable without launching a browser; only ``render_chart`` needs one.

Design constraints this file encodes (they are not stylistic preferences):

- **The palette is validated, not chosen by eye.** Series colors are slots 1-2
  of a categorical palette verified for colorblind separation (worst-pair CVD
  ΔE 24.7, normal-vision ΔE 33.6, both far above the >=8 / >=15 floors). Do not
  swap a hue without re-running that check — the operator reading these charts
  is colorblind, so this is a correctness property, not decoration.
- **Text never wears the series color.** Marks carry identity; labels, values
  and axis text use ink tokens. A colored swatch sits *beside* text instead.
- **One value axis, always from a zero baseline** for bars — a truncated bar
  axis misstates ratios, which is the whole point of a measurement chart.
- **Provenance is a field, not a caption someone might forget.** ``source``
  renders as a footer line so a published chart always says what produced it
  and over what sample.

A PNG has no hover layer, so the interaction tier of the house data-viz method
does not apply; its role is taken by ``chart_alt_text``, which serializes the
full series/category matrix into the alt attribute so a screen reader (and any
LLM re-reading the post) gets the numbers, not just "a chart".
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from services.logger_config import get_logger

logger = get_logger(__name__)

# --- Palette -----------------------------------------------------------------
# Categorical slots 1-2 of the validated default palette, light mode. Verified
# with the data-viz validator on the #fcfcfb surface: lightness band PASS,
# chroma floor PASS, CVD separation dE 24.7 PASS, normal-vision dE 33.6 PASS,
# contrast >= 3:1 PASS. Slots 3-4 are the documented next hues in the fixed
# order (aqua, yellow) — assigned in order, never cycled.
_SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
_MAX_SERIES = len(_SERIES_COLORS)

_SURFACE = "#fcfcfb"
_INK_PRIMARY = "#0b0b0b"
_INK_SECONDARY = "#52514e"
_INK_MUTED = "#84837d"
_GRID = "#e6e5e1"

# The worker container is slim; DejaVu is what Debian-family images actually
# ship, so it is named explicitly rather than trusting `sans-serif` to resolve
# to something with digits of consistent width.
_FONT_STACK = (
    'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", '
    '"DejaVu Sans", Arial, sans-serif'
)

# --- Mark specs (house data-viz method) --------------------------------------
_BAR_MAX_THICKNESS = 24      # never fill the band; the leftover is air
_BAR_END_RADIUS = 4          # rounded data-end, square at the baseline
_SURFACE_GAP = 2             # separates touching marks, in the surface color
_LINE_WIDTH = 2
_MARKER_RADIUS = 4           # >= 8px diameter
_MARKER_RING = 2


@dataclass
class Series:
    """One named set of values, aligned to ``ChartSpec.categories``."""

    label: str
    values: list[float]


@dataclass
class ChartSpec:
    """Everything needed to draw one chart. No I/O, no defaults that guess."""

    form: Literal["bar", "line"]
    title: str
    categories: list[str]
    series: list[Series]
    subtitle: str = ""
    value_label: str = ""
    value_suffix: str = ""
    source: str = ""
    width: int = 1200
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Fail loud on a malformed spec rather than drawing something wrong."""
        if self.form not in ("bar", "line"):
            raise ValueError(f"unsupported chart form {self.form!r} (bar|line)")
        if not self.categories:
            raise ValueError("chart spec has no categories")
        if not self.series:
            raise ValueError("chart spec has no series")
        if len(self.series) > _MAX_SERIES:
            # Cycling hues would put two indistinguishable colors on one chart.
            # The house rule is to fold to "Other" or facet — which is a
            # decision for the caller, not something to paper over here.
            raise ValueError(
                f"{len(self.series)} series exceeds the {_MAX_SERIES}-slot "
                "categorical palette; fold to 'Other' or split the chart"
            )
        for s in self.series:
            if len(s.values) != len(self.categories):
                raise ValueError(
                    f"series {s.label!r} has {len(s.values)} values but there "
                    f"are {len(self.categories)} categories"
                )


# --- Scale helpers -----------------------------------------------------------


def nice_ticks(max_value: float, target: int = 5) -> list[float]:
    """Round tick values from 0 to >= ``max_value``.

    Steps snap to 1/2/2.5/5 x 10^k so ticks read as clean numbers. Returns at
    least ``[0, step]`` so a chart of all-zero data still has an axis.
    """
    if max_value <= 0 or not math.isfinite(max_value):
        return [0.0, 1.0]
    raw = max_value / max(target, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = 10 * magnitude
    for mult in (1, 2, 2.5, 5, 10):
        candidate = mult * magnitude
        if raw <= candidate:
            step = candidate
            break
    # The last tick MUST be >= max_value: it is the value the axis scales to,
    # so a short tick list silently rescales every mark past the last gridline
    # and pushes its label off-canvas (a 235 tok/s bar drew past a 200 axis and
    # lost its label — caught by rendering the chart and looking at it).
    ticks: list[float] = [0.0]
    v = 0.0
    while v < max_value - step * 1e-9:
        v += step
        ticks.append(round(v, 10))
    return ticks


def format_value(value: float, suffix: str = "") -> str:
    """Human tick/label text: thousands-separated, minimal decimals."""
    if value != value:  # NaN
        return "—"
    if abs(value) >= 1000:
        text = f"{value:,.0f}"
    elif abs(value) >= 100:
        text = f"{value:.0f}"
    elif abs(value) >= 10:
        text = f"{value:.1f}".rstrip("0").rstrip(".")
    else:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


# --- Alt text ----------------------------------------------------------------


def chart_alt_text(spec: ChartSpec) -> str:
    """Serialize the whole data matrix into alt text.

    A PNG cannot carry the house method's table view or hover layer, so the alt
    attribute does that job: it names the form, the title, and every
    category/series value. Screen readers get the numbers, and so does any
    later LLM pass re-reading the published post.
    """
    kind = "Bar chart" if spec.form == "bar" else "Line chart"
    parts = [f"{kind}: {spec.title}."]
    if spec.subtitle:
        parts.append(f"{spec.subtitle}.")
    for cat_idx, cat in enumerate(spec.categories):
        readings = ", ".join(
            f"{s.label} {format_value(s.values[cat_idx], spec.value_suffix)}"
            for s in spec.series
        )
        parts.append(f"{cat}: {readings}.")
    if spec.source:
        parts.append(f"Source: {spec.source}.")
    return " ".join(parts)


# --- SVG builders ------------------------------------------------------------


def _legend_svg(spec: ChartSpec, x: float, y: float) -> str:
    """Swatch + label row. Present for >= 2 series, omitted for one.

    A single-series legend restates the title and costs vertical space; the
    title already names what is plotted.
    """
    if len(spec.series) < 2:
        return ""
    out: list[str] = []
    cursor = x
    for idx, s in enumerate(spec.series):
        color = _SERIES_COLORS[idx]
        out.append(
            f'<rect x="{cursor:.1f}" y="{y - 9:.1f}" width="12" height="12" '
            f'rx="3" fill="{color}"/>'
        )
        out.append(
            f'<text x="{cursor + 18:.1f}" y="{y:.1f}" font-size="14" '
            f'fill="{_INK_SECONDARY}">{_esc(s.label)}</text>'
        )
        # Advance past the swatch, the gap, and an estimate of the text run.
        cursor += 18 + len(s.label) * 7.6 + 26
    return "".join(out)


def _bar_svg(spec: ChartSpec) -> tuple[str, int]:
    """Grouped horizontal bars. Returns (svg, height)."""
    n_series = len(spec.series)
    n_cats = len(spec.categories)

    # Left gutter scales with the longest category label so nothing is clipped.
    longest = max((len(c) for c in spec.categories), default=0)
    gutter = min(320, max(90, int(longest * 7.9) + 16))

    pad_l, pad_r, pad_t = gutter, 84, 16
    legend_h = 30 if n_series >= 2 else 0
    band = max(
        n_series * (_BAR_MAX_THICKNESS + _SURFACE_GAP) + 22,
        n_series * 14 + 26,
    )
    plot_h = band * n_cats
    height = int(pad_t + legend_h + plot_h + 52)
    plot_w = spec.width - pad_l - pad_r
    plot_top = pad_t + legend_h

    max_v = max((max(s.values) for s in spec.series), default=0.0)
    ticks = nice_ticks(max_v)
    scale_max = ticks[-1] or 1.0

    def vx(value: float) -> float:
        return pad_l + (max(value, 0.0) / scale_max) * plot_w

    out: list[str] = [
        f'<rect width="{spec.width}" height="{height}" fill="{_SURFACE}"/>',
    ]

    # Gridlines + value-axis ticks. Hairline, solid, recessive; drawn first so
    # every mark sits on top of them.
    for t in ticks:
        gx = vx(t)
        out.append(
            f'<line x1="{gx:.1f}" y1="{plot_top:.1f}" x2="{gx:.1f}" '
            f'y2="{plot_top + plot_h:.1f}" stroke="{_GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{gx:.1f}" y="{plot_top + plot_h + 22:.1f}" '
            f'font-size="13" fill="{_INK_MUTED}" text-anchor="middle">'
            f'{_esc(format_value(t))}</text>'
        )

    thickness = min(
        _BAR_MAX_THICKNESS,
        max(6, (band - 22 - (n_series - 1) * _SURFACE_GAP) / n_series),
    )

    for c_idx, cat in enumerate(spec.categories):
        band_top = plot_top + c_idx * band
        group_h = n_series * thickness + (n_series - 1) * _SURFACE_GAP
        y0 = band_top + (band - group_h) / 2

        out.append(
            f'<text x="{pad_l - 12:.1f}" y="{band_top + band / 2 + 5:.1f}" '
            f'font-size="14" fill="{_INK_PRIMARY}" text-anchor="end">'
            f'{_esc(cat)}</text>'
        )

        for s_idx, s in enumerate(spec.series):
            value = s.values[c_idx]
            color = _SERIES_COLORS[s_idx]
            y = y0 + s_idx * (thickness + _SURFACE_GAP)
            bar_w = max(vx(value) - pad_l, 0.0)
            # Square at the baseline, rounded at the data end.
            if bar_w > _BAR_END_RADIUS:
                out.append(
                    f'<path d="M{pad_l:.1f},{y:.1f} '
                    f'H{pad_l + bar_w - _BAR_END_RADIUS:.1f} '
                    f'a{_BAR_END_RADIUS},{_BAR_END_RADIUS} 0 0 1 '
                    f'{_BAR_END_RADIUS},{_BAR_END_RADIUS} '
                    f'V{y + thickness - _BAR_END_RADIUS:.1f} '
                    f'a{_BAR_END_RADIUS},{_BAR_END_RADIUS} 0 0 1 '
                    f'-{_BAR_END_RADIUS},{_BAR_END_RADIUS} '
                    f'H{pad_l:.1f} Z" fill="{color}"/>'
                )
            elif bar_w > 0:
                out.append(
                    f'<rect x="{pad_l:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                    f'height="{thickness:.1f}" fill="{color}"/>'
                )
            # Value at the tip, in ink — never in the series color.
            out.append(
                f'<text x="{pad_l + bar_w + 8:.1f}" '
                f'y="{y + thickness / 2 + 4.5:.1f}" font-size="13" '
                f'fill="{_INK_SECONDARY}">'
                f'{_esc(format_value(value, spec.value_suffix))}</text>'
            )

    # Baseline drawn last so it reads as the anchor the bars grow from.
    out.append(
        f'<line x1="{pad_l:.1f}" y1="{plot_top:.1f}" x2="{pad_l:.1f}" '
        f'y2="{plot_top + plot_h:.1f}" stroke="{_INK_MUTED}" stroke-width="1"/>'
    )
    out.append(_legend_svg(spec, pad_l, pad_t + 14))
    return "".join(out), height


def _line_svg(spec: ChartSpec) -> tuple[str, int]:
    """Multi-series line chart over ordered categories. Returns (svg, height)."""
    n_cats = len(spec.categories)
    pad_l, pad_r, pad_t, pad_b = 76, 132, 16, 54
    legend_h = 30 if len(spec.series) >= 2 else 0
    height = 460
    plot_top = pad_t + legend_h
    plot_h = height - plot_top - pad_b
    plot_w = spec.width - pad_l - pad_r

    max_v = max((max(s.values) for s in spec.series), default=0.0)
    ticks = nice_ticks(max_v)
    scale_max = ticks[-1] or 1.0

    def px(idx: int) -> float:
        if n_cats == 1:
            return pad_l + plot_w / 2
        return pad_l + (idx / (n_cats - 1)) * plot_w

    def py(value: float) -> float:
        return plot_top + plot_h - (max(value, 0.0) / scale_max) * plot_h

    out: list[str] = [
        f'<rect width="{spec.width}" height="{height}" fill="{_SURFACE}"/>',
    ]
    for t in ticks:
        gy = py(t)
        out.append(
            f'<line x1="{pad_l:.1f}" y1="{gy:.1f}" x2="{pad_l + plot_w:.1f}" '
            f'y2="{gy:.1f}" stroke="{_GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{pad_l - 12:.1f}" y="{gy + 4.5:.1f}" font-size="13" '
            f'fill="{_INK_MUTED}" text-anchor="end">'
            f'{_esc(format_value(t))}</text>'
        )

    # X labels thinned to ~8 so they never collide.
    stride = max(1, math.ceil(n_cats / 8))
    for idx, cat in enumerate(spec.categories):
        if idx % stride and idx != n_cats - 1:
            continue
        out.append(
            f'<text x="{px(idx):.1f}" y="{plot_top + plot_h + 24:.1f}" '
            f'font-size="13" fill="{_INK_MUTED}" text-anchor="middle">'
            f'{_esc(cat)}</text>'
        )

    for s_idx, s in enumerate(spec.series):
        color = _SERIES_COLORS[s_idx]
        pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(s.values))
        out.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="{_LINE_WIDTH}" stroke-linejoin="round" '
            f'stroke-linecap="round"/>'
        )
        # End marker with a surface ring so overlapping series stay legible,
        # plus a direct label — the endpoint only, never every point.
        ex, ey = px(n_cats - 1), py(s.values[-1])
        out.append(
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="{_MARKER_RADIUS}" '
            f'fill="{color}" stroke="{_SURFACE}" stroke-width="{_MARKER_RING}"/>'
        )
        out.append(
            f'<text x="{ex + 12:.1f}" y="{ey + 4.5:.1f}" font-size="13" '
            f'fill="{_INK_SECONDARY}">'
            f'{_esc(format_value(s.values[-1], spec.value_suffix))}</text>'
        )

    out.append(_legend_svg(spec, pad_l, pad_t + 14))
    return "".join(out), height


def build_chart_html(spec: ChartSpec) -> str:
    """Self-contained HTML for ``spec``. Pure — no I/O, no network."""
    spec.validate()
    body_svg, plot_h = (_bar_svg(spec) if spec.form == "bar" else _line_svg(spec))

    head_h = 62 if spec.subtitle else 40
    foot_h = 34 if spec.source else 8
    total_h = plot_h + head_h + foot_h

    header = (
        f'<text x="28" y="34" font-size="21" font-weight="600" '
        f'fill="{_INK_PRIMARY}">{_esc(spec.title)}</text>'
    )
    if spec.subtitle:
        header += (
            f'<text x="28" y="56" font-size="14" fill="{_INK_SECONDARY}">'
            f'{_esc(spec.subtitle)}</text>'
        )
    footer = ""
    if spec.source:
        footer = (
            f'<text x="28" y="{total_h - 13}" font-size="12" '
            f'fill="{_INK_MUTED}">{_esc(spec.source)}</text>'
        )
    axis_label = ""
    if spec.value_label:
        axis_label = (
            f'<text x="{spec.width - 28}" y="{total_h - 13}" font-size="12" '
            f'fill="{_INK_MUTED}" text-anchor="end">'
            f'{_esc(spec.value_label)}</text>'
        )

    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>html,body{{margin:0;padding:0;background:{_SURFACE};}}"
        f"svg{{display:block;font-family:{_FONT_STACK};}}</style>"
        f'</head><body><svg id="chart" xmlns="http://www.w3.org/2000/svg" '
        f'width="{spec.width}" height="{total_h}" '
        f'viewBox="0 0 {spec.width} {total_h}">'
        f'<rect width="{spec.width}" height="{total_h}" fill="{_SURFACE}"/>'
        f"{header}"
        f'<g transform="translate(0,{head_h})">{body_svg}</g>'
        f"{footer}{axis_label}"
        "</svg></body></html>"
    )


async def render_chart(
    spec: ChartSpec,
    *,
    scale: int = 2,
    timeout_ms: int = 30000,
) -> bytes | None:
    """Render ``spec`` to PNG bytes via headless chromium.

    Returns ``None`` (never raises) when playwright is unavailable or the
    render fails — mirroring ``services.preview_screenshot`` so a caller can
    treat a missing chart as "no image for this slot" rather than a pipeline
    failure. A malformed *spec*, by contrast, raises from ``validate()``:
    that is a programming error, not an environment one.
    """
    html_doc = build_chart_html(spec)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning(
            "[chart_render] playwright not installed — cannot render %r",
            spec.title,
        )
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": spec.width, "height": 800},
                    device_scale_factor=scale,
                )
                page = await context.new_page()
                await page.set_content(html_doc, wait_until="load", timeout=timeout_ms)
                element = await page.query_selector("#chart")
                if element is None:  # pragma: no cover - set_content would have thrown
                    logger.warning("[chart_render] chart element missing after render")
                    return None
                return await element.screenshot(type="png")
            finally:
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001 — silent-ok: best-effort close in finally; the PNG is already captured and a close failure must not mask it
                    pass
    except Exception as e:
        logger.warning(
            "[chart_render] render failed for %r: %s", spec.title, str(e)[:200],
        )
        return None


__all__ = [
    "ChartSpec",
    "Series",
    "build_chart_html",
    "chart_alt_text",
    "format_value",
    "nice_ticks",
    "render_chart",
]
