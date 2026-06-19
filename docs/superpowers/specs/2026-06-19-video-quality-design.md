# Video Quality Layer — director self-critique + per-shot vision-QA + hero shots + SFX

**Status:** Design — pending operator review (2026-06-19).
**Scope:** Raise rendered-video quality from Phase-1 "functional" to "genuinely high quality" by closing the open scene-selection loop and adding hero generative shots + a cue-based SFX layer — all as extensions of the already-shipped `media_pipeline`, not a rebuild.
**Parent epic:** [Glad-Labs/poindexter#689](https://github.com/Glad-Labs/poindexter/issues/689) (media_pipeline redesign). This advances its Phase-3 items #687 (SFX) + #669 (generative source) and adds a new per-shot quality loop not previously specced.
**Design doc it builds on:** [`docs/architecture/video-pipeline-redesign.md`](../../architecture/video-pipeline-redesign.md).

---

## 1. Problem

Phase 1 of the media pipeline renders 16:9 long + 9:16 short video from an LLM director's shot list (slideshow SDXL stills + Pexels stock), with narration, ambient bed, captions, QA gates, and YouTube distribution. It works, but the quality ceiling is capped by three gaps:

1. **The director is generate-and-pray.** [`generate_video_shot_list.py`](../../../src/cofounder_agent/modules/content/stages/generate_video_shot_list.py) emits a `visual_prompt`/stock-query per beat on a _standard-tier_ local model, and the result ships with no check that the picture matches the beat, looks good, or is on-brand. `media_qa` only validates mechanical things (human-detection, caption presence, A/V sync) — never "is this the _right_ visual?"
2. **No motion.** Every shot is a still (Ken Burns) or stock clip. There's no way to spend extra effort on a few high-impact beats.
3. **No sound design.** Only a continuous ambient music bed (#679) exists; no discrete sound effects on transitions/emphasis.

## 2. Decisions (from the 2026-06-19 brainstorm)

- **Visual ceiling = Hybrid.** Polished motion-graphics base (stylized SDXL/Z-Image stills + Ken Burns + curated Pexels stock, on-brand palette) with **1–3 generative "hero" shots** per long video for impact. Not full generative video (impractical/uncanny on a single 5090 for 3–8 min explainers); not slideshow-only.
- **Scene quality = two loops.** A **plan-review** pass (director self-critiques its shot list before render) **and** a **render-check** (per-shot vision-QA scores the rendered frame and regenerates or falls back on a miss). This is the centerpiece — it turns generate-and-pray into a closed loop.
- **Hero renderer = Wan 2.2 `TI2V-5B`** behind the existing source seam. Verified current latest open Wan (the "Wan 2.7" in SEO blogs does not exist — the official `Wan-Video` GitHub org has only 2.1/2.2). Apache-2.0, in-family bump to the existing `wan-server`, 5B coexists with Ollama without GPU swap-thrash. The 14B variant and LTX-2.3 (native audio+video, custom revenue-capped license) are weight-swaps behind the same seam if 5B underwhelms.
- **SFX = director callouts only.** SFX fire **only** where the director intentionally places an `sfx_cue` on a beat that earns it — no mechanical whoosh on every transition (rejected as too random). Placed via ffmpeg `adelay` into the existing mix; clips from a curated royalty-free library + Stable Audio Open for stings. Master switch `video_sfx_enabled` defaults **off**, so "no SFX at all" is the out-of-the-box state. One layer covers slideshow, stock, _and_ hero shots.
- **ComfyUI = shelved.** It's "hands" (render primitives), which already exist and work; it replaces none of the "brain" (director, QA loop, gates, orchestration) where the actual effort lives. Kept as an _optional_ future render-layer consolidation behind the source seam — a separate, swappable sub-project, GPL-safe only as an HTTP sidecar (never vendored).

## 3. Architecture — four bounded pieces on the existing spine

The `media_pipeline` graph and `canonical_blog` director are unchanged in shape; we add/extend four things. Nothing here touches the gate model, distribution, or reconciliation.

### 3.1 `review_shot_list` — director self-critique (Stage 1)

A new step after [`generate_video_shot_list`](../../../src/cofounder_agent/modules/content/stages/generate_video_shot_list.py) in the `canonical_blog` graph. The director critiques **its own** shot list against the script and revises **once**:

- **Coverage** — does every key narration beat have a shot?
- **Variety** — break runs of near-identical visual intents (beyond the mechanical same-source pacing already enforced by `_reconcile_shot_list`).
- **Hero selection** — mark the **1–3** highest-impact beats `source: generative` (bounded by `video_hero_shots_max`, default 3).
- **On-brand** — nudge prompts toward the brand palette/style.

Implementation notes:

- A **distinct graph node**, not a second call inside the director, so `atom_runs`/audit captures the critique pass independently.
- Runs on a **writer-grade model**, not standard tier. `video_director_model` is already a DB key (today resolves to the weak standard tier via `resolve_tier_model`); point it at the writer model (gemma-4-31B).
- Output is the same `VideoShotList` schema ([`schemas/video_shot_list.py`](../../../src/cofounder_agent/schemas/video_shot_list.py)), re-validated + re-reconciled. Failure is non-halting (fall back to the unreviewed list).
- In Stage 1 → the reviewed plan is what the human approves at **Gate 1**.

### 3.2 Per-shot vision-QA loop — render-check (Stage 2)

The integration point is [`services/video_renderers/shot_list_renderer.render_shot_list`](../../../src/cofounder_agent/services/video_renderers/shot_list_renderer.py) (the real per-shot loop; the `media.render_long_video`/`render_short_video` atoms are thin wrappers over it via `_media_render.render_from_state`). The loop gains a verify-and-repair step:

```
for shot in reviewed_shot_list:
    asset = resolve_source(shot)            # slideshow · stock · generative(hero)
    frame = render(asset)
    score = vision_qa(frame, shot)          # reuses modules/content/atoms/qa_vision.py
        → match-to-beat? on-brand palette? not garbled / text-salad?
    if score < video_shot_qa_threshold:
        regenerate (new seed / critic-revised prompt)   # up to video_shot_qa_max_retries (default 2)
        else fallback: stock query → holdover crossfade  # never ship a bad shot
    emit finding(kind="shot_quality_fallback") on fallback
```

- **Reuses the `qa.vision` rail** ([`qa_vision.py`](../../../src/cofounder_agent/modules/content/atoms/qa_vision.py)) — same vision model already scoring featured images and doing caption re-captioning. Add a per-shot scoring entry point that takes a frame + the shot's `narration_segment`/`visual_prompt`/intent.
- **Bounded** — `video_shot_qa_max_retries` caps regeneration; then a deterministic fallback chain (stock → holdover) guarantees termination. No infinite loops.
- **Fail-soft + visible** — a fallback is a `finding` (per redesign §9 "never silently ship a degraded video"), surfaced on the Findings dashboard.
- **Scoped** — gate the whole loop behind `video_shot_qa_enabled` (default true) so it can be disabled per-niche.

### 3.3 Wan 2.2 hero-shot source atom (Stage 2)

When `shot.source == "generative"`, `resolve_source` routes to the generative renderer instead of SDXL/Pexels; the result still passes through the §3.2 vision-QA gate like any other shot.

- **Renderer:** the existing [`scripts/wan-server.py`](../../../scripts/wan-server.py) container, weights bumped to **`Wan-AI/Wan2.2-TI2V-5B`** (Apache-2.0). Image-to-video: animate the shot's stylized SDXL still (keeps brand consistency) into a short clip.
- **Budget:** `video_hero_shots_max` (default 3) caps count; each is GPU-scheduled (`gpu.lock`) against Ollama, run async post-publish (the pipeline already does). A hero render failure → fall back to the still (Ken Burns), emit a finding.
- **Seam:** `wan_server_url` + a `generative_video_model` DB key so the renderer/model is swappable (14B, or LTX-2.3 later) without code changes.

### 3.4 SFX cue layer (Stage 2 composition)

Extends [`services/media_compositors/ffmpeg_local.py`](../../../src/cofounder_agent/services/media_compositors/ffmpeg_local.py) (which already does multi-input `amix` with per-input gain for the #679 ambient bed). For each cue, `adelay` a clip to its offset and fold it into the existing mix.

- **Cue source — director callouts only:** the director emits intentional `sfx_cue: {type, t, intensity}` cues on the **specific shots that warrant a sound**, as a new field on `Shot` (it already times the shot list, so offsets are free). There is **no** automatic per-transition SFX — that was rejected as too mechanical/random. A beat with no cue gets no sound, so "nothing where nothing's needed" is the natural behavior, and `video_sfx_enabled=false` turns the layer off entirely.
- **Clip source:** a small curated royalty-free SFX library (keyed by `sfx_cue.type`) for one-shots; [`audio_gen_service`](../../../src/cofounder_agent/services/audio_gen_service.py) (Stable Audio Open) for longer stings/risers.
- **Bounded:** `video_sfx_max_per_min` caps how many cues the director can land per minute, so SFX stays sparse even if it over-salts.
- **DB-tunable:** `video_sfx_enabled`, `video_sfx_volume_dbfs`, `video_sfx_library`, `video_sfx_max_per_min`.
- Covers slideshow, stock, AND hero shots uniformly — one SFX system.

## 4. Data flow

```
Stage 1 (canonical_blog):
  generate_video_shot_list → review_shot_list → [Gate 1 human approves reviewed plan]
        shot_list now carries: per-shot source (incl. ≤3 generative), refined prompts, optional sfx_cue[]

Stage 2 (media_pipeline, inside shot_list_renderer):
  for shot: resolve_source → render → qa.vision → regen/fallback   ← §3.2 + §3.3
  compose: shots + transitions + narration@offset + ambient bed + SFX cues + captions  ← §3.4
  → media_qa → [Gate 2 human] → distribute
```

## 5. Configuration (all DB-backed, `settings_defaults.py`)

| Key                         | Default                 | Purpose                                                        |
| --------------------------- | ----------------------- | -------------------------------------------------------------- |
| `video_director_model`      | writer model            | director + critique model (was standard tier)                  |
| `video_hero_shots_max`      | `3`                     | cap on generative hero shots per long video                    |
| `generative_video_model`    | `Wan-AI/Wan2.2-TI2V-5B` | hero-shot model (swappable)                                    |
| `video_shot_qa_enabled`     | `true`                  | master switch for the render-check loop                        |
| `video_shot_qa_threshold`   | `qa.vision` pass mark   | min vision score to accept a shot; calibrate on sample renders |
| `video_shot_qa_max_retries` | `2`                     | regenerations before fallback                                  |
| `video_sfx_enabled`         | `false`                 | master switch — off = no SFX; on = director callouts           |
| `video_sfx_volume_dbfs`     | `-18`                   | SFX level under the voice                                      |
| `video_sfx_max_per_min`     | `4`                     | cap on director SFX callouts per minute (keep it sparse)       |
| `video_sfx_library`         | (curated set)           | SFX clip source (royalty-free library + Stable Audio stings)   |

## 6. Error handling

Every new step is **fail-soft and visible** — degrade, never crash, never silently ship degraded:

- `review_shot_list` failure → use the unreviewed list (non-halting).
- vision-QA below threshold → bounded regenerate → stock → holdover; each fallback emits a `finding`.
- hero render failure → fall back to the still; emit a finding.
- SFX/library failure → drop the cue, keep the video; log at warning (not silent — above the alerting bar, per the silent-except ratchet).
- A partial render (e.g. 3 of 8 shots) emits a finding and does **not** ship (redesign §9).

## 7. Observability

- `atom_runs` captures `review_shot_list` and the per-shot loop outcomes (free from the graph_def model).
- New `finding` kinds: `shot_quality_fallback`, `hero_render_fallback` → Findings dashboard.
- Video-render Grafana panels (#678) gain per-source success rate incl. generative + vision-QA pass/regenerate/fallback counts.

## 8. Testing (contract tests, per "docs + tests default")

- `review_shot_list`: revises coverage/variety, marks ≤`video_hero_shots_max` hero shots, schema-valid, non-halting on failure.
- Vision-QA loop state machine: accept ≥ threshold; regenerate < threshold up to cap; fallback chain (stock → holdover) terminates; finding emitted on fallback.
- Hero source atom: `source == generative` routes to wan-server; failure falls back to still + finding.
- SFX (director callouts): `adelay` offsets match the cue timings; **no** SFX emitted when the director emits no cues; the `video_sfx_max_per_min` cap is enforced; volume honors the DB key; covers all three sources.
- Determinism where claimed: same shot list + seeds → same fallback decisions.

## 9. Out of scope (explicitly deferred)

- ComfyUI render-layer consolidation (separate optional sub-project, behind the seam).
- LTX-2.3 / Wan 14B (swappable later via `generative_video_model`).
- Generative for _all_ shots (hybrid = a few hero shots only).
- Audio-native QA model (Qwen2-Audio, #1193) — stays deferred until the model's available.
- TikTok/Reels distribution (#685/#686) — gated behind the YouTube proving ground.

## 10. Rollout (sequencing — each independently shippable)

1. **Director quality** — `review_shot_list` atom + bump `video_director_model` to writer-grade. Immediate quality lift, lowest risk, no render changes.
2. **Vision-QA loop** — the render-check in `shot_list_renderer`. The centerpiece; reuses `qa.vision`.
3. **SFX layer** — director-callout `sfx_cue` → `adelay` into `ffmpeg_local` + curated library; defaults off (`video_sfx_enabled`).
4. **Hero shots** — wire `Wan2.2-TI2V-5B` into `wan-server` as the `generative` source atom.

Order is deliberate: 1–2 lift quality on the existing slideshow/stock path before adding the heavier generative + sound-design surface in 3–4.
