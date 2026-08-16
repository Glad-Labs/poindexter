# Featured-image fan-out (Phase 1)

**Status:** shipped 2026-08-16, dark-launched (`image_fanout_enabled`
defaults to `false`).

## What

For the **featured (hero) image only**, the same brief is rendered by up to
three models and a vision judge picks the winner:

| Candidate | Renderer                                                     | Warm speed | Notes                                                                         |
| --------- | ------------------------------------------------------------ | ---------- | ----------------------------------------------------------------------------- |
| `zimage`  | production image-gen server (`_render_image_gen`, OCR-gated) | ~10-15s    | today's model; the stage renders it first while the server is warm            |
| `schnell` | FLUX.1-schnell fp8 via the ComfyUI sidecar                   | ~3s        | Apache-2.0 (schnell, NOT dev)                                                 |
| `qwen`    | Qwen-Image fp8 via the ComfyUI sidecar                       | ~24s       | first load after an unload takes minutes — the per-candidate budget covers it |

The 2026-08-15 bake-off motivated this: schnell/qwen beat z-image on prompt
adherence for diagram/text-adjacent briefs, and z-image is structurally
**unable** to serve text-bearing briefs (its own OCR gate blocks the render;
production then ships stock). Under fan-out that class is covered instead.

## Why fan-out before routing

**The judged fan-out is the router's training data.** Every judged render
writes an `image_fanout_judged` row to `audit_log` (winner + per-candidate
scores + brief). Phase 2's class→provider routing map is seeded from these
win rates — running Phase 1 collects the dataset while already shipping the
better image. Variety falls out too: models trade wins across briefs, so
the catalog stops being single-model monotone.

## Shape

- **`services/image_fanout.py`** — orchestrator + the two ComfyUI txt2img
  graphs (byte-for-byte the bake-off runner's proven shapes) + the judge +
  the outcome row. Never imports stage code.
- **Stage hook** — `source_featured_image._try_image_gen_featured` renders
  zimage via its existing retry/OCR path, then hands off:
  fan-out returns the winning `(path, meta)` and the stage's R2-upload /
  `gen_meta` flow continues unchanged (`gen_meta.fanout` carries winner +
  scores). Fan-out returning nothing → the existing Pexels-stock fallback,
  untouched. `resolve_stage_timeout_seconds` grows by the fan-out budget so
  the node wrapper can't kill a render it asked for.
- **Judge** — mirrors `shot_vision_qa.score_shot_frame`: one image per call
  to `qa_vision_model` via `dispatch_complete` (cost_logs + Langfuse free),
  prompt key `qa.featured_image_fanout` (content-qa SKILL.md pack),
  `max_tokens=1024` for the qwen3-vl think-trace budget. Fail-soft: an
  unscorable candidate keeps `score=None`; all-`None` falls open to
  `image_fanout_priority` order (default zimage-first = today's behaviour).
- **VRAM choreography** — zimage renders first (image-gen warm from the
  inline batch); the service then hard-unloads image-gen (decline-gated
  rung) before ComfyUI loads. ComfyUI swaps schnell→qwen internally; the
  reclaim ladder's `_unload_comfyui` rung frees it for the later video
  render. Cost: the next post's inline batch pays one image-gen cold reload
  (~60s).
- **Checkpoint scanning** — all-in-one checkpoints (schnell) live in the
  host `diffusion_models/` dir; the image bakes
  `scripts/comfyui-extra-model-paths.yaml` so `CheckpointLoaderSimple`
  scans it (no fifth mount, one host dir per model kind).

## Settings (all `app_settings`, `image_fanout_*`)

`enabled` (false) · `candidates` / `priority` (`zimage,schnell,qwen`) ·
`judge_enabled` (true) · `comfyui_url` (`http://comfyui:8188`) ·
`render_timeout_s` (600) · `width`/`height` (1024) · per-model knobs
(`schnell_checkpoint/steps/cfg`, `qwen_model/text_encoder/vae/steps/cfg/shift`).

## Operator runbook

1. Image weights into `~/.poindexter/comfyui/models/` (alongside the video
   set): `flux1-schnell-fp8.safetensors` + the three `qwen_image_*` files
   → `diffusion_models/` + `text_encoders/` + `vae/` per filename.
2. Rebuild the sidecar once (bakes the model-path yaml):
   `bash scripts/start-stack.sh up -d --build comfyui`
3. Flip: `poindexter settings set image_fanout_enabled true`
4. Watch the two "Featured fan-out" panels on the Pipeline board.
   Roll back by flipping the setting off.

## Known gaps (Phase 2)

- ComfyUI candidates have **no OCR gate** — text discipline rides the
  prompt's textless clause + the judge's legible-text score cap (≤40).
- Inline images stay single-model (fan-out is featured-only).
- The class→provider routing map (seeded from this phase's audit rows) and
  provider extraction into registered ImageProvider plugins.
