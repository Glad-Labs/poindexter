# Featured-image fan-out (Phase 1)

**Status:** shipped 2026-08-16, dark-launched (`image_fanout_enabled`
defaults to `false`).

## What

For the **featured (hero) image only**, the same brief is rendered by up to
four models and a vision judge picks the winner:

| Candidate | Renderer                                                     | Warm speed | Notes                                                                         |
| --------- | ------------------------------------------------------------ | ---------- | ----------------------------------------------------------------------------- |
| `zimage`  | production image-gen server (`_render_image_gen`, OCR-gated) | ~10-15s    | today's model; the stage renders it first while the server is warm            |
| `schnell` | FLUX.1-schnell fp8 via the ComfyUI sidecar                   | ~3s        | Apache-2.0 (schnell, NOT dev)                                                 |
| `klein`   | FLUX.2-klein-4B distilled via the ComfyUI sidecar            | ~4s        | Apache-2.0 and ungated (4B ONLY — see the licence note below)                 |
| `qwen`    | Qwen-Image fp8 via the ComfyUI sidecar                       | ~24s       | first load after an unload takes minutes — the per-candidate budget covers it |

The 2026-08-15 bake-off motivated this: schnell/qwen beat z-image on prompt
adherence for diagram/text-adjacent briefs, and z-image is structurally
**unable** to serve text-bearing briefs (its own OCR gate blocks the render;
production then ships stock). Under fan-out that class is covered instead.
klein joined 2026-08-27 as BFL's flux1-schnell successor — same 4-step
budget, a newer architecture, and a licence we can actually publish under.

### Licence note — only the 4B klein is usable here

BFL ships FLUX.2 under two different licences. **`FLUX.2-klein-4B` is
Apache 2.0 and ungated**; `FLUX.2-dev` and `FLUX.2-klein-9B` carry
`flux-non-commercial-license`, which disqualifies them for a pipeline that
publishes commercially. `image_fanout_klein_model` must stay pointed at a
4B file — repointing it at a 9B checkpoint would be a licence violation,
not just a bigger download.

## Why fan-out before routing

**The judged fan-out is the router's training data.** Every judged render
writes an `image_fanout_judged` row to `audit_log` (winner + per-candidate
scores + brief). Phase 2's class→provider routing map is seeded from these
win rates — running Phase 1 collects the dataset while already shipping the
better image. Variety falls out too: models trade wins across briefs, so
the catalog stops being single-model monotone.

## Shape

- **`services/image_fanout.py`** — orchestrator + the three ComfyUI txt2img
  graphs (schnell/qwen are byte-for-byte the bake-off runner's proven
  shapes; klein is ComfyUI's own `image_flux2_klein_text_to_image`
  template, distilled subgraph) + the judge + the outcome row. Never
  imports stage code. `_COMFY_CANDIDATES` is the single list of
  self-rendered candidates — the wanted-filter and the graph dispatch both
  read it, because a name in one but not the other is skipped in silence.
- **Stage hook** — `source_featured_image._try_image_gen_featured` renders
  zimage via its existing retry/OCR path, then hands off:
  fan-out returns the winning `(path, meta)` and the stage's R2-upload /
  `gen_meta` flow continues unchanged (`gen_meta.fanout` carries winner +
  scores). Fan-out returning nothing → the existing Pexels-stock fallback,
  untouched. `resolve_stage_timeout_seconds` grows by the fan-out budget so
  the node wrapper can't kill a render it asked for.
- **Judge** — mirrors `shot_vision_qa.score_shot_frame`: one image per call
  to `qa_vision_model` via `dispatch_complete` (cost_logs + Langfuse free),
  prompt key `qa.featured_image_fanout` (content-qa SKILL.md pack).
  Fail-soft: an unscorable candidate keeps `score=None`; all-`None` falls
  open to `image_fanout_priority` order (default zimage-first = today's
  behaviour).

  **The judge scores each candidate in an INDEPENDENT call** — it never sees
  the candidates side by side. Anything phrased in the prompt as a hard
  threshold (today: "any legible text caps the score at 40") is therefore
  applied by separate stochastic calls, and will fire unevenly on borderline
  images by construction. That is a property of the design, not a model
  defect; read a cross-candidate inconsistency as evidence about the _rule
  shape_ before concluding the judge is miscalibrated.

