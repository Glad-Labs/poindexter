# Chart rendering — drawing measurements instead of imagining them

`blog-generation/SKILL.md` tells the writer **never** to ask for a chart or a
diagram. That instruction is correct — SDXL renders axis labels as garbled
glyphs — but it closed off the single most useful illustration a measurement
post can carry: the measurements.

`services/chart_render.py` is the honest alternative. A chart is **drawn from
numbers**, never generated from a prompt, so every tick, label and bar length
is a fact rather than a plausible-looking shape.

## The two pieces

| Piece          | Path                                | Job                                               |
| -------------- | ----------------------------------- | ------------------------------------------------- |
| Renderer       | `services/chart_render.py`          | `ChartSpec` → self-contained HTML/SVG → PNG bytes |
| Image provider | `services/image_providers/chart.py` | JSON spec → rendered PNG → R2 → `ImageResult`     |

`build_chart_html` is **pure** — spec in, HTML string out, no I/O. Geometry,
ticks, escaping and palette are therefore unit-testable without launching a
browser; only `render_chart` needs one.

## Why chromium and not a plotting library

`playwright` + its bundled chromium are **already a production dependency** —
the vision QA rail (`services/preview_screenshot.py`) and the `screenshot`
image provider both drive it. Rendering SVG-in-HTML at `device_scale_factor=2`
therefore costs no new wheel, no new system package, and no new container
layer, and it gives real text layout with correct font metrics — which is
precisely what a plotting library's headless backends handle worst.

The worker container installs chromium via `playwright install chromium`
(Dockerfile). On a host dev env that has not run that, `render_chart` returns
`None` and logs — the same fail-open posture as `capture_preview_screenshot`.

## Forms

`bar` (grouped horizontal) and `line` (multi-series over ordered categories).
Those two cover the shapes measurement content actually needs: _compare things_
and _watch a thing move_. Anything else should be added deliberately rather
than by generalizing these.

## Design rules the code enforces

These are correctness properties, not taste:

- **The palette is validated, not chosen by eye.** Series colors are slots 1–4
  of a categorical palette verified for colorblind separation — worst adjacent
  pair CVD ΔE **24.7** and normal-vision ΔE **33.6**, against floors of ≥8 and
  ≥15. The operator reading these charts is colorblind. Re-run the data-viz
  validator before touching a hue.
- **Hues are assigned in fixed order, never cycled.** A 5th series raises
  rather than reusing slot 1 — two indistinguishable colors on one chart is a
  wrong chart. Fold to "Other" or split it; that is the caller's call.
- **Text never wears the series color.** Marks carry identity; labels, values
  and axis text use ink tokens, with a colored swatch _beside_ the text. A test
  asserts every `<text fill=…>` is an ink token.
- **Bars start at a zero baseline.** A truncated bar axis misstates ratios,
  which is the entire point of a measurement chart.
- **The axis maximum must cover the largest value.** `nice_ticks` returns a
  final tick ≥ `max_value`. This was a real bug: a 235 tok/s bar drew past a
  200 axis and lost its value label. Found by rendering the chart and looking
  at it — which is a required step, not an optional one.
- **A category label is never clipped.** The left gutter is sized from the
  widest _rendered_ label, and past a cap the label is ellipsized rather than
  sliced at the canvas edge — see below.
- **Provenance is a field, not a caption someone might forget.** `source`
  renders as a footer line, so a published chart always says what produced it
  and over what sample.

## Text width: the estimate has to be conservative

Labels are _placed_ by arithmetic in Python and _laid out_ by chromium, so the
gutter is sized from an estimate of the rendered width. The asymmetry matters:
over-estimating only shifts the plot right, while under-estimating slices a
glyph off the canvas edge — and a sliced glyph reads as a **different string**
(`qwen3-vl:30b-a3b-instruct` rendered as `wen3-vl:30b-a3b-instruct` in two
published charts on 2026-09-23, R2 `images/charts/bcc985ab.webp` and
`34b0a7f7.webp`).

That bug had two causes, and the second is the interesting one:

1. The gutter was `len(label) * 7.9 + 16` — sized from the character **count**,
   which is blind to _which_ characters, and it never subtracted the 12px the
   label is drawn back from the baseline. A 210px label got a 201px budget.
2. **The font is not the font this file asks for.** `fc-list` inside
   `poindexter-worker` (2026-09-23) shows the image ships **JetBrains Mono** and
   Liberation only — none of `system-ui` / `Segoe UI` / Roboto / Helvetica Neue
   / **DejaVu Sans** / Arial that `_FONT_STACK` names, despite a code comment
   claiming DejaVu is "what Debian-family images actually ship". So
   `sans-serif` falls back to JetBrains Mono and every published chart renders
   **monospace** at a flat 0.60em — 8.40px/char at 14px, against an estimate of
   7.9.

`text_width` therefore takes per-character advances as the **max over every
font the stack can land on** (JetBrains Mono's 0.60em floor, Liberation/Arial,
DejaVu), all measured in chromium via `getBoundingClientRect` at 100px. It is
pure, so the clipping property is testable without a browser:
`TestCategoryLabelsAreNeverClipped` asserts `x - text_width(label) >= 0` for
every anchored label, including labels longer than anything in the catalog.

