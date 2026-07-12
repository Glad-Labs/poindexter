# High-Emotion TTS Engine Bake-Off — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator render one script through the current Kokoro/Speaches baseline plus two emotion-capable TTS engines (CosyVoice2, Chatterbox) and pick a winner by ear — with zero change to the live content pipeline.

**Architecture:** Two opt-in OpenAI-compatible TTS sidecar containers (behind a `tts-hq` compose profile) each expose `POST /v1/audio/speech`. Two thin `TTSProvider` classes call a shared HTTP helper extracted from the existing Speaches client (reusing its ffmpeg loudnorm normalization). A new `poindexter media tts-bakeoff` CLI renders a built-in sample through each engine into a scratch dir. The live pipeline (`synthesize_speech`) is untouched.

**Tech Stack:** Python 3.11, `click` (CLI), `httpx` (async HTTP), `pytest` (unit, mocked HTTP), Docker Compose profiles, FastAPI (sidecar shims), ffmpeg (audio normalization).

## Global Constraints

- **Zero live-pipeline change.** Do NOT modify `services/podcast_service.py`, `modules/content/stages/generate_media_scripts.py`, or the dispatch behavior of `podcast_tts_engine`. The bake-off is CLI-only.
- **`app_settings` values are NEVER NULL** — use `''` as the unset sentinel (empty string), never `None`/NULL.
- **New settings go in `services/settings_defaults.py`**, NOT in a migration file (seeder runs every boot; migrations run once).
- **Commercial-clean licenses only** — CosyVoice2 (Apache-2.0), Chatterbox (MIT). Do not introduce a non-commercial-licensed model.
- **Sidecars are opt-in** — `profiles: ["tts-hq"]` in both `docker-compose.local.yml` and `docker-compose.consumer.yml`. Never in the default stack. Kokoro/Speaches stays the default engine.
- **Fail-loud, no silent defaults** — a missing required setting or an unreachable sidecar surfaces a clear operator error; it never silently no-ops.
- **Existing tests stay green** — the Task 1 refactor is behavior-preserving; run the full `test_tts_service.py` after it.
- **Frequent commits** — one commit per task minimum, following the TDD cycle in each task.

## File Structure

**Created:**

- `src/cofounder_agent/services/tts_providers/bakeoff_sample.py` — the built-in reference script constant.
- `src/cofounder_agent/services/tts_providers/cosyvoice2.py` — `CosyVoice2TTSProvider`.
- `src/cofounder_agent/services/tts_providers/chatterbox.py` — `ChatterboxTTSProvider`.
- `src/cofounder_agent/tests/unit/services/tts_providers/test_cosyvoice2.py`
- `src/cofounder_agent/tests/unit/services/tts_providers/test_chatterbox.py`
- `src/cofounder_agent/tests/unit/services/tts_providers/test_bakeoff_sample.py`
- `src/cofounder_agent/tests/unit/cli/test_media_tts_bakeoff.py`
- `scripts/tts_sidecars/chatterbox_server.py` — FastAPI shim for Chatterbox.
- `scripts/tts_sidecars/cosyvoice2_server.py` — FastAPI shim for CosyVoice2.
- `scripts/Dockerfile.chatterbox`, `scripts/Dockerfile.cosyvoice2`

**Modified:**

- `src/cofounder_agent/services/tts_service.py` — extract `render_openai_tts`; `synthesize_speech` delegates to it.
- `src/cofounder_agent/services/settings_defaults.py` — add `plugin.tts_provider.{cosyvoice2,chatterbox}.*` defaults.
- `src/cofounder_agent/poindexter/cli/media.py` — add the `tts-bakeoff` subcommand.
- `docker-compose.local.yml`, `docker-compose.consumer.yml` — add the two `tts-hq`-profile services.
- `src/cofounder_agent/skills/content/tts/SKILL.md`, `docs/operations/voice-stt-tts.md` — document the engines + the command.

---

### Task 1: Extract the shared `render_openai_tts` helper

Refactor the OpenAI `/v1/audio/speech` call + ffmpeg normalization out of `synthesize_speech` into a reusable async function, so the new providers reuse it. Behavior-preserving — the existing `test_tts_service.py` must stay green.

**Files:**

- Modify: `src/cofounder_agent/services/tts_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_tts_service.py` (add cases; keep existing green)

**Interfaces:**

- Produces: `async render_openai_tts(*, base_url: str, model: str, voice: str, text: str, response_format: str = "mp3", api_key: str = "speaches", extra_body: dict[str, Any] | None = None, remux_enabled: bool = True, remux_mode: str = "reencode", remux_bitrate: str = "96k", loudnorm_enabled: bool = True, loudnorm_i: str = "-16", loudnorm_tp: str = "-1.5", loudnorm_lra: str = "11", loudnorm_ar: str = "44100") -> bytes | None` — POSTs to `{base_url}/audio/speech`, returns normalized audio bytes, or `None` on any HTTP/transport error (never raises). Uses module-level `httpx.AsyncClient` and `_remux_concatenated_audio` so existing patch points still work.
- Consumes: existing module constants `_DEFAULT_FORMAT`, `_DEFAULT_REMUX_MODE`, `_DEFAULT_REMUX_BITRATE`, `_DEFAULT_LOUDNORM_*`, `_HTTP_TIMEOUT`, and `_remux_concatenated_audio`.

- [ ] **Step 1: Write the failing test**

Add to `test_tts_service.py` inside `TestTtsService`:

```python
async def test_render_openai_tts_posts_and_normalizes(self, monkeypatch):
    """render_openai_tts POSTs the OpenAI body (with extra_body merged) and
    returns the normalized bytes."""
    import services.tts_service as mod
    from services.tts_service import render_openai_tts

    captured = {}

    async def _fake_remux(audio_bytes, fmt, **kwargs):
        captured["remux"] = (audio_bytes, fmt, kwargs)
        return b"NORMALIZED"

    monkeypatch.setattr(mod, "_remux_concatenated_audio", _fake_remux)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"RAW-audio"

    async def _post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return mock_response

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=_post)

    with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
        out = await render_openai_tts(
            base_url="http://cosyvoice2:8000/v1",
            model="cosyvoice2",
            voice="stock",
            text="Hello VRAM",
            response_format="mp3",
            extra_body={"instruct": "cheerful"},
        )

    assert out == b"NORMALIZED"
    assert captured["url"] == "http://cosyvoice2:8000/v1/audio/speech"
    assert captured["json"]["input"] == "Hello VRAM"
    assert captured["json"]["response_format"] == "mp3"
    assert captured["json"]["instruct"] == "cheerful"   # extra_body merged in


async def test_render_openai_tts_returns_none_on_error(self):
    """A transport error returns None, never raises."""
    from services.tts_service import render_openai_tts

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=ConnectionError("down"))

    with patch("services.tts_service.httpx.AsyncClient", return_value=mock_client):
        out = await render_openai_tts(
            base_url="http://x:8000/v1", model="m", voice="v", text="hi",
        )
    assert out is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_tts_service.py -k render_openai_tts -v`
Expected: FAIL with `ImportError: cannot import name 'render_openai_tts'`.

