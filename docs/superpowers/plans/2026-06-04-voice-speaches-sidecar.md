# Voice Speaches Warm STT/TTS Sidecar — Implementation Plan (#1088)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move STT and TTS off the voice-agent hot path into one warm GPU-resident `speaches` container so a voice-container restart no longer pays the ~12s Whisper cold-start.

**Architecture:** Stand up [Speaches](https://github.com/speaches-ai/speaches) (`ghcr.io/speaches-ai/speaches:latest-cuda`, port 8000) — one container serving BOTH OpenAI-compatible endpoints: `POST /v1/audio/transcriptions` (faster-whisper) and `POST /v1/audio/speech` (Kokoro). Both voice rooms become thin clients via Pipecat 1.1.0's already-installed `OpenAISTTService` (a `SegmentedSTTService`) and `OpenAITTSService`, both pointed at the Speaches `base_url`. The swap is gated behind two DB-backed `*_mode` settings defaulting to `inprocess`, so merging is a zero-behavior-change deploy; cutover is a flag flip + restart, and flip-back is the rollback.

**Tech Stack:** Docker Compose, Speaches (faster-whisper + Kokoro), Pipecat 1.1.0 (`pipecat.services.openai.stt/tts`), Postgres `app_settings` migration, pytest.

---

## Phase 0 — Verification spike (DONE, 2026-06-04)

Resolved **before** any code, per the design's verify-first gate:

- **Pipecat 1.1.0 is installed in the voice image** (`docker exec poindexter-voice-agent-livekit python -c "import pipecat"` → `Pipecat 1.1.0`).
- `pipecat.services.openai.stt.OpenAISTTService(*, model, api_key, base_url, language, ...)` — MRO is `[BaseWhisperSTTService, SegmentedSTTService, STTService, ...]`. **It is segmented** → buffers a VAD-bounded turn and does one batch POST to `/v1/audio/transcriptions`. This is exactly our turn model. **Resolves design §5 to Option A (no custom subclass).**
- `pipecat.services.openai.tts.OpenAITTSService(*, base_url, voice, model, speed, ...)` → POSTs `/v1/audio/speech`. Bonus: it honors `speed` (the in-process `KokoroTTSService` silently ignored `tts_speed`).
- Speaches confirmed: STT `POST /v1/audio/transcriptions` (model id `Systran/faster-whisper-medium`); TTS `POST /v1/audio/speech` taking `{model, voice, input, speed}` with Kokoro model `speaches-ai/Kokoro-82M-v1.0-ONNX`; voices use the stock Kokoro pack (`af_heart`, `bf_*`).

**Key nuance baked into this plan:** the STT _model value_ differs by mode. In-process Pipecat wants the Whisper enum (`medium`); Speaches wants an HF id (`Systran/faster-whisper-medium`). So sidecar STT gets its **own** key (`voice_agent_stt_model`) rather than reusing `voice_agent_whisper_model`. The TTS **voice** id (`bf_emma`/`bf_isabella`) is identical in both modes, so `voice_agent_tts_voice` (and the per-room `voice_agent_claude_code_tts_voice` override) carry over unchanged; only the TTS **model** id is new (`voice_agent_tts_model`).

---

## File Structure

| File                                                                                          | Responsibility                                                                                   | Action |
| --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ------ |
| `docker-compose.local.yml`                                                                    | Add the `speaches` GPU service (mirror of `sdxl-server`)                                         | Modify |
| `src/cofounder_agent/services/migrations/20260604_120000_seed_voice_speaches_sidecar_keys.py` | Seed the 6 sidecar `app_settings` keys (modes default `inprocess`)                               | Create |
| `src/cofounder_agent/services/voice_agent.py`                                                 | Add `_build_stt()` / `_build_tts()` mode-aware seams; wire them into `build_voice_pipeline_task` | Modify |
| `src/cofounder_agent/tests/unit/services/test_voice_agent_build_stt_tts.py`                   | Unit tests for the two seams (both modes + fail-loud)                                            | Create |
| `docs/operations/voice-stt-tts.md`                                                            | Runbook: architecture, cutover, rollback, VRAM                                                   | Create |

---

## New `app_settings` keys (all seeded by the Phase-2 migration)

| Key                        | Default                            | Meaning                                                                                 |
| -------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------- |
| `voice_agent_stt_mode`     | `inprocess`                        | `sidecar` \| `inprocess`. No silent default — invalid value fails loud.                 |
| `voice_agent_stt_base_url` | `http://speaches:8000/v1`          | Speaches STT endpoint (compose service name).                                           |
| `voice_agent_stt_model`    | `Systran/faster-whisper-medium`    | faster-whisper HF id used **only** in sidecar mode.                                     |
| `voice_agent_tts_mode`     | `inprocess`                        | `sidecar` \| `inprocess`.                                                               |
| `voice_agent_tts_base_url` | `http://speaches:8000/v1`          | Speaches TTS endpoint — same service by default; separate key so it can be split later. |
| `voice_agent_tts_model`    | `speaches-ai/Kokoro-82M-v1.0-ONNX` | Kokoro model id used **only** in sidecar mode.                                          |

Reused unchanged: `voice_agent_whisper_model` (in-process enum), `voice_agent_tts_voice` (`bf_emma`), `voice_agent_claude_code_tts_voice` (`bf_isabella`), `voice_agent_tts_speed`.

---

### Task 1: Add the `speaches` compose service (Phase 1)

Mirrors `sdxl-server` (GPU reservation + HF cache + healthcheck). `WHISPER__TTL: "-1"` keeps the faster-whisper model resident so "warm" is actually warm (the model is NOT idle-offloaded). No `depends_on` from the voice agents — sidecar mode is opt-in, and coupling startup would delay voice boot in the default `inprocess` mode.

**Files:**

- Modify: `docker-compose.local.yml` (insert a new service after the `wan-server` block, before `pgadmin`)

- [ ] **Step 1: Insert the `speaches` service**

Insert directly after the `wan-server` service's `healthcheck` block (after the line `      retries: 3` that closes `wan-server`, around line 982) and before the `# pgAdmin` comment:

```yaml
# ===========================================
# Speaches — warm STT + TTS sidecar (#1088)
# ONE GPU container serving BOTH OpenAI-compatible endpoints:
#   POST /v1/audio/transcriptions  (faster-whisper)
#   POST /v1/audio/speech          (Kokoro — same bf_emma/bf_isabella
#                                   voices the in-process path uses)
# The voice-agent containers are thin clients (Pipecat OpenAISTTService /
# OpenAITTSService pointed at http://speaches:8000/v1) only when
# voice_agent_stt_mode / voice_agent_tts_mode = sidecar. WHISPER__TTL=-1
# keeps the model resident so a voice-container restart never reloads it.
# ===========================================
speaches:
  image: ghcr.io/speaches-ai/speaches:latest-cuda
  container_name: poindexter-speaches
  restart: unless-stopped
  ports:
    - '8001:8000'
  environment:
    # Keep loaded models resident forever (-1 = never idle-offload), so
    # the first turn after a voice-container restart is warm. The whole
    # point of #1088: move the cold-start cost to Speaches' boot (one-time)
    # instead of every voice-container restart.
    WHISPER__TTL: '-1'
  volumes:
    # Share the host HuggingFace cache (faster-whisper + Kokoro weights
    # download once, survive rebuilds) — same idiom as sdxl/wan.
    - ${USERPROFILE:-.}/.cache/huggingface:/home/ubuntu/.cache/huggingface
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  extra_hosts:
    - 'host.docker.internal:host-gateway'
  healthcheck:
    test:
      [
        'CMD-SHELL',
        'python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen(''http://localhost:8000/health'').status==200 else 1)"',
      ]
    interval: 30s
    timeout: 10s
    retries: 3
```

> Note on the host port: `8001:8000` (the **container** listens on 8000; the voice agents reach it in-network as `http://speaches:8000`). Host 8001 avoids colliding with anything already on 8000 (GlitchTip web is on 8080, but keep 8000 free for ad-hoc). The in-network base_url is unaffected.

- [ ] **Step 2: Bring it up and confirm the container is healthy**

```bash
docker compose -f docker-compose.local.yml up -d speaches
docker ps --filter name=poindexter-speaches --format "{{.Names}} {{.Status}}"
```

Expected: `poindexter-speaches Up (healthy)` after the first model pull (first boot downloads weights; allow a few minutes, then the healthcheck flips to healthy).

- [ ] **Step 3: Verify BOTH endpoints out-of-band (the real acceptance for Phase 1)**

STT (warm check — run twice, second call must be sub-second):

```bash
# generate a 1s test wav inside the container
docker exec poindexter-speaches python -c "import wave,struct;w=wave.open('/tmp/t.wav','wb');w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b''.join(struct.pack('<h',0) for _ in range(16000)));w.close()"
# first call (loads model), then time the second (warm)
docker exec poindexter-speaches sh -c "curl -s -o /dev/null http://localhost:8000/v1/audio/transcriptions -F file=@/tmp/t.wav -F model=Systran/faster-whisper-medium; echo '--- warm timing ---'; time curl -s http://localhost:8000/v1/audio/transcriptions -F file=@/tmp/t.wav -F model=Systran/faster-whisper-medium"
```

Expected: the warm (second) call returns in well under 1s.

TTS (confirm `bf_emma` AND `bf_isabella` are valid Kokoro voices in the pack):

```bash
docker exec poindexter-speaches sh -c 'for v in bf_emma bf_isabella; do echo "voice=$v"; curl -s -o /tmp/$v.mp3 http://localhost:8000/v1/audio/speech -H "Content-Type: application/json" -d "{\"model\":\"speaches-ai/Kokoro-82M-v1.0-ONNX\",\"voice\":\"$v\",\"input\":\"Glad Labs voice check.\"}"; ls -la /tmp/$v.mp3; done'
```

Expected: both `bf_emma.mp3` and `bf_isabella.mp3` are non-empty audio files.

> If `/health` 404s, the Speaches health path differs — re-point the healthcheck at `http://localhost:8000/v1/models` (the OpenAI models-list endpoint always exists) and re-up. If `bf_emma`/`bf_isabella` are NOT in the pack, list the available voices with `curl -s http://localhost:8000/v1/audio/speech/voices` (or `/v1/models`) and pick the nearest British-female voices; record the chosen ids and use them in Task 5's live verify — do NOT silently fall back.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.local.yml
git commit -F - <<'EOF'
feat(voice): add warm Speaches STT/TTS sidecar container (#1088)

One GPU container (ghcr.io/speaches-ai/speaches:latest-cuda) serving both
/v1/audio/transcriptions (faster-whisper) and /v1/audio/speech (Kokoro).
WHISPER__TTL=-1 keeps the model resident so a voice-container restart no
longer reloads Whisper. Voice agents stay on the in-process path until the
sidecar *_mode flags flip (next tasks) — this commit is additive.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 2: Migration seeding the 6 sidecar keys (Phase 2)

Modes default `inprocess`, so applying this migration is a **zero-behavior-change** deploy. Mirrors the existing `20260604_051500_seed_voice_claude_code_tts_voice_override.py` pattern (`INSERT ... ON CONFLICT (key) DO NOTHING`).

**Files:**

- Create: `src/cofounder_agent/services/migrations/20260604_120000_seed_voice_speaches_sidecar_keys.py`

- [ ] **Step 1: Write the migration**

```python
"""Migration 20260604_120000: seed warm Speaches STT/TTS sidecar keys.

ISSUE: Glad-Labs/glad-labs-stack#1088 (move STT/TTS to a warm sidecar to
kill the ~12s Whisper cold-start; part of the #1006 always-on workstream).

Seeds six keys that let ``build_voice_pipeline_task`` build STT/TTS as
thin HTTP clients of the warm ``speaches`` container instead of loading
Whisper + Kokoro in-process. Both ``*_mode`` keys default to ``inprocess``
so this migration is a **no-op for behavior** — the voice path is
unchanged until an operator flips a mode to ``sidecar`` and restarts the
voice containers (backcompat-now-required: merge changes nothing live).

The STT model value differs by mode: in-process Pipecat wants the Whisper
enum (``medium``, via the reused ``voice_agent_whisper_model``); Speaches
wants an HF id (``Systran/faster-whisper-medium``), so sidecar STT gets its
own ``voice_agent_stt_model``. The TTS voice id (``bf_emma`` /
``bf_isabella``) is identical in both modes, so only the TTS *model* id is
new (``voice_agent_tts_model``).

``ON CONFLICT DO NOTHING`` so a live value is never clobbered by a re-apply.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_KEYS = [
    (
        "voice_agent_stt_mode",
        "inprocess",
        "STT backend for the voice pipeline (#1088): 'sidecar' = thin "
        "client of the warm Speaches container; 'inprocess' = load "
        "faster-whisper in-process (legacy). No silent default — an "
        "invalid value fails loud at pipeline build.",
    ),
    (
        "voice_agent_stt_base_url",
        "http://speaches:8000/v1",
        "Speaches STT endpoint (OpenAI-compatible) used when "
        "voice_agent_stt_mode=sidecar. Compose service name on the "
        "shared poindexter network.",
    ),
    (
        "voice_agent_stt_model",
        "Systran/faster-whisper-medium",
        "faster-whisper model id passed to Speaches when "
        "voice_agent_stt_mode=sidecar. NOTE: an HF id, not the Pipecat "
        "Whisper enum used by the in-process voice_agent_whisper_model.",
    ),
    (
        "voice_agent_tts_mode",
        "inprocess",
        "TTS backend for the voice pipeline (#1088): 'sidecar' = thin "
        "client of the warm Speaches container; 'inprocess' = run Kokoro "
        "in-process (legacy). No silent default.",
    ),
    (
        "voice_agent_tts_base_url",
        "http://speaches:8000/v1",
        "Speaches TTS endpoint used when voice_agent_tts_mode=sidecar. "
        "Same Speaches service as STT by default; separate key so STT and "
        "TTS can be split onto different hosts later without a migration.",
    ),
    (
        "voice_agent_tts_model",
        "speaches-ai/Kokoro-82M-v1.0-ONNX",
        "Kokoro model id passed to Speaches when voice_agent_tts_mode="
        "sidecar. The voice id (bf_emma / bf_isabella) still comes from "
        "voice_agent_tts_voice / voice_agent_claude_code_tts_voice — those "
        "carry over unchanged because the voice pack is identical.",
    ),
]


async def up(pool) -> None:
    """Seed the Speaches sidecar keys, idempotently."""
    async with pool.acquire() as conn:
        for key, value, description in _KEYS:
            await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_active, is_secret)
                VALUES ($1, $2, 'voice', $3, true, false)
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
                description,
            )
        logger.info(
            "Migration seed_voice_speaches_sidecar_keys: applied (%d keys)",
            len(_KEYS),
        )


async def down(pool) -> None:
    """Drop the Speaches sidecar keys."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key = ANY($1::text[])
            """,
            [k for k, _, _ in _KEYS],
        )
        logger.info(
            "Migration seed_voice_speaches_sidecar_keys down: reverted"
        )
```

- [ ] **Step 2: Lint the migration (catches collisions + missing runner interface)**

Run: `cd src/cofounder_agent && python ../../scripts/ci/migrations_lint.py`
Expected: exit 0, no collision reported for `20260604_120000`.

- [ ] **Step 3: Apply against prod DB and verify the six rows**

Run:

```bash
docker exec -i poindexter-worker python - <<'PY'
import asyncio, asyncpg, os
async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    # Apply via the worker's migration runner entry (idempotent).
    from services.migrations import _load_and_run  # if available; else run the file's up()
    rows = await conn.fetch(
        "SELECT key, value FROM app_settings WHERE key LIKE 'voice_agent_stt_%' "
        "OR key LIKE 'voice_agent_tts_mode' OR key LIKE 'voice_agent_tts_base_url' "
        "OR key LIKE 'voice_agent_tts_model' ORDER BY key"
    )
    for r in rows: print(r['key'], '=', r['value'])
    await conn.close()
asyncio.run(main())
PY
```

Expected (after the migration runs at next worker boot, or applied directly): the six keys present with their defaults; `*_mode` both `inprocess`.

> The canonical apply path is the worker's startup migration runner (it records the filename in `schema_migrations`). Restarting the worker after merge applies it. For an immediate check before merge, the runner can be invoked directly per `docs/operations/migrations.md`.

- [ ] **Step 4: Commit**

```bash
git add src/cofounder_agent/services/migrations/20260604_120000_seed_voice_speaches_sidecar_keys.py
git commit -F - <<'EOF'
feat(voice): seed Speaches sidecar app_settings keys, mode=inprocess (#1088)

Six keys (stt/tts mode + base_url + model). Both *_mode default to
inprocess so this is a zero-behavior-change migration; the voice path is
unchanged until an operator flips to sidecar and restarts the containers.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 3: `_build_stt()` seam (Phase 2, TDD)

A focused helper that returns the in-process `WhisperSTTService` or the Speaches-backed `OpenAISTTService` based on `voice_agent_stt_mode`. Fails loud on `sidecar` with an empty url/model, and on any unknown mode (no silent defaults).

**Files:**

- Modify: `src/cofounder_agent/services/voice_agent.py` (add helper near `_resolve_whisper_model`, ~line 150)
- Test: `src/cofounder_agent/tests/unit/services/test_voice_agent_build_stt_tts.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the mode-aware STT/TTS seams in voice_agent (#1088).

Reuses the heavy-pipecat stub harness from test_voice_agent_service_mode
so ``import services.voice_agent`` resolves without pipecat installed
(the unit-test env has no pipecat — it lives in the voice image).
"""

from __future__ import annotations

import sys
import types

import pytest

from tests.unit.services.test_voice_agent_service_mode import (
    _ensure_pipecat_stubs,
)


def _stub_openai_services() -> tuple[type, type]:
    """Inject fake pipecat.services.openai.{stt,tts} modules and return the
    two sentinel classes so tests can assert the sidecar path used them.
    """
    stt_cls = type(
        "OpenAISTTService",
        (),
        {"__init__": lambda self, **kw: setattr(self, "kw", kw)},
    )
    tts_cls = type(
        "OpenAITTSService",
        (),
        {"__init__": lambda self, **kw: setattr(self, "kw", kw)},
    )
    pkg = types.ModuleType("pipecat.services.openai")
    sys.modules["pipecat.services.openai"] = pkg
    stt_mod = types.ModuleType("pipecat.services.openai.stt")
    stt_mod.OpenAISTTService = stt_cls
    sys.modules["pipecat.services.openai.stt"] = stt_mod
    tts_mod = types.ModuleType("pipecat.services.openai.tts")
    tts_mod.OpenAITTSService = tts_cls
    sys.modules["pipecat.services.openai.tts"] = tts_mod
    return stt_cls, tts_cls


class _Cfg:
    def __init__(self, **values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


@pytest.fixture(autouse=True)
def _stubs():
    _ensure_pipecat_stubs()
    yield


def test_build_stt_inprocess_returns_whisper():
    import services.voice_agent as va
    cfg = _Cfg(voice_agent_stt_mode="inprocess", voice_agent_whisper_model="base")
    stt = va._build_stt(cfg)
    assert stt.__class__.__name__ == "WhisperSTTService"


def test_build_stt_sidecar_returns_openai_client():
    stt_cls, _ = _stub_openai_services()
    import services.voice_agent as va
    cfg = _Cfg(
        voice_agent_stt_mode="sidecar",
        voice_agent_stt_base_url="http://speaches:8000/v1",
        voice_agent_stt_model="Systran/faster-whisper-medium",
    )
    stt = va._build_stt(cfg)
    assert isinstance(stt, stt_cls)
    assert stt.kw["base_url"] == "http://speaches:8000/v1"
    assert stt.kw["model"] == "Systran/faster-whisper-medium"


def test_build_stt_sidecar_empty_url_fails_loud():
    _stub_openai_services()
    import services.voice_agent as va
    cfg = _Cfg(voice_agent_stt_mode="sidecar", voice_agent_stt_base_url="", voice_agent_stt_model="x")
    with pytest.raises(ValueError, match="voice_agent_stt_base_url"):
        va._build_stt(cfg)


def test_build_stt_unknown_mode_fails_loud():
    import services.voice_agent as va
    cfg = _Cfg(voice_agent_stt_mode="bogus")
    with pytest.raises(ValueError, match="voice_agent_stt_mode"):
        va._build_stt(cfg)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && python -m pytest tests/unit/services/test_voice_agent_build_stt_tts.py -k build_stt -v`
Expected: FAIL — `AttributeError: module 'services.voice_agent' has no attribute '_build_stt'`.

- [ ] **Step 3: Implement `_build_stt`**

Add after `_resolve_whisper_model` (ends ~line 149) in `services/voice_agent.py`:

```python
def _build_stt(site_config: Any) -> Any:
    """Build the STT stage per ``voice_agent_stt_mode`` (#1088).

    ``sidecar`` → a Pipecat ``OpenAISTTService`` (a SegmentedSTTService:
    buffers a VAD-bounded turn, one batch POST to /v1/audio/transcriptions)
    pointed at the warm Speaches container. ``inprocess`` → the legacy
    in-process ``WhisperSTTService`` (GPU, ~12s first-load). No silent
    default: an unknown mode, or sidecar with an empty url/model, fails
    loud.
    """
    mode = (site_config.get("voice_agent_stt_mode", "inprocess") or "").strip().lower()
    if mode == "sidecar":
        base_url = (site_config.get("voice_agent_stt_base_url", "") or "").strip()
        if not base_url:
            raise ValueError(
                "voice_agent_stt_mode=sidecar but voice_agent_stt_base_url "
                "is empty — set it (e.g. http://speaches:8000/v1)."
            )
        model = (site_config.get("voice_agent_stt_model", "") or "").strip()
        if not model:
            raise ValueError(
                "voice_agent_stt_mode=sidecar but voice_agent_stt_model is "
                "empty — set the faster-whisper id (e.g. "
                "Systran/faster-whisper-medium)."
            )
        from pipecat.services.openai.stt import OpenAISTTService
        # Speaches ignores the key, but the OpenAI SDK requires a non-empty
        # one at construction.
        return OpenAISTTService(base_url=base_url, api_key="speaches", model=model)
    if mode != "inprocess":
        raise ValueError(
            f"voice_agent_stt_mode={mode!r} is invalid — expected "
            "'sidecar' or 'inprocess'."
        )
    whisper_model_name = site_config.get("voice_agent_whisper_model", "base")
    return WhisperSTTService(model=_resolve_whisper_model(whisper_model_name))
```

- [ ] **Step 4: Run to verify pass**

Run: `cd src/cofounder_agent && python -m pytest tests/unit/services/test_voice_agent_build_stt_tts.py -k build_stt -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/voice_agent.py src/cofounder_agent/tests/unit/services/test_voice_agent_build_stt_tts.py
git commit -F - <<'EOF'
feat(voice): add mode-aware _build_stt seam (sidecar|inprocess) (#1088)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 4: `_build_tts()` seam (Phase 2, TDD)

Parallel to `_build_stt`. Resolves the voice id via the existing `_resolve_tts_voice` (so the per-room `tts_voice_override` keeps working in both modes), then returns the in-process `KokoroTTSService` or the Speaches-backed `OpenAITTSService` (which, unlike in-process Kokoro, actually honors `tts_speed`).

**Files:**

- Modify: `src/cofounder_agent/services/voice_agent.py`
- Test: `src/cofounder_agent/tests/unit/services/test_voice_agent_build_stt_tts.py` (extend)

- [ ] **Step 1: Add the failing tests**

Append to `test_voice_agent_build_stt_tts.py`:

```python
def test_build_tts_inprocess_returns_kokoro():
    import services.voice_agent as va
    cfg = _Cfg(voice_agent_tts_mode="inprocess", voice_agent_tts_voice="bf_emma")
    tts = va._build_tts(cfg, None)
    assert tts.__class__.__name__ == "KokoroTTSService"


def test_build_tts_sidecar_uses_override_voice_and_speed():
    _, tts_cls = _stub_openai_services()
    import services.voice_agent as va
    cfg = _Cfg(
        voice_agent_tts_mode="sidecar",
        voice_agent_tts_base_url="http://speaches:8000/v1",
        voice_agent_tts_model="speaches-ai/Kokoro-82M-v1.0-ONNX",
        voice_agent_tts_voice="bf_emma",
        voice_agent_tts_speed="1.25",
    )
    # Per-room override wins over the shared bf_emma.
    tts = va._build_tts(cfg, "bf_isabella")
    assert isinstance(tts, tts_cls)
    assert tts.kw["base_url"] == "http://speaches:8000/v1"
    assert tts.kw["model"] == "speaches-ai/Kokoro-82M-v1.0-ONNX"
    assert tts.kw["voice"] == "bf_isabella"
    assert tts.kw["speed"] == 1.25


def test_build_tts_sidecar_empty_model_fails_loud():
    _stub_openai_services()
    import services.voice_agent as va
    cfg = _Cfg(
        voice_agent_tts_mode="sidecar",
        voice_agent_tts_base_url="http://speaches:8000/v1",
        voice_agent_tts_model="",
        voice_agent_tts_voice="bf_emma",
    )
    with pytest.raises(ValueError, match="voice_agent_tts_model"):
        va._build_tts(cfg, None)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && python -m pytest tests/unit/services/test_voice_agent_build_stt_tts.py -k build_tts -v`
Expected: FAIL — `_build_tts` not defined.

- [ ] **Step 3: Implement `_build_tts`**

Add immediately after `_build_stt` in `services/voice_agent.py`:

```python
def _build_tts(site_config: Any, tts_voice_override: str | None) -> Any:
    """Build the TTS stage per ``voice_agent_tts_mode`` (#1088).

    The voice id is resolved the same way in both modes (so a per-room
    ``tts_voice_override`` like the claude-code room's bf_isabella keeps
    working). ``sidecar`` → ``OpenAITTSService`` POSTing /v1/audio/speech
    to Speaches (honors tts_speed). ``inprocess`` → in-process Kokoro
    (ignores speed). No silent default.
    """
    voice = _resolve_tts_voice(site_config, tts_voice_override)
    mode = (site_config.get("voice_agent_tts_mode", "inprocess") or "").strip().lower()
    if mode == "sidecar":
        base_url = (site_config.get("voice_agent_tts_base_url", "") or "").strip()
        if not base_url:
            raise ValueError(
                "voice_agent_tts_mode=sidecar but voice_agent_tts_base_url "
                "is empty — set it (e.g. http://speaches:8000/v1)."
            )
        model = (site_config.get("voice_agent_tts_model", "") or "").strip()
        if not model:
            raise ValueError(
                "voice_agent_tts_mode=sidecar but voice_agent_tts_model is "
                "empty — set the Kokoro id (e.g. "
                "speaches-ai/Kokoro-82M-v1.0-ONNX)."
            )
        speed = float(site_config.get("voice_agent_tts_speed", 1.0))
        from pipecat.services.openai.tts import OpenAITTSService
        return OpenAITTSService(
            base_url=base_url,
            api_key="speaches",
            model=model,
            voice=voice,
            speed=speed,
        )
    if mode != "inprocess":
        raise ValueError(
            f"voice_agent_tts_mode={mode!r} is invalid — expected "
            "'sidecar' or 'inprocess'."
        )
    return KokoroTTSService(settings=KokoroTTSService.Settings(voice=voice))
```

- [ ] **Step 4: Run to verify pass**

Run: `cd src/cofounder_agent && python -m pytest tests/unit/services/test_voice_agent_build_stt_tts.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/voice_agent.py src/cofounder_agent/tests/unit/services/test_voice_agent_build_stt_tts.py
git commit -F - <<'EOF'
feat(voice): add mode-aware _build_tts seam (sidecar|inprocess) (#1088)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 5: Wire the seams into `build_voice_pipeline_task` (Phase 2)

Replace the direct `WhisperSTTService(...)` / `KokoroTTSService(...)` construction with the new helpers. The `tts_voice` / `whisper_model_name` / `tts_speed` reads at the top of the builder become log-only (the helpers own the real reads), so the log line still reports what's running.

**Files:**

- Modify: `src/cofounder_agent/services/voice_agent.py` (the body of `build_voice_pipeline_task`, ~lines 230-274)

- [ ] **Step 1: Replace the STT construction**

Find (line ~246):

```python
    stt = WhisperSTTService(model=_resolve_whisper_model(whisper_model_name))
```

Replace with:

```python
    stt = _build_stt(site_config)
```

- [ ] **Step 2: Replace the TTS construction**

Find (lines ~266-274):

```python
    # Pipecat 1.1 deprecated KokoroTTSService(voice_id=...) in favor of
    # settings=Settings(voice=...). Passing voice= as a kwarg gets eaten
    # by **kwargs and the underlying kokoro_onnx ends up with voice=None
    # → crash on first synthesis. Pipecat hardcodes speed=1.0 internally
    # so tts_speed isn't honored today; kept the setting for forward-compat.
    _ = tts_speed  # noqa: F841 — pipecat doesn't accept speed yet
    tts = KokoroTTSService(
        settings=KokoroTTSService.Settings(voice=tts_voice),
    )
```

Replace with:

```python
    # STT/TTS are mode-aware (#1088): in-process (legacy) or a thin client
    # of the warm Speaches sidecar, per voice_agent_{stt,tts}_mode. The
    # sidecar OpenAITTSService honors tts_speed (in-process Kokoro did not).
    tts = _build_tts(site_config, tts_voice_override)
```

- [ ] **Step 3: Update the log line to report the resolved modes**

Find (lines ~232-244 — the `whisper_model_name` read and the `log.info(...)`). Replace the `log.info` call (line ~241):

```python
    log.info(
        "Voice pipeline — llm=%s voice=%s whisper=%s vad_stop=%.2fs",
        llm_model, tts_voice, whisper_model_name, vad_stop_secs,
    )
```

with:

```python
    stt_mode = (site_config.get("voice_agent_stt_mode", "inprocess") or "").strip()
    tts_mode = (site_config.get("voice_agent_tts_mode", "inprocess") or "").strip()
    log.info(
        "Voice pipeline — llm=%s voice=%s stt=%s/%s tts=%s vad_stop=%.2fs",
        llm_model, tts_voice, stt_mode, whisper_model_name, tts_mode,
        vad_stop_secs,
    )
```

> `whisper_model_name`, `tts_voice`, and `tts_speed` are still read at the top of the builder for this log line / voice resolution; they remain valid. The `_ = tts_speed` no-op line is deleted (the helper now consumes it in sidecar mode), so confirm no `F841`/unused-var lint remains for `tts_speed` — it's referenced by `_build_tts` via `site_config`, and at the top only if still logged. If `tts_speed` becomes unused at the top after this edit, delete its assignment (line ~231) too.

- [ ] **Step 4: Run the full voice_agent unit suite (guard against regressions)**

Run: `cd src/cofounder_agent && python -m pytest tests/unit/services/test_voice_agent_build_stt_tts.py tests/unit/services/test_voice_agent_service_mode.py tests/unit/services/test_voice_pipecat.py -v`
Expected: all pass (the new 7 + the existing service-mode + pipecat tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/voice_agent.py
git commit -F - <<'EOF'
feat(voice): route build_voice_pipeline_task STT/TTS through mode seams (#1088)

build_voice_pipeline_task now builds STT/TTS via _build_stt/_build_tts, so
flipping voice_agent_{stt,tts}_mode=sidecar switches both rooms to the warm
Speaches container. Defaults are inprocess → no behavior change on merge.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
```

---

### Task 6: Cutover, live verification, and docs (Phase 3)

Merge of Tasks 1-5 changes nothing live (modes default `inprocess`). This task is the **deliberate flag flip** + the acceptance test that maps to the bug, plus the rollback proof and the runbook.

**Files:**

- Create: `docs/operations/voice-stt-tts.md`

- [ ] **Step 1: Confirm Speaches is healthy and the voice image has the latest bind-mounted code**

```bash
docker ps --filter name=poindexter-speaches --format "{{.Names}} {{.Status}}"   # Up (healthy)
docker restart poindexter-voice-agent-livekit poindexter-voice-agent-claude-code
```

(Bind-mounted `./src/cofounder_agent` — restart picks up the merged seams. Per worker-deploy-bind-mount: verify the host file actually changed if a Windows file-lock race is suspected.)

- [ ] **Step 2: Flip both modes to sidecar (DB-first config)**

```bash
docker exec -i poindexter-worker python - <<'PY'
import asyncio, asyncpg, os
async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    for k in ("voice_agent_stt_mode", "voice_agent_tts_mode"):
        await conn.execute("UPDATE app_settings SET value='sidecar' WHERE key=$1", k)
    rows = await conn.fetch("SELECT key,value FROM app_settings WHERE key IN ('voice_agent_stt_mode','voice_agent_tts_mode')")
    for r in rows: print(r['key'], '=', r['value'])
    await conn.close()
asyncio.run(main())
PY
docker restart poindexter-voice-agent-livekit poindexter-voice-agent-claude-code
```

Expected: both keys `sidecar`; containers restart clean.

- [ ] **Step 3: Confirm each bot logged the sidecar modes**

```bash
docker logs --since 2m poindexter-voice-agent-livekit 2>&1 | grep "Voice pipeline"
docker logs --since 2m poindexter-voice-agent-claude-code 2>&1 | grep "Voice pipeline"
```

Expected: `stt=sidecar/... tts=sidecar ...` on both.

- [ ] **Step 4: Live acceptance — the bug test**

Restart `poindexter-voice-agent-livekit` once more, then immediately join the `poindexter` room (via the tap-to-join URL) and say one short sentence. Watch the bot log for the transcript + STT timing:

```bash
docker logs -f --since 30s poindexter-voice-agent-livekit 2>&1 | grep -iE "Transcription|stt|metrics"
```

Expected: the **first** utterance after the restart is transcribed with sub-second STT latency (no ~12s dead-air stall). Repeat for the `claude-code` room — confirm it speaks in `bf_isabella` and the `poindexter` room speaks in `bf_emma`.

- [ ] **Step 5: Prove the rollback (flip back to inprocess)**

```bash
docker exec -i poindexter-worker python - <<'PY'
import asyncio, asyncpg, os
async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    for k in ("voice_agent_stt_mode","voice_agent_tts_mode"):
        await conn.execute("UPDATE app_settings SET value='inprocess' WHERE key=$1", k)
    await conn.close()
asyncio.run(main())
PY
docker restart poindexter-voice-agent-livekit
docker logs --since 1m poindexter-voice-agent-livekit 2>&1 | grep "Voice pipeline"
```

Expected: log shows `stt=inprocess/...`; the in-process path still works (rollback proven). Then flip STT+TTS back to `sidecar` to leave the system on the warm path.

- [ ] **Step 6: Write the runbook**

Create `docs/operations/voice-stt-tts.md`:

```markdown
# Voice STT/TTS — warm Speaches sidecar (#1088)

## What it is

One GPU container (`poindexter-speaches`, `ghcr.io/speaches-ai/speaches:latest-cuda`,
in-network `http://speaches:8000`) serves both OpenAI-compatible endpoints:
`/v1/audio/transcriptions` (faster-whisper) and `/v1/audio/speech` (Kokoro).
`WHISPER__TTL=-1` keeps the model resident, so a voice-container restart no
longer pays the ~12s Whisper cold-start.

## How the voice agents use it

`services/voice_agent.py::build_voice_pipeline_task` builds STT/TTS via the
mode-aware `_build_stt` / `_build_tts` seams:

- `voice_agent_stt_mode` / `voice_agent_tts_mode` = `sidecar` → Pipecat
  `OpenAISTTService` / `OpenAITTSService` pointed at `voice_agent_stt_base_url`
  / `voice_agent_tts_base_url`.
- `= inprocess` → legacy in-process Whisper / Kokoro (the rollback path).

| Key                        | Default                                                          |
| -------------------------- | ---------------------------------------------------------------- |
| `voice_agent_stt_mode`     | `inprocess` (flip to `sidecar` after Speaches is up)             |
| `voice_agent_stt_base_url` | `http://speaches:8000/v1`                                        |
| `voice_agent_stt_model`    | `Systran/faster-whisper-medium` (HF id, NOT the in-process enum) |
| `voice_agent_tts_mode`     | `inprocess`                                                      |
| `voice_agent_tts_base_url` | `http://speaches:8000/v1`                                        |
| `voice_agent_tts_model`    | `speaches-ai/Kokoro-82M-v1.0-ONNX`                               |

The voice id (`voice_agent_tts_voice` = `bf_emma`, per-room
`voice_agent_claude_code_tts_voice` = `bf_isabella`) carries over unchanged.

## Cutover

1. `docker compose -f docker-compose.local.yml up -d speaches`; wait for healthy.
2. Set `voice_agent_stt_mode` + `voice_agent_tts_mode` = `sidecar`.
3. `docker restart poindexter-voice-agent-livekit poindexter-voice-agent-claude-code`.

## Rollback

Set both `*_mode` back to `inprocess` and restart the two voice containers.
Instant; no schema change.

## VRAM

Net new continuous VRAM ≈ 2GB (faster-whisper medium int8/fp16 ~1.5-2GB +
Kokoro ~few hundred MB). Confirm headroom on Hardware & Power (`nvidia_gpu_*`)
after cutover. If VRAM-pressured, run Speaches' Kokoro on CPU (Speaches config)
— the escape hatch for the exit-137/SIGKILL memory-pressure history.
```

- [ ] **Step 7: Commit + open PR**

```bash
git add docs/operations/voice-stt-tts.md
git commit -F - <<'EOF'
docs(voice): runbook for the warm Speaches STT/TTS sidecar (#1088)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git push -u origin feat/voice-speaches-sidecar
gh pr create --repo Glad-Labs/glad-labs-stack --base main \
  --title "feat(voice): warm Speaches STT/TTS sidecar — kill the ~12s cold-start (#1088)" \
  --body "Closes #1088. Adds the warm Speaches sidecar + mode-aware _build_stt/_build_tts seams. Merge is zero-behavior-change (modes default inprocess); cutover is a flag flip + restart, proven reversible. See docs/operations/voice-stt-tts.md."
```

---

## Self-Review

**Spec coverage** (design §3-§11):

- §3 Speaches one-container approach → Task 1. ✅
- §4 `_build_stt`/`_build_tts` seams in `voice_agent.py` → Tasks 3-5. ✅
- §5 Pipecat wiring A-vs-B → resolved to A in Phase 0; Tasks 3-4 use `OpenAISTTService`/`OpenAITTSService` directly. ✅
- §6 DB-backed config, modes default inprocess → Task 2. ✅ (Refined: separate `voice_agent_stt_model` / `voice_agent_tts_model` for the HF-id-vs-enum nuance the design glossed.)
- §7 VRAM budget → documented in Task 6 runbook. ✅
- §8 Observability (healthcheck) → Task 1 healthcheck; Hardware & Power VRAM check in Task 6. ✅ (Pipecat `enable_metrics` already times STT/TTS — the Task 6 live test reads it.)
- §9 Testing (build helpers both modes + fail-loud; integration smoke; live) → Tasks 3-4 (unit), Task 1 step 3 (smoke), Task 6 step 4 (live). ✅
- §10 Rollout phases 0-3 → Phase 0 (done) + Tasks 1-6. ✅
- §11 Decisions (Speaches; Kokoro on GPU; inprocess kept as rollback) → honored. ✅

**Placeholder scan:** No TBD/TODO; every code step shows full code; every command has expected output. The two genuine unknowns (Speaches `/health` path, `bf_emma`/`bf_isabella` presence in the pack) are explicit verify-and-branch steps in Task 1 step 3 with concrete fallbacks, not silent assumptions.

**Type consistency:** `_build_stt(site_config)` and `_build_tts(site_config, tts_voice_override)` signatures match their call sites in Task 5. Setting keys are spelled identically across the migration (Task 2), helpers (Tasks 3-4), and runbook (Task 6). `_resolve_tts_voice` / `_resolve_whisper_model` are reused (already in `voice_agent.py`), not redefined.