The gutter is capped (`_GUTTER_MAX_PX`, and never more than
`_GUTTER_MAX_FRACTION` of the canvas) so labels cannot eat the plot area.
Past the cap a label is **ellipsized**, not clipped — the full name still
reaches the reader through `chart_alt_text`, which a sliced glyph cannot do.

Charts currently publish in a monospace face as a consequence of (2). Adding
`"Liberation Sans"` to `_FONT_STACK` would give them a proportional face, but
that changes the typography of every published chart, so it is a deliberate
decision rather than a drive-by — it is **not** done.

## Accessibility

A PNG carries no hover layer and no table view, so `chart_alt_text` serializes
the **entire** category/series matrix into the alt attribute. A screen reader
gets the numbers, and so does any later LLM pass re-reading the published post.
The provider puts that string on `ImageResult.alt_text` automatically.

## The `[CHART:]` marker path

`ChartProvider` now has a caller. The writer places a marker naming a
catalogued **key**, and three hops carry it to a rendered image — the same
plumbing `[SCREENSHOT:]` already uses:

```
[CHART: llm-decode-vs-delivered]        <- writer, in the draft
  |- [IMAGE-N: chart:llm-decode-vs-delivered]   _writer_markers
       |- plan["chart_target"]                   content.plan_image_markers
            |- chart_catalog.resolve(key, pool)  <- the SERVICE owns the query
                 |- ChartProvider.fetch(spec)     <- receives finished data
```

**The writer picks which chart, never what it says.** `services/chart_catalog.py`
maps a key to a `ChartSpec` built from live rows; the provider is handed
finished JSON. That split is the whole security property — a query surface
reachable from a writer-emitted marker would be an injection seam, which is why
the provider still has no SQL in it and a test still says it never will.

Charts are **code-defined**, not settings-defined: an operator-authored query
reachable from an LLM-chosen key is exactly what this avoids. The per-install
lever is the allowlist (`chart_catalog_enabled_keys`, empty = whole catalog),
not the query.

A chart is an **evidence** marker and is budgeted like a screenshot —
`writer_max_evidence_per_kind` (default 1), separate from the
`writer_max_inline_images` illustration cap, so placing one never costs the
post a generated illustration; see
[screenshot-image-provider.md](screenshot-image-provider.md#two-budgets-one-numbering-sequence).
Unlike screenshots, charts carry no topic gate: the catalog allowlist is the
gate, and a chart only exists where the measurements it plots do.

Every failure collapses to the same empty slot the screenshot branch produces —
unknown key, key disabled here, too few models to compare, a query that raised,
a render that returned nothing. **A wrong chart is worse than no chart.**

### Catalogued charts

| key                       | what it draws                                                                                    |
| ------------------------- | ------------------------------------------------------------------------------------------------ |
| `llm-decode-vs-delivered` | per-model raw decode speed vs the throughput the application actually receives, from `cost_logs` |

It shares `benchmark_findings.measure_models` with the
[topic source](benchmark-findings.md) that proposes these posts, so the chart
and the prose come from one query rather than two that can drift apart.

Adding a chart means adding a builder function — one read-only query returning
a fully-populated `ChartSpec`, including the `source` line, because a published
chart must always say what produced it.

## The provider takes data, never a query

`ChartProvider` accepts a JSON **chart spec** — categories and values already
computed by whoever owns the data (a job, an atom, a benchmark sweep). It
renders and uploads. That boundary is deliberate:

> An image plugin that could fetch its own data would need a query surface, and
> a query surface reachable from a writer-emitted marker is an injection seam.

This is the same lesson as `ScreenshotProvider`'s target allowlist (poindexter#1002),
applied one step earlier — here there is nothing to inject at all. **There is no
SQL in the provider and a test asserts there never will be.** A malformed spec
fails `ChartSpec.validate()` and the slot falls through to the caller's normal
"no image for this placeholder" handling.

A named-chart surface (operator-authored queries in `app_settings`, keyed like
`plugin.image_provider.screenshot.targets`) is the natural next step — but it
belongs in a **service that owns the query**, handing the result here as a spec.

## Payload

```json
{
  "form": "bar",
  "title": "Your local LLM benchmark is measuring the wrong thing",
  "subtitle": "Decode speed vs. what the application actually receives",
  "categories": ["qwen2.5:7b", "phi4:14b"],
  "series": [
    { "label": "Raw decode", "values": [235.1, 124.7] },
    { "label": "Delivered to caller", "values": [55.6, 25.3] }
  ],
  "value_label": "output tokens / second",
  "source": "Poindexter cost_logs — 9,110 instrumented production calls"
}
```

## Settings

`plugin.image_provider.chart.*` in `app_settings`:

| Key          | Default | Meaning                                                                         |
| ------------ | ------- | ------------------------------------------------------------------------------- |
| `scale`      | `2`     | device pixel ratio; keeps axis text crisp through the uploader's WebP transcode |
| `width`      | `1200`  | spec width in points; a spec's own `width` wins                                 |
| `timeout_ms` | `30000` | render timeout                                                                  |
| `upload_to`  | `r2`    | `r2` or `none` (serves a `file://` URL)                                         |

The shared uploader transcodes PNG → WebP@80 and fits the result inside
1920×1920, so a 1200pt spec at `scale=2` (2400px) lands at 1920px — still ~1.6×
the CSS width the blog displays it at.
