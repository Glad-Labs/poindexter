# Image OCR text-leakage gate

**Status:** live and **enforcing** since 2026-08-08 (poindexter#1004). Scoring
and retry have been live since 2026-07-13. **Backend-agnostic since 2026-09-23**
— any rendered image can be scanned, not just image-gen's own (see
[Scanning any image](#scanning-any-image)). **Related:**
[`image-fanout.md`](image-fanout.md),
[`screenshot-image-provider.md`](screenshot-image-provider.md),
`scripts/image-gen-server.py`, `services/image_text_scan.py`
(`services/image_ocr_gate.py` is now a backcompat re-export).

Generated images must carry **no legible text**. Both
`src/cofounder_agent/skills/content/blog-generation/SKILL.md` and the operator
image policy say so outright, alongside the ban on people, faces and hands.
This gate is the automated backstop for the text half of that rule.

## Why a gate and not a better prompt

Guidance-distilled models (Z-Image-Turbo) run at `guidance_scale=0`, where
`negative_prompt` has no effect — the pipeline does not even accept the kwarg.
The 2026-07 bake-off (glad-labs-stack#2386) measured `z_image_turbo` leaking
~57× more readable text than the best-scoring alternative, and showed that a
"textless composition" clause in the _positive_ prompt does not move the number
(still 25.33 avg leaked chars/image with the clause applied). Keeping
`z_image_turbo` for its aesthetic therefore means catching leakage after the
fact: `POST /generate` OCR-scans every render and re-rolls the seed, bounded
and keep-best.

It is a deterministic check-and-retry, not a model swap.

## The 2026-08 enforcement gap

The gate scored correctly from day one. It just did not _act_.

When retries were exhausted the server returned the best (still-leaking)
attempt anyway and set `ocr_gate_passed=false` on the response — a field **no
caller ever read**. Between 2026-07-13 and 2026-08-08, **67 of 693 renders
(9.7%) failed the gate and shipped regardless**, some carrying over 700 legible
characters. One of them became the hero image on a publish-bound draft with
"Poindexter Philosophy" set in large bold type across the top ~20% of the
frame — EasyOCR read it back at confidence **1.000**.

A second, latent hole sat next to it: a scoring failure was recorded as
`text_chars = 0`, which is indistinguishable from "scanned, clean". A missing
or broken `easyocr` would therefore have disabled the gate silently while every
response reported a pass — the same fail-open shape the QA rails avoid by
returning `None` plus a finding rather than faking a perfect score.

Both are closed. The gate now reports honestly and blocks.

## Verdict vocabulary

`GET /health` and every `POST /generate` response carry `ocr_gate_status`:

| Status        | Meaning                                             | `ocr_text_chars` | `ocr_gate_passed` |
| ------------- | --------------------------------------------------- | ---------------- | ----------------- |
| `pass`        | Scanned; at or under `max_chars`                    | int              | `true`            |
| `fail`        | Scanned; over `max_chars` after every retry         | int              | `false`           |
| `unavailable` | **Could not scan** — OCR engine missing or erroring | `null`           | `false`           |
| `disabled`    | Gate off; no claim made about this image            | `0`              | `true`            |

`ocr_text_chars: null` is load-bearing. It means _unverified_, never _verified
clean_ — do not coerce it to `0` anywhere downstream. `ocr_gate_passed` is
`true` only for a scanned-and-clean image (or a deliberately disabled gate);
the gate does not claim a pass for an image it never scanned.

`GET /health` additionally exposes `ocr_reader_loaded`, so a probe can tell
"gate configured" from "gate actually working" without generating an image.

## Enforcement

A blocked render returns **HTTP 422** with the verdict in `detail`, and the
image is deleted rather than served:

```json
{
  "detail": {
    "error": "ocr_gate_rejected",
    "ocr_gate_status": "fail",
    "ocr_text_chars": 20,
    "ocr_gate_attempts": 3,
    "threshold": 6,
    "model": "z_image_turbo",
    "message": "…"
  }
}
```

422 rather than 5xx: the render _succeeded_, its content is unacceptable, and
repeating the request unchanged just re-runs re-rolls the server already
performed `image_ocr_gate_max_attempts` times.

A rejection lands on the path a failed render already takes — Pexels when
`image_stock_fallback_enabled` is on, otherwise no image plus a warn-severity
`image_gen_downgrade` finding. That is the intended trade: a missing hero is an
operator ping, a text-branded hero is a policy breach on a public page.

**Expect roughly 10% of renders to be rejected** at `max_chars=6`, based on the
four weeks of measured history above.

## Caller contract

An OCR rejection is a **verdict, not a window**. The ordinary render failures
the callers retry — image-gen restarting for a VRAM reclaim, a GPU lock
timeout — clear in seconds. A rejection does not: the server already re-rolled
the seed `max_attempts` times, so a client retry buys a second full set of
re-rolls for the same answer.

`services/image_text_scan.py` is the shared seam (it absorbed the old
`services/image_ocr_gate.py`, which still re-exports these names). It lives in
`services/` (substrate) rather than under `modules/content/` so the `services/`
callers can use it without importing backwards across the module boundary.

```python
from poindexter.services.image_text_scan import (
    describe_ocr_gate_rejection, is_ocr_gate_rejection, safe_json,
)

if resp.status_code != 200:
    body = safe_json(resp)
    if is_ocr_gate_rejection(resp.status_code, body):
        logger.warning("%s", describe_ocr_gate_rejection(body))
        return None          # terminal — do NOT retry
    ...                      # every other non-200 keeps its retry
```

Wired at the content render sites:

| Call site                                                               | Behaviour on 422                                                                           |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `modules/content/atoms/_image_helpers._try_image_gen`                   | returns `None` (already single-shot)                                                       |
| `modules/content/atoms/_image_helpers._render_one_with_retry`           | returns `None`, skipping remaining attempts                                                |
| `modules/content/stages/source_featured_image._render_image_gen`        | returns `(None, {"ocr_gate_rejected": True})`; the stage's retry loop breaks on the flag   |
| `services/video_service._consume_image_gen_response` (shot-list stills) | returns `None`, logged as a verdict ("not a transient failure") rather than a server fault |

`services/image_providers/flux_schnell.py` POSTs to a _different_ server (the
FLUX sidecar on 9838), which has no gate — so it scans its own render instead;
see below.

## Scanning any image

**Until 2026-09-23 the gate existed only inside `POST /generate`, welded to
image-gen's own renders.** Everything else went unscanned — most consequentially
the featured fan-out's three ComfyUI candidates. Over 30 days (74 fan-outs,
`audit_log.event_type='image_fanout_judged'`) zimage was ejected from **19 of
74** contests as `ocr_gate_rejected` while `schnell` / `klein` / `qwen`, held to
no text rule at all, won **77%** of heroes. Their stand-ins were a positive
"textless" clause (measured not to work, above) and a vision judge that scored
a hero while reading its gibberish headline as "its title". So the only
provider held to the rule was the one being benched.

The scan is now a service anyone can call:

```python
from poindexter.services.image_text_scan import scan_image_text, should_exclude, TextScanSettings

scan = await scan_image_text(path_or_bytes, kind="generate", site_config=site_config)
scan.status        # pass | fail | unavailable | disabled | not_scanned
scan.text_chars    # int, or None when not measured — never coerce to 0
scan.coverage_pct  # share of the frame covered by text boxes, or None
should_exclude(scan, TextScanSettings.from_site_config(site_config))
```

**It returns coverage, not just a character count.** EasyOCR's
`readtext(detail=1)` yields `(box, text, confidence)`; the gate always threw
the boxes away. The scan keeps them and reports the share of the frame covered
by the **union** of the confident detections' bounding boxes (a headline often
comes back as a line box plus word boxes — summing would double-count).

**Where it runs: `POST /scan` on the image-gen server** (raw image bytes in the
body, optional `min_confidence`). The reader there is already resident and
CPU-only, so a scan competes with no render for VRAM, and the worker image does
not grow easyocr + torch. `/scan` is independent of the diffusion pipeline — a
DEGRADED server can still scan, and a scan neither loads the pipeline nor
refreshes the idle timer. Status contract: `200` measured; `400` unusable
request (empty / not an image / bad confidence); `413` too large; `503
{"error": "ocr_unavailable"}` the OCR engine itself failed. The client
(`ImageGenServerScanBackend`) retries connection errors and bare 502/503/504 —
the fan-out hard-unloads image-gen right before its ComfyUI renders, so the
first scan can land mid-restart — but never retries `ocr_unavailable`, a 4xx,
or a **404, which means the running image predates `/scan` and must be
rebuilt**. The backend is behind a `TextScanBackend` protocol so the engine can
move without touching a consumer. The coupling is real: if image-gen is down,
scanning is `unavailable`.

### Text policy is per provider kind

Image providers declare a `kind`. **Charts and screenshots are supposed to be
full of text** — a blanket no-text rule would reject every `[CHART:]` render
and every operator-surface screenshot. So:

| Kind            | Produced by                                          | Policy           | Scanned? |
| --------------- | ---------------------------------------------------- | ---------------- | -------- |
| `generate`      | image_gen, ai_generation, flux_schnell, the fan-out  | `forbidden`      | yes      |
| `chart`         | `image_providers/chart.py`                           | `expected`       | no       |
| `screenshot`    | `image_providers/screenshot.py`                      | `expected`       | no       |
| `composed`      | the brand hero (`services/brand_hero.py`, real type) | `expected`       | no       |
| `search`        | Pexels                                               | `not_applicable` | no       |
| _anything else_ | —                                                    | none             | **no**   |

**An unknown kind is not scanned, never forbidden** — adding a provider must not
silently start rejecting its output. `image_text_kind_policy` overrides kinds
one by one; a test forces every registered provider's kind onto the table.
Consumers that only see a URL (the qa.vision rail) classify it by the prefixes
the providers write under (`images/charts/`, `images/screenshots/`,
`images/featured/brand-`, the Pexels host); an unrecognised URL is not scanned.

### One rule, one set of knobs

The verdict is the same `image_ocr_gate_max_chars` at the same
`image_ocr_gate_min_confidence`, gated by the same `_enabled` / `_enforce` /
`_fail_closed_when_unavailable` rows the server applies to its own renders —
there is no second, drifting copy of the rule. `should_exclude` mirrors the
server's `should_reject_render` exactly: `fail` blocks under `enforce`;
`unavailable` blocks only when `fail_closed_when_unavailable` is also on.

### Consumers

| Consumer                                                            | What the scan does there                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/image_fanout.py`                                          | scans **all four** candidates after render, before judging. Over threshold ⇒ excluded from the contest and recorded under the row's `excluded` list. All excluded ⇒ `(None, meta)`, and the stage takes its no-image path. See [`image-fanout.md`](image-fanout.md).                     |
| `services/image_providers/flux_schnell.py`                          | scans its render; `fail` ⇒ the file is deleted and the provider returns `[]`. The scan rides on `ImageResult.metadata["text_scan"]`.                                                                                                                                                     |
| `modules/content/multi_model_qa._check_image_relevance` (qa.vision) | the **measured** coverage replaces the judge's estimated `text_coverage` as the input to #3973's penalty ramp for `generate`-kind images; the estimate stays the fallback when a scan is unavailable. `expected`-kind images (charts, screenshots, the brand hero) take no text penalty. |

**Measured coverage is box area, not the "band".** On the fan-out that produced
task 243f3123's hero, the real reader measured the winning `klein` render at
**27 chars / 11.9%** of the frame (`TLMENEITIR`, `looedz`, …) and `schnell`
at 7 chars / 19.6% (`Macline`); `qwen` was clean. Both leakers are now
excluded on characters. But note 11.9% against the judge's eyeballed ~40%:
boxes enclose glyphs, not the band around them. #3973's ramp
(`qa_vision_text_ignore_coverage_pct` 5 → `qa_vision_text_full_penalty_pct` 40) was calibrated against eyeballed estimates, so a measured input deducts
less for the same image. In practice every `generate` path is now held to
`max_chars=6` before qa.vision sees it, so the rail mostly meets small
residual text; if the penalty needs to bite harder on measured input, lower
`qa_vision_text_full_penalty_pct` — do not reintroduce the estimate.

## Settings

All DB-backed (`app_settings`), read directly by the image-gen server over
asyncpg — it has no `SiteConfig` — refreshed every ~60s and on `POST /reload`.

| Key                                           | Default | Effect                                                                                                                                                                                                                                                           |
| --------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `image_ocr_gate_enabled`                      | `true`  | Master switch. Off ⇒ status `disabled`, no scan.                                                                                                                                                                                                                 |
| `image_ocr_gate_max_chars`                    | `6`     | Inclusive threshold. Above the 1–2 char noise floor of a stray misread, below a genuinely mangled label.                                                                                                                                                         |
| `image_ocr_gate_max_attempts`                 | `3`     | Seed re-rolls before giving up.                                                                                                                                                                                                                                  |
| `image_ocr_gate_min_confidence`               | `0.3`   | OCR detections below this are ignored as noise.                                                                                                                                                                                                                  |
| `image_ocr_gate_enforce`                      | `true`  | Whether a `fail` verdict **blocks** (422) or merely annotates. Off = pre-2026-08 behaviour.                                                                                                                                                                      |
| `image_ocr_gate_fail_closed_when_unavailable` | `false` | Whether `unavailable` also blocks. Deliberately separate: a broken easyocr should not become a total image-generation outage. Off is still honest, not fail-open — the status, the warning-severity audit row and the ERROR log all say the image is unverified. |

Text-scan client (`services/image_text_scan.py`), read through `SiteConfig`:

| Key                                     | Default                                                                                         | Effect                                                                     |
| --------------------------------------- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `image_text_scan_server_url`            | `''`                                                                                            | Where `POST /scan` lives. Empty ⇒ `image_gen_server_url`.                  |
| `image_text_scan_timeout_seconds`       | `90`                                                                                            | Per request. Covers the reader's lazy load in a freshly restarted process. |
| `image_text_scan_attempts`              | `4`                                                                                             | Transport attempts (restart window). A verdict is never retried.           |
| `image_text_scan_retry_backoff_seconds` | `5`                                                                                             | Between transport attempts.                                                |
| `image_text_kind_policy`                | `generate=forbidden,chart=expected,screenshot=expected,composed=expected,search=not_applicable` | Per-kind override of the code table.                                       |

Raising `max_chars` is the throughput lever if 10% rejection is too costly;
turning `enforce` off is the escape hatch, and it restores the exact behaviour
that let 67 images through.

## Observability

Every gated generation writes an `image_ocr_gate_result` row to `audit_log`
(`severity=warning` when not passed), carrying `status`, `rejected`,
`text_chars`, `attempt_scores`, `attempts` and `threshold`. It surfaces in:

- **Grafana → Pipeline** — avg leaked chars, pass %, retried count (24h).
  These aggregate with `AVG(…::numeric)` and `COUNT(*) FILTER (…)`, both of
  which skip SQL `NULL`, so an `unavailable` run drops out of the averages
  instead of dragging them toward a fictitious zero.
- **Operator console → pipeline events** — per-task timeline entry, rendering
  `unavailable` as a word rather than `0`, and marking blocked renders.

The fan-out's per-candidate scans land in `image_fanout_judged` rows
(`candidates[].text_scan`, `excluded[]`) and feed the Pipeline board's fan-out
panels plus `ProbeFanoutDatasetHealthJob`, which pages when more than
`image_fanout_probe_max_text_scan_unavailable_pct` (10%) of scanned candidates
competed **unverified** — the scan fails open by design, so a dark scanner and
a clean dataset would otherwise look identical.

## Reproducing

```bash
curl -sS -X POST http://localhost:9836/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a bold sign reading POINDEXTER PHILOSOPHY, large lettering","width":1024,"height":1024}'
```

With enforcement on, a leaking render returns 422 and the body above rather
than an image. `GET /health` shows the live gate config and whether the OCR
reader has loaded. To scan an image image-gen did not render:

```bash
curl -sS -X POST 'http://localhost:9836/scan?min_confidence=0.3' \
  -H 'Content-Type: application/octet-stream' --data-binary @candidate.png
# {"text_chars": 27, "text_coverage_pct": 11.87, "detections": 5, ...}
```