- **Judge output budget — `_judge_token_budget`.** qwen3-vl's reasoning
  channel shares `max_tokens` with the JSON answer, and when the trace
  exhausts the budget the call returns **empty content, not truncated
  JSON** — it fails the parse having looked like a healthy request the whole
  way. Measured over the first 12 days: 31 of 115 candidate scores lost,
  every one having generated exactly the configured budget. Raising
  1024 → 2048 only moved the loss rate 30.6% → 20.9%, because a reasoning
  trace is the wrong order of magnitude for a nudge.

  `image_fanout_judge_max_tokens` (2048) is now a **floor**: a thinking
  vision model is lifted to `qa_vision_thinking_num_predict` (8000), reusing
  the key rather than adding a second budget that would have to be kept in
  agreement. This is the same failure, model and fix as
  `multi_model_qa._maybe_bump_vision_thinking_budget` (the
  `vision_scorer_unavailable` RCA of 2026-07-12) — the fan-out judge simply
  never inherited it. An empty completion now reports `empty vision
response`, distinct from `unparseable vision response`; conflating them
  once cost an investigation that went looking for a parser bug.

- **Candidate retention** — `image_fanout_retain_candidates` (true) uploads
  **every** candidate to the object store under
  `fanout/<YYYYMMDD>/<task>/<candidate>-<HHMMSS>.png` (time-suffixed so a
  task re-run cannot overwrite the images an earlier row points at)
  and records the public URL on its entry in the judged row. Without it the
  fan-out is unauditable by construction: candidates are `mkstemp` files,
  only the winner's path is returned to the stage, and the losers survive in
  the worker's `/tmp` only until the next container restart — so a score
  could not be checked against the image it described for any of the first
  48 rows. The winner is uploaded here too even though the stage separately
  publishes it: this key holds what the _judge_ saw, before any downstream
  resize. Best-effort per candidate, and a quiet no-op when no object-store
  credentials are configured. Nothing prunes these yet — the dated prefix is
  there so age-based pruning stays a `list_keys` away.
- **VRAM choreography** — zimage renders first (image-gen warm from the
  inline batch); the service then hard-unloads image-gen (decline-gated
  rung) before ComfyUI loads. ComfyUI swaps schnell→klein→qwen internally
  — the default candidate order is ascending by footprint (17 / ~16 / ~28
  GB) so the heaviest load lands last. The reclaim ladder's
  `_unload_comfyui` rung frees it for the later video render, and
  `run_featured_fanout` frees ComfyUI after its own candidates (the
  2026-08-24 fix for the 503 starvation window). Cost: the next post's
  inline batch pays one image-gen cold reload (~60s).
- **No ComfyUI version bump for klein.** The pinned `v0.9.2` sidecar
  (`scripts/Dockerfile.comfyui`) already ships the FLUX.2 core nodes —
  `EmptyFlux2LatentImage`, `Flux2Scheduler`, and `CLIPLoader` type
  `flux2`. Verify on a live sidecar with
  `curl -s localhost:8188/object_info | grep -o Flux2Scheduler` rather
  than assuming; klein needs no custom node, which keeps the
  core-nodes-only security posture.
- **klein samples differently from the other two.** FLUX.2 has no
  `KSampler` path: `Flux2Scheduler` emits SIGMAS from
  `(steps, width, height)`, so the graph runs
  `RandomNoise` + `CFGGuider` + `KSamplerSelect` + `Flux2Scheduler` →
  `SamplerCustomAdvanced`. Its negative is a `ConditioningZeroOut` of the
  positive rather than a second empty encode — at cfg 1 the guider ignores
  it either way, and the text encoder is an 8GB Qwen3-4B, so skipping the
  second pass is a real saving. That encoder is part of the architecture,
  not an interchangeable CLIP.
