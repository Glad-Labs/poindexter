# High-Emotion TTS Engine Bake-Off — Design

**Date:** 2026-07-10
**Status:** Design (awaiting review)
**Related:** `docs/superpowers/specs/2026-06-04-voice-warm-stt-tts-sidecars-design.md` (Speaches sidecar prior art), `skills/content/tts/SKILL.md`

## Problem

Podcast + video narration currently renders through **Kokoro-82M via the
Speaches container**. Kokoro is fast, tiny, and Apache-2.0, but it is
**expressively flat** — no emotion or delivery control. The goal is more
natural, emotional narration.

The original request was to swap in `Qwen/Qwen2-Audio-7B-Instruct`. That model
is **ruled out**: it is an `audio-text-to-text` model (audio understanding /
transcription — audio → text), the opposite direction from TTS (text → audio).
It has no speech-synthesis head and cannot produce audio. Verified against the
HuggingFace model card.

Two real TTS candidates were chosen and verified on HuggingFace:

| Model          | Repo                          | License    | Emotion control              | Cloning   | Size  |
| -------------- | ----------------------------- | ---------- | ---------------------------- | --------- | ----- |
| **CosyVoice2** | `FunAudioLLM/CosyVoice2-0.5B` | Apache-2.0 | instruction / emotion string | zero-shot | 0.5B  |
| **Chatterbox** | `ResembleAI/chatterbox`       | MIT        | `exaggeration` dial          | one-shot  | ~0.5B |

