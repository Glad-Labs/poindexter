# Image-Model Bake-Off — Design Spec

- **Date:** 2026-07-12
- **Status:** Approved (design) — pending implementation plan
- **Branch:** `claude/image-model-text-quality-f55cee`
- **Author:** Claude (with Matt)

## Problem

The active image model is **Z-Image-Turbo** (`app_settings.image_generation_model = z_image_turbo`).
It produces higher-quality images than the previous SDXL-Lightning, but consistently
renders **unwanted, mangled text** into images (garbled signage, labels, UI glyphs).

Root cause (confirmed in code and in the `diffusers` library):

1. Z-Image-Turbo is **guidance-distilled** (runs at `guidance_scale=0.0`). Its registry
   entry sets `supports_negative_prompt=False`, and the server's `/generate` path only
   passes `negative_prompt` when `config.supports_negative_prompt` is true
   (`scripts/image-gen-server.py`). So the operator's `image_negative_prompt` —
   which begins `"text, words, letters, numbers, watermark…"` — is **never sent**.
2. Even if forced through, the `diffusers` ZImagePipeline **ignores `negative_prompt`
   when `guidance_scale < 1`**. So the standard "no text" lever is structurally dead
   on this model.
3. Z-Image's headline feature is **bilingual text rendering** — it _reaches for_ text
   whenever a prompt implies a screen/sign/label/chart. At 9 turbo steps that text
   renders half-formed → the mangled glyphs.

The same limitation applies to every guidance-distilled model (FLUX.1-schnell,
Krea-2-Turbo, SDXL-Lightning). Models with real classifier-free guidance (CFG) —
Chroma, CogView4, Lumina, SD3.5, Qwen-Image — honor negative prompts.

## Goal

Empirically compare a broad field of current open text-to-image models on **two axes
that matter to Glad Labs** — (a) how much unwanted text they leak, and (b) image
quality — plus practical gates (VRAM footprint, latency, license), and pick a winner
(or a per-tier winner) to replace or supplement Z-Image-Turbo.

### Non-goals

- Not building a standing capability — this is a throwaway `scripts/` one-off.
- Not touching the live production image-gen server during the run.
- Not shipping a model in this work; the deliverable is evidence + the winner's config.

## Roster (12)

SDXL-Lightning is included as the _old_ baseline so results read old → current → candidates.

| #   | Model            | HF repo                                        | Mechanism       | License                                         | VRAM note                           |
| --- | ---------------- | ---------------------------------------------- | --------------- | ----------------------------------------------- | ----------------------------------- |
| 1   | SDXL-Lightning   | `ByteDance/SDXL-Lightning` (LoRA on SDXL base) | distilled       | OpenRAIL++                                      | old baseline (already in registry)  |
| 2   | Z-Image-Turbo    | `Tongyi-MAI/Z-Image-Turbo`                     | distilled (g=0) | Apache-2.0                                      | **current control** (~13 GB)        |
| 3   | Chroma1-HD       | `lodestones/Chroma1-HD`                        | **CFG**         | Apache-2.0                                      | 8.9B; fp8/GGUF fits 16 GB           |
| 4   | Chroma1-Flash    | `lodestones/Chroma1-Flash`                     | distilled       | Apache-2.0                                      | fast Chroma variant                 |
| 5   | CogView4-6B      | `zai-org/CogView4-6B`                          | **CFG**         | Apache-2.0                                      | 6B DiT + GLM-4 encoder → fp8 likely |
| 6   | Lumina-Image-2.0 | `Alpha-VLLM/Lumina-Image-2.0`                  | **CFG**         | Apache-2.0                                      | 2B, easy                            |
| 7   | FLUX.2-klein-4B  | `black-forest-labs/FLUX.2-klein-4B`            | verify          | Apache-2.0                                      | 4B                                  |
| 8   | HiDream-I1-Fast  | `HiDream-ai/HiDream-I1-Fast`                   | distilled       | MIT                                             | 17B → fp8/nf4                       |
| 9   | Qwen-Image       | `Qwen/Qwen-Image`                              | **CFG**         | Apache-2.0                                      | 20B → fp8 (~20 GB)                  |
| 10  | FLUX.1-schnell   | `black-forest-labs/FLUX.1-schnell`             | distilled       | Apache-2.0 _(gated)_                            | Matt accepts license                |
| 11  | SD3.5-Medium     | `stabilityai/stable-diffusion-3.5-medium`      | **CFG**         | Stability Community (free <$1M) _(gated)_       | Matt accepts license                |
| 12  | Krea-2-Turbo     | `krea/Krea-2-Turbo`                            | distilled       | custom `krea-2-community-license` _(eval-only)_ | Matt accepts; will not ship in OSS  |

**Gated/custom-license action (Matt):** accept licenses on the HF pages for #10, #11, #12
before the run. Links provided at implementation time.