- **Checkpoint scanning** — all-in-one checkpoints (schnell) live in the
  host `diffusion_models/` dir; the image bakes
  `scripts/comfyui-extra-model-paths.yaml` so `CheckpointLoaderSimple`
  scans it (no fifth mount, one host dir per model kind).

## Settings (all `app_settings`, `image_fanout_*`)

`enabled` (false) · `candidates` / `priority`
(`zimage,schnell,klein,qwen`) · `judge_enabled` (true) · `comfyui_url`
(`http://comfyui:8188`) · `render_timeout_s` (600) · `width`/`height`
(1024) · `judge_max_tokens` (2048, a floor — see the judge-budget note
above) · `retain_candidates` (true) · per-model knobs (`schnell_checkpoint/steps/cfg`,
`qwen_model/text_encoder/vae/steps/cfg/shift`,
`klein_model/text_encoder/vae/steps/cfg`).

`candidates` is the render **order**; `priority` is the tie-break and
judge-down fall-open order. The suffix tracks the ComfyUI loader, not the
model: `schnell_checkpoint` is an all-in-one `CheckpointLoaderSimple` file,
while `qwen_model` and `klein_model` are `UNETLoader` diffusion models with
their text encoder and VAE in separate files.

### Per-candidate resolution

`image_fanout_<name>_width` / `_height` override the global
`image_fanout_width` / `_height`. `''` (the app_settings convention for
unset) inherits the global, and **every candidate ships inheriting**, so
this changes nobody's behaviour until an operator opts in.

They ship empty rather than pre-tuned on purpose. These models genuinely
don't share a best size — Qwen-Image is trained at 1328², FLUX.1-schnell
degrades above roughly 1.2 MP, klein is native 1024 — but the obvious
tuning does not fit the hardware:

| qwen render     | Peak VRAM (32,607 MiB card) | Headroom    |
| --------------- | --------------------------- | ----------- |
| 1024² (1.05 MP) | 31,185 MiB                  | ~1.4 GB     |
| 1328² (1.76 MP) | 31,537 MiB                  | **~1.0 GB** |

Measured 2026-08-27 on the operator card, which also drives the desktop. A
14B video render already OOM'd this same GPU at 31.9 GB, so ~1 GB is not a
margin to spend unattended — qwen sits near the ceiling at either size.
Raise it only on a card with real headroom or an idle one, and confirm by
looking for a ComfyUI execution error rather than assuming it held.

**Each judged render records the size it used** (`width`/`height` per
candidate in the `image_fanout_judged` row). Resolution is a confound for
the Phase-2 routing dataset: without it in the row, retuning a candidate's
size splits the history into before/after halves that read identically.
The stage-rendered `zimage` carries no dimensions — this service doesn't
size it, and a fabricated global would misattribute it.

### Every zimage-less row says why

`zimage` is the production model, so its absence from a fan-out is the one
gap that corrupts the routing dataset quietly: a row without it looks like
the judge preferred the others rather than like the candidate never
rendered. Each such row therefore carries `zimage_absent_reason` —
the stage's failure string (`HTTP 503`, `ReadTimeout`), `ocr_gate_rejected`,
`render returned nothing`, or `not in candidates` when the operator has
taken `zimage` out of `image_fanout_candidates`.

That last value exists because **silence is indistinguishable from a
producer bug.** The deliberate case originally fell through with no field
at all, and the one row it produced (2026-08-27) cost a round of forensics
to rule out as a starvation before the candidate list explained it. Reading
the dataset, treat a zimage-less row with no reason as a bug in this
service — not as a deselection.

klein's `steps`/`cfg` defaults (4 / 1.0) are the **distilled** variant's.
`flux-2-klein-base-4b.safetensors` is the same architecture without baked
guidance and wants ~20 steps at cfg 5 — the file and the two numbers have
to move together or the render is noise.

## Operator runbook