Both licenses are commercial-clean and OSS-shippable in Poindexter. CosyVoice2
is from FunAudioLLM (Alibaba's speech team, the Qwen org family) — so the
original advice pointed the right direction; "Qwen2-voice" was a naming mix-up.

## Current-state findings (the wiring reality)

The advertised `TTSProvider` plugin seam is **vestigial** and must not be
assumed to be the live path:

1. **No registration.** `pyproject.toml` has entry-point groups for
   `image_providers`, `llm_providers`, `stages`, `taps`, `probes`, `jobs`,
   `topic_sources`, `modules` — but **no `poindexter.tts_providers` group**.
   The `KokoroTTSProvider` class exists but is not registered anywhere.
2. **No dispatch.** Both live synthesis call sites —
   `modules/content/stages/generate_media_scripts.py:147` and
   `services/podcast_service.py::_generate_with_voice` (line ~1120) — call
   `tts_service.synthesize_speech()` directly, hardwired to the Speaches
   OpenAI-compatible `/v1/audio/speech` endpoint.
3. **Engine string is cosmetic.** `podcast_tts_engine` is read once
   (`podcast_service.py:1069`) only to _label_ the recorded media asset
   (`provider_plugin=f"tts.{engine}"`). It never selects a provider.

**Reusable asset:** `tts_service.synthesize_speech` is already a generic
OpenAI-compatible `/v1/audio/speech` HTTP client with a battle-tested ffmpeg
post-chain — multi-segment remux repair + EBU-R128 loudnorm (the `qa.audio`
clip-gate fix). New HTTP-backed engines should reuse this, not reinvent it.

## Goals / Non-goals

**Goals**

- Hear all three engines (kokoro / cosyvoice2 / chatterbox) render the _same_
  reference script, with emotion settings applied.
- Pick a winner by ear. Keep Kokoro as the default until a challenger wins.
- Commercial-safe licenses; no production risk during evaluation.

**Non-goals (YAGNI for this iteration)**

- Per-sentence / per-segment emotion markup.
- Voice-cloning workflow — round 1 uses each engine's stock voice so we compare
  quality + emotion, not clone fidelity.
- Always-on VRAM residency — sidecars cold-load via an opt-in profile.
- Any change to the live pipeline dispatch **before** a winner is chosen.

## Approach — phased

### Phase 1 — Bake-off (zero changes to the live pipeline)

Each component with its purpose / interface / dependencies:

1. **Two sidecar containers** — `cosyvoice2`, `chatterbox`, each exposing
   OpenAI-compatible `POST /v1/audio/speech`, gated behind compose
   `profiles: ["tts-hq"]` in `docker-compose.local.yml` (and mirrored, still
   profile-gated, in `docker-compose.consumer.yml`). Modeled on the existing
   `image-gen-server` profile pattern. Where the upstream project ships no
   native OpenAI server, a ~30-line FastAPI shim in the image adapts the model
   to the `/v1/audio/speech` contract.
   - _Interface:_ HTTP `/v1/audio/speech` (`{model, voice, input, response_format}`) + `/health`.
   - _Depends on:_ the model weights (downloaded on first run), a GPU (cold-loaded).

2. **Shared HTTP-TTS helper** — extract the OpenAI request + the remux/loudnorm
   post-chain out of `tts_service.synthesize_speech` into a reusable async
   function (e.g. `tts_service.render_openai_tts(base_url, model, voice, text, ...)`).
   `synthesize_speech` becomes a thin caller of it (behavior-preserving), and the
   new providers call the same helper — one normalization path, no copy-paste.
   - _Interface:_ `async (base_url, model, voice, text, fmt, loudnorm cfg) -> bytes`.
   - _Depends on:_ `httpx`, ffmpeg (fail-soft, as today).

3. **Two `TTSProvider` classes** — `CosyVoice2TTSProvider`,
   `ChatterboxTTSProvider` in `services/tts_providers/`, mirroring the
   `KokoroTTSProvider` shape (`name` / `sample_rate_hz` / `default_format`;
   `async synthesize(text, output_path, *, voice, config) -> TTSResult`).
   Emotion is passed through `config` (read from `plugin.tts_provider.<name>`):
   - CosyVoice2: `instruct` / `emotion` natural-language string.
   - Chatterbox: `exaggeration` (float) + `cfg_weight` (float).
   - _Interface:_ the `plugins/tts_provider.py::TTSProvider` Protocol.
   - _Depends on:_ the shared HTTP helper (2) + its sidecar (1).

4. **CLI bake-off harness** — a new `tts-bakeoff` subcommand under the existing
   `poindexter media` group (`cli/media.py`):
   `poindexter media tts-bakeoff [--script <file>] [--engines kokoro,cosyvoice2,chatterbox] [--voice <id>]`.
   **Ships with a built-in reference script** (a short ~150-word neutral
   tech-narration passage, in-repo as a package resource) so the command runs
   with **zero arguments**; `--script <file>` overrides it to point at any real
   episode. The sample is deliberately chosen to exercise both the emotion
   settings and the `tts_pronunciations` normalizer — a few tech terms
   (VRAM, GHz) plus emphasis-worthy sentences. Renders through each engine →
   normalized files in a scratch dir + a manifest row per engine (engine, voice,
   duration, size, path). **No DB writes, no publish.** One dead engine reports
   and is skipped; the others still render.

5. **app_settings defaults** — new `plugin.tts_provider.cosyvoice2.*` /
   `plugin.tts_provider.chatterbox.*` keys (emotion defaults, voice) + the two
   sidecar base URLs, added to `services/settings_defaults.py` (per convention —
   **not** a migration file). `podcast_tts_engine` default is unchanged.

6. **Tests + docs** — mocked-HTTP unit tests per provider (mirroring
   `tests/unit/services/test_tts_service.py`), a Protocol-conformance test
   (mirroring `tests/unit/plugins/test_tts_provider.py`), and a test for the
   extracted shared helper. Update `skills/content/tts/SKILL.md` and
   `docs/operations/voice-stt-tts.md` with the new engines + the bake-off command.

**Data flow (Phase 1):**

```
CLI tts-bakeoff
  └─ for engine in engines:
       provider.synthesize(text, out, voice, config)
         ├─ kokoro   → in-process KPipeline → wav            (existing class)
         └─ http     → sidecar /v1/audio/speech → raw bytes
                        → shared remux + loudnorm → file
  └─ write manifest (engine, voice, duration, size, path)
```

**Error handling:** providers raise on failure (per the Protocol contract); the
CLI catches per-engine and continues, printing a clear remediation for an
unreachable sidecar (the `--profile tts-hq up` command). Normalization stays
fail-soft (returns raw bytes on any ffmpeg error), as today.

### Phase 2 — Cutover (sketch only; triggers **only** if a challenger wins)

1. Add the `poindexter.tts_providers` entry-point group to `pyproject.toml`
   (mirroring `image_providers`) + a loader modeled on the image-provider loader.
2. Migrate the Speaches HTTP client into a registered `SpeachesTTSProvider` so
   current behavior becomes a provider (back-compat: default engine unchanged).
3. Route `_generate_with_voice` + `generate_media_scripts` through the registry
   keyed on `podcast_tts_engine`; namespace per-engine config so `base_url` /
   `voice` / `model` are no longer Speaches-global.
4. Flip `podcast_tts_engine` to the winner. If **Kokoro wins**: tear down the
   sidecars, keep the finding — ship nothing to the live path.

## Risks & mitigations

- **VRAM contention** with the 31B writer / Z-Image / vision models →
  profile-gated, cold-loaded, absent from the default stack; narration is not
  latency-critical so it can queue through `gpu_scheduler`.
- **Per-engine OpenAI-shim effort** (especially CosyVoice2) → the FastAPI shim
  is the fallback and is ~30 LOC; Chatterbox has known OpenAI-compatible servers.
- **License** → both verified Apache-2.0 / MIT: commercial-clean, OSS-shippable.
- **Subjective "which sounds better"** → mitigated by same-script A/B + your ear;
  that irreducible judgment is exactly why we render rather than pick from specs.

## Testing strategy

Unit tests run with **mocked HTTP** — no model or container needed:
per-provider request-shaping + result contract, Protocol conformance, and the
shared-helper normalization. No integration-container test in Phase 1 (sidecars
are opt-in). Manual verification is the bake-off itself.

## Success criteria (Phase 1)

`poindexter media tts-bakeoff` (no arguments — using the built-in sample)
produces three comparable, loudnorm'd clips from one script with emotion
settings applied; all tests green;
`tts` SKILL.md + voice-stt-tts docs updated; **zero change to the live
pipeline**. Decision gate: you pick a winner (or Kokoro stays and the sidecars
come down).
