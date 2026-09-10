# Screenshot image provider

**Status:** shipped 2026-08-08 (poindexter#1002). Inert until an operator
configures at least one target.

Poindexter writes about Poindexter. When it does, the honest illustration for
"our task queue scores every draft" is the QA Rails board showing the real
scores — not a diffusion model's impression of a dashboard.

Diffusion is actively the wrong tool for this. `blog-generation/SKILL.md`
already tells the writer **never** to ask for a diagram or chart, because SDXL
renders axis labels as garbled glyphs. That left a gap: system posts either
shipped with a decorative stock photo that evidenced nothing, or with no image
at all.

This provider closes the gap by capturing the real screen.

## The path

```text
writer places  [SCREENSHOT: qa-rails]
      ↓ _writer_markers.number_inline_markers
   [IMAGE-2: screenshot:qa-rails]        ← shares ONE number sequence with [IMAGE:]
      ↓ content.plan_image_markers
   {"num": "2", "desc": "qa-rails", "screenshot_target": "qa-rails"}
      ↓ content.generate_images          ← routed away from the image-gen batch
   ScreenshotProvider.fetch("qa-rails", config)
      ↓ services.preview_screenshot      ← the headless chromium the vision QA rail already drives
      ↓ R2UploadService                  ← PNG → WebP@80, ≤1920×1920
   ImageResult(url=…, source="screenshot")
      ↓ content.inject_images            ← `screenshot` branch; truthful width/height
   <img src="…" width="1600" height="1150" loading="lazy" />
```

Two details in that chain are load-bearing:

**Screenshot markers share one numbering sequence with `[IMAGE:]` markers.**
`content.inject_images` matches `image_results` back to placeholders _by
number_. A separate sequence would collide and inject the wrong image into the
wrong slot.

**Dimensions are read from the captured PNG's IHDR, not from the request.** A
`full_page` capture is much taller than the viewport it was requested with
(the operator console renders 1600×1800 as a viewport, 1600×4320 full-page).
Those numbers become the `<img width height>` attributes, so echoing the
request instead of measuring the result means layout shift on the published
page. They ride on the `image_results` entry through to
`content.inject_images`, which uses them instead of the image-gen branch's
1024×1024 square.

**Every stage that branches on `source` needs a `screenshot` arm.** Both
`content.inject_images` (`image_gen` / `pexels` / else-strip) and
`content.image_rebuild_gate` (`source != "image_gen"` ⇒ stock fallback)
defaulted to treating an unrecognised source as failure. The first silently
stripped a successfully captured image from the body; the second aborted any
rebuild of a draft containing a screenshot marker. Adding a fourth source
later means auditing those branches — grep for `"image_gen"`.

## Two budgets, one numbering sequence

`[SCREENSHOT:]` and `[CHART:]` are **evidence** markers; `[IMAGE:]` is an
illustration. They share one number sequence (above) but **not one budget**:

| marker                    | cap                                        | filled by          |
| ------------------------- | ------------------------------------------ | ------------------ |
| `[IMAGE:]` + agent top-up | `writer_max_inline_images` (default 3)     | image-gen / Pexels |
| `[SCREENSHOT:]`           | `writer_max_evidence_per_kind` (default 1) | ScreenshotProvider |
| `[CHART:]`                | `writer_max_evidence_per_kind` (default 1) | ChartProvider      |

Until 2026-09-09 (stack#3614 follow-up) all three shared the illustration cap,
and `content.plan_image_markers` ran the Image Decision Agent **only when the
draft carried no markers at all**. The two_pass writer gained `[SCREENSHOT:]`
on 2026-09-02, so a single screenshot marker read as "illustrations planned",
the agent never ran, and every canonical_blog draft that took the offer shipped
with one dashboard capture and zero generated images. The atom now **tops up**:
it counts the illustrations the writer placed, asks the agent for the remaining
slots (numbered after the writer's markers, never a second image in a section
that already has one), and renumbers the merged body in document order.
`image_plans` is rebuilt from that final body, so numbers and plans cannot
disagree. A failed top-up keeps the writer's markers and the
`inline_images_skipped` finding says how many it kept.

## Only on posts about this system

"Poindexter writes about Poindexter" was the premise, but the prompt used to
enumerate the allowlist on **every** draft. The writer took the offer where it
made no sense: a Findings-board capture was the only image on a post about
forever chemicals (2026-09-05), and the QA Rails board illustrated posts about
a MUD, native web tricks, and a dad's 90s coding advice. Every one of those sat
on an `external` topic batch; every legitimate screenshot sat on an `internal`
one.

`ai_content_generator.screenshot_targets_for_post` gates the block on two
OR-ed, operator-tunable signals:

| setting                     | default      | matches                                                                                                      |
| --------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------ |
| `screenshot_topic_kinds`    | `internal`   | the topic batch's `picked_candidate_kind` — internal candidates are drawn from the operator's own prior work |
| `screenshot_topic_keywords` | `poindexter` | case-insensitive substring over topic / angle / tags, for operator-created tasks with no batch lineage       |

`writer_core._read_topic_kind` reads the kind from `topic_batches` via
`pipeline_tasks.topic_batch_id` at draft time — not from a stamp in
`stage_data.metadata`, which the writer's later upsert rewrites wholesale (a
stamp would be gone by the time a `preview_gate` text-regen re-runs the
writer). Off-topic posts get an explicit `none for this post — … do not use
[SCREENSHOT: …] markers` line rather than a blank list, for the same reason an
unconfigured install does. Both CSVs empty is the explicit off switch.

## Targets are an allowlist, never a URL from the model

The marker carries a target **key**. The URL behind it comes from
`app_settings.plugin.image_provider.screenshot.targets`.

This is a security boundary, not a convenience. The worker sits on the Docker
network with reachable admin surfaces (Grafana, Prefect, pgAdmin, the cloud
metadata endpoint), and the marker's content originates from an LLM. A
provider that accepted raw URLs would be an SSRF seam driven by model output.

An unlisted key logs the valid keys and returns nothing:

```text
[ScreenshotProvider] screenshot target 'mission-control' is not in the
allowlist; configured targets: operator-console, qa-rails
```

The writer prompt enumerates the configured keys (`{screenshot_targets}`), so
the model picks from the real list rather than inventing plausible ones. An
install with no targets configured gets `none configured — do not use
[SCREENSHOT: …] markers`, and the writer omits the marker entirely.

## Configuration

`plugin.image_provider.screenshot.targets` is a JSON object. It **ships
empty** on purpose — the URLs are per-install, and an empty allowlist means a
marker resolves to nothing rather than to somebody else's dashboard.

```bash
poindexter settings set plugin.image_provider.screenshot.targets '{
  "qa-rails": {
    "url": "http://localhost:3000/d/qa-rails?from=now-90d&to=now&kiosk",
    "width": 1600, "height": 1150, "wait_ms": 9000,
    "alt": "The QA Rails dashboard: per-reviewer approval rate and score distribution"
  },
  "operator-console": {
    "url": "http://localhost:8002/console/",
    "width": 1600, "height": 1800, "wait_ms": 9000,
    "alt": "The Poindexter operator console"
  }
}'
```

| Per-target key | Default    | Notes                                                                                                                                               |
| -------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `url`          | required   | A bare string value is shorthand for `{"url": "…"}`.                                                                                                |
| `width`        | 1600       | Desktop-width on purpose — operator dashboards reflow to a useless single column below ~1200px.                                                     |
| `height`       | 1000       | Viewport height. With `full_page` the capture runs taller.                                                                                          |
| `full_page`    | `false`    | Whole scrollable page. The console is a fixed-height app that scrolls internally, so `full_page` gains nothing there — use a tall viewport instead. |
| `wait_ms`      | 6000       | Settle time after `networkidle`. Grafana lazy-loads panels; too short and they capture empty.                                                       |
| `alt`          | target key | Alt text for the `<img>`.                                                                                                                           |

Sibling settings: `…screenshot.timeout_ms` (60000),
`…screenshot.upload_to` (`r2`, or `none` to serve a `file://` URL in dev).

### Capture the surface from where the reader's numbers are true

Grafana dashboards render fine from inside the worker container
(`http://grafana:3000` on the Docker network, and anonymous Viewer access is
enabled).

**The operator console does not.** It makes browser-direct calls to
`localhost:3000`/`:9091`/`:8002`, which inside the worker resolve to the
worker itself — so it renders half the stack as down and every history panel
as "no data in range". Point console-style targets at a URL that resolves the
same way it does for the operator, or the screenshot will libel your own
system in public. This is exactly the failure that made the first capture for
the "Building Poindexter" post unusable.

## Failure behaviour

A screenshot slot that cannot be filled drops the placeholder and emits a
`screenshot_capture_failed` finding (`severity=warn`, visible on the
[Findings board](http://localhost:3000/d/findings)). The post ships without
that image.

It deliberately does **not** fall back to image-gen or Pexels. The marker
exists because a real screen was the point; substituting a diffusion render of
a dashboard would misrepresent the system to the reader — the exact failure
the provider was built to prevent. This mirrors the QA-rail fail-open pattern:
a degraded rail returns nothing and files a finding, never a fabricated pass.

Usual causes: the target is absent from the allowlist, or the surface was down
when the pipeline ran.

## Related

- [`services/image_providers/screenshot.py`](../../src/cofounder_agent/poindexter/services/image_providers/screenshot.py) — the provider
- [`services/preview_screenshot.py`](../../src/cofounder_agent/poindexter/services/preview_screenshot.py) — the shared capture helper (also drives the `qa.vision` rail)
- [`modules/content/atoms/_writer_markers.py`](../../src/cofounder_agent/poindexter/modules/content/atoms/_writer_markers.py) — marker parsing + the two budgets
- [`modules/content/atoms/content_plan_image_markers.py`](../../src/cofounder_agent/poindexter/modules/content/atoms/content_plan_image_markers.py) — the top-up planner
- [`brand-hero.md`](brand-hero.md) — the sibling answer for the **featured** image: composed from brand tokens, because diffusion cannot set type
- [`anti-hallucination.md`](anti-hallucination.md) — why "show the real number" is a house rule