- [ ] **Step 3: Implement `render_openai_tts` and make `synthesize_speech` delegate**

In `services/tts_service.py`, add the helper ABOVE `synthesize_speech`:

```python
async def render_openai_tts(
    *,
    base_url: str,
    model: str,
    voice: str,
    text: str,
    response_format: str = _DEFAULT_FORMAT,
    api_key: str = "speaches",
    extra_body: dict[str, Any] | None = None,
    remux_enabled: bool = True,
    remux_mode: str = _DEFAULT_REMUX_MODE,
    remux_bitrate: str = _DEFAULT_REMUX_BITRATE,
    loudnorm_enabled: bool = True,
    loudnorm_i: str = _DEFAULT_LOUDNORM_I,
    loudnorm_tp: str = _DEFAULT_LOUDNORM_TP,
    loudnorm_lra: str = _DEFAULT_LOUDNORM_LRA,
    loudnorm_ar: str = _DEFAULT_LOUDNORM_AR,
) -> bytes | None:
    """POST to an OpenAI-compatible /audio/speech endpoint and normalize.

    Engine-agnostic core shared by ``synthesize_speech`` (Speaches) and the
    bake-off TTSProvider plugins. Returns normalized audio bytes, or ``None``
    on any transport/HTTP error (never raises — callers fall back).

    ``extra_body`` is merged into the JSON request for non-standard engine
    knobs (CosyVoice2 ``instruct``; Chatterbox ``exaggeration`` /
    ``cfg_weight``). The remux + EBU-R128 loudnorm pass is the same one the
    Speaches path uses (see ``_remux_concatenated_audio``).
    """
    base_url = (base_url or "").rstrip("/")
    fmt = (response_format or _DEFAULT_FORMAT).lower()
    body: dict[str, Any] = {
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": fmt,
    }
    if extra_body:
        body.update(extra_body)

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                f"{base_url}/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
            )
    except Exception as exc:
        logger.warning(
            "[tts_service] TTS endpoint unreachable at %s: %s", base_url, exc,
        )
        return None

    if resp.status_code != 200:
        logger.warning(
            "[tts_service] TTS endpoint returned %d: %s",
            resp.status_code, (getattr(resp, "text", "") or "")[:200],
        )
        return None

    audio_bytes = resp.content
    if not audio_bytes:
        logger.warning("[tts_service] TTS endpoint returned empty body")
        return None

    if remux_enabled or loudnorm_enabled:
        audio_bytes = await _remux_concatenated_audio(
            audio_bytes, fmt, mode=remux_mode, bitrate=remux_bitrate,
            loudnorm=loudnorm_enabled,
            loudnorm_i=loudnorm_i, loudnorm_tp=loudnorm_tp,
            loudnorm_lra=loudnorm_lra, loudnorm_ar=loudnorm_ar,
        )
    return audio_bytes
```

Then replace the body of `synthesize_speech` (the block from the `httpx.AsyncClient` POST through the remux call, lines ~215-280) with a delegating call, keeping the enable-check, text-strip, config reads, and file-write:

```python
    base_url = _resolve(site_config, "podcast_tts_base_url", _DEFAULT_BASE_URL)
    voice = voice or _resolve(site_config, "podcast_tts_voice", _DEFAULT_VOICE)
    model = _resolve(site_config, "podcast_tts_model", _DEFAULT_MODEL)
    fmt = _resolve(site_config, "podcast_tts_format", _DEFAULT_FORMAT).lower()

    audio_bytes = await render_openai_tts(
        base_url=base_url, model=model, voice=voice, text=text,
        response_format=fmt,
        remux_enabled=_resolve_bool(site_config, "podcast_tts_remux_enabled", True),
        remux_mode=_resolve(site_config, "podcast_tts_remux_mode", _DEFAULT_REMUX_MODE),
        remux_bitrate=_resolve(site_config, "podcast_tts_remux_bitrate", _DEFAULT_REMUX_BITRATE),
        loudnorm_enabled=_resolve_bool(site_config, "podcast_tts_loudnorm_enabled", True),
        loudnorm_i=_resolve_numeric_str(site_config, "podcast_tts_loudnorm_i", _DEFAULT_LOUDNORM_I),
        loudnorm_tp=_resolve_numeric_str(site_config, "podcast_tts_loudnorm_tp", _DEFAULT_LOUDNORM_TP),
        loudnorm_lra=_resolve_numeric_str(site_config, "podcast_tts_loudnorm_lra", _DEFAULT_LOUDNORM_LRA),
        loudnorm_ar=_resolve_numeric_str(site_config, "podcast_tts_loudnorm_ar", _DEFAULT_LOUDNORM_AR),
    )
    if audio_bytes is None:
        return None

    logger.info(
        "[tts_service] TTS synthesized: %d bytes (voice=%s, fmt=%s)",
        len(audio_bytes), voice, fmt,
    )

    if output_path:
        try:
            await asyncio.to_thread(_write_bytes, output_path, audio_bytes)
            logger.info("[tts_service] Wrote podcast audio to %s", output_path)
        except Exception as exc:
            logger.warning("[tts_service] Failed to write %s: %s", output_path, exc)

    return audio_bytes
```

- [ ] **Step 4: Run the new + existing tests to verify all pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_tts_service.py -v`
Expected: PASS — the two new `render_openai_tts` tests AND all pre-existing tests (regression guard for the refactor).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/tts_service.py src/cofounder_agent/tests/unit/services/test_tts_service.py
git commit -m "refactor(tts): extract render_openai_tts helper from synthesize_speech"
```

---

### Task 2: Built-in bake-off reference sample

A short in-repo tech-narration script so `tts-bakeoff` runs with zero arguments. Chosen to exercise the emotion settings (varied, emphasis-worthy sentences) and the `tts_pronunciations` normalizer (tech terms like VRAM, GHz).

**Files:**

- Create: `src/cofounder_agent/services/tts_providers/bakeoff_sample.py`
- Test: `src/cofounder_agent/tests/unit/services/tts_providers/test_bakeoff_sample.py`

**Interfaces:**

- Produces: `SAMPLE_SCRIPT: str` — a ~150-word narration passage.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/tts_providers/test_bakeoff_sample.py
"""The built-in bake-off sample is present and exercises pronunciation + emotion."""

import pytest


@pytest.mark.unit
def test_sample_script_is_substantial_and_exercises_pronunciation():
    from services.tts_providers.bakeoff_sample import SAMPLE_SCRIPT

    assert isinstance(SAMPLE_SCRIPT, str)
    # ~150 words — enough audio to judge naturalness across a few sentences.
    assert len(SAMPLE_SCRIPT.split()) >= 100
    # Deliberately contains tokens the tts_pronunciations map rewrites.
    assert "VRAM" in SAMPLE_SCRIPT
    assert "GHz" in SAMPLE_SCRIPT
    # Multiple sentences so emotion/prosody has something to work with.
    assert SAMPLE_SCRIPT.count(".") >= 4
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/tts_providers/test_bakeoff_sample.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.tts_providers.bakeoff_sample'`.

- [ ] **Step 3: Create the sample module**

