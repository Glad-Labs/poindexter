# Never drop a shot — per-shot fallback ladder for the video renderer

**Date:** 2026-07-15
**Issue:** [Glad-Labs/poindexter#865](https://github.com/Glad-Labs/poindexter/issues/865)
**Status:** implemented 2026-07-15 ([glad-labs-stack#2633](https://github.com/Glad-Labs/glad-labs-stack/pull/2633)) — 3-rung ladder + real-source ratio gate + cross-platform branded card, verified end-to-end (dead image-gen host → complete card-filled MP4).

## Problem

Roughly a third of rendered videos ship with a visible duration/timeline defect.
A 30-day live audit of the 38 distinct `canonical_blog` video tasks:

- **Long videos render far shorter than planned** — `av_desync` findings show the
  rendered long video averaging **171s against a 253s shot-list plan** (−32%), max
  drift 145s.
- **The cause is dropped shots.** Every long-lane `av_desync` task is also a
  `partial_render` task (10/10 overlap). Partial renders keep as few as **4 of 11**
  shots (long) or **2 of 7** (short); the missing shots are silently excluded from
  the concat.
- The `video_render_min_shot_ratio` gate (added 2026-07-03) rejects+redispatches
  the worst (<50%) renders, so the catastrophic June cases are mitigated — but
  post-gate, degraded videos (50–92% kept) still ship with 1–5 shots dropped, and
  `av_desync` still fires on ~3 tasks/12 days.

The failures cluster by source family (whole groups of shots fail in one render),
which is the fingerprint of **a source going unavailable mid-render** — the
image-gen server wedging, or Pexels returning nothing — not random per-shot
bad luck (matches the known "image-gen host-port wedge" and "post-crash boot
window" operational gotchas).

## Root cause (one line)

`services/video_renderers/shot_list_renderer.py:1121`:

```python
rendered = [r for r in shot_results if r.success and r.clip_path]
```

Any shot whose render hard-fails (no `clip_path`) is filtered out. The concat is
built from `rendered` only, so it shrinks below the plan. The compositor then
lays the full-length narration over the shorter concat and, when the voice
outruns the visuals, **freeze-extends the last frame**
(`services/media_compositors/ffmpeg_local.py:818`, `_compute_narration_pad_s`) —
so the viewer gets a shortened video with a frozen tail rather than a present
beat.

**Why the long lane stays A/V-tight once shots are complete:** the director's
target is estimated from the script at `word_count / 2.5 wps`
(`modules/content/stages/generate_video_shot_list.py:111`,
`_estimate_target_duration`, clamped `[20, 300]`). The `qa.audio` Check F
(`audio_duration_mismatch`, same 2.5 wps) fired **once in 30 days on the long
lane** — so long narration ≈ the plan. Making every long shot render therefore
gives a complete **and** tight video with no silent-tail risk.

## Goal & non-goals

**Goal:** every planned shot renders _something_, so the visual timeline always
equals the plan. On the long lane (narration ≈ plan) this yields a complete,
A/V-tight video; on the short lane it yields a complete shot set (the residual
short-lane frozen tail is a separate script-length bug, below).

**Non-goals (deliberately out of scope):**

1. **The too-long short script.** Check F fired **10× on the short lane, avg ratio
   3.86** — the actual short narration runs ~4× longer than
   `_estimate_short_duration`'s `[15, 45]` clamp, so the short "overshoot" is
   driven by the _script_ being 2–3 minutes of speech, not by dropped shots.
   Rescaling the video would only _mask_ that. Tracked as a fast-follow.
2. **Root-causing the image-gen wedge itself.** The ladder makes an outage
   non-fatal to the render; fixing the wedge is separate infra work.
3. **Narration-fit rescale of shot durations.** Not needed once shots are complete
   (long narration ≈ plan); it would also mask non-goal #1.

## Where this lives (the seam)

Two files, one clean new unit each:

- `services/video_renderers/shot_list_renderer.py` — the two-pass render
  (`_render_pass → _score_pass → _repair_pass → _finalize_pass`) produces
  `shot_results`, then line 1121 drops failures. The ladder slots in as a new
  **`_backfill_failed_shots` pass** between `_finalize_pass` and the concat build,
  so by line 1121 every shot has a `clip_path`. `_render_one_shot` is unchanged.
- `modules/content/atoms/_media_render.py:214-278` — the ship/redispatch gate. It
  currently keys off `shots_rendered / shots_total`; with the ladder that ratio is
  always 1.0, so the gate is re-keyed to the **real-source ratio**.

## Design

### 1. The fallback ladder

`_backfill_failed_shots(states, *, render_kwargs, site_config, pool)` iterates the
shot states whose render produced no `clip_path` and walks each through rungs
until one yields a clip. Ordering is deliberate — cheapest/most-independent first:

1. **Primary source** — already attempted in `_render_pass`/`_repair_pass`
   (unchanged: keeps today's pexels video→photo, hero→Ken-Burns, and
   pexels-miss→holdover behavior). A shot reaching the backfill pass has already
   failed rung 1.
2. **Cross-family substitute** — an **image-gen-family** shot that hard-failed
   (server wedged — the dominant, family-correlated failure) is retried as a
   **Pexels** clip using a query derived from the shot's `intent`. This converts a
   GPU/image-gen outage into _real footage_ rather than a card.
   - _Asymmetry (intentional):_ the reverse direction (a missed Pexels shot →
     image-gen still) is **not** done. Pexels already holds over on miss and
     rarely hard-drops; more importantly, image-gen-substituting a human-subject
     Pexels query would risk the no-AI-humans policy
     (`skills/content/video-director/SKILL.md`). Missed Pexels shots fall straight
     to rung 3.
3. **Guaranteed branded card** — `_render_brand_card()` (below). Pure ffmpeg,
   zero external deps — **physically cannot fail** — so a shot slot is never empty
   even in a total image-gen + Pexels outage.

Each backfilled shot's `duration_s` and `narration_offset_s` are preserved
unchanged, so the concat still sums to the plan and downstream offsets stay
aligned — no timeline recomputation.

**First-shot edge case:** shot 0 cannot hold over (no prior clip). Today a failed
shot 0 drops outright; under the ladder it lands on rung 2 or the card, so the
video never opens on a missing beat.

### 2. The guaranteed card — `_render_brand_card`

```
_render_brand_card(*, work_dir, duration_s, width, height,
                   wordmark: str, site_config) -> Path
```

Two-tier so the guarantee is airtight:

- **Preferred:** an ffmpeg `lavfi` dark-navy background + centered `{site_name}`
  wordmark via `drawtext` (brand palette from the documented dark-techno set —
  deep navy `#0A1A2F` field, cyan/white wordmark). Per the "plain card" decision,
  **wordmark only — no shot `intent` text** (avoids an LLM string reading
  awkwardly on screen).
- **Floor (cannot fail):** if the `drawtext` command errors (e.g. no fontconfig
  font on the worker image), fall back to a **text-less solid `color=` clip** of
  the same dimensions/duration. `color` needs no font and no filter that can be
  absent, so this rung always produces a valid MP4.

Held for the shot's `duration_s` at the render dimensions/fps. Gated by
`video_fallback_card_enabled` (default `true`); when disabled, a shot that can't
render still drops (today's behavior) — the switch exists for A/B and for the
"always ship, never card" preference.

### 3. Gate reframe — real-source ratio

The gate's _intent_ — "defer the whole render when infra is down, rather than ship
garbage" — is kept; only its _metric_ changes. `ShotListRenderResult` gains:

- `shots_substituted: int` — landed on rung 2
- `shots_carded: int` — landed on rung 3

`_media_render.py` computes `real_ratio = (shots_total - shots_carded) / shots_total`
and compares against a new setting **`video_render_min_real_source_ratio`**
(default `0.5`):

- `real_ratio >= threshold` → ship the complete video (advisory `shot_fallback`
  finding per carded shot so an isolated miss is visible).
- `real_ratio < threshold` → **reject + redispatch** (mostly-card render = real
  outage; defer until infra healthy), exactly as the current gate does — same
  `partial_render_rejected` finding path, re-keyed message.

Only rung-3 cards reduce `real_ratio`. **Holdovers count as real-source**: a
QA-rejected shot that holds over carries a real prior clip (`success=True`, has a
`clip_path`), so it never reaches the backfill pass and is a rung-1 outcome. This
preserves today's behavior (the current `shots_rendered` gate also counts
holdovers as rendered); holdover-heavy detection is explicitly _not_ added here.

The old `video_render_min_shot_ratio` becomes inert (`shots_rendered` is now
always `shots_total`). It is left **seeded-but-unread / deprecated** rather than
deleted — removing a seeded key would need scrubbing every seed source
(`baseline.seeds.sql` + `settings_defaults.py` + the generated extract JSON) or it
resurrects on boot and trips `settings_seed_drift_lint`. A one-line comment marks
it deprecated in `settings_defaults.py`.

### 4. Settings (→ `settings_defaults.py`, per the seed-in-baseline rule)

| Key                                  | Default                     | Meaning                                                                                                             |
| ------------------------------------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `video_fallback_card_enabled`        | `true`                      | Master switch for rung 3 (the guaranteed card). Off = legacy drop behavior.                                         |
| `video_render_min_real_source_ratio` | `0.5`                       | Below this fraction of real-source (non-card) shots, reject+redispatch.                                             |
| `video_render_min_shot_ratio`        | _(deprecated, left seeded)_ | Superseded by the real-source ratio; inert (ladder guarantees full render). Not deleted — avoids seed reseed-drift. |

Card colors default to brand-palette constants; promote to settings only if an
operator needs to tune them (YAGNI for now).

### 5. Observability

- **Finding:** advisory `shot_fallback` (info) per carded shot, dedup-keyed
  `shot_fallback:{task_id}:{shot_idx}`, so the ladder's activity is visible in the
  Findings board and the fallback rate is trackable.
- **Per-shot audit:** the existing `video_shot_rendered` `audit_log` rows gain a
  `rung` / `fill_source` field (`primary` | `substitute` | `card`) so the Pipeline
  / QA Rails dashboards can chart real-vs-card over time — the leading indicator of
  an image-gen/Pexels outage.

## Testing

Unit (host-run, matching the existing media-test pattern that skips in-container):

- `_render_brand_card` produces a valid MP4 at the requested duration/dims with
  **no network**; the text-less floor renders when `drawtext` is forced to fail.
- `_backfill_failed_shots` fills every empty slot and always terminates at the card;
  preserves `duration_s` / `narration_offset_s`; first-shot (idx 0) failure is
  backfilled, not dropped.
- Cross-family substitute: an image-gen-family failure attempts a Pexels query; a
  missed Pexels shot does **not** attempt image-gen (goes to card).
- Gate reframe (`_media_render`): rejects when `real_ratio < threshold` **even
  though every slot is filled**; ships with `shot_fallback` findings when a few
  cards but `real_ratio >= threshold`.
- `services.md` regen (new render-path surface) — required doc gate.

## Live validation (not prod dispatch)

Per the media-verify-in-process rule: prove the fix with an **in-process atom run
inside `prefect-worker`** (`build_container` + `render_shot_list` + `ffprobe`),
**not** a prod re-dispatch (re-dispatching a published post can re-upload to
YouTube). Force a rung-3 path by pointing `image_gen_server_url` at a dead URL and
confirm: (a) a complete MP4 whose duration equals the shot-list plan, (b) every
slot present, (c) `shots_carded` reflects the forced failures, (d) the gate
rejects when cards dominate. This is a render-side change, so it also validates on
a re-render of an existing post (re-render reads frozen scripts but re-runs the
renderer).

## Files touched

- `services/video_renderers/shot_list_renderer.py` — `_backfill_failed_shots`,
  `_render_brand_card`, `ShotListRenderResult.{shots_substituted,shots_carded}`
- `modules/content/atoms/_media_render.py` — gate re-keyed to real-source ratio
- `services/settings_defaults.py` — new settings; mark `video_render_min_shot_ratio` deprecated (left seeded)
- audit field `rung` on the per-shot `video_shot_rendered` record
- tests under `tests/unit/services/` + `docs/reference/services.md` (regen)

## Risks & tradeoffs

- **§3 changes when a render defers.** Net strictly better (stop shipping degraded
  _and_ stop redispatching ones that needed a single card), but a mostly-card
  render now waits for infra instead of shipping — consistent with "stability >
  speed." Flip `video_fallback_card_enabled` off to revert to drop-and-gate.
- **Cards are complete-but-bland.** Acceptable by design: the card is the
  outage-only floor, rung 2 keeps real footage flowing during single-family
  outages, and the `shot_fallback` finding surfaces when cards appear so an outage
  is caught rather than normalized.
- **`drawtext` font availability** on the worker image is unverified; the text-less
  color floor removes it from the critical path (the guarantee never depends on a
  font).

## Fast-follows (separate specs)

1. **Short-script length** — bring the narrated short script (and/or
   `_estimate_short_duration`) in line with the 15–45s retention window so the
   short lane is tight, not just complete.
2. **Image-gen wedge root cause** — the recurring host-port/boot-window
   unavailability that drives the mass shot failures the ladder now absorbs.
