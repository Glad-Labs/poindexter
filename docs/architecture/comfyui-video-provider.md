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
   prompt ("subtle head movements", "steady framing") is read literally by
   Wan; "nearly still, shoulders relaxed, no gestures" calms it further.
3. **`video_comfyui_s2v_cfg`** (default 6.0) and **`video_comfyui_s2v_shift`**
   (8.0) — higher CFG amplifies prompt adherence and motion amplitude; ~4.0 is
   the calmer end of the model's usable range. Change one at a time and A/B
   it on the same portrait + narration (`/data/comfyui-spike/handoff/headcmp/s2v_ab.py`).

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

Residual: a fitted window longer than `video_comfyui_s2v_max_chunks × 4.8 s`
still renders short and the compositor loops the clip for the remainder — the
renderer logs it (`speech is Xs but … covers only Ys`).

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
