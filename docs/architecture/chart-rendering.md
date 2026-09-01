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
- **Provenance is a field, not a caption someone might forget.** `source`
  renders as a footer line, so a published chart always says what produced it
  and over what sample.

## Accessibility

A PNG carries no hover layer and no table view, so `chart_alt_text` serializes
the **entire** category/series matrix into the alt attribute. A screen reader
gets the numbers, and so does any later LLM pass re-reading the published post.
The provider puts that string on `ImageResult.alt_text` automatically.

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
