# Voice: warm STT/TTS sidecar services (#1088)

**Status:** Draft for review
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
it *twice* (once per room). The user-visible symptom: the first utterance after
any restart gets ~12s of dead air, and can time out entirely or be cancelled by
VAD interruption (observed live 2026-06-04 — a voice turn timed out).

This directly undermines the "always-on" goal of #1006.

## 2. Goal / Non-goals

**Goal:** Decouple the STT and TTS models from the voice-agent hot path so that
restarting a voice-agent container no longer reloads models. The first utterance
after a restart should be ~as fast as a warm turn (sub-second model latency),
because the model lives in a separate, long-lived, warm service.

**Non-goals:**
- Context/session continuity — already solved (pinned `claude -p --resume`,
  ahead of VoiceMode). This issue is **only** the STT/TTS latency layer.
- Changing the LLM/brain dispatch, the two-room split, or LiveKit transport.
- Streaming/partial-transcript STT. We keep the current segmented (VAD-bounded)
  turn model; we are only moving *where* the segment is transcribed.

## 3. Approach

Mirror the **SDXL idiom** already in the stack: a GPU model runs as its own
long-lived HTTP container (`sdxl-server` on `:9836`), and callers are thin
clients. We stand up **one shared pair** of warm sidecars; **both** voice rooms
become thin clients of the same pair (a shared sensor, like SDXL serves the
whole pipeline).

- **STT sidecar:** `faster-whisper-server` (OpenAI-compatible
  `POST /v1/audio/transcriptions`), GPU, model preloaded and kept resident.
- **TTS sidecar:** `kokoro-fastapi` (OpenAI-compatible `POST /v1/audio/speech`),
  exposes the **same Kokoro voice packs we already use** (`bf_emma`,
  `bf_isabella`) — so the per-room voice settings carry over **unchanged**.

Both are **prebuilt, mature OSS images** (no-wheel-reinvention): we run them as-is
and point Pipecat at them. The edge that's ours (always-on + two-room + brain
dispatch) is untouched.

### Why these two specifically
- They are the exact services VoiceMode runs warm; this is a known-good pattern.
- Both speak the **OpenAI audio API**, which Pipecat already has client services
  for — minimal custom glue.
- Kokoro-fastapi uses the identical voice packs → zero voice-quality regression,
  zero settings churn.

### Considered alternatives
1. **whisper.cpp-server** (VoiceMode's default) instead of faster-whisper-server.
   Rejected: faster-whisper (CTranslate2) is already our in-process engine, the
   medium weights are already cached, and it has a clean OpenAI-compatible
   server image. Lower migration risk, same model family.
2. **Keep models in-process but pre-warm on boot** (background thread loads the
   model before the first turn). Rejected: doesn't remove the tax, just hides it
   on the *first* turn; every restart still reloads, and a restart mid-warm
   still stalls. Doesn't serve "instant restarts."
3. **Per-room sidecars** (one STT+TTS pair per room). Rejected: doubles VRAM for
   no benefit — STT/TTS are stateless request/response, trivially shared.

## 4. Architecture

```
                 ┌─────────────────────────────┐
                 │  faster-whisper-server       │  GPU, warm
                 │  :8000  /v1/audio/transcripts │  (medium, resident)
                 └──────────────▲──────────────┘
                                │ HTTP (batch, per VAD segment)
   ┌───────────────┐           │
   │ voice-agent-  │───────────┘
   │ livekit       │───────────┐
   └───────────────┘           │ HTTP (per assistant utterance)
   ┌───────────────┐           ▼
   │ voice-agent-  │   ┌─────────────────────────────┐
   │ claude-code   │──▶│  kokoro-fastapi              │  GPU (or CPU), warm
   └───────────────┘   │  :8880  /v1/audio/speech     │  (82M, resident)
                       └─────────────────────────────┘
```

The Pipecat pipeline shape in `build_voice_pipeline_task` is **unchanged**:

```
transport.input() → STT → user_aggregator → LLM → TTS → transport.output() → assistant_aggregator
```

Only the `STT` and `TTS` *node implementations* swap from in-process model
holders to HTTP clients. VAD, turn-taking, the LLM/brain stage, and tool wiring
are untouched.

### Components

| Unit | What it does | Interface | Depends on |
| --- | --- | --- | --- |
| `faster-whisper-server` container | Warm Whisper; transcribes one audio segment per call | `POST /v1/audio/transcriptions` (multipart) → `{text}` | GPU, weight cache volume |
| `kokoro-fastapi` container | Warm Kokoro; synthesizes one utterance per call | `POST /v1/audio/speech` → audio bytes | GPU/CPU, weight cache volume |
| `_build_stt()` in `voice_agent.py` | Returns a Pipecat STT node: sidecar client when configured, else in-process | `(site_config) → STTService` | sidecar URL setting |
| `_build_tts()` in `voice_agent.py` | Returns a Pipecat TTS node: sidecar client when configured, else in-process | `(site_config) → TTSService` | sidecar URL setting |

**Isolation note:** `build_voice_pipeline_task` is already a single ~160-line
builder doing several jobs (LLM, STT, TTS, VAD, context). Extracting `_build_stt`
and `_build_tts` as small focused helpers is a targeted improvement that serves
this work — it isolates the swap behind two seams and shrinks the builder.

## 5. Pipecat client wiring (the one real risk)

faster-whisper-server's `/v1/audio/transcriptions` is a **batch** endpoint (POST
a complete audio file, get text back). Pipecat STT services are frame-based.
The correct base class is Pipecat's **`SegmentedSTTService`**, which buffers
VAD-bounded audio and calls `run_stt(audio)` once per segment — exactly our
turn model. Two wiring options, in preference order:

