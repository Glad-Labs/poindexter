# ComfyUI video provider — Wan 2.2 14B hero clips

**Status:** shipped 2026-08-15, dark-launched (`video_generative_provider`
defaults to `wan21`).

## Why

The 2026-08-15 spike root-caused the "hero slop" (palette inversion, content
morphing away from the init still) to the **model + sampler config**, not the
prompts:

| Animator                                                | Same init + same production prompt                      | Time @ 832×480 |
| ------------------------------------------------------- | ------------------------------------------------------- | -------------- |
| wan-server (5B, 50 steps, diffusers repo-default shift) | palette inverts, hallucinates objects by 2.5s           | ~147s          |
| 5B via ComfyUI (shift 8, uni_pc, 20 steps)              | palette holds, still hallucinates content (grew a hand) | 150s           |
| **14B fp8 via ComfyUI, 20 steps**                       | composition/palette/style all hold                      | 468s           |
| **14B fp8 + lightx2v 4-step LoRA**                      | near-14B quality                                        | **123s**       |

The renderer's prompts were already fine — `_compose_hero_wan_prompt` appends
director-authored motion language, and the baseline slopped _with_ it. The 4-step
LoRA configuration is faster than the 5B sidecar while being in a different
quality class, so it is the provider's default regime.

## Shape

- **`services/video_providers/comfyui.py`** — `ComfyUIProvider`
  (`VideoProvider` protocol, `kind="generate"`, i2v-only by design). Speaks
  ComfyUI's REST API: `/upload/image` (init still in), `/prompt` (API-format
  graph), `/history/{id}` (poll), `/view` (MP4 out). **No shared bind mounts
  with the worker** — everything crosses the HTTP boundary.
