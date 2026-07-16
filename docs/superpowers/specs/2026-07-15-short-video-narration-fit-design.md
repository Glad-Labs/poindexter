# Short-form video: fit visuals to narration, cap the script — design

**Date:** 2026-07-15
**Issue:** [Glad-Labs/poindexter#867](https://github.com/Glad-Labs/poindexter/issues/867) (parent epic #689)
**Status:** implemented 2026-07-16 (glad-labs-stack#2637). Live A/B proof (real
ffmpeg, 42s shot list vs 70s narration): fit=ON output 70.03s with 5 distinct
frames across the last 20s (moving tail); fit=OFF output 70.50s with 1 (the
frozen tail). `_fit_scene_durations` plan spanned the narration (70.00s, max
scene 5.0s ≤ 9s ceiling).

## Problem

Short-form (9:16) videos end with a **frozen final frame** held for the trailing
seconds. Root cause: the short lane sizes its visuals and its narration
independently and never reconciles them.

- **Visual timeline** — `short_shot_list.total_duration_s` is set by
  [`_estimate_short_duration`](../../../src/cofounder_agent/modules/content/stages/generate_video_shot_list.py)
  (`words / 2.5`, then **clamped to [15, 45]s**).
- **Narration audio** — [`media_render_narration`](../../../src/cofounder_agent/modules/content/atoms/media_render_narration.py)
  is the TTS of the **full, unclamped** `short_summary_script`.

When the script exceeds ~112 words (45s × 2.5 wps), the shot list caps at 45s
but the narration runs its full length. The compositor's
[`_compute_narration_pad_s`](../../../src/cofounder_agent/services/media_compositors/ffmpeg_local.py)
then **clones the final frame** to cover the overhang — that held frame is the
frozen tail.

Two compounding faults:

1. **Prompt vs. clamp disagree by design.** `_build_scene_prompt` asks the LLM
   for "60-second / ~150 words," but the clamp caps visuals at 45s. So even a
   fully compliant script guarantees a ~15s frozen tail.
2. **No reconciliation + script runaway.** Nothing trims the narration or
   extends the visuals to match, and the model routinely overshoots the word
   target.

### Evidence — 30 most-recent shorts (est. narration ÷ shot-list total)

| short script | est. narration | visuals (clamped) | ratio  |
| ------------ | -------------- | ----------------- | ------ |
| 103 words    | 41s            | 39s               | 1.05 ✓ |
| 145 words    | 58s            | 45s               | 1.29   |
| 164 words    | 66s            | 45s               | 1.47   |
| 209 words    | 84s            | 45s               | 1.86   |
| 394 words    | 158s           | 45s               | 3.50   |

Systematic: any script over ~112 words leaves a frozen tail; the median short
overshoots. Real short shot lists are **8–9 shots**, deliberately paced —
a ~2s hook opener, then 4–8s shots that escalate (e.g. `2.0, 5.0, 6.0, 5.0,
6.0, 6.0, 7.0, 8.0`).

## Goals / non-goals

**Goals**

- A short video's visuals always span its narration — **no frozen tail, no cut
  audio**, for any script length.
- Shorts stay short: script generation targets one coherent length and a
  runaway script is capped.
- Preserve the director's crafted per-shot pacing (hook + escalation) in the
  common case.
- Every tunable is a DB setting; no hardcoded counts in prompts.

**Non-goals**

- Changing the long lane (narration ≈ plan there; untouched).
- Trimming narration audio at render time (rejected — risks the mid-sentence
  cut #1874 fixed).
- A new director model or shot-list schema change.

## Design

Two complementary parts. Part 1 is the structural guarantee (Stage-2 renderer,
fixes existing posts on re-render); Part 2 keeps shorts short (Stage-1
generation, new runs only).

### Part 1 — renderer fits visuals to the actual narration (short lane only)

The narration's real duration is only known in Stage 2 (after TTS), so the
reconciliation lives in the renderer, where both the shot list and the rendered
narration audio exist.

**Where:** [`render_shot_list`](../../../src/cofounder_agent/services/video_renderers/shot_list_renderer.py)
gains an optional `narration_fit` config. [`render_from_state`](../../../src/cofounder_agent/modules/content/atoms/_media_render.py)
passes it **only for the short lane** (the `media.render_short_video` atom sets
a `narration_fit=True` flag; the long atom does not). Scoping to the short lane
keeps the long lane's legitimately-longer shots and deliberate pacing untouched.

**Algorithm** (applied after the shots render, before the `CompositionRequest`
is built, when `narration_fit` is on and a narration `audio_path` exists):

1. ffprobe the narration duration `narration_dur` (reuse the compositor's
   `_ffprobe_blocking` / `_parse_probe`; a shared probe helper). If unreadable →
   skip rescale (fall back to today's pad behavior; logged, not silent).
2. `shot_total = Σ rendered[i].duration_s`. If `shot_total <= 0` → skip.
3. `scale = narration_dur / shot_total`.
   - If `scale <= 1.0 + ε` → **no-op** (narration already fits; e.g. a
     comfortably-sized script). The compositor's small tail-pad handles the
     sub-second residual.
4. Else lay out the scenes to span `narration_dur`:
   - Per-shot target duration `d[i] = min(rendered[i].duration_s × scale,
video_short_max_shot_seconds)`. The `× scale` preserves the director's
     **relative** pacing (hook stays proportionally shortest); the `min(…, cap)`
     stops any single image being held too long.
   - Lay `d[i]` in order, accumulating time. **Cycle** back to shot 0 when the
     list is exhausted before reaching `narration_dur` (only happens when the
     per-shot cap bit — i.e. a large scale). Repeating a visual a couple times
     over a long short is normal shorts grammar.
   - Trim the final laid scene so the total lands at `narration_dur` +
     `narration_tail_pad_s` (the existing intentional ~0.5s tail).
   - Defensive guard: cap the scene count (e.g. 200) so a pathological
     narration can never loop unbounded.
5. Build `CompositionScene`s from the laid-out sequence (each references its
   shot's `clip_path`, so cycling is free — no re-render).

**Worked examples** (from real data):

- Common (post-Part-2): 8 shots `[2,5,6,5,6,6,7,8]` = 45s, narration 54s →
  scale 1.2 → `[2.4,6,7.2,6,7.2,7.2,8.4,9.6]` = 54s. No cap hit, no cycle,
  pacing preserved.
- Pathological (old runaway on re-render): 45s of shots, narration 158s →
  scale 3.5 → every shot caps at 9s → one pass ≈ 72s → cycle ~2.2× to fill
  158s at a steady ~7–9s cadence. No 28s hold, no freeze.

Captions (`short_caption_srt_path`) are timed to the narration audio, which is
unchanged, so rescaling visuals never desyncs them.

### Part 2 — cap the short script at generation (Stage 1)

**2a — one canonical target, parameterized prompt.** Introduce
`video_short_target_seconds` (default 45). Derive the word target
`target_words = round(video_short_target_seconds × _WORDS_PER_SECOND)`.

- [`_estimate_short_duration`](../../../src/cofounder_agent/modules/content/stages/generate_video_shot_list.py)'s
  upper clamp becomes `video_short_target_seconds` (was hardcoded 45), threaded
  in from `site_config`.
- [`_build_scene_prompt`](../../../src/cofounder_agent/modules/content/stages/generate_media_scripts.py)
  derives its "~N-second / ~M-word" ask from `video_short_target_seconds` and
  `target_words` (placeholder substitution) instead of the hardcoded "60s / 150
  words." This fixes the prompt-vs-clamp disagreement **and** satisfies the
  "no pinned counts in prompts" rule.

**2b — deterministic runaway cap.** Introduce `video_short_max_seconds`
(default 60); `max_words = round(video_short_max_seconds × _WORDS_PER_SECOND)`.
After `_parse_scene_output` yields `short_summary`, if its word count exceeds
`max_words`, trim to the **last complete sentence within `max_words`** (no
mid-sentence cut, no LLM call) and emit an advisory `short_script_trimmed`
finding (info severity — Findings board, non-paging) carrying original vs.
trimmed word counts. Scripts between `target_words` and `max_words` ride
untrimmed; Part 1 stretches the visuals to fit them (scale ≤ 1.33).

Regeneration was considered and rejected (latency/cost for marginal gain over a
firmer prompt + a deterministic trim — YAGNI).

### How the parts reconcile

Stage 1 caps the script to ≤ `video_short_max_seconds` and targets
`video_short_target_seconds`, so `short_shot_list.total ≈ 45s` and narration ≈
45–60s. Stage 2's renderer then rescales the visuals to the **actual** narration
(scale ≈ 1.0–1.33 — gentle, pacing preserved). Part 1 is the structural
backstop that absorbs any TTS-vs-estimate drift, so a freeze can never reappear
even if the estimate is off.

## Settings (all in `settings_defaults.py`, category `media`/`video`)

| key                            | default | purpose                                                        |
| ------------------------------ | ------- | -------------------------------------------------------------- |
| `video_short_target_seconds`   | `45`    | Canonical short target — prompt ask + shot-list clamp upper.   |
| `video_short_max_seconds`      | `60`    | Hard cap; a runaway script is trimmed to fit this.             |
| `video_narration_fit_enabled`  | `true`  | Master switch for Part 1 (short-lane visual fit-to-narration). |
| `video_short_max_shot_seconds` | `9`     | Per-shot ceiling in the rescale (no image held longer).        |

## Testing

- **Part 1 unit** (`test_shot_list_renderer.py`): scale ≤ 1 → no-op; scale ~1.2
  → proportional rescale sums to narration, pacing ratios preserved, no cap hit;
  large scale → per-shot cap enforced + sequence cycles + total spans narration
  - scene-count guard; ffprobe-unreadable → graceful skip. Narration-fit gated
    off → today's behavior.
- **Part 2 unit** (`test_generate_media_scripts.py` / video-shot-list tests):
  clamp upper follows `video_short_target_seconds`; prompt string carries the
  derived numbers, no hardcoded 150/60; trim fires only above `max_words`, cuts
  on a sentence boundary, emits the finding; under-`max_words` untouched.
- **Live proof** (in-process, real ffmpeg): synth a >150-word narration + a 45s
  shot list, render the short, ffprobe the output — assert
  `abs(output_dur − narration_dur) ≤ tail_pad` (no frozen tail) and no single
  scene exceeds the ceiling.
- Full regression across `atoms/`, `video_renderers/`, and the settings
  structural tests; silent-except ratchet + ruff clean.

## Rollout / scope

- **Part 1** helps existing posts on re-render (renderer-side); **Part 2** is
  Stage-1, so it only shapes **new** runs (a re-render reads the frozen Stage-1
  script — the known re-render≠regen gotcha).
- An old post with a frozen runaway script, re-rendered, will render **long but
  un-frozen** (Part 1 cycles to fill). Acceptable and transitional; new runs
  stay short via Part 2. We deliberately do **not** trim narration audio at
  render time.
- One PR against `glad-labs-stack`, ~5 TDD tasks, closes poindexter#867.

## Risks

- **Long-lane regression:** mitigated by scoping Part 1 to the short lane
  (explicit `narration_fit` flag, not a global change).
- **Over-aggressive trim:** `video_short_max_seconds` (60s) leaves generous
  headroom over the 45s target, so trims are rare and only bite true runaways;
  the advisory finding makes trim frequency observable.
- **ffprobe failure:** falls back to today's pad behavior (a bounded freeze),
  never a crash.
