"""voice_agent.py — Real-time voice agent for Poindexter.

Real-time voice conversation loop running ENTIRELY on your machine
(Ollama-first, no paid APIs):

    Mic → Silero VAD → faster-whisper STT → Ollama LLM → Kokoro TTS → Speakers

Single process. No cloud, no API keys. Built on Pipecat (Apache-2.0).
All configuration lives in ``app_settings`` (DB-first config plane —
zero env-var dependency per the project's standard pattern).

## Surfaces

- **Local mic loop** (``run_local()``, ``python -m services.voice_agent``)
- **WebRTC over Tailscale** (``services.voice_agent_webrtc``) — phone /
  laptop access from anywhere on the tailnet, same pipeline. Runs as
  the always-on ``voice-agent-webrtc`` Docker service (#383).
- **LiveKit room participant** (``services.voice_agent_livekit``) —
  multi-party voice room. Runs as the always-on
  ``voice-agent-livekit`` Docker service (#383). See
  ``docs/operations/voice-stt-tts.md``.
- Future: Discord voice bot adapter, multi-agent voice rooms.

The pipeline-builder (``build_voice_pipeline_task``) is the shared
engine — both surfaces call it with their own transport.

## Install

System dep (portaudio for the local-mic surface only):

    sudo apt-get install -y portaudio19-dev   # Linux
    # macOS:  brew install portaudio
    # Windows: portaudio ships with python's pyaudio wheel — no extra step

Python deps (Apache-2.0 / MIT all the way down):

    pip install \\
        "pipecat-ai[silero,whisper,ollama,kokoro,local,webrtc]" \\
        pipecat-ai-small-webrtc-prebuilt \\
        sounddevice numpy

First run downloads model weights:
- Silero VAD ONNX (~2 MB)
- faster-whisper base.en (~140 MB)
- Kokoro 82M ONNX (~325 MB) + voice packs

Subsequent runs are warm (everything cached under ``~/.cache/``).

## Run (local mic loop)

    python -m services.voice_agent

Talk into your default mic. Ctrl+C to exit.

## Configuration (DB-driven via app_settings)

All knobs are app_settings rows — change at runtime, no restart needed.
Defaults are seeded by migrations 0104 + 0107 + 0108.

| Key                              | Default                              | Purpose                              |
| -------------------------------- | ------------------------------------ | ------------------------------------ |
| voice_agent_llm_model            | glm-4.7-5090:latest                  | Ollama model tag                     |
| voice_agent_ollama_url           | http://localhost:11434/v1            | Ollama OpenAI-compat base URL        |
| voice_agent_tts_voice            | bf_emma                              | Kokoro voice id (top-graded UK fem)  |
| voice_agent_tts_speed            | 1.0                                  | Kokoro playback speed                |
| voice_agent_whisper_model        | base                                 | faster-whisper model size            |
| voice_agent_vad_stop_secs        | 0.2                                  | End-of-speech silence window         |
| voice_agent_system_prompt        | (Emma, terse Glad Labs assistant)    | Agent personality                    |
| voice_agent_webrtc_host          | 0.0.0.0                              | WebRTC bind host                     |
| voice_agent_webrtc_port          | 8003                                 | WebRTC bind port                     |

Edit any setting via the CLI:

    poindexter settings set voice_agent_tts_voice bf_isabella
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
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
)
from pipecat.services.whisper.stt import (
    WhisperSTTService,
)
from pipecat.transports.base_transport import BaseTransport
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

# pipecat.transports.local.audio imports sounddevice / PortAudio at
# module load. In a headless container (the always-on voice-agent-livekit
# / voice-agent-webrtc surfaces, #383) there's no audio device and that
# import raises ``OSError: PortAudio library not found`` even though the
# LiveKit / WebRTC surfaces never touch a local mic. Defer the import to
# ``run_local()`` so the headless surfaces import this module cleanly.


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
# Shared pipeline builder
# ---------------------------------------------------------------------------


def _resolve_whisper_model(name: str) -> WhisperModel:
    """Pipecat's Whisper ``Model`` is an enum keyed on ``value`` (e.g. 'base',
    'small'). Accept either the enum value ('base') or name ('BASE') so
    operators don't have to memorize which case the lookup wants.
    """
    try:
        return WhisperModel(name)
    except ValueError:
        try:
            return WhisperModel[name.upper()]
        except KeyError as exc:
            valid = ", ".join(m.value for m in WhisperModel)
            raise ValueError(
                f"voice_agent_whisper_model={name!r} is not a valid "
                f"Pipecat Whisper model. Valid values: {valid}",
            ) from exc


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


def _resolve_tts_voice(site_config: Any, override: str | None) -> str:
    """Resolve the Kokoro voice id for this surface.

    A non-empty ``override`` (after whitespace strip) wins — this is how a
    single surface (e.g. the ``claude-code`` LiveKit room via
    ``voice_agent_claude_code_tts_voice``) runs a distinct voice. An
    empty/whitespace/``None`` override falls back to the shared
    ``voice_agent_tts_voice`` setting (default ``bf_emma``), so surfaces
    that don't pass an override are unaffected.
    """
    chosen = (override or "").strip()
    if chosen:
        return chosen
    return site_config.get("voice_agent_tts_voice", "bf_emma")


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
        # Pipecat VERSIONS DIFFER on voice validation:
        #   - Older builds gate `voice` through a hardcoded VALID_VOICES map of
        #     OpenAI's own catalog (alloy/nova/shimmer/…). Speaches serves Kokoro
        #     voices (bf_emma/bf_isabella/…) which aren't in that map, so the
        #     stock ``VALID_VOICES[voice]`` lookup raises KeyError and TTS dies
        #     on every turn (the #1088 sidecar cutover hit this).
        #   - pipecat 1.1.0 (what the voice image ships) DROPPED VALID_VOICES
        #     entirely — `voice` is a free string, no gating. The #1153/#1157
        #     "access VALID_VOICES from the class" fix then crash-looped the
        #     agent with AttributeError on every start.
        # Guard on the attribute: register the Kokoro voice as an identity
        # pass-through only when the map exists (older pipecat). On 1.1.0 the
        # raw voice flows straight through, which is exactly what Speaches wants.
        valid_voices = getattr(OpenAITTSService, "VALID_VOICES", None)
        if isinstance(valid_voices, dict):
            valid_voices.setdefault(voice, voice)
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


def build_voice_pipeline_task(
    transport: BaseTransport,
    site_config: Any,
    *,
    log: logging.Logger | None = None,
    tools: list[Any] | None = None,
    llm: Any | None = None,
    system_prompt_override: str | None = None,
    tts_voice_override: str | None = None,
) -> PipelineTask:
    """Build a configured :class:`PipelineTask` for the given transport.

    Reads every knob from ``app_settings`` via ``site_config``. Used by
    every voice surface (local mic, SmallWebRTC, LiveKit) so the
    pipeline shape stays in one place.

    Args:
        transport: The Pipecat transport that owns mic/speaker IO.
        site_config: SiteConfig instance for DB-backed settings.
        log: Optional logger (defaults to module logger).
        tools: Optional list of Python coroutine functions to expose as
            LLM tool calls. Each callable is registered via
            ``llm.register_direct_function`` — Pipecat introspects
            the signature + docstring to build the OpenAI tool schema.
            Ignored when ``llm`` is supplied (the caller owns wiring).
        llm: Optional pre-built Pipecat LLM service. When supplied,
            replaces the default Ollama-backed stage. Used by
            ``voice_agent_livekit --brain claude-code`` to swap in
            the Claude Code subprocess bridge.
        system_prompt_override: If set, used in place of the
            ``voice_agent_system_prompt`` setting. Useful for the
            Claude bridge, which already has its own system prompt
            via the project's CLAUDE.md and shouldn't get the
            "you are Emma" preamble.
        tts_voice_override: If set (non-empty after strip), used in
            place of the shared ``voice_agent_tts_voice`` setting.
            Lets a single surface run a different Kokoro voice from
            the rest — e.g. the ``claude-code`` LiveKit room speaking
            as a distinct voice while the public ``poindexter`` room
            keeps Emma. Empty/None falls back to the shared key, so
            surfaces that don't pass it are unaffected.
    """
    log = log or logging.getLogger("voice_agent")

    # poindexter#485 fail-loud sweep: previously fell back to Matt's
    # specific ``glm-4.7-5090:latest`` model when the setting was
    # missing. The baseline seed populates ``voice_agent_llm_model``
    # on fresh installs (services/migrations/0000_baseline.seeds.sql),
    # so an empty/missing value here means the operator explicitly
    # cleared it — fail loud rather than silently pick a model that
    # may not be loaded in Ollama.
    llm_model = (site_config.get("voice_agent_llm_model", "") or "").strip()
    if not llm_model:
        raise ValueError(
            "voice_agent: ``voice_agent_llm_model`` is unset — set "
            "the Ollama model tag via `poindexter set-setting "
            "voice_agent_llm_model <tag>` before starting the voice "
            "agent."
        )
    ollama_url = site_config.get(
        "voice_agent_ollama_url", "http://localhost:11434/v1",
    )
    tts_voice = _resolve_tts_voice(site_config, tts_voice_override)
    vad_stop_secs = float(site_config.get("voice_agent_vad_stop_secs", 0.2))
    system_prompt = system_prompt_override or (
        site_config.get("voice_agent_system_prompt", "")
        or _DEFAULT_SYSTEM_PROMPT
    )

    # STT/TTS are mode-aware (#1088): in-process (legacy) or thin clients of
    # the warm Speaches sidecar, per voice_agent_{stt,tts}_mode. The helpers
    # own the model/url reads; we read the modes here only for the log line.
    stt_mode = (site_config.get("voice_agent_stt_mode", "inprocess") or "").strip()
    tts_mode = (site_config.get("voice_agent_tts_mode", "inprocess") or "").strip()
    log.info(
        "Voice pipeline — llm=%s voice=%s stt_mode=%s tts_mode=%s vad_stop=%.2fs",
        llm_model, tts_voice, stt_mode, tts_mode, vad_stop_secs,
    )

    stt = _build_stt(site_config)

    # Build (or reuse) the LLM stage. Default is the local Ollama path;
    # callers can inject any Pipecat LLM service to swap brains — the
    # Claude Code subprocess bridge is the marquee alternative.
    if llm is None:
        llm = OLLamaLLMService(model=llm_model, base_url=ollama_url)
    assert llm is not None  # narrow for the type checker

    # Tool wiring for the Ollama brain. Skipped when the caller provided
    # a custom llm — those services manage their own tool surface (the
    # Claude bridge inherits its tools from the operator's MCP servers,
    # not from a Pipecat-side schema).
    tool_schema = None
    if tools and not system_prompt_override:
        from pipecat.adapters.schemas.tools_schema import ToolsSchema
        for fn in tools:
            llm.register_direct_function(fn)
        tool_schema = ToolsSchema(standard_tools=list(tools))
        log.info("Registered %d tool(s) on the LLM stage", len(tools))
    # STT/TTS are mode-aware (#1088): in-process (legacy) or a thin client
    # of the warm Speaches sidecar, per voice_agent_{stt,tts}_mode. The
    # sidecar OpenAITTSService honors tts_speed (in-process Kokoro did not).
    tts = _build_tts(site_config, tts_voice_override)

    # Pipecat 1.1 API: LLMContext (universal, not OpenAI-specific) + a
    # standalone LLMContextAggregatorPair (replaces the old
    # llm.create_context_aggregator helper).
    context_kwargs: dict[str, Any] = {
        "messages": [{"role": "system", "content": system_prompt}],
    }
    if tool_schema is not None:
        context_kwargs["tools"] = tool_schema
    context = LLMContext(**context_kwargs)
    # Pipecat 1.1 wires VAD on the user-aggregator params, NOT on the
    # transport params. Passing vad_analyzer to a transport's params is
    # silently dropped by pydantic v2 — speech-start/stop events never
    # fire and STT never runs.
    #
    # Default user_turn_strategies includes TurnAnalyzerUserTurnStopStrategy
    # (the smart-turn ML detector). Live testing 2026-05-05 showed it
    # over-classifies as INCOMPLETE — Matt would speak a full sentence and
    # the model kept waiting for "more", forcing a 3s silence-fallback for
    # every turn. Many turns never reached the LLM at all. Replace with
    # the simpler SpeechTimeoutUserTurnStopStrategy (VAD-only, fixed
    # timeout after STT emits a transcript). Tunable via
    # voice_agent_user_speech_timeout (default 0.8s).
    user_speech_timeout = float(
        site_config.get("voice_agent_user_speech_timeout", 0.8)
    )
    context_aggregator = LLMContextAggregatorPair(
        context=context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(stop_secs=vad_stop_secs),
            ),
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[
                    SpeechTimeoutUserTurnStopStrategy(
                        user_speech_timeout=user_speech_timeout,
                    )
                ],
            ),
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

    return PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        # Pipecat defaults to cancel_on_idle_timeout=True with a 300-second
        # idle window: if no BotSpeakingFrame / UserSpeakingFrame fires for
        # 5 minutes, the pipeline is cancelled and the bot disconnects from
        # the LiveKit room. For a local-mic / Discord / phone-summon flow
        # that's reasonable, but for the always-on /voice/join surface it
        # makes the bot vanish during a quiet stretch — the operator taps
        # Connect mid-reconnect and gets silence. Disable the cancel; the
        # service-mode wrapper handles graceful shutdown via SIGINT.
        idle_timeout_secs=None,
        cancel_on_idle_timeout=False,
    )


# ---------------------------------------------------------------------------
# Local-mic surface (entry point)
# ---------------------------------------------------------------------------


async def run_local(
    site_config: Any,
    brain: str = "ollama",
    project_dir: str | None = None,
) -> None:
    """Run the voice agent on local mic + speakers.

    Reads every knob from ``app_settings`` via ``site_config``. Blocking
    until Ctrl+C.

    Args:
        site_config: live SiteConfig instance.
        brain: which LLM stage to wire in. ``"ollama"`` (default) uses
            the local glm-4.7-5090 with 3 read-only Poindexter tools.
            ``"claude-code"`` swaps in the ClaudeCodeBridge — every
            voice turn shells out to ``claude -p`` under the operator's
            Max OAuth sub. Same flag and same shape as
            ``services.voice_agent_livekit``.
        project_dir: when ``brain == "claude-code"``, the directory
            ``claude`` is spawned in (determines which CLAUDE.md loads).
            Defaults to cwd.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("voice_agent")

    # Lazy import — see top-of-module note. Only the local-mic surface
    # needs PortAudio; the LiveKit + WebRTC containerised surfaces never
    # touch a local audio device.
    from pipecat.transports.local.audio import (
        LocalAudioTransport,
        LocalAudioTransportParams,
    )

    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    if brain == "claude-code":
        from services.voice_agent_claude_code import ClaudeCodeBridgeLLMService

        extra = os.environ.get("CLAUDE_BOT_EXTRA_ARGS", "").split()
        sess = os.environ.get("CLAUDE_BOT_SESSION_ID", "").strip() or None
        llm_service = ClaudeCodeBridgeLLMService(
            cwd=project_dir or os.getcwd(),
            extra_args=extra or None,
            session_id=sess,
        )
        if sess:
            log.info(
                "Resuming Claude session_id=%s (preserves prior turns)", sess,
            )
        # Claude bridge ignores Pipecat-side tools (it has its own MCP
        # harness) and gets a TTS-friendly system prompt override so we
        # don't double-prompt with Emma's local-LLM persona.
        task = build_voice_pipeline_task(
            transport, site_config, log=log,
            llm=llm_service,
            system_prompt_override=(
                "You are speaking out loud to Matt over a local mic. "
                "Keep replies short and natural — under 20 seconds of "
                "speech unless he asks for more. No markdown, no bullet "
                "lists, no code blocks; this goes through TTS. When you "
                "take an action (edit a file, run a command, push a "
                "PR), summarise the outcome in one sentence rather than "
                "narrating the steps."
            ),
        )
    else:
        task = build_voice_pipeline_task(transport, site_config, log=log)

    log.info(
        "Audio transport ready. Start talking when the model downloads "
        "finish (first run only). Ctrl+C to exit.",
    )
    # No on_client_connected event for LocalAudioTransport (that's a
    # WebRTC concept). Skip the greet — operator sees the log line above
    # and can speak first.

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


async def _bootstrap_and_run(brain: str, project_dir: str | None) -> None:
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
        await run_local(site_config, brain=brain, project_dir=project_dir)
    finally:
        await pool.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Poindexter voice agent — local mic surface. Pipecat pipeline: "
            "Whisper → LLM → Kokoro. Pick the LLM with --brain."
        ),
    )
    parser.add_argument(
        "--brain",
        choices=["ollama", "claude-code"],
        default="ollama",
        help=(
            "LLM stage to wire in. 'ollama' (default) is the snappy "
            "local glm-4.7-5090 with three read-only Poindexter tools. "
            "'claude-code' shells out to `claude -p` under the operator's "
            "Max OAuth sub — slower but full repo / MCP / edit access."
        ),
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help=(
            "Used with --brain=claude-code. The directory `claude` is "
            "spawned in (determines which CLAUDE.md loads). Defaults to "
            "the current working directory."
        ),
    )
    args = parser.parse_args()

    try:
        asyncio.run(_bootstrap_and_run(args.brain, args.project_dir))
    except KeyboardInterrupt:
        sys.exit(0)
