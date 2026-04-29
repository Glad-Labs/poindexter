"""voice_agent.py — Local real-time voice agent for Poindexter.

Real-time voice conversation loop running ENTIRELY on your machine
(Ollama-first, no paid APIs):

    Mic → Silero VAD → faster-whisper STT → Ollama LLM → Kokoro TTS → Speakers

Single process. No cloud, no API keys. Built on Pipecat (Apache-2.0).
All configuration lives in ``app_settings`` (DB-first config plane —
zero env-var dependency per the project's standard pattern).

## Roadmap

This file is the engine. Surfaces are added on top:

- Local mic loop (this entry point — ``run_local()``)
- Poindexter MCP tools wired into the LLM step (so "what's in my approval
  queue" calls the existing mcp-server/server.py tools)
- WebRTC transport for phone/laptop access over Tailscale
- Discord voice bot adapter
- Multi-agent voice rooms (foundation for the tactical-sim product)

## Install

System dep (portaudio for mic+speaker access):

    sudo apt-get install -y portaudio19-dev   # Linux
    # macOS:  brew install portaudio
    # Windows: portaudio ships with python's pyaudio wheel — no extra step

Python deps (Apache-2.0 / MIT all the way down):

    pip install \
        "pipecat-ai[silero,whisper,ollama,kokoro,local]" \
        sounddevice numpy

First run downloads model weights:
- Silero VAD ONNX (~2 MB)
- faster-whisper base.en (~140 MB)
- Kokoro 82M ONNX (~325 MB) + voice packs

Subsequent runs are warm (everything cached under ``~/.cache/``).

## Run (local mic loop)

    python -m services.voice_agent

Talk into your default mic. The agent transcribes when you stop speaking,
sends to Ollama, speaks the response in the bf_emma British-female voice.
Ctrl+C to exit.

## Configuration (DB-driven via app_settings)

All knobs are app_settings rows — change at runtime, no restart needed.
Defaults are seeded by migration ``0104_seed_voice_agent_defaults.py``.

| Key                              | Default                              | Purpose                              |
| -------------------------------- | ------------------------------------ | ------------------------------------ |
| voice_agent_llm_model            | glm-4.7-5090:latest                  | Ollama model tag                     |
| voice_agent_ollama_url           | http://host.docker.internal:11434    | Ollama base URL                      |
| voice_agent_tts_voice            | bf_emma                              | Kokoro voice id (top-graded UK fem)  |
| voice_agent_tts_speed            | 1.0                                  | Kokoro playback speed                |
| voice_agent_whisper_model        | base.en                              | faster-whisper model size            |
| voice_agent_system_prompt        | (Emma, terse Glad Labs assistant)    | Agent personality                    |

Edit any setting via the CLI:

    poindexter settings set voice_agent_tts_voice bf_isabella

If latency hurts:
- Drop ``voice_agent_whisper_model`` to ``tiny.en`` (~40 MB, ~2-3× faster)
- Confirm the 5090 isn't busy with SDXL (kill the worker container if needed)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.whisper.stt import (
    Model as WhisperModel,
    WhisperSTTService,
)
from pipecat.transports.local.audio import (
    LocalAudioTransport,
    LocalAudioTransportParams,
)


_DEFAULT_SYSTEM_PROMPT = (
    "You are Emma, a concise voice assistant for Matt at Glad Labs. "
    "Speak naturally — your output goes through text-to-speech, so "
    "avoid markdown, bullet lists, and code blocks. Use short "
    "sentences. If Matt asks a factual question you don't know the "
    "answer to, say so plainly rather than guessing. Default to "
    "responses under 30 seconds of speech (~80 words) unless he "
    "explicitly asks for a longer one."
)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run_local(site_config: Any) -> None:
    """Run the voice agent on local mic + speakers.

    Reads every knob from ``app_settings`` via ``site_config``. Blocking
    until Ctrl+C.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("voice_agent")

    llm_model = site_config.get(
        "voice_agent_llm_model", "glm-4.7-5090:latest",
    )
    ollama_url = site_config.get(
        "voice_agent_ollama_url", "http://host.docker.internal:11434",
    )
    tts_voice = site_config.get("voice_agent_tts_voice", "bf_emma")
    tts_speed = float(site_config.get("voice_agent_tts_speed", 1.0))
    whisper_model_name = site_config.get(
        "voice_agent_whisper_model", "base.en",
    )
    system_prompt = (
        site_config.get("voice_agent_system_prompt", "")
        or _DEFAULT_SYSTEM_PROMPT
    )

    log.info(
        "Starting voice agent (local mic) — llm=%s voice=%s whisper=%s",
        llm_model, tts_voice, whisper_model_name,
    )

    # Pipecat 1.1 moved vad_analyzer OFF the transport params and onto
    # the user-aggregator params (see LLMContextAggregatorPair below).
    # Passing vad_analyzer to LocalAudioTransportParams is silently
    # dropped by pydantic v2 — speech-start/stop events never fire and
    # STT never runs. Spent way too long diagnosing this.
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    # Pipecat's Whisper Model is an enum keyed on `value` (e.g. 'base',
    # 'small'). Accept either the enum value ('base') or name ('BASE')
    # so operators don't have to memorize which case the lookup wants.
    try:
        whisper_model_enum = WhisperModel(whisper_model_name)
    except ValueError:
        try:
            whisper_model_enum = WhisperModel[whisper_model_name.upper()]
        except KeyError as exc:
            valid = ", ".join(m.value for m in WhisperModel)
            raise ValueError(
                f"voice_agent_whisper_model={whisper_model_name!r} is not a "
                f"valid Pipecat Whisper model. Valid values: {valid}",
            ) from exc
    stt = WhisperSTTService(model=whisper_model_enum)
    llm = OLLamaLLMService(model=llm_model, base_url=ollama_url)
    tts = KokoroTTSService(voice=tts_voice, speed=tts_speed)

    # Pipecat 1.1 API: LLMContext (universal, not OpenAI-specific) + a
    # standalone LLMContextAggregatorPair (replaces the old
    # llm.create_context_aggregator helper).
    context = LLMContext(
        messages=[{"role": "system", "content": system_prompt}],
    )
    # VAD lives here in Pipecat 1.1 (see transport block above).
    context_aggregator = LLMContextAggregatorPair(
        context=context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ],
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    log.info(
        "Audio transport ready. Start talking when the model downloads "
        "finish (first run only). Ctrl+C to exit.",
    )
    # No on_client_connected event for LocalAudioTransport (that's a
    # WebRTC concept). Skip the greet — operator sees the log line above
    # and can speak first. We can revisit a "type a message to start"
    # path later if the silent-start UX is bad.

    runner = PipelineRunner()
    try:
        await runner.run(task)
    except KeyboardInterrupt:
        log.info("Exiting on user interrupt")


def _ensure_brain_on_path() -> None:
    """Walk up parents until ``brain/bootstrap.py`` is found, then add
    that repo root to ``sys.path``. Same trick used by
    ``poindexter/cli/setup.py`` and ``poindexter/cli/migrate.py`` so the
    voice agent works regardless of which directory it's launched from.
    """
    from pathlib import Path

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "brain" / "bootstrap.py").is_file():
            p = str(parent)
            if p not in sys.path:
                sys.path.insert(0, p)
            return


async def _bootstrap_and_run() -> None:
    """Build a SiteConfig from the live DB and start the local mic loop."""
    import asyncpg

    _ensure_brain_on_path()
    from brain.bootstrap import require_database_url
    from services.site_config import SiteConfig

    dsn = require_database_url(source="voice_agent")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        site_config = SiteConfig()
        await site_config.load(pool)
        await run_local(site_config)
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(_bootstrap_and_run())
    except KeyboardInterrupt:
        sys.exit(0)