- **(A) Pipecat's built-in OpenAI-compatible STT/TTS services**, pointed at the
  sidecar `base_url`. If Pipecat ships `OpenAISTTService` (segmented) and
  `OpenAITTSService` that accept a custom `base_url`, we use them directly —
  near-zero custom code.
- **(B) Thin custom `SegmentedSTTService` subclass** (~40–60 lines) that POSTs
  the segment to `/v1/audio/transcriptions`, plus Pipecat's `OpenAITTSService`
  (or a thin TTS client) for Kokoro. Used only if (A)'s services don't exist or
  don't accept a base_url.

**Phase 0 of implementation is a verification spike** that resolves A-vs-B
against the actually-installed Pipecat version *before* any container work.
This is the "verify-first" gate from the prior session notes — we do not
refactor the live audio path on an assumption.

## 6. Configuration (DB-backed, per config-in-DB principle)

New `app_settings` keys (seeded by a migration):

| Key | Default | Meaning |
| --- | --- | --- |
| `voice_agent_stt_mode` | `inprocess` | `sidecar` \| `inprocess` — explicit, no silent default |
| `voice_agent_stt_base_url` | `http://faster-whisper-server:8000/v1` | STT sidecar (compose service name) |
| `voice_agent_tts_mode` | `inprocess` | `sidecar` \| `inprocess` |
| `voice_agent_tts_base_url` | `http://kokoro-fastapi:8880/v1` | TTS sidecar |

Existing `voice_agent_whisper_model` and `voice_agent_tts_voice` (plus the
per-room override `voice_agent_claude_code_tts_voice`) are **reused** — the
model size and voice id pass through to the sidecar request, so no settings are
orphaned.

**Cutover is deliberately a flag flip.** The migration seeds the URLs but leaves
`*_mode = inprocess`, so merging is a **zero-behavior-change** deploy
(backcompat-now-required). We stand up the sidecars, verify them out-of-band,
then flip `voice_agent_stt_mode=sidecar` / `voice_agent_tts_mode=sidecar` and
restart the two voice containers to cut over. Flipping back is instant if
anything regresses.

## 7. VRAM / resource budget

The GPU (RTX 5090, 32GB) is already shared by Ollama, SDXL, and Wan (Wan
auto-unloads on idle). A warm Whisper sidecar holds VRAM **continuously**:

- **Whisper-medium, int8/float16:** ~1.5–2GB resident. Acceptable headroom.
- **Kokoro (82M):** trivial; can run **CPU** to spend zero VRAM. Default Kokoro
  to GPU for latency but document the CPU env flag as the VRAM-pressure escape
  hatch (the exit-137/SIGKILL memory-pressure history is the reason to keep this
  knob visible).

Net new continuous VRAM ≈ 1.5–2GB. We confirm against `nvidia_gpu_*` on the
Hardware & Power dashboard after cutover (visual-verification principle).

## 8. Observability

- Both sidecars get a Docker `healthcheck` (HTTP probe) like sdxl/wan.
- Add the two services to the System Health / Hardware & Power dashboards
  (service up/down). Latency: the existing Pipecat metrics
  (`enable_metrics=True`) already time the STT/TTS stages — we confirm the
  first-utterance-after-restart time drops from ~12s to sub-second on the
  Observability board.
- Per-room Pyroscope tag deferred (already noted as deferred in #1088).

## 9. Testing

- **Unit:** `_build_stt` / `_build_tts` return the sidecar client when
  `*_mode=sidecar` + a URL is set, and the in-process service otherwise; fail
  loud when `mode=sidecar` but the URL is empty (no-silent-defaults).
- **Unit:** the custom STT client (if option B) POSTs the right multipart body
  and parses `{text}`, against a mocked HTTP endpoint.
- **Integration smoke:** with the sidecars up, a canned WAV through the STT
  client returns expected text; a short string through the TTS client returns
  non-empty audio bytes.
- **Live:** restart `voice-agent-livekit`, speak immediately, confirm no ~12s
  stall (the acceptance test that maps directly to the bug).

## 10. Rollout / acceptance

1. **Phase 0** — Pipecat verification spike (resolve §5 A-vs-B). Gate.
2. **Phase 1** — add the two compose services (SDXL-style), weight-cache volumes,
   healthchecks. Bring them up, verify out-of-band (curl the OpenAI endpoints).
3. **Phase 2** — `_build_stt`/`_build_tts` seams + the client(s) + migration
   seeding the keys (mode defaults `inprocess`). Tests. Merge = no behavior
   change.
4. **Phase 3** — flip `*_mode=sidecar`, restart both voice containers, live-verify
   the cold-start is gone. Update `docs/operations/voice-stt-tts.md`.

**Acceptance:** first utterance after a voice-container restart is answered with
sub-second STT latency (no ~12s stall); both rooms work; VRAM headroom intact on
the dashboard; flip-back to `inprocess` proven as the rollback.

## 11. Open questions for review

- **Q1:** OK to default Kokoro TTS to **GPU**, with CPU documented as the
  VRAM-pressure fallback? (vs. CPU-by-default to spend zero VRAM.)
- **Q2:** Keep the in-process path indefinitely as the rollback seam, or remove
  it a release after cutover proves stable?
- **Q3:** faster-whisper-server vs. its successor **Speaches** (same project,
  renamed) — pin the stable `faster-whisper-server` image, or adopt Speaches now?