1. Image weights into `~/.poindexter/comfyui/models/` (alongside the video
   set), each into the subdir its loader scans:

   | File                                     | Subdir             | Size    | From                                          |
   | ---------------------------------------- | ------------------ | ------- | --------------------------------------------- |
   | `flux1-schnell-fp8.safetensors`          | `diffusion_models` | 17 GB   | Comfy-Org/flux1-schnell                       |
   | `qwen_image_2512_fp8_e4m3fn.safetensors` | `diffusion_models` | 20 GB   | Comfy-Org Qwen-Image repackage                |
   | `qwen_2.5_vl_7b_fp8_scaled.safetensors`  | `text_encoders`    | 9.4 GB  | ″                                             |
   | `qwen_image_vae.safetensors`             | `vae`              | 0.25 GB | ″                                             |
   | `flux-2-klein-4b.safetensors`            | `diffusion_models` | 7.8 GB  | Comfy-Org/vae-text-encorder-for-flux-klein-4b |
   | `qwen_3_4b.safetensors`                  | `text_encoders`    | 8.0 GB  | ″                                             |
   | `flux2-vae.safetensors`                  | `vae`              | 0.34 GB | ″                                             |

   The klein three come from Comfy-Org's repackage under `split_files/`
   (note the repo's misspelled name, `vae-text-encorder-…` — that is the
   real slug, not a typo here):

   ```bash
   R=https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-4b/resolve/main/split_files
   M=~/.poindexter/comfyui/models
   curl -L -o $M/diffusion_models/flux-2-klein-4b.safetensors "$R/diffusion_models/flux-2-klein-4b.safetensors"
   curl -L -o $M/text_encoders/qwen_3_4b.safetensors           "$R/text_encoders/qwen_3_4b.safetensors"
   curl -L -o $M/vae/flux2-vae.safetensors                     "$R/vae/flux2-vae.safetensors"
   ```

2. Rebuild the sidecar once (bakes the model-path yaml):
   `bash scripts/start-stack.sh up -d --build comfyui`
3. Flip: `poindexter settings set image_fanout_enabled true`
4. Watch the two "Featured fan-out" panels on the Pipeline board.
   Roll back by flipping the setting off.

### Adding a candidate to an install that already ran

The boot seeder is `INSERT … ON CONFLICT (key) DO NOTHING`, so **changing
the default for an existing key never restamps the DB row.** New keys
(`image_fanout_klein_*`) seed themselves on the next boot, but
`image_fanout_candidates` and `image_fanout_priority` already exist — a
running install keeps its old three-model list and the new candidate never
renders, silently. Widen them explicitly:

```bash
poindexter settings set image_fanout_candidates zimage,schnell,klein,qwen
poindexter settings set image_fanout_priority zimage,schnell,klein,qwen
```

Then confirm on the "wins & presence" panel that the new name appears with
a non-zero **Rendered** count. It is listed there at zero renders as soon
as it is in `image_fanout_candidates`, which is the point: a candidate that
is configured but never renders is a starvation bug, and the panel is what
makes the difference visible.

## Known gaps (Phase 2)

- ComfyUI candidates have **no OCR gate** — text discipline rides the
  prompt's textless clause + the judge's legible-text score cap (≤40).
- Inline images stay single-model (fan-out is featured-only).
- The class→provider routing map (seeded from this phase's audit rows) and
  provider extraction into registered ImageProvider plugins.

  **Do not seed that map from the raw win rates yet.** Audited over the first
  48 judged rows (2026-08-16 → 08-27), only 42% of wins were an actual
  comparative judge preference; the other 58% were resolved by
  `image_fanout_priority` order (score ties, rows where the judge scored only
  one candidate, judge down). Presence is lopsided too — zimage appeared in
  21 rows against schnell's 47 — so a raw win _rate_ is confounded before the
  judge speaks. The judge is also near-binary in practice: 71% of scores
  ≥ 90, 24% ≤ 40, and only 4.8% anywhere in the 41–89 band. The retention and
  budget fixes above address the mechanical half; the score-distribution half
  is a `qa.featured_image_fanout` prompt question, deliberately left until
  there is enough per-candidate data to answer it without over-fitting.
  Tracked in Glad-Labs/poindexter#1032.