```python
# services/tts_providers/bakeoff_sample.py
"""Built-in reference script for `poindexter media tts-bakeoff`.

A short, self-contained tech-narration passage so the bake-off runs with zero
arguments. Chosen to exercise both the emotion settings (varied, emphatic
sentences) and the tts_pronunciations normalizer (VRAM, GHz, GB) — the same
pronunciation surface a real podcast episode hits.
"""

from __future__ import annotations

SAMPLE_SCRIPT = (
    "Here is something worth getting excited about. This year's graphics cards "
    "ship with sixteen GB of VRAM and boost past three GHz — and honestly, the "
    "difference is night and day. Games that used to stutter now run glass-smooth. "
    "But raw speed was never the whole story. The real leap is efficiency: more "
    "frames per watt, quieter fans, and a chip that sips power at idle. Think about "
    "what that means for a small studio running models at home. No cloud bill, no "
    "waiting in a queue, no compromise. You get to iterate all day on hardware that "
    "fits under your desk. That is the shift nobody saw coming, and it changes who "
    "gets to build. The future of local compute is not just faster. It is yours."
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/tts_providers/test_bakeoff_sample.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/tts_providers/bakeoff_sample.py src/cofounder_agent/tests/unit/services/tts_providers/test_bakeoff_sample.py
git commit -m "feat(tts): built-in reference sample for the bake-off"
```

---

### Task 3: `app_settings` defaults for the two engines

Seed the per-engine config (`plugin.tts_provider.<name>.*`) the providers + CLI read. Empty strings are the unset sentinel where there is no meaningful default.

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (insert after `podcast_tts_loudnorm_ar`, before `scheduled_publisher_poll_seconds` — around line 1092)
- Test: `src/cofounder_agent/tests/unit/services/tts_providers/test_bakeoff_sample.py` (append a defaults presence check — same file to avoid a new test module for a data-only change)

**Interfaces:**

- Produces (settings keys): `plugin.tts_provider.cosyvoice2.base_url`, `.model`, `.instruct`, `plugin.tts_provider.chatterbox.base_url`, `.model`, `.exaggeration`, `.cfg_weight`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/services/tts_providers/test_bakeoff_sample.py`:

```python
@pytest.mark.unit
def test_bakeoff_engine_defaults_seeded():
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["plugin.tts_provider.cosyvoice2.base_url"] == "http://cosyvoice2:8000/v1"
    assert DEFAULTS["plugin.tts_provider.cosyvoice2.model"] == "cosyvoice2"
    assert DEFAULTS["plugin.tts_provider.chatterbox.base_url"] == "http://chatterbox:8000/v1"
    assert DEFAULTS["plugin.tts_provider.chatterbox.model"] == "chatterbox"
    # Emotion knobs have sane, comparable starting values.
    assert DEFAULTS["plugin.tts_provider.chatterbox.exaggeration"] == "0.5"
    assert DEFAULTS["plugin.tts_provider.chatterbox.cfg_weight"] == "0.5"
    # instruct is the unset sentinel (neutral) until the operator tunes it.
    assert DEFAULTS["plugin.tts_provider.cosyvoice2.instruct"] == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/tts_providers/test_bakeoff_sample.py -k defaults -v`
Expected: FAIL with `KeyError: 'plugin.tts_provider.cosyvoice2.base_url'`.

- [ ] **Step 3: Add the defaults**

In `services/settings_defaults.py`, immediately after the `'podcast_tts_loudnorm_ar': '44100',` line:

```python
    # --- Bake-off TTS engines (Phase 1, opt-in `tts-hq` compose profile) -------
    # Emotion-capable challengers to the default Kokoro/Speaches narration,
    # compared offline via `poindexter media tts-bakeoff`. NOT wired into the
    # live pipeline — podcast_tts_engine still selects Speaches. Both sidecars
    # speak the OpenAI /v1/audio/speech contract.
    # CosyVoice2-0.5B (Apache-2.0) — instruction-controllable emotion/style.
    'plugin.tts_provider.cosyvoice2.base_url': 'http://cosyvoice2:8000/v1',
    'plugin.tts_provider.cosyvoice2.model': 'cosyvoice2',
    # Natural-language delivery instruction; '' = neutral (unset sentinel).
    'plugin.tts_provider.cosyvoice2.instruct': '',
    # Chatterbox (MIT) — emotion via the `exaggeration` dial (0.0-1.0).
    'plugin.tts_provider.chatterbox.base_url': 'http://chatterbox:8000/v1',
    'plugin.tts_provider.chatterbox.model': 'chatterbox',
    # 0.5 is the upstream-recommended neutral default; raise for more emotion.
    'plugin.tts_provider.chatterbox.exaggeration': '0.5',
    # cfg_weight pacing knob; lower = slower/more deliberate delivery.
    'plugin.tts_provider.chatterbox.cfg_weight': '0.5',
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/tts_providers/test_bakeoff_sample.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/tts_providers/test_bakeoff_sample.py
git commit -m "feat(tts): seed cosyvoice2 + chatterbox engine defaults"
```

---

### Task 4: `CosyVoice2TTSProvider`

A `TTSProvider` that renders through the CosyVoice2 sidecar via `render_openai_tts`, passing the emotion `instruct` in `extra_body`.

**Files:**

- Create: `src/cofounder_agent/services/tts_providers/cosyvoice2.py`
- Test: `src/cofounder_agent/tests/unit/services/tts_providers/test_cosyvoice2.py`

**Interfaces:**

- Consumes: `services.tts_service.render_openai_tts` (Task 1), `plugins.tts_provider.TTSResult`.
- Produces: `class CosyVoice2TTSProvider` with `name = "cosyvoice2"`, `sample_rate_hz = 24000`, `default_format = "mp3"`, and `async synthesize(text, output_path, *, voice=None, config=None) -> TTSResult`. Reads `config` keys `base_url`, `model`, `instruct`, `response_format`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/tts_providers/test_cosyvoice2.py
"""Unit tests for CosyVoice2TTSProvider — mocked render_openai_tts (no sidecar)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from plugins.tts_provider import TTSProvider


@pytest.mark.unit
class TestCosyVoice2TTSProvider:
    def test_conforms_to_protocol(self):
        from services.tts_providers.cosyvoice2 import CosyVoice2TTSProvider
        assert isinstance(CosyVoice2TTSProvider(), TTSProvider)
        assert CosyVoice2TTSProvider().name == "cosyvoice2"

    async def test_synthesize_passes_instruct_and_writes_file(self, tmp_path):
        from services.tts_providers.cosyvoice2 import CosyVoice2TTSProvider

        out = tmp_path / "cosy.mp3"
        with patch(
            "services.tts_providers.cosyvoice2.render_openai_tts",
            new=AsyncMock(return_value=b"MP3BYTES"),
        ) as m:
            result = await CosyVoice2TTSProvider().synthesize(
                "Hello world", out,
                voice=None,
                config={
                    "base_url": "http://cosyvoice2:8000/v1",
                    "model": "cosyvoice2",
                    "instruct": "speak cheerfully",
                },
            )

        # emotion instruction is threaded through extra_body
        kwargs = m.await_args.kwargs
        assert kwargs["extra_body"] == {"instruct": "speak cheerfully"}
        assert kwargs["base_url"] == "http://cosyvoice2:8000/v1"
        assert kwargs["text"] == "Hello world"
        # file written + result populated
        assert out.read_bytes() == b"MP3BYTES"
        assert result.audio_path == out
        assert result.file_size_bytes == len(b"MP3BYTES")
        assert result.metadata["engine"] == "cosyvoice2"

    async def test_synthesize_raises_when_sidecar_returns_none(self, tmp_path):
        from services.tts_providers.cosyvoice2 import CosyVoice2TTSProvider

        with patch(
            "services.tts_providers.cosyvoice2.render_openai_tts",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="cosyvoice2"):
                await CosyVoice2TTSProvider().synthesize(
                    "hi", tmp_path / "x.mp3", config={"base_url": "http://c:8000/v1"},
                )

    async def test_synthesize_rejects_empty_text(self, tmp_path):
        from services.tts_providers.cosyvoice2 import CosyVoice2TTSProvider
        with pytest.raises(ValueError):
            await CosyVoice2TTSProvider().synthesize("   ", tmp_path / "x.mp3")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/tts_providers/test_cosyvoice2.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.tts_providers.cosyvoice2'`.