- **Speech path (talking heads, 2026-09-14)** — the same provider renders
  **Wan 2.2 S2V 14B** when the caller passes `config["audio_path"]`: the init
  still is the presenter reference, the speech file goes up through the same
  `/upload/image` endpoint (ComfyUI's input store is type-agnostic), wav2vec2
  encodes it, and the clip runs in 77-frame chunks chained with
  `WanSoundImageToVideoExtend` until the audio is covered (or
  `video_comfyui_s2v_max_chunks` stops it — then `metadata.audio_truncated`
  is true). Details in [Speech-to-video](#speech-to-video-talking-heads).
- **Selection seam** — `shot_list_renderer._render_generative_clip` reads
  `video_generative_provider` per clip: `wan21` (default) or `comfyui`.
  Flipping is a settings change, no deploy. Provider failures surface
  `last_error` into the `hero_render_fallback` finding (poindexter#996), and
  the existing fallback ladder (Ken Burns still) is unchanged.
- **Sidecar** — `scripts/Dockerfile.comfyui` (pinned release tag, torch
  2.8/cu128 Blackwell base, **core nodes only** — custom nodes are ComfyUI's
  malware surface; adding any requires a security review). Compose service
  `comfyui` behind `--profile comfyui`, port bound to `127.0.0.1:8188` only
  (the API is unauthenticated), `--reserve-vram` headroom for the desktop.
- **GPL-3 boundary** — ComfyUI is consumed strictly over HTTP as a sidecar,
  never vendored or imported; the image clones the pinned tag at build time.
  (Same boundary the 2026-06-19 video-quality design specified.)
- **VRAM contract** — `gpu_scheduler._unload_comfyui` posts
  `/free {"unload_models": true, "free_memory": true}`, **declining while
  `/queue` shows work** (the #3094 lesson: a running renderer is not
  reclaimable). It sits last on `dispatch_media_pipeline`'s reclaim ladder
  and rides the renderer's between-lanes clear (`_clear_wan_for_stills`).
  Cold-boot is handled provider-side by a `/system_stats` ready-wait
  (`video_comfyui_ready_wait_s`, the #3102 shape).

## Settings (all `app_settings`)

| Key                                                                           | Default                          | Meaning                                                                                                                                                          |
| ----------------------------------------------------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `video_generative_provider`                                                   | `wan21`                          | Animator: `wan21` or `comfyui`                                                                                                                                   |
| `video_comfyui_server_url`                                                    | `http://comfyui:8188`            | Sidecar URL                                                                                                                                                      |
| `video_comfyui_steps` / `video_comfyui_cfg`                                   | `4` / `1.0`                      | Sampler regime (lightx2v 4-step). Quality tier: `20` / `3.5` + LoRA off                                                                                          |
| `video_comfyui_use_lightning_lora`                                            | `true`                           | Wire the distill LoRAs                                                                                                                                           |
| `video_comfyui_shift`                                                         | `5.0`                            | ModelSamplingSD3 (official 14B i2v template value)                                                                                                               |
| `video_comfyui_length_frames` / `video_comfyui_fps`                           | `81` / `16`                      | Model-native ~5s profile (caller fps ignored — compositor conforms/loops)                                                                                        |
| `video_comfyui_negative_prompt`                                               | canonical Wan negative (Chinese) | Model-native negative                                                                                                                                            |
| `video_comfyui_{high,low}_model`, `_text_encoder`, `_vae`, `_lora_{high,low}` | repackaged filenames             | Weight swaps are settings-only                                                                                                                                   |
| `video_comfyui_timeout_s`                                                     | `900`                            | End-to-end render budget (20-step 960×544 measured 644s)                                                                                                         |
| `video_comfyui_ready_wait_s`                                                  | `90`                             | Cold-boot wait before first submit                                                                                                                               |
| `video_comfyui_workflow_override_json`                                        | `''`                             | Full graph swap: API-format JSON with `__PROMPT__`/`__WIDTH__`/… placeholders, substituted typed                                                                 |
| `video_comfyui_s2v_model` / `_audio_encoder`                                  | `wan2.2_s2v_14B_fp8_scaled…` / `wav2vec2_large_english_fp16…` | Speech-path weights (same repackaged repo + its `audio_encoders/`)         |
| `video_comfyui_s2v_steps` / `_cfg` / `_shift` / `_sampler`                     | `20` / `6.0` / `8.0` / `uni_pc`  | S2V sampler regime (spike-validated: identity + lip shapes hold)          |
| `video_comfyui_s2v_length_frames`                                             | `77`                             | One S2V chunk at the model's 16 fps (4.8 s)                               |
| `video_comfyui_s2v_max_chunks`                                                | `6`                              | Longest clip = chunks × 4.8 s (~29 s); longer speech is flagged truncated |
| `video_comfyui_s2v_timeout_per_chunk_s`                                       | `900`                            | Render budget per chunk (an idle 5090 needs ~420 s)                       |
| `video_comfyui_s2v_workflow_override_json`                                    | `''`                             | S2V graph swap; same placeholders plus `__AUDIO__`                        |
| `comfyui_ram_recycle_{enabled,watermark_gb,cooldown_minutes}`                 | `true` / `20` / `60`             | Brain-side host-RAM recycle: queue-idle-verified `docker restart` when the sidecar's PID-1 RSS+swap crosses the watermark ([details](video-render-vram-gate.md)) |

## Operator runbook (enable on a host)

1. **Weights** (~38GB) into `~/.poindexter/comfyui/models/` (ComfyUI layout),
   from `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` `split_files/`:
   - `diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors`
   - `diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors`
   - `text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`
   - `vae/wan_2.1_vae.safetensors`
   - `loras/wan2.2_i2v_lightx2v_4steps_lora_v1_{high,low}_noise.safetensors`
   - **Talking heads (optional, +17 GB):**
     `diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors` and
     `audio_encoders/wav2vec2_large_english_fp16.safetensors`. The compose
     service mounts `audio_encoders/` read-only; the image ships that dir
     empty, so without the mount the speech path fails at ComfyUI validation
     naming the missing encoder.
2. `docker compose -f docker-compose.local.yml --profile comfyui up -d --build`
3. Verify: `curl -s localhost:8188/system_stats | head -c 200`
4. Flip: `poindexter settings set video_generative_provider comfyui`
5. Watch the next render's hero shots (QA Rails / hero_render_fallback
   findings). Roll back by flipping the setting to `wan21`.

VRAM reality on a 32GB card shared with a desktop: 832×480 peaks ~25-29GB,
960×544 ~28GB, **1280×720×81f does not fit** (spike OOM at 31.9GB) — full
720p needs block-swap custom nodes (security review first) or an idle card.

## Speech-to-video (talking heads)

The 2026-09-14 spike rendered a photoreal and a flat-vector presenter from a
Qwen Image portrait plus 4.8 s of the pipeline's narration: identity, light
and background held for every frame, mouth shapes tracked the speech, and the
stylized reference did not drift toward photoreal. Cost was the finding —
**~420 s and 31.9 GB peak per 4.8 s chunk on an otherwise idle 5090**, so a
render owns the whole card for its duration.

How the provider does it (`_fetch_speech`):

1. `config["audio_path"]` selects the path; `config["image_path"]` is the
   presenter reference (a portrait, not a scene still).
2. Duration comes from `config["audio_duration_s"]` when the caller knows it
   (the shot-list renderer does), else an ffprobe via the shared
   `podcast_sting_mixer.probe_duration_s`. Unknown duration fails loud — a
   one-chunk guess would ship a clip that stops mid-sentence.
3. `s2v_chunks_for(audio_s, length, fps, max_chunks)` picks the chunk count;
   `build_s2v_graph` wires chunk 1 as `WanSoundImageToVideo` and every later
   chunk as `WanSoundImageToVideoExtend` fed the previous chunk's sampled
   latent (its motion reference), decodes each chunk, concatenates with
   `ImageBatch`, and `CreateVideo` muxes the **full** speech track back in.
4. Upload still → upload audio → `/prompt` → `/history` (budget =
   `timeout_per_chunk_s × chunks`) → `/view`, the same ladder as i2v; every
   failure returns `[]` with `last_error` naming the step.

Result metadata: `s2v: true`, `model: wan2.2-s2v-14b-fp8`, `chunks`,
`chunk_frames`, `audio_seconds`, `audio_truncated`, `sampler`. `source` stays
the plugin name — mode is metadata, not a new provider.

**Multi-chunk is wired per the node contract and unit-tested for wiring; the
spike exercised single chunks.** The first multi-chunk render should be watched
for a seam at the 4.8 s boundary (the Extend node's motion reference is what
prevents it).

In the pipeline the caller is `shot_list_renderer._render_presenter_clip`
(`source: "presenter"`, see [media-personas](media-personas.md)); ad-hoc
callers pass the same two files.

Operational rules that follow from the numbers:

- Admit the render through the GPU scheduler with the whole card as the
  reservation. The spike's 28 GB left Ollama primary 3.9 GB and the live
  integration tests on the self-hosted runner failed with a 500 (2026-09-14).
- Keep the reference portrait in the niche's media policy
  (`niche.<slug>.media.style_policy` / `human_subjects`, see
  `docs/architecture/media-subject-policy.md`); a photoreal presenter on a
  niche that forbids people is a policy error upstream, not a render error.
- Publishing a realistic synthetic presenter to YouTube needs
  `status.containsSyntheticMedia`, which `publish_adapters/youtube.py` does
  not send yet — that gate lands with the presenter-persona work, before any
  such upload.

## Frame-rate conformance: interpolate, don't duplicate

Wan renders at its native **16 fps** (S2V; hero i2v through ComfyUI) and the
compositor assembles the timeline at **30 fps**. Left alone, ffmpeg conforms
the rate by duplicating frames, so every talking-head and hero shot played
with each frame shown twice — visibly stuttery next to the 30 fps stock and
Ken Burns scenes (operator feedback 2026-09-17, "do the interpolation anyway
for better quality").

Since stack#3841 `_render_generative_clip` motion-interpolates every clip it
produces **in place, right after the provider writes it**, with ffmpeg's
`minterpolate` (motion-compensated, bidirectional). Doing it there — at the
provider's small native geometry, before the compositor scales to 1080p — is
what keeps it cheap: measured 27 s of CPU for a 9.6 s 960x544 clip (154 → 286
frames, 32 cores). In the compositor at 1080p the same filter would cost
minutes per scene, which is why it does *not* live there. Stock clips (24–30
fps already) never pass through this path.

Settings: `video_clip_interpolation_enabled` (default true),
`video_clip_interpolation_target_fps` (30 — must match the compositor's output
rate), `video_clip_interpolation_filter` (the chain, `{fps}` substituted; swap
in `framerate=fps={fps}` for a cheaper blend, or a RIFE node later),
`video_clip_interpolation_timeout_s` (600). Best-effort by contract: the
interpolated file replaces the original only after ffmpeg exits 0 with output
on disk; any failure keeps the provider's clip and logs why.


### The interpolator: RIFE, with ffmpeg as the fallback

`minterpolate` warps pixels along estimated motion vectors. Where estimation
fails — on a talking head that is the mouth and teeth — the result **morphs**
(operator, 2026-09-18: "a weird morphing of the image"). It is inherent to
block-matching motion compensation, not a tuning problem: `mi_mode=blend`
removes the warping but ghosts moving edges, and `mi_mode=dup` is the judder we
added interpolation to remove.

Since stack#3862 the renderer prefers the **RIFE sidecar** (`rife-server`,
`scripts/rife-server.py`), which predicts the intermediate frame with a learned
flow model. Notes that matter:

- **It is a sidecar, not a ComfyUI node.** `Dockerfile.comfyui` is deliberately
  core-nodes-only ("custom nodes are ComfyUI's malware surface") and RIFE ships
  as a custom node pack. A sidecar keeps that boundary *and* serves every clip
  the renderer makes, including hero i2v from the `wan21` provider.
- **Weights and architecture are one pinned, MIT-licensed HF repo**
  (`TensorForger/RIFE-safetensors`, RIFE v4 / ECCV2022-RIFE, © Megvii), 12 MB,
  vendored into the image at build time — never into the repo tree, the same
  posture as the pinned ComfyUI clone.
- **The model only does midpoints.** An arbitrary rate change is recursive
  bisection to a power-of-two dense grid, then a nearest-frame resample. The
  grid deliberately overshoots the target by `RIFE_DENSE_MULTIPLE` (2.0):
  stopping at 32 fps for a 30 fps ask leaves up to 15.6 ms of timing error,
  nearly half an output frame, which reads as judder; 64 fps halves it to
  7.8 ms for 3 model calls per source pair instead of 1.
- **It never squats the render card.** 12 MB, unloads after 5 minutes idle,
  and honours `/unload` (soft and hard) like every other sidecar.

`video_clip_interpolation_engine`: `auto` (RIFE when it answers, else ffmpeg),
`rife` (RIFE or leave the native rate — an operator who would rather ship 16 fps
than morphed faces), `ffmpeg` (block matching only). The fallback direction is
deliberate: an ffmpeg-interpolated clip is a quality regression, a missing clip
is a lost shot.

## Presenter motion register: three settings, no code

Operator feedback 2026-09-17: the talking head "doesn't look natural" — too
animated. The S2V model takes its motion register from three places, all
`app_settings`:

1. **`video_presenter_negative_prompt`** (stack#3840) — the presenter's own
   negative. The shared `video_comfyui_negative_prompt` is the canonical Wan
   negative for hero i2v clips and *penalises stillness* (静态 / 静止 /
   静止不动的画面): exactly the pressure an illustration needs and exactly the
   wrong one for a person talking to camera, whom it pushes into head-bobbing
   and exaggerated expressions. The presenter default keeps the quality and
   anatomy terms, drops the three anti-stillness terms, and names the
   talking-head failure modes (夸张的表情, 摇头晃脑, 大幅度的头部动作, 手势, 抖动).
   Empty inherits the shared negative. Chinese on purpose — the model was
   trained against a Chinese negative and an English one is measurably weaker.
2. **`video_presenter_render_prompt`** — motion language in the positive
   prompt is read literally by Wan, and **this lever dominates**: the default
   asks for "calm and composed, nearly still, minimal head movement, relaxed
   shoulders, no hand gestures, locked-off camera".
3. **`video_comfyui_s2v_cfg`** (default 4.0, vs 6.0 on the i2v lane) and
   **`video_comfyui_s2v_shift`** (8.0) — CFG amplifies prompt adherence *and*
   motion amplitude, so a talking head wants the calm end of the range.

**Measured 2026-09-17** (one portrait, one 9.6 s line, one seed; motion index =
mean mouth/head-region frame delta, tool `headcmp/motion_index.py`):

| variant | motion index | peak |
| --- | --- | --- |
| prior defaults ("natural facial expressions, subtle head movements", cfg 6.0) | 6.57 | 28.2 |
| + presenter negative prompt only | 6.38 | 30.0 |
| + calm wording + cfg 4.0 (**today's defaults**) | 5.59 | 24.8 |

The negative prompt alone barely moved it. **Asking for stillness beat
forbidding motion by roughly 4x** — reach for the positive prompt first when
tuning register, and A/B on the same portrait + narration + seed
(`headcmp/s2v_ab.py`) rather than changing several knobs at once.

## Audio pace: a chunk eats 5.000 s of speech but renders 4.8125 s of video

Three numbers, all read from ComfyUI's `comfy_extras/nodes_wan.py` rather than
inferred:

- `latent_t = ((length - 1) // 4) + 1` — **20** for the default 77 frames.
- `batch_frames = latent_t * 4` — **80** audio-embed buckets consumed per
  chunk, and `get_audio_embed_bucket_fps` samples those buckets at
  `target_fps = fps` (16), so one chunk eats **5.000 s** of speech.
- the VAE decodes `latent_t` latents into `(latent_t - 1) * 4 + 1` = **77**
  frames — **4.8125 s** of video (confirmed on render e4ccafa2: 6 chunks
  produced 461 frames).

So the mouth runs **80/77 = 3.9 % fast**, and the error is *cumulative within a
clip*: ~0.41 s by the end of a 10.6 s opening, ~0.96 s by the end of a 24.5 s
closing. That is exactly the asymmetry the operator reported — "the intro
presenter is pretty good, the final is a little bit off still" — and it is why
the defect hid for so long: every short clip looked fine.

**No choice of `length` fixes it.** Video frames are always `4·latent_t − 3`
while the audio window is always `4·latent_t`; the slip is exactly three frames
per chunk for every length. Since stack#3848 the provider instead **stretches
the conditioning audio** by that factor (`atempo`, pitch preserved) before
upload, so chunk *k* covers real speech `[(k−1)·length/fps, k·length/fps]` —
precisely the video it renders. The clip's own audio track stays real-time: a
second `LoadAudio` node feeds `CreateVideo` whenever the two differ, so a
viewer of the standalone clip hears unmodified speech. Setting:
`video_comfyui_s2v_audio_pace_correction_enabled`. Best-effort — a failed
stretch conditions on the original audio, because an uncorrected clip is the
old behaviour while a missing clip is a lost shot.

This also makes `s2v_chunks_for` honest: it counts chunks at the video rate,
which is only the speech rate once the audio is corrected.

**How it was caught.** Lip-sync cross-correlation was too noisy to see it
(r ≈ 0.1). The decisive evidence was arithmetic read from the node's source,
plus a CPU-only three-arm test on the already-rendered clip: resampling the
video by 80/77 raised its correlation with the audio (+0.056) while the inverse
lowered it (−0.008) — `headcmp/stretch_test.py`. Reach for a transform test on
existing output before spending a GPU hour on a re-render.

## Chunk chaining: the Extend node reads its audio offset off the latent

Wan 2.2 S2V renders 77 frames (4.8 s) per chunk; longer speech is chained with
`WanSoundImageToVideoExtend`. That node has no "offset" input — it derives
where this chunk sits in the speech from the **length of the latent it is
handed** (`frame_offset = video_latent.shape[-3] * 4`, then
`wan_sound_to_video` slices the audio embedding at that offset), and uses only
the latent's last 19 frames as motion reference. So the graph must hand every
Extend the **whole video so far**, concatenated along time with
`LatentConcat(dim="t")` (stack#3843). Handing it just the previous chunk — the
obvious wiring, and what the graph did until 2026-09-17 — gave every chunk
after the second the same 4.8 s offset: the closing talking head of render
c1c43a8b (17.6 s, four chunks) mouthed the words from 4.8–9.6 s twice over,
while two-chunk opening shots always looked right. Measured per chunk with a
mouth-motion vs audio-envelope cross-correlation: chunks 1–2 aligned within
0.1 s, chunks 3–4 off by 1–3 s and incoherent.

The lesson generalizes: when a node infers a parameter from a tensor's shape,
the wiring that "looks like" the tutorial can still be wrong — read the
node's `execute` before chaining it.

## Presenter speech is cut on the FITTED timeline

A presenter shot lip-syncs to a window of the narration track. The director
plans that window (`narration_offset_s` + `duration_s`) on its **own estimated
timeline**, but the assembly's narration-fit rescales every scene so the
visuals span the *actual* voiceover — on 2026-09-17 (render 671c94b3) 13 shots
planned at 209 s were stretched 1.36× over a 284 s narration. Cutting the
speech at the planned offset put the closing face on screen at 4:01–4:39
speaking the sentences that had played at 3:23–3:50.

Since stack#3839 the presenter phase (which runs last, when every other shot's
rendered duration is known) asks `_fitted_shot_window` where the shot will land
once `_fit_scene_durations` has laid the scenes out — the same call, the same
end-card carve-out (`_endcard_fit_target`), so both agree — and cuts the
speech there. The clip reports the **director's** duration back, because the
assembly fits every rendered duration again; reporting the fitted value would
stretch the presenter scene twice. The rule: whoever slices audio for a scene
must use the timeline the assembly builds, not the one the director imagined.

A fitted window longer than `video_comfyui_s2v_max_chunks × 4.8 s` still
renders short (the renderer logs `speech is Xs but … covers only Ys`); since
stack#3842 the presenter scene sets `CompositionScene.hold_last_frame`, so the
compositor freezes the final frame for the remainder (`tpad=stop_mode=clone`)
instead of looping the clip back to its first frame mid-sentence. Looping stays
the default for every other source — an abstract hero clip loops invisibly.

## Version pin: how to bump ComfyUI safely

`Dockerfile.comfyui` pins a release tag and the comment says to bump
deliberately, because the pinned version *is* the render behaviour behind the
provider. The 2026-09-18 bump (v0.9.2 → v0.36.0, driven by LTX-2.5 evaluation)
established the procedure worth repeating:

1. **Build the candidate as a SEPARATE image and run it on another port**
   (`comfyui-ltx-spike:v0.36.0` on :8189) with the same read-only model
   mounts. Production keeps serving from its pin throughout; nothing about the
   experiment can alter a live hero render.
2. **Diff `/object_info` between the two servers**, not by eye but against the
   node classes and input fields our own providers emit. The bump added 406
   classes and removed 29 — all of the removals paid third-party API nodes we
   never use. Every one of the 20 classes we emit survived with every field
   intact.
3. **Then render both lanes for real on the candidate**, submitting the graphs
   `build_graph()` / `build_s2v_graph()` actually produce rather than a
   hand-written approximation, and check the output is real imagery rather
   than a black frame. Hero came back 81 frames @ 832×480; presenter S2V 154
   frames, exactly 2 × 77, which also exercises the `LatentConcat` chunk chain.

Step 2 alone is not enough — "the node exists" and "the output is unchanged"
are different claims, and only step 3 tests the second.

## Headroom accounting: the animator's own pool counts

ComfyUI keeps its caching-allocator pool between prompts. After the first
hero clip the device reads ~15 GB fuller than it is for the *second* clip,
because the memory is held by the very process about to render it. Measured
2026-09-17 15:50 on the fifth presenter render: 25.9 GB free before hero 1,
11.3 GB after it, and heroes 2 and 3 were downgraded to Ken Burns stills on a
card whose only occupant was the animator.

Two gates therefore read **live free + ComfyUI's `torch_vram_total`** (from
`/system_stats`) rather than live free alone, and only when ComfyUI is the
process that will use it:

- the presenter (S2V) floor, `video_presenter_min_free_vram_gb` (stack#3825);
- the hero plate ladder, `_fit_hero_dims_to_free_vram`, when
  `video_generative_provider=comfyui` (stack#3838). With `wan21` as the
  animator ComfyUI's pool is *not* wan's headroom and only live free counts.

The rule generalizes: a sidecar's cached pool is headroom for **that sidecar's
next request** and dead weight for everyone else's. Do not add it to a gate
that admits a different process.

## Non-goals (this iteration)

- Replacing wan-server (it stays the default; retire only after comfyui has
  survived real render windows).
- The image side (z-image vs FLUX/Qwen through ComfyUI) — separate bake-off.
- Consumer-stack inclusion — 14B fp8 wants ~26GB peak; revisit with GGUF
  quants or block-swap for the 8-16GB target.
