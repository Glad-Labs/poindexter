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

| Key                                                                           | Default                            | Meaning                                                                                          |
| ----------------------------------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------ |
| `video_generative_provider`                                                   | `wan21`                            | Animator: `wan21` or `comfyui`                                                                   |
| `video_comfyui_server_url`                                                    | `http://host.docker.internal:8188` | Sidecar URL                                                                                      |
| `video_comfyui_steps` / `video_comfyui_cfg`                                   | `4` / `1.0`                        | Sampler regime (lightx2v 4-step). Quality tier: `20` / `3.5` + LoRA off                          |
| `video_comfyui_use_lightning_lora`                                            | `true`                             | Wire the distill LoRAs                                                                           |
| `video_comfyui_shift`                                                         | `5.0`                              | ModelSamplingSD3 (official 14B i2v template value)                                               |
| `video_comfyui_length_frames` / `video_comfyui_fps`                           | `81` / `16`                        | Model-native ~5s profile (caller fps ignored — compositor conforms/loops)                        |
| `video_comfyui_negative_prompt`                                               | canonical Wan negative (Chinese)   | Model-native negative                                                                            |
| `video_comfyui_{high,low}_model`, `_text_encoder`, `_vae`, `_lora_{high,low}` | repackaged filenames               | Weight swaps are settings-only                                                                   |
| `video_comfyui_timeout_s`                                                     | `900`                              | End-to-end render budget (20-step 960×544 measured 644s)                                         |
| `video_comfyui_ready_wait_s`                                                  | `90`                               | Cold-boot wait before first submit                                                               |
| `video_comfyui_workflow_override_json`                                        | `''`                               | Full graph swap: API-format JSON with `__PROMPT__`/`__WIDTH__`/… placeholders, substituted typed |

## Operator runbook (enable on a host)

1. **Weights** (~38GB) into `~/.poindexter/comfyui/models/` (ComfyUI layout),
   from `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` `split_files/`:
   - `diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors`
   - `diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors`
   - `text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`
   - `vae/wan_2.1_vae.safetensors`
   - `loras/wan2.2_i2v_lightx2v_4steps_lora_v1_{high,low}_noise.safetensors`
2. `docker compose -f docker-compose.local.yml --profile comfyui up -d --build`
3. Verify: `curl -s localhost:8188/system_stats | head -c 200`
4. Flip: `poindexter settings set video_generative_provider comfyui`
5. Watch the next render's hero shots (QA Rails / hero_render_fallback
   findings). Roll back by flipping the setting to `wan21`.

VRAM reality on a 32GB card shared with a desktop: 832×480 peaks ~25-29GB,
960×544 ~28GB, **1280×720×81f does not fit** (spike OOM at 31.9GB) — full
720p needs block-swap custom nodes (security review first) or an idle card.

## Non-goals (this iteration)

- Replacing wan-server (it stays the default; retire only after comfyui has
  survived real render windows).
- The image side (z-image vs FLUX/Qwen through ComfyUI) — separate bake-off.
- Consumer-stack inclusion — 14B fp8 wants ~26GB peak; revisit with GGUF
  quants or block-swap for the 8-16GB target.