- [ ] **Step 3: Implement the provider**

```python
# services/tts_providers/cosyvoice2.py
"""CosyVoice2TTSProvider — emotion-capable TTS via the cosyvoice2 sidecar.

FunAudioLLM/CosyVoice2-0.5B (Apache-2.0). Instruction-controllable emotion:
the `instruct` config string ("speak with excitement", "calm and measured")
is passed through to the sidecar as a non-standard OpenAI-body field.

Renders through the shared `render_openai_tts` helper so it reuses the same
EBU-R128 loudnorm normalization as the Speaches path — clips are directly
comparable in the bake-off. Not wired into the live pipeline (Phase 1).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from plugins.tts_provider import TTSResult
from services.tts_service import render_openai_tts

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://cosyvoice2:8000/v1"
_DEFAULT_MODEL = "cosyvoice2"
_SAMPLE_RATE = 24000


class CosyVoice2TTSProvider:
    """Render audio with the CosyVoice2 sidecar (emotion via `instruct`)."""

    name = "cosyvoice2"
    sample_rate_hz = _SAMPLE_RATE
    default_format = "mp3"

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> TTSResult:
        cfg = config or {}
        if not text.strip():
            raise ValueError("CosyVoice2TTSProvider: refusing to synthesize empty text")

        base_url = str(cfg.get("base_url") or _DEFAULT_BASE_URL)
        model = str(cfg.get("model") or _DEFAULT_MODEL)
        fmt = str(cfg.get("response_format") or self.default_format).lower()
        instruct = str(cfg.get("instruct") or "").strip()

        extra_body: dict[str, Any] = {}
        if instruct:
            extra_body["instruct"] = instruct

        logger.info(
            "CosyVoice2TTSProvider: synthesizing %d chars (instruct=%r)",
            len(text), instruct,
        )

        audio = await render_openai_tts(
            base_url=base_url, model=model, voice=voice or "default",
            text=text, response_format=fmt, extra_body=extra_body or None,
        )
        if audio is None:
            raise RuntimeError(
                f"cosyvoice2 sidecar returned no audio ({base_url}) — is the "
                f"tts-hq profile up? `docker compose --profile tts-hq up -d cosyvoice2`"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output_path.write_bytes, audio)
        size = output_path.stat().st_size

        return TTSResult(
            audio_path=output_path,
            duration_seconds=max(1, len(text.split()) * 60 // 150),  # ~150 wpm est.
            voice=voice or "default",
            sample_rate=self.sample_rate_hz,
            audio_format=fmt,
            file_size_bytes=size,
            metadata={"engine": "cosyvoice2", "instruct": instruct},
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/tts_providers/test_cosyvoice2.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/tts_providers/cosyvoice2.py src/cofounder_agent/tests/unit/services/tts_providers/test_cosyvoice2.py
git commit -m "feat(tts): CosyVoice2TTSProvider (emotion via instruct)"
```

---

### Task 5: `ChatterboxTTSProvider`

A `TTSProvider` for the Chatterbox sidecar, passing `exaggeration` + `cfg_weight` in `extra_body`.

**Files:**

- Create: `src/cofounder_agent/services/tts_providers/chatterbox.py`
- Test: `src/cofounder_agent/tests/unit/services/tts_providers/test_chatterbox.py`

**Interfaces:**

- Consumes: `services.tts_service.render_openai_tts`, `plugins.tts_provider.TTSResult`.
- Produces: `class ChatterboxTTSProvider` with `name = "chatterbox"`, `sample_rate_hz = 24000`, `default_format = "mp3"`, `async synthesize(...)`. Reads `config` keys `base_url`, `model`, `exaggeration`, `cfg_weight`, `response_format`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/tts_providers/test_chatterbox.py
"""Unit tests for ChatterboxTTSProvider — mocked render_openai_tts (no sidecar)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.tts_provider import TTSProvider


@pytest.mark.unit
class TestChatterboxTTSProvider:
    def test_conforms_to_protocol(self):
        from services.tts_providers.chatterbox import ChatterboxTTSProvider
        assert isinstance(ChatterboxTTSProvider(), TTSProvider)
        assert ChatterboxTTSProvider().name == "chatterbox"

    async def test_synthesize_passes_emotion_knobs(self, tmp_path):
        from services.tts_providers.chatterbox import ChatterboxTTSProvider

        out = tmp_path / "cb.mp3"
        with patch(
            "services.tts_providers.chatterbox.render_openai_tts",
            new=AsyncMock(return_value=b"CBBYTES"),
        ) as m:
            result = await ChatterboxTTSProvider().synthesize(
                "Hello", out,
                config={
                    "base_url": "http://chatterbox:8000/v1",
                    "model": "chatterbox",
                    "exaggeration": "0.8",
                    "cfg_weight": "0.3",
                },
            )

        extra = m.await_args.kwargs["extra_body"]
        assert extra == {"exaggeration": 0.8, "cfg_weight": 0.3}
        assert out.read_bytes() == b"CBBYTES"
        assert result.metadata["engine"] == "chatterbox"
        assert result.metadata["exaggeration"] == 0.8

    async def test_bad_float_falls_back_to_default(self, tmp_path):
        from services.tts_providers.chatterbox import ChatterboxTTSProvider
        with patch(
            "services.tts_providers.chatterbox.render_openai_tts",
            new=AsyncMock(return_value=b"X"),
        ) as m:
            await ChatterboxTTSProvider().synthesize(
                "hi", tmp_path / "x.mp3",
                config={"exaggeration": "not-a-number"},
            )
        # non-numeric operator value falls back to 0.5, never crashes the render
        assert m.await_args.kwargs["extra_body"]["exaggeration"] == 0.5

    async def test_raises_when_sidecar_returns_none(self, tmp_path):
        from services.tts_providers.chatterbox import ChatterboxTTSProvider
        with patch(
            "services.tts_providers.chatterbox.render_openai_tts",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="chatterbox"):
                await ChatterboxTTSProvider().synthesize("hi", tmp_path / "x.mp3")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/tts_providers/test_chatterbox.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the provider**

```python
# services/tts_providers/chatterbox.py
"""ChatterboxTTSProvider — emotion-capable TTS via the chatterbox sidecar.

