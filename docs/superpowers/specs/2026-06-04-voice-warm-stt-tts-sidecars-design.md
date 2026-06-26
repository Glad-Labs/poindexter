# Voice: warm STT/TTS sidecar (Speaches) (#1088)

**Status:** Draft for review (rev 2 — single-container Speaches)
**Date:** 2026-06-04
**Issue:** [Glad-Labs/glad-labs-stack#1088](https://github.com/Glad-Labs/glad-labs-stack/issues/1088) (part of the #1006 always-on workstream)
**Author:** host-brain voice session (Matt + Claude)

---

## 1. Problem

Both voice-agent containers (`voice-agent-livekit`, `voice-agent-claude-code`)
load **Whisper-medium in-process** via Pipecat's `WhisperSTTService` and run
**Kokoro TTS in-process** via `KokoroTTSService`. The Whisper model load costs
**~12.4s on the first transcription after every container start** (warm turns
are ~0.3s).

Because the containers bind-mount `./src/cofounder_agent` and rely on a restart
to pick up code changes (Python module cache), **restarts are frequent** — so
the always-on voice line pays the ~12s cold-start tax repeatedly, and now pays
it _twice_ (once per room). The user-visible symptom: the first utterance after
any restart gets ~12s of dead air, and can time out entirely or be cancelled by
VAD interruption (observed live 2026-06-04 — multiple voice turns timed out
during this very design session).

This directly undermines the "always-on" goal of #1006.

## 2. Goal / Non-goals

**Goal:** Decouple the STT and TTS models from the voice-agent hot path so that
restarting a voice-agent container no longer reloads models. The first utterance
after a restart should be ~as fast as a warm turn, because the models live in a
separate, long-lived, warm service.

**Non-goals:**

- Context/session continuity — already solved (pinned `claude -p --resume`,
  ahead of VoiceMode). This issue is **only** the STT/TTS latency layer.
- Changing the LLM/brain dispatch, the two-room split, or LiveKit transport.
- Streaming/partial-transcript STT. We keep the current segmented (VAD-bounded)
  turn model; we only move _where_ the segment is transcribed.

## 3. Approach

Mirror the **image-gen idiom** already in the stack: a GPU model runs as its own
long-lived HTTP container (`image-gen-server` on `:9836`), and callers are thin
clients.

**Decision (Matt, 2026-06-04): use [Speaches](https://github.com/speaches-ai/speaches)
— ONE container that serves BOTH STT and TTS.** Speaches (the renamed successor
to `faster-whisper-server`) is an OpenAI-compatible server exposing both
`POST /v1/audio/transcriptions` (faster-whisper) **and** `POST /v1/audio/speech`
(Kokoro), with the **same Kokoro voice packs we already use** (`bf_emma`,
`bf_isabella`). So a single warm GPU process replaces both in-process models,
and **both voice rooms become thin clients of the one Speaches service.**

Why one container beats the original two-sidecar plan:

- One GPU process, one weight cache volume, one healthcheck, one thing to
  monitor — less surface area.
- Same model family we already run (faster-whisper / Kokoro) → no quality
  regression, voice ids carry over **unchanged**.
- It's a prebuilt, mature OSS image (no-wheel-reinvention). The edge that's ours
  (always-on + two-room + brain dispatch) is untouched.

**Config seam stays decoupled even though the implementation is one box:** the
STT and TTS client nodes read **two separate base-URL settings** that both
_default_ to the same Speaches URL. If we ever want to split them back onto
separate hosts (e.g. STT on GPU, TTS on CPU elsewhere), it's a settings change,
not a schema migration.

### Considered alternatives

1. **Two separate sidecars** (`faster-whisper-server` + `kokoro-fastapi`) — the
   rev-1 plan. Rejected in favor of Speaches: same engines, but two containers,
   two caches, two healthchecks for no functional gain.
2. **Pre-warm in-process on boot** (background thread loads the model before the
   first turn). Rejected: doesn't remove the tax, just hides it on the first
   turn; every restart still reloads, and a restart mid-warm still stalls.
3. **whisper.cpp-server** (VoiceMode's STT). Rejected: faster-whisper is already
   our engine and Speaches bundles it plus TTS.

## 4. Architecture

```
                 ┌──────────────────────────────────────┐
                 │  speaches  (ONE container, GPU, warm) │
                 │   POST /v1/audio/transcriptions  (STT)│
                 │   POST /v1/audio/speech          (TTS)│
                 │   faster-whisper medium + Kokoro, both│
                 │   resident                            │
                 └───────▲───────────────────▲──────────┘
              STT (batch │ per VAD segment)   │ TTS (per assistant utterance)
   ┌───────────────┐     │                    │
   │ voice-agent-  │─────┘                    │
   │ livekit       │──────────────────────────┘
   └───────────────┘     │                    │
   ┌───────────────┐     │                    │
   │ voice-agent-  │─────┘                    │
   │ claude-code   │──────────────────────────┘
   └───────────────┘
```

The Pipecat pipeline shape in `build_voice_pipeline_task` is **unchanged**:

```
transport.input() → STT → user_aggregator → LLM → TTS → transport.output() → assistant_aggregator
```

Only the `STT` and `TTS` _node implementations_ swap from in-process model
holders to HTTP clients pointed at Speaches. VAD, turn-taking, the LLM/brain
stage, and tool wiring are untouched.

### Components

| Unit                               | What it does                                                                            | Interface                                                | Depends on                 |
| ---------------------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------- | -------------------------- |
| `speaches` container               | Warm faster-whisper + Kokoro; transcribes a segment / synthesizes an utterance per call | `POST /v1/audio/transcriptions`, `POST /v1/audio/speech` | GPU, weight cache volume   |
| `_build_stt()` in `voice_agent.py` | Returns a Pipecat STT node: Speaches client when `stt_mode=sidecar`, else in-process    | `(site_config) → STTService`                             | `voice_agent_stt_base_url` |
| `_build_tts()` in `voice_agent.py` | Returns a Pipecat TTS node: Speaches client when `tts_mode=sidecar`, else in-process    | `(site_config) → TTSService`                             | `voice_agent_tts_base_url` |

**Isolation note:** `build_voice_pipeline_task` is already a ~160-line builder
doing several jobs (LLM, STT, TTS, VAD, context). Extracting `_build_stt` and
`_build_tts` as small focused helpers isolates the swap behind two seams and
shrinks the builder — a targeted improvement that serves this work.

## 5. Pipecat client wiring (the one real risk)

Speaches' `/v1/audio/transcriptions` is a **batch** endpoint (POST a complete
audio file, get text). Pipecat STT services are frame-based; the correct base
class is Pipecat's **`SegmentedSTTService`**, which buffers VAD-bounded audio and
calls `run_stt(audio)` once per segment — exactly our turn model. Two wiring
options, in preference order:

- **(A) Pipecat's built-in OpenAI-compatible STT/TTS services**, pointed at the
  Speaches `base_url`. If Pipecat ships an `OpenAISTTService` (segmented) and
  `OpenAITTSService` that accept a custom `base_url`, we use them directly —
  near-zero custom code.
- **(B) Thin custom `SegmentedSTTService` subclass** (~40–60 lines) that POSTs
  the segment to Speaches `/v1/audio/transcriptions`, plus Pipecat's
  `OpenAITTSService` (or a thin TTS client) pointed at `/v1/audio/speech`.

**Phase 0 of implementation is a verification spike** that resolves A-vs-B
against the actually-installed Pipecat version _before_ any container work.
This is the "verify-first" gate from the prior session notes — we do not refactor
the live audio path on an assumption.

## 6. Configuration (DB-backed, per config-in-DB principle)

New `app_settings` keys (seeded by a migration):

| Key                        | Default                   | Meaning                                                                                    |
| -------------------------- | ------------------------- | ------------------------------------------------------------------------------------------ |
| `voice_agent_stt_mode`     | `inprocess`               | `sidecar` \| `inprocess` — explicit, no silent default                                     |
| `voice_agent_stt_base_url` | `http://speaches:8000/v1` | STT endpoint (compose service name)                                                        |
| `voice_agent_tts_mode`     | `inprocess`               | `sidecar` \| `inprocess`                                                                   |
| `voice_agent_tts_base_url` | `http://speaches:8000/v1` | TTS endpoint — **same Speaches service by default**, separate key so it can be split later |

Existing `voice_agent_whisper_model` and `voice_agent_tts_voice` (plus the
per-room override `voice_agent_claude_code_tts_voice`) are **reused** — the
model size and voice id pass through to the Speaches request, so no settings are
orphaned.

**Cutover is deliberately a flag flip.** The migration seeds the URLs but leaves
`*_mode = inprocess`, so merging is a **zero-behavior-change** deploy
(backcompat-now-required). We stand up Speaches, verify it out-of-band, then flip
`voice_agent_stt_mode=sidecar` / `voice_agent_tts_mode=sidecar` and restart the
two voice containers to cut over. Flipping back is instant if anything regresses.

## 7. VRAM / resource budget

The GPU (RTX 5090, 32GB) is already shared by Ollama, image-gen, and Wan (Wan
auto-unloads on idle). The warm Speaches process holds VRAM **continuously**:

- **faster-whisper medium, int8/float16:** ~1.5–2GB resident.
- **Kokoro (82M):** trivial (~few hundred MB on GPU). **Decision: run Kokoro on
  GPU inside Speaches** (default); if VRAM pressure appears, Speaches can run
  Kokoro on CPU — documented as the escape hatch (the exit-137/SIGKILL
  memory-pressure history is why this knob stays visible).

Net new continuous VRAM ≈ 2GB for the one Speaches process. Confirm against
`nvidia_gpu_*` on the Hardware & Power dashboard after cutover.

## 8. Observability

- Speaches gets a Docker `healthcheck` (HTTP probe) like image-gen/wan.
- Add the service to the System Health / Hardware & Power dashboards (up/down).
- The existing Pipecat metrics (`enable_metrics=True`) already time the STT/TTS
  stages — confirm the first-utterance-after-restart time drops from ~12s to
  sub-second on the Observability board.
- Per-room Pyroscope tag deferred (already noted as deferred in #1088).

## 9. Testing

- **Unit:** `_build_stt` / `_build_tts` return the Speaches client when
  `*_mode=sidecar` + a URL is set, and the in-process service otherwise; fail
  loud when `mode=sidecar` but the URL is empty (no-silent-defaults).
- **Unit:** the STT client (if option B) POSTs the right multipart body and
  parses `{text}`, against a mocked HTTP endpoint.
- **Integration smoke:** with Speaches up, a canned WAV through the STT client
  returns expected text; a short string through the TTS client returns non-empty
  audio bytes.
- **Live:** restart `voice-agent-livekit`, speak immediately, confirm no ~12s
  stall (the acceptance test that maps directly to the bug).

## 10. Rollout / acceptance

1. **Phase 0** — Pipecat verification spike (resolve §5 A-vs-B). Gate.
2. **Phase 1** — add the `speaches` compose service (image-gen-style), weight-cache
   volume, healthcheck, GPU reservation. Bring it up, verify out-of-band (curl
   both OpenAI endpoints; confirm `bf_emma` TTS works).
3. **Phase 2** — `_build_stt`/`_build_tts` seams + the client(s) + migration
   seeding the keys (mode defaults `inprocess`). Tests. Merge = no behavior
   change.
4. **Phase 3** — flip `*_mode=sidecar`, restart both voice containers,
   live-verify the cold-start is gone. Update `docs/operations/voice-stt-tts.md`.

**Acceptance:** first utterance after a voice-container restart is answered with
sub-second STT latency (no ~12s stall); both rooms work; VRAM headroom intact on
the dashboard; flip-back to `inprocess` proven as the rollback.

## 11. Resolved decisions

- **Q3 — image:** ✅ **Speaches, one container for both STT and TTS** (Matt,
  2026-06-04). Replaces the rev-1 two-sidecar plan.
- **Q1 — Kokoro placement:** ✅ **GPU inside Speaches** (default); CPU documented
  as the VRAM-pressure fallback.
- **Q2 — in-process path:** ✅ **kept as the rollback seam** (the `inprocess`
  mode), at least until sidecar cutover is proven stable in prod. Revisit
  removal a release later.
