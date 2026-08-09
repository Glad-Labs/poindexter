# Image OCR text-leakage gate

**Status:** live and **enforcing** since 2026-08-08 (poindexter#1004). Scoring
and retry have been live since 2026-07-13. **Related:**
[`screenshot-image-provider.md`](screenshot-image-provider.md),
`scripts/image-gen-server.py`, `services/image_ocr_gate.py`.

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

`services/image_ocr_gate.py` is the shared seam. It lives in `services/`
(substrate) rather than under `modules/content/` so the two `services/` callers
can use it without importing backwards across the module boundary.

```python
from services.image_ocr_gate import (
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

| Call site                                                        | Behaviour on 422                                                                         |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `modules/content/atoms/_image_helpers._try_image_gen`            | returns `None` (already single-shot)                                                     |
| `modules/content/atoms/_image_helpers._render_one_with_retry`    | returns `None`, skipping remaining attempts                                              |
| `modules/content/stages/source_featured_image._render_image_gen` | returns `(None, {"ocr_gate_rejected": True})`; the stage's retry loop breaks on the flag |

`services/image_providers/flux_schnell.py` and
`services/video_renderers/shot_list_renderer.py` also POST `/generate`. They
inherit the rejection as an ordinary non-200 (no image, existing fallback) —
correct but without the distinguishing log line; adopting the helper there is a
follow-up.

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

## Reproducing

```bash
curl -sS -X POST http://localhost:9836/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a bold sign reading POINDEXTER PHILOSOPHY, large lettering","width":1024,"height":1024}'
```

With enforcement on, a leaking render returns 422 and the body above rather
than an image. `GET /health` shows the live gate config and whether the OCR
reader has loaded.