ResembleAI/chatterbox (MIT). Emotion via the `exaggeration` dial (0.0-1.0);
`cfg_weight` controls pacing. Both are passed to the sidecar as non-standard
OpenAI-body fields. Reuses `render_openai_tts` for identical loudnorm
normalization. Not wired into the live pipeline (Phase 1).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from plugins.tts_provider import TTSResult
from services.tts_service import render_openai_tts

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://chatterbox:8000/v1"
_DEFAULT_MODEL = "chatterbox"
_SAMPLE_RATE = 24000


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class ChatterboxTTSProvider:
    """Render audio with the Chatterbox sidecar (emotion via exaggeration)."""

    name = "chatterbox"
    sample_rate_hz = _SAMPLE_RATE
    default_format = "mp3"

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> TTSResult:
        cfg = config or {}
        if not text.strip():
            raise ValueError("ChatterboxTTSProvider: refusing to synthesize empty text")

        base_url = str(cfg.get("base_url") or _DEFAULT_BASE_URL)
        model = str(cfg.get("model") or _DEFAULT_MODEL)
        fmt = str(cfg.get("response_format") or self.default_format).lower()
        exaggeration = _as_float(cfg.get("exaggeration"), 0.5)
        cfg_weight = _as_float(cfg.get("cfg_weight"), 0.5)

        logger.info(
            "ChatterboxTTSProvider: synthesizing %d chars (exaggeration=%.2f)",
            len(text), exaggeration,
        )

        audio = await render_openai_tts(
            base_url=base_url, model=model, voice=voice or "default",
            text=text, response_format=fmt,
            extra_body={"exaggeration": exaggeration, "cfg_weight": cfg_weight},
        )
        if audio is None:
            raise RuntimeError(
                f"chatterbox sidecar returned no audio ({base_url}) — is the "
                f"tts-hq profile up? `docker compose --profile tts-hq up -d chatterbox`"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output_path.write_bytes, audio)
        size = output_path.stat().st_size

        return TTSResult(
            audio_path=output_path,
            duration_seconds=max(1, len(text.split()) * 60 // 150),
            voice=voice or "default",
            sample_rate=self.sample_rate_hz,
            audio_format=fmt,
            file_size_bytes=size,
            metadata={
                "engine": "chatterbox",
                "exaggeration": exaggeration,
                "cfg_weight": cfg_weight,
            },
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/tts_providers/test_chatterbox.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/tts_providers/chatterbox.py src/cofounder_agent/tests/unit/services/tts_providers/test_chatterbox.py
git commit -m "feat(tts): ChatterboxTTSProvider (emotion via exaggeration)"
```

---

### Task 6: `poindexter media tts-bakeoff` CLI command

Render the sample (or `--script`) through each engine and write files + a manifest to a scratch dir. Baseline engine `speaches` renders via `render_openai_tts` from the existing `podcast_tts_*` settings; `cosyvoice2`/`chatterbox` render via their provider classes with config from `plugin.tts_provider.<name>.*`.

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/media.py` (append the command + helpers)
- Test: `src/cofounder_agent/tests/unit/cli/test_media_tts_bakeoff.py`

**Interfaces:**

- Consumes: `services.tts_providers.cosyvoice2.CosyVoice2TTSProvider`, `services.tts_providers.chatterbox.ChatterboxTTSProvider`, `services.tts_service.render_openai_tts`, `services.tts_providers.bakeoff_sample.SAMPLE_SCRIPT`, `SiteConfig`.
- Produces: `poindexter media tts-bakeoff [--script PATH] [--engines a,b,c] [--voice ID] [--out-dir DIR]` — writes `<out-dir>/<engine>.mp3` per engine + `<out-dir>/manifest.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_media_tts_bakeoff.py
"""Unit tests for `poindexter media tts-bakeoff` — mocked renders, no sidecars."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.media import media_group


@pytest.mark.unit
class TestTtsBakeoff:
    def test_renders_each_engine_and_writes_manifest(self, tmp_path):
        async def _fake_render_one(engine, text, voice, out_path, site_config):
            out_path.write_bytes(f"{engine}-audio".encode())
            return {"engine": engine, "path": str(out_path),
                    "bytes": out_path.stat().st_size, "duration_s": 12}

        with patch("poindexter.cli.media._render_bakeoff_engine",
                   new=AsyncMock(side_effect=_fake_render_one)), \
             patch("poindexter.cli.media._make_bakeoff_site_config",
                   new=AsyncMock(return_value=object())):
            runner = CliRunner()
            result = runner.invoke(media_group, [
                "tts-bakeoff",
                "--engines", "speaches,chatterbox",
                "--out-dir", str(tmp_path),
            ])

        assert result.exit_code == 0, result.output
        assert (tmp_path / "speaches.mp3").read_bytes() == b"speaches-audio"
        assert (tmp_path / "chatterbox.mp3").read_bytes() == b"chatterbox-audio"
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert {row["engine"] for row in manifest} == {"speaches", "chatterbox"}

    def test_one_dead_engine_does_not_abort_the_others(self, tmp_path):
        async def _fake_render_one(engine, text, voice, out_path, site_config):
            if engine == "cosyvoice2":
                raise RuntimeError("sidecar down")
            out_path.write_bytes(b"ok")
            return {"engine": engine, "path": str(out_path), "bytes": 2, "duration_s": 1}

        with patch("poindexter.cli.media._render_bakeoff_engine",
                   new=AsyncMock(side_effect=_fake_render_one)), \
             patch("poindexter.cli.media._make_bakeoff_site_config",
                   new=AsyncMock(return_value=object())):
            runner = CliRunner()
            result = runner.invoke(media_group, [
                "tts-bakeoff", "--engines", "speaches,cosyvoice2", "--out-dir", str(tmp_path),
            ])

        assert result.exit_code == 0, result.output
        assert (tmp_path / "speaches.mp3").exists()      # good engine still rendered
        assert not (tmp_path / "cosyvoice2.mp3").exists()  # failed engine skipped
        assert "sidecar down" in result.output              # failure surfaced
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_media_tts_bakeoff.py -v`
Expected: FAIL — `AttributeError: module 'poindexter.cli.media' has no attribute '_render_bakeoff_engine'` (or command not found).

- [ ] **Step 3: Implement the command + helpers**

Append to `poindexter/cli/media.py` (after the `open` command):

```python
# ---------------------------------------------------------------------------
# ``tts-bakeoff`` — render one script through multiple TTS engines to compare
# ---------------------------------------------------------------------------

_BAKEOFF_PROVIDERS: dict[str, str] = {
    # engine name -> "module:ClassName" (imported lazily so a broken optional
    # provider never breaks `media --help`).
    "cosyvoice2": "services.tts_providers.cosyvoice2:CosyVoice2TTSProvider",
    "chatterbox": "services.tts_providers.chatterbox:ChatterboxTTSProvider",
}


async def _make_bakeoff_site_config(pool):
    """Load a SiteConfig for reading per-engine + Speaches settings."""
    return await _make_site_config(pool)


def _bakeoff_engine_config(engine: str, site_config: Any) -> dict[str, Any]:
    """Build the provider `config` dict from `plugin.tts_provider.<engine>.*`."""
    prefix = f"plugin.tts_provider.{engine}."
    keys = {
        "cosyvoice2": ("base_url", "model", "instruct"),
        "chatterbox": ("base_url", "model", "exaggeration", "cfg_weight"),
    }.get(engine, ())
    return {k: site_config.get(prefix + k, "") for k in keys}


async def _render_bakeoff_engine(engine, text, voice, out_path, site_config):
    """Render `text` through one engine → `out_path`; return a manifest row."""
    from services import tts_service

    if engine == "speaches":
        # Current-prod baseline: render the way the live pipeline does, reusing
        # the existing Speaches settings + the shared normalization helper.
        audio = await tts_service.render_openai_tts(
            base_url=site_config.get("podcast_tts_base_url", "http://speaches:8000/v1"),
            model=site_config.get("podcast_tts_model", "speaches-ai/Kokoro-82M-v1.0-ONNX"),
            voice=voice or site_config.get("podcast_tts_voice", "bf_emma"),
            text=text, response_format="mp3",
        )
        if audio is None:
            raise RuntimeError(
                "speaches baseline returned no audio — is poindexter-speaches up?"
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(audio)
        return {"engine": engine, "path": str(out_path),
                "bytes": out_path.stat().st_size,
                "duration_s": max(1, len(text.split()) * 60 // 150)}

    target = _BAKEOFF_PROVIDERS.get(engine)
    if target is None:
        raise click.UsageError(
            f"unknown engine {engine!r}; known: speaches,{','.join(_BAKEOFF_PROVIDERS)}"
        )
    mod_name, cls_name = target.split(":")
    import importlib
    provider = getattr(importlib.import_module(mod_name), cls_name)()
    result = await provider.synthesize(
        text, out_path, voice=voice or None,
        config=_bakeoff_engine_config(engine, site_config),
    )
    return {"engine": engine, "path": str(result.audio_path),
            "bytes": result.file_size_bytes, "duration_s": result.duration_seconds}


@media_group.command(name="tts-bakeoff")
@click.option("--script", "script_path", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Script file to render (default: built-in sample).")
@click.option("--engines", default="speaches,cosyvoice2,chatterbox", show_default=True,
              help="Comma-separated engines to render.")
@click.option("--voice", default=None, help="Voice override (engine-specific).")
@click.option("--out-dir", "out_dir", type=click.Path(file_okay=False), default=None,
              help="Output dir (default: ~/.poindexter/tts-bakeoff/<timestamp>).")
def cmd_tts_bakeoff(script_path, engines, voice, out_dir):
    """Render one script through multiple TTS engines for an A/B listen.

    Offline + read-only: writes audio files + a manifest to a scratch dir. Does
    NOT touch the live pipeline or the DB. Needs the `tts-hq` compose profile up
    for the cosyvoice2 / chatterbox sidecars:
    `docker compose --profile tts-hq up -d`.
    """
    from datetime import datetime

    from services.tts_providers.bakeoff_sample import SAMPLE_SCRIPT

    text = Path(script_path).read_text(encoding="utf-8") if script_path else SAMPLE_SCRIPT
    engine_list = [e.strip() for e in engines.split(",") if e.strip()]
    if out_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = str(_HOST_POINDEXTER / "tts-bakeoff" / stamp)
    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    async def _go():
        pool = await _make_pool()
        try:
            site_config = await _make_bakeoff_site_config(pool)
        finally:
            await pool.close()
        rows: list[dict[str, Any]] = []
        for engine in engine_list:
            out_path = out_root / f"{engine}.mp3"
            try:
                rows.append(await _render_bakeoff_engine(
                    engine, text, voice, out_path, site_config,
                ))
                click.secho(f"  ✓ {engine:<12} → {out_path}", fg="green")
            except Exception as exc:  # noqa: BLE001 — one dead engine must not abort others
                click.secho(f"  ✗ {engine:<12} {exc}", fg="red")
        return rows

    rows = _run(_go())
    (out_root / "manifest.json").write_text(json.dumps(rows, indent=2))
    click.secho(f"\n{len(rows)} engine(s) rendered → {out_root}", fg="cyan", bold=True)
    click.echo("Listen and compare, then set the winner as podcast_tts_engine (Phase 2).")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/cli/test_media_tts_bakeoff.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the whole touched test surface as a regression guard**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_tts_service.py tests/unit/services/tts_providers/ tests/unit/cli/test_media_tts_bakeoff.py tests/unit/plugins/test_tts_provider.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/media.py src/cofounder_agent/tests/unit/cli/test_media_tts_bakeoff.py
git commit -m "feat(cli): poindexter media tts-bakeoff (offline A/B render)"
```

---

### Task 7: Chatterbox sidecar (Dockerfile + FastAPI shim + compose service)

An OpenAI-compatible `/v1/audio/speech` sidecar wrapping `chatterbox-tts` (MIT). Verified by build + curl (a container wrapping a GPU model is not unit-tested).

**Files:**

- Create: `scripts/tts_sidecars/chatterbox_server.py`, `scripts/Dockerfile.chatterbox`
- Modify: `docker-compose.local.yml`, `docker-compose.consumer.yml` (add `chatterbox` service under `profiles: ["tts-hq"]`)

**Interfaces:**

- Produces: HTTP `POST /v1/audio/speech` (`{model, input, voice, response_format, exaggeration, cfg_weight}` → audio bytes) + `GET /health`.

- [ ] **Step 1: Write the FastAPI shim**

```python
# scripts/tts_sidecars/chatterbox_server.py
"""OpenAI-compatible /v1/audio/speech shim for ResembleAI Chatterbox (MIT).

Reads the standard OpenAI body plus two non-standard emotion knobs
(`exaggeration`, `cfg_weight`). Encodes to the requested format with ffmpeg
so the client's loudnorm pass (which only processes mp3/aac/opus) applies.
Model is loaded lazily on first request and cached.
"""

from __future__ import annotations

import io
import os
import subprocess

import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

app = FastAPI()
_model = None


def _get_model():
    global _model
    if _model is None:
        from chatterbox.tts import ChatterboxTTS  # heavy import, defer
        _model = ChatterboxTTS.from_pretrained(device=os.environ.get("TTS_DEVICE", "cuda"))
    return _model


def _encode(samples, sample_rate: int, fmt: str) -> bytes:
    """WAV samples -> requested container via ffmpeg (mp3/wav/opus/aac)."""
    wav_buf = io.BytesIO()
    sf.write(wav_buf, samples, sample_rate, format="WAV")
    if fmt == "wav":
        return wav_buf.getvalue()
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", "pipe:0", "-f", fmt, "pipe:1"],
        input=wav_buf.getvalue(), capture_output=True,
    )
    if proc.returncode != 0:
        raise HTTPException(500, f"ffmpeg encode failed: {proc.stderr[:300]!r}")
    return proc.stdout


class SpeechRequest(BaseModel):
    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str = "mp3"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    if not req.input.strip():
        raise HTTPException(400, "empty input")
    model = _get_model()
    # NOTE: confirm signature against the pinned chatterbox-tts release at build
    # time. As of chatterbox-tts 0.x: generate(text, exaggeration=, cfg_weight=)
    # returns a torch tensor [1, N] at model.sr.
    wav = model.generate(req.input, exaggeration=req.exaggeration, cfg_weight=req.cfg_weight)
    samples = wav.squeeze(0).detach().cpu().numpy()
    audio = _encode(samples, int(model.sr), req.response_format.lower())
    media = {"mp3": "audio/mpeg", "wav": "audio/wav",
             "opus": "audio/opus", "aac": "audio/aac"}.get(
        req.response_format.lower(), "application/octet-stream")
    return Response(content=audio, media_type=media)
```

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# scripts/Dockerfile.chatterbox
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg wget && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    chatterbox-tts fastapi "uvicorn[standard]" soundfile

RUN useradd -m appuser
USER appuser
WORKDIR /app
COPY --chown=appuser tts_sidecars/chatterbox_server.py /app/server.py

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Add the compose service**

Add to BOTH `docker-compose.local.yml` and `docker-compose.consumer.yml` (in `services:`), modeled on the `image-gen-server` block:

```yaml
# Chatterbox TTS bake-off sidecar (opt-in). MIT; emotion via `exaggeration`.
# Start: docker compose --profile tts-hq up -d chatterbox
chatterbox:
  profiles: ['tts-hq']
  build:
    context: ./scripts
    dockerfile: Dockerfile.chatterbox
  container_name: poindexter-chatterbox
  restart: unless-stopped
  ports:
    - '8011:8000'
  environment:
    TTS_DEVICE: 'cuda'
  volumes:
    - ${USERPROFILE:-.}/.cache/huggingface:/home/appuser/.cache/huggingface
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  healthcheck:
    test: ['CMD-SHELL', 'wget -qO /dev/null http://localhost:8000/health']
    interval: 30s
    timeout: 10s
    retries: 3
```

- [ ] **Step 4: Build + verify (manual — GPU container, no unit test)**

Run:

```bash
docker compose -f docker-compose.local.yml --profile tts-hq build chatterbox
docker compose -f docker-compose.local.yml --profile tts-hq up -d chatterbox
# wait for model download on first boot, then:
curl -fsS http://localhost:8011/health
curl -fsS -X POST http://localhost:8011/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"input":"Testing one two three.","response_format":"mp3","exaggeration":0.7}' \
  -o /tmp/chatterbox.mp3 && ffprobe /tmp/chatterbox.mp3
```

Expected: `{"status":"ok"}`, then a playable `/tmp/chatterbox.mp3` whose ffprobe duration is > 0. If `model.generate` signature differs, adjust the one marked call in the shim.

- [ ] **Step 5: Commit**

```bash
git add scripts/tts_sidecars/chatterbox_server.py scripts/Dockerfile.chatterbox docker-compose.local.yml docker-compose.consumer.yml
git commit -m "feat(tts): chatterbox OpenAI-compatible sidecar (tts-hq profile)"
```

---

### Task 8: CosyVoice2 sidecar (Dockerfile + FastAPI shim + compose service)

Same OpenAI-compatible contract, wrapping CosyVoice2-0.5B (Apache-2.0). Its install clones the upstream repo (not a single pip package) and it uses a bundled short reference clip as its stock speaker (CosyVoice2's native voice mechanism), with `instruct` driving emotion.

**Files:**

- Create: `scripts/tts_sidecars/cosyvoice2_server.py`, `scripts/Dockerfile.cosyvoice2`
- Modify: `docker-compose.local.yml`, `docker-compose.consumer.yml` (add `cosyvoice2` service under `profiles: ["tts-hq"]`)

**Interfaces:**

- Produces: HTTP `POST /v1/audio/speech` (`{model, input, voice, response_format, instruct}` → audio bytes) + `GET /health`.

- [ ] **Step 1: Write the FastAPI shim**

```python
# scripts/tts_sidecars/cosyvoice2_server.py
"""OpenAI-compatible /v1/audio/speech shim for CosyVoice2-0.5B (Apache-2.0).

`instruct` drives emotion/style. Uses a bundled 16 kHz reference clip as the
stock speaker (CosyVoice2's native zero-shot voice mechanism). Encodes to the
requested format with ffmpeg so the client's loudnorm pass applies.
"""

from __future__ import annotations

import io
import os
import subprocess

import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

app = FastAPI()
_engine = None
_prompt = None

_MODEL_DIR = os.environ.get("COSYVOICE_MODEL_DIR", "/app/pretrained_models/CosyVoice2-0.5B")
_PROMPT_WAV = os.environ.get("COSYVOICE_PROMPT_WAV", "/app/assets/stock_speaker_16k.wav")


def _get_engine():
    global _engine, _prompt
    if _engine is None:
        from cosyvoice.cli.cosyvoice import CosyVoice2  # heavy import
        from cosyvoice.utils.file_utils import load_wav
        _engine = CosyVoice2(_MODEL_DIR, load_jit=False, load_trt=False, fp16=False)
        _prompt = load_wav(_PROMPT_WAV, 16000)
    return _engine, _prompt


def _encode(samples, sample_rate: int, fmt: str) -> bytes:
    wav_buf = io.BytesIO()
    sf.write(wav_buf, samples, sample_rate, format="WAV")
    if fmt == "wav":
        return wav_buf.getvalue()
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", "pipe:0", "-f", fmt, "pipe:1"],
        input=wav_buf.getvalue(), capture_output=True,
    )
    if proc.returncode != 0:
        raise HTTPException(500, f"ffmpeg encode failed: {proc.stderr[:300]!r}")
    return proc.stdout


class SpeechRequest(BaseModel):
    model: str | None = None
    input: str
    voice: str | None = None
    response_format: str = "mp3"
    instruct: str = ""


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    if not req.input.strip():
        raise HTTPException(400, "empty input")
    engine, prompt = _get_engine()
    instruct = req.instruct or "Speak in a clear, natural, neutral narration voice."
    # NOTE: confirm against the pinned CosyVoice2 release. inference_instruct2 yields
    # dicts with a 'tts_speech' torch tensor at engine.sample_rate.
    import numpy as np
    chunks = []
    for out in engine.inference_instruct2(req.input, instruct, prompt, stream=False):
        chunks.append(out["tts_speech"].squeeze(0).detach().cpu().numpy())
    samples = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    audio = _encode(samples, int(engine.sample_rate), req.response_format.lower())
    media = {"mp3": "audio/mpeg", "wav": "audio/wav",
             "opus": "audio/opus", "aac": "audio/aac"}.get(
        req.response_format.lower(), "application/octet-stream")
    return Response(content=audio, media_type=media)
```

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# scripts/Dockerfile.cosyvoice2
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg git wget && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Pin the upstream repo commit at build time (replace <PINNED_SHA>).
RUN git clone https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice \
    && cd /app/CosyVoice && git checkout <PINNED_SHA> \
    && git submodule update --init --recursive
RUN pip install --no-cache-dir -r /app/CosyVoice/requirements.txt \
    fastapi "uvicorn[standard]" soundfile
ENV PYTHONPATH=/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS

# Model weights downloaded at build (or mounted via the HF cache volume).
RUN python -c "from modelscope import snapshot_download; \
    snapshot_download('iic/CosyVoice2-0.5B', local_dir='/app/pretrained_models/CosyVoice2-0.5B')"

# A short public-domain / self-recorded 16 kHz neutral clip acts as the stock
# speaker. Ship one at scripts/tts_sidecars/assets/stock_speaker_16k.wav.
COPY tts_sidecars/assets/stock_speaker_16k.wav /app/assets/stock_speaker_16k.wav
COPY tts_sidecars/cosyvoice2_server.py /app/server.py

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Add the compose service**

Add to BOTH compose files, mirroring the chatterbox block:

```yaml
# CosyVoice2 TTS bake-off sidecar (opt-in). Apache-2.0; emotion via `instruct`.
# Start: docker compose --profile tts-hq up -d cosyvoice2
cosyvoice2:
  profiles: ['tts-hq']
  build:
    context: ./scripts
    dockerfile: Dockerfile.cosyvoice2
  container_name: poindexter-cosyvoice2
  restart: unless-stopped
  ports:
    - '8012:8000'
  volumes:
    - ${USERPROFILE:-.}/.cache/huggingface:/home/appuser/.cache/huggingface
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  healthcheck:
    test: ['CMD-SHELL', 'wget -qO /dev/null http://localhost:8000/health']
    interval: 30s
    timeout: 10s
    retries: 3
```

- [ ] **Step 4: Build + verify (manual)**

Run:

```bash
docker compose -f docker-compose.local.yml --profile tts-hq build cosyvoice2
docker compose -f docker-compose.local.yml --profile tts-hq up -d cosyvoice2
curl -fsS http://localhost:8012/health
curl -fsS -X POST http://localhost:8012/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"input":"Testing one two three.","response_format":"mp3","instruct":"Speak with warm excitement."}' \
  -o /tmp/cosyvoice2.mp3 && ffprobe /tmp/cosyvoice2.mp3
```

Expected: `{"status":"ok"}`, then a playable `/tmp/cosyvoice2.mp3`. If `inference_instruct2` differs in the pinned release, adjust the one marked call.

- [ ] **Step 5: Commit**

```bash
git add scripts/tts_sidecars/cosyvoice2_server.py scripts/Dockerfile.cosyvoice2 docker-compose.local.yml docker-compose.consumer.yml
git commit -m "feat(tts): cosyvoice2 OpenAI-compatible sidecar (tts-hq profile)"
```

---

### Task 9: Documentation

Document the engines + the command so the operator can run the bake-off.

**Files:**

- Modify: `src/cofounder_agent/skills/content/tts/SKILL.md`
- Modify: `docs/operations/voice-stt-tts.md`

- [ ] **Step 1: Add an engines section to the TTS SKILL.md**

Append to `skills/content/tts/SKILL.md`:

```markdown
## Bake-off engines (opt-in, Phase 1)

Beyond the default Kokoro/Speaches narration, two emotion-capable engines can
be compared offline via `poindexter media tts-bakeoff`:

| Engine       | License    | Emotion knob                                        | Sidecar port |
| ------------ | ---------- | --------------------------------------------------- | ------------ |
| `speaches`   | Apache-2.0 | — (baseline)                                        | 8001         |
| `cosyvoice2` | Apache-2.0 | `plugin.tts_provider.cosyvoice2.instruct` (string)  | 8012         |
| `chatterbox` | MIT        | `plugin.tts_provider.chatterbox.exaggeration` (0-1) | 8011         |

Bring the sidecars up: `docker compose --profile tts-hq up -d`. They are NOT in
the default stack and do NOT affect the live pipeline — `podcast_tts_engine`
still selects Speaches. Cutover to a winner is a separate Phase 2 change.
```

- [ ] **Step 2: Add a runbook section to voice-stt-tts.md**

Append a "TTS engine bake-off" section to `docs/operations/voice-stt-tts.md`:

```markdown
## TTS engine bake-off (`poindexter media tts-bakeoff`)

Render one script through multiple engines to compare emotion + naturalness:

    docker compose --profile tts-hq up -d          # start the sidecars
    poindexter media tts-bakeoff                    # built-in sample, all engines
    poindexter media tts-bakeoff --script ep.md --engines speaches,chatterbox

Outputs `<engine>.mp3` + `manifest.json` under
`~/.poindexter/tts-bakeoff/<timestamp>/`. Offline and read-only — no DB writes,
no publish. Tune emotion via `plugin.tts_provider.<engine>.*` settings between
runs. All clips get the same EBU-R128 loudnorm as production, so they are
directly comparable.
```

- [ ] **Step 3: Verify docs render (no broken code fences) + commit**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/tts_providers/ tests/unit/cli/test_media_tts_bakeoff.py -q`
Expected: PASS (final green guard).

```bash
git add src/cofounder_agent/skills/content/tts/SKILL.md docs/operations/voice-stt-tts.md
git commit -m "docs(tts): document bake-off engines + poindexter media tts-bakeoff"
```

---

## Self-Review

**Spec coverage:**

- Two opt-in `tts-hq` sidecars (Chatterbox, CosyVoice2) → Tasks 7, 8. ✓
- Shared HTTP-TTS helper reusing remux/loudnorm → Task 1. ✓
- Two `TTSProvider` classes with emotion config → Tasks 4, 5. ✓
- `poindexter media tts-bakeoff` CLI with built-in sample + `--script` override → Tasks 2, 6. ✓
- `app_settings` defaults in `settings_defaults.py` (not a migration) → Task 3. ✓
- Tests (mocked HTTP) + Protocol conformance + docs → Tasks 4/5/6 tests, 9. ✓
- Zero live-pipeline change; Kokoro stays default → enforced by Global Constraints; no task touches `podcast_service.py` / `generate_media_scripts.py` / `podcast_tts_engine` dispatch. ✓
- Out-of-scope items (cloning, per-segment emotion, always-on VRAM) → not present in any task. ✓

**Placeholder scan:** The only intentional `<PINNED_SHA>` (Task 8 Dockerfile) and the bundled `stock_speaker_16k.wav` asset are real build-time inputs, not logic placeholders; both are called out explicitly with the verification step that resolves them. No `TODO`/`TBD`/"implement later" in any code step.

**Type consistency:** `render_openai_tts(...)` signature is identical across Task 1 (definition), Tasks 4/5 (callers via `extra_body=`), and Task 6 (baseline caller). `TTSResult` fields (`audio_path`, `file_size_bytes`, `duration_seconds`, `metadata`) match `plugins/tts_provider.py` and are used consistently. Provider `config` keys (`base_url`, `model`, `instruct` / `exaggeration` / `cfg_weight`) match the `settings_defaults.py` keys in Task 3 and the CLI's `_bakeoff_engine_config` in Task 6.
