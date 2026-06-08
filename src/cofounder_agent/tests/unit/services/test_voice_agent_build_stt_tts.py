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
        {
            "__init__": lambda self, **kw: setattr(self, "kw", kw),
            "VALID_VOICES": {},  # Mock the class attribute
        },
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


# ---------------------------------------------------------------------------
# _build_stt
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _build_tts
# ---------------------------------------------------------------------------


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


def test_build_tts_sidecar_without_valid_voices_attr():
    """pipecat 1.1.0 (the voice image) has NO ``VALID_VOICES`` attribute.

    Regression for the crash-loop introduced by #1153/#1157: the unconditional
    ``OpenAITTSService.VALID_VOICES.setdefault(...)`` raised AttributeError on
    every agent start. The guarded version must pass the raw voice straight
    through without touching the (absent) map.
    """
    # Stub an OpenAITTSService that, like pipecat 1.1.0, has no VALID_VOICES.
    tts_cls = type(
        "OpenAITTSService",
        (),
        {"__init__": lambda self, **kw: setattr(self, "kw", kw)},
    )
    pkg = types.ModuleType("pipecat.services.openai")
    sys.modules["pipecat.services.openai"] = pkg
    tts_mod = types.ModuleType("pipecat.services.openai.tts")
    tts_mod.OpenAITTSService = tts_cls
    sys.modules["pipecat.services.openai.tts"] = tts_mod

    import services.voice_agent as va
    cfg = _Cfg(
        voice_agent_tts_mode="sidecar",
        voice_agent_tts_base_url="http://speaches:8000/v1",
        voice_agent_tts_model="speaches-ai/Kokoro-82M-v1.0-ONNX",
        voice_agent_tts_voice="bf_emma",
    )
    # Must NOT raise AttributeError; voice flows straight through.
    tts = va._build_tts(cfg, None)
    assert isinstance(tts, tts_cls)
    assert tts.kw["voice"] == "bf_emma"
    assert not hasattr(tts_cls, "VALID_VOICES")


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
