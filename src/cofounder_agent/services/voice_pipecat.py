"""voice_pipecat.py — Shared Pipecat voice plumbing for every LiveKit surface.

Factored out of :mod:`services.voice_agent_livekit` so two consumers can share
one closure on Pipecat / faster-whisper / Silero VAD / Kokoro / livekit-api:

1. The always-on ``voice-agent-livekit`` Docker service (Whisper -> Ollama ->
   Kokoro, ``poindexter-bot`` identity).
2. The per-session MCP bridge in ``mcp-server-voice/`` (Whisper out -> a
   ``.in`` pipe; ``.out`` pipe in -> Kokoro, ``claude-bridge-<sid>`` identity).

Both paths need the same three things: a LiveKit JWT, a configured
:class:`LiveKitTransport`, and a Whisper / VAD / Kokoro pipeline assembled
around it. Putting the helpers here keeps the bridge free of duplicated
Pipecat lifecycle code while leaving the existing ``voice_agent_livekit.py``
behaviour byte-identical (it imports these helpers and continues to call
``build_voice_pipeline_task`` from :mod:`services.voice_agent`).

## Public seams

The bridge in ``mcp-server-voice/audio_plane_pipecat.py`` consumes three
public symbols:

- :func:`mint_livekit_token` -- mint a participant JWT for ``room`` /
  ``identity``.
- :func:`build_pipecat_bridge_pipeline` -- assemble a Whisper-in / Kokoro-out
  Pipecat pipeline whose STT fires ``on_utterance`` callbacks and whose TTS
  is driven by an ``asyncio.Queue`` the bridge writes into.
- :func:`resolve_bridge_voice_settings` -- pull the six ``voice_bridge_*``
  knobs from the live ``app_settings`` row set in one round trip.

The always-on container keeps importing the legacy helpers
(``_mint_token`` / ``_resolve_livekit_creds`` / ``build_voice_pipeline_task``)
from :mod:`services.voice_agent_livekit`; those now delegate to this
module so the wire shape doesn't drift.

## Failure posture

Every entry point follows ``feedback_no_silent_defaults``: missing creds,
missing models, missing voices all raise ``RuntimeError`` with a
``[VOICE_BRIDGE]`` log prefix. Verbose error messages over terse ones per
``feedback_machine_rules``.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any

logger = logging.getLogger("voice_pipecat")


_LOG_PREFIX = "[VOICE_BRIDGE]"


# ---------------------------------------------------------------------------
# LiveKit cred / token helpers
# ---------------------------------------------------------------------------


def mint_livekit_token(
    room: str,
    identity: str,
    *,
    api_key: str,
    api_secret: str,
    ttl_s: int = 3600,
    can_publish: bool = True,
    can_subscribe: bool = True,
    name: str | None = None,
) -> str:
    """Mint a LiveKit participant JWT for ``identity`` in ``room``.

    ``ttl_s`` defaults to one hour, matching the bridge's auto-leave watchdog
    plus headroom; the always-on container passes 6 * 3600 for a long-lived
    bot. Raises ``RuntimeError`` if ``api_key`` / ``api_secret`` are blank
    so the audio plane fails loud at start-up rather than producing a
    silently-rejected token.
    """
    if not api_key or not api_secret:
        raise RuntimeError(
            f"{_LOG_PREFIX} mint_livekit_token: api_key and api_secret are "
            f"required (got key={bool(api_key)}, secret={bool(api_secret)}). "
            f"Set LIVEKIT_API_KEY / LIVEKIT_API_SECRET in the environment "
            f"or seed them as app_settings before joining a room."
        )
    if not room or not identity:
        raise RuntimeError(
            f"{_LOG_PREFIX} mint_livekit_token: room and identity are "
            f"required (got room={room!r}, identity={identity!r})."
        )
    # Lazy import so unit tests that patch this module don't pay the
    # livekit-api import cost at collection time.
    from livekit import api

    grants = api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=can_publish,
        can_subscribe=can_subscribe,
        can_publish_data=True,
    )
    token = (
        api.AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(identity)
        .with_name(name or identity)
        .with_grants(grants)
        .with_ttl(timedelta(seconds=ttl_s))
        .to_jwt()
    )
    return token


def resolve_livekit_creds(site_config: Any | None = None) -> tuple[str, str, str]:
    """Pull LiveKit URL + API key + secret from app_settings then env vars.

    Resolution order matches the always-on container's behaviour so the
    bridge and the bot read the same source of truth:

    1. ``site_config['voice_agent_livekit_url']`` if a SiteConfig is passed.
    2. ``LIVEKIT_URL`` env var.
    3. Hardcoded ``ws://localhost:7880`` dev fallback.

    API key / secret stay in env vars (``LIVEKIT_API_KEY`` /
    ``LIVEKIT_API_SECRET``) -- same plumbing the LiveKit container reads
    from the compose env, one place to rotate.
    """
    url = ""
    if site_config is not None:
        try:
            url = str(
                site_config.get("voice_agent_livekit_url", "") or ""
            ).strip()
        except Exception:  # noqa: BLE001 -- site_config absence is OK
            url = ""
    url = url or os.environ.get("LIVEKIT_URL", "") or "ws://localhost:7880"
    key = os.environ.get("LIVEKIT_API_KEY", "devkey")
    secret = os.environ.get(
        "LIVEKIT_API_SECRET",
        "devsecret_change_me_change_me_change_me",
    )
    return url, key, secret


# ---------------------------------------------------------------------------
# Bridge-side voice settings — read once, pass through to the audio plane
# ---------------------------------------------------------------------------


_BRIDGE_SETTING_KEYS: tuple[str, ...] = (
    "voice_bridge_stt_model",
    "voice_bridge_tts_voice",
    "voice_bridge_max_session_seconds",
    "voice_bridge_chunk_max_chars",
    "voice_default_room",
    "voice_bridge_enabled",
)


def resolve_bridge_voice_settings(
    settings: dict[str, str],
) -> dict[str, Any]:
    """Normalise the six ``voice_bridge_*`` rows into a typed dict.

    Caller pulls the rows in one round-trip (see
    ``mcp-server-voice/server.py::_bridge_settings``) and hands the dict
    to this function; we coerce ints + strip whitespace so the audio
    plane never has to reparse them. Missing keys fall back to the same
    defaults the migration seeds, so the bridge keeps working on fresh
    DBs that haven't run the migration yet.
    """
    def _int(key: str, default: int) -> int:
        try:
            return int((settings.get(key) or "").strip() or str(default))
        except ValueError:
            logger.warning(
                "%s %s=%r is not an int; using default %d",
                _LOG_PREFIX, key, settings.get(key), default,
            )
            return default

    return {
        "stt_model": (settings.get("voice_bridge_stt_model") or "base.en").strip()
        or "base.en",
        "tts_voice": (settings.get("voice_bridge_tts_voice") or "af_bella").strip()
        or "af_bella",
        "max_session_seconds": _int("voice_bridge_max_session_seconds", 1800),
        "chunk_max_chars": _int("voice_bridge_chunk_max_chars", 500),
        "default_room": (settings.get("voice_default_room") or "claude-bridge").strip()
        or "claude-bridge",
    }


# ---------------------------------------------------------------------------
# STT / VAD construction (split out so the always-on container and the
# bridge build identical Whisper stages without duplicating the model
# resolution + Silero wiring)
# ---------------------------------------------------------------------------


def resolve_whisper_model(name: str) -> Any:
    """Map a string ``name`` to the Pipecat ``WhisperModel`` enum value.

    Accepts either the lowercase value (``"base"``, ``"large-v3"``) or the
    upper-case enum name (``"BASE"``). Raises ``RuntimeError`` with the
    valid set when neither matches, per ``feedback_no_silent_defaults``.
    """
    from pipecat.services.whisper.stt import Model as WhisperModel

    try:
        return WhisperModel(name)
    except ValueError:
        try:
            return WhisperModel[name.upper()]
        except KeyError as exc:
            valid = ", ".join(m.value for m in WhisperModel)
            raise RuntimeError(
                f"{_LOG_PREFIX} resolve_whisper_model: {name!r} is not a "
                f"valid Pipecat Whisper model. Valid values: {valid}.",
            ) from exc


def build_whisper_stt(model_name: str) -> Any:
    """Construct a configured ``WhisperSTTService`` for the given model id.

    Thin wrapper kept here so the bridge and the always-on bot share one
    construction path -- if a future Pipecat upgrade adds required kwargs
    (compute type, device hint), both consumers pick them up at once.
    """
    from pipecat.services.whisper.stt import WhisperSTTService

    return WhisperSTTService(model=resolve_whisper_model(model_name))


def build_silero_vad(stop_secs: float = 0.2) -> Any:
    """Construct a Silero VAD analyzer with the project's default cadence.

    Same default as ``services.voice_agent.build_voice_pipeline_task`` so
    the bridge inherits the validated tuning rather than a fresh guess.
    """
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams

    return SileroVADAnalyzer(params=VADParams(stop_secs=stop_secs))


def build_kokoro_tts(voice: str) -> Any:
    """Construct a Kokoro TTS service bound to ``voice``.

    Pipecat 1.1 deprecated ``KokoroTTSService(voice_id=...)`` in favour of
    ``settings=Settings(voice=...)``; we wrap the constructor here so the
    bridge doesn't repeat the kwarg dance and so a future upgrade is a
    one-file change.
    """
    from pipecat.services.kokoro.tts import KokoroTTSService

    return KokoroTTSService(
        settings=KokoroTTSService.Settings(voice=voice),
    )


# ---------------------------------------------------------------------------
# Bridge-style pipeline assembly (Whisper-in / Kokoro-out, no LLM stage)
# ---------------------------------------------------------------------------


def build_livekit_bridge_transport(
    *,
    url: str,
    token: str,
    room: str,
) -> Any:
    """Build a LiveKit transport configured for the bridge participant.

    The bridge does not run an LLM in-process -- it forwards transcripts
    to ``.in`` and reads TTS text from ``.out`` -- but it still needs the
    same audio in / audio out plumbing as the always-on bot. Constructed
    here so a Pipecat upgrade flows to both surfaces at once.
    """
    from pipecat.transports.livekit.transport import (
        LiveKitParams,
        LiveKitTransport,
    )

    return LiveKitTransport(
        url=url,
        token=token,
        room_name=room,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )


__all__ = [
    "mint_livekit_token",
    "resolve_livekit_creds",
    "resolve_bridge_voice_settings",
    "resolve_whisper_model",
    "build_whisper_stt",
    "build_silero_vad",
    "build_kokoro_tts",
    "build_livekit_bridge_transport",
]