**Repo-existence caveat:** exact repo IDs for brand-new models (FLUX.2-klein, Chroma1-\*)
are verified at implementation time; the field may be trimmed if a repo/pipeline isn't
yet available in a stable `diffusers`.

## Fair-fight configuration

Each model runs at **its own best config** — anything else rigs the comparison:

- **CFG models** (Chroma1-HD, CogView4, Lumina-2.0, SD3.5-Medium, Qwen-Image): pass the
  real negative prompt (`text, words, letters, numbers, signage, UI, labels, watermark,
captions…`) at `guidance_scale ≈ 4–5`, native step count.
- **Distilled models** (SDXL-Lightning, Z-Image, Chroma1-Flash, FLUX.1-schnell,
  FLUX.2-klein?, HiDream-Fast, Krea-2-Turbo): `guidance_scale = 0`, native low step count,
  plus a **positive** "clean, textless composition, no text, no signage, no UI" clause
  appended to the prompt (the only steering a guidance-free model responds to).

Per-model step/guidance/dtype defaults live in the harness config, mirroring the live
server's `ModelConfig` field shape.

## Prompt set (~10, fixed across all models)

- **Real offenders:** the actual inline + featured prompts from recent posts that produced
  mangled text (pulled from `media_assets` / pipeline history at implementation time).
- **Text-tempting:** GPU benchmark chart, dashboard UI, labeled hardware diagram, "config
  file on screen" — scenes that bait a text renderer.
- **Text-neutral controls:** isometric server room, abstract data-flow illustration.

## Scoring (5 dimensions per model)

1. **Text-leakage (primary):** run OCR (easyocr/tesseract — deterministic, CPU, no GPU
   contention) over every output and **count detected characters**. Lower = better. This
   is the number that answers the original question.
2. **Quality:** Matt's eyeball on contact sheets (optionally an aesthetic/vision score).
3. **Peak VRAM:** clears the **16 GB consumer tier** (Poindexter v1) or only the 32 GB
   operator tier? Recorded from `torch.cuda.max_memory_allocated`.
4. **Latency:** seconds/image at best config.
5. **License tier:** ship-in-OSS? (Apache/MIT ✅; SD3.5 community <$1M ⚠️; Krea ❌).

## Harness architecture

**Standalone throwaway script** — `scripts/image_bakeoff.py` (a `scripts/` one-off, not a
`services/` module):

- Runs in its **own fresh venv** with an up-to-date `diffusers` (several candidates are
  brand-new pipelines that may exceed the live server's pinned version).
- Loads each model **one at a time** → generates the full prompt set → records peak VRAM +
  latency → unloads (frees CUDA cache) → next model. OOM/load failure is **recorded, not
  fatal** (a model that can't fit is useful bake-off data).
- Each model entry is a dict mirroring the server's `ModelConfig` fields, so the **winner's
  config drops straight into the live `REGISTRY`** afterward.
- **Isolation:** never imports or calls the live image-gen server; targets the **5090**
  (the 3090 stays vision-pinned); run in a quiet content window so it doesn't contend with
  pipeline GPU use.

## Outputs

- Per-prompt **contact sheets** (grid across all 12 models) for visual quality comparison.
- A **scored results table** (model × text-chars, peak-VRAM, latency, license, notes).
- Each model's `ModelConfig` dict, ready for the live registry.
- Saved to a results dir (scratchpad or repo-ignored path — not committed).

## Risks & mitigations

- **Download volume (~100 GB+):** run in a quiet window; models cached under HF cache.
- **Gated models:** Matt accepts 3 licenses before the run; harness fails-soft and records
  "license not accepted" if a download 403s.
- **`diffusers` version compat:** isolated venv on bleeding-edge `diffusers`; live server
  untouched. Trim any model whose pipeline isn't yet in a usable `diffusers`.
- **VRAM/OOM on big models (Qwen 20B, HiDream 17B):** attempt bf16 → fp8 fallback → record
  OOM and skip. OOM at ≤16 GB = fails consumer tier (recorded, not an error).
- **GPU contention with the live pipeline:** run when content generation is idle; targets
  the 5090 only.

## Productionization path (post-bake-off)

Winner's `ModelConfig` is added to `scripts/image-gen-server.py::REGISTRY` (and the mirrored
`services/image_service.py::IMAGE_MODEL_REGISTRY`), `app_settings.image_generation_model`
is flipped, the server `/reload`s, and — if the winner is a CFG model — the (currently inert)
`image_negative_prompt` becomes live again. This is a follow-up change, not part of the
bake-off itself.

## Open items (resolved at implementation time)

- Verify exact repo IDs + `diffusers` pipeline classes for FLUX.2-klein, Chroma1-HD/Flash,
  Krea-2-Turbo, CogView4, Lumina-2.0.
- Extract the real offending prompts from `media_assets` / pipeline history.
- Confirm FLUX.2-klein's guidance mechanism (CFG vs distilled) for the fair-fight config.
