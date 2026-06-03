"""Unit tests for ``services.voice_pipecat`` helpers.

Pipecat is not a backend dependency (it ships only in the voice extra /
the voice-agent container image), so every test that touches a function
which lazy-imports ``pipecat.services.whisper.stt`` installs a tiny stub
of that module first. ``voice_pipecat`` itself imports no pipecat at
module load (all heavy imports are function-local), so importing the
module under test is safe without the stub.

Covers the 2026-06-02 voice-bridge migration fixes:
- ``resolve_whisper_model`` maps the dropped ``.en`` model names back to
  their multilingual base (backcompat shim).
- ``build_whisper_stt`` pins the host bridge to CPU inference.
- ``resolve_bridge_voice_settings`` defaults ``stt_model`` to ``base``.
"""

from __future__ import annotations

import enum
import sys
import types
from typing import Any

import pytest


class _Model(enum.Enum):
    """Stand-in for ``pipecat.services.whisper.stt.Model`` (value-keyed)."""

    TINY = "tiny"
    BASE = "base"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE_V3 = "large-v3"


def _install_whisper_stub(
    monkeypatch: pytest.MonkeyPatch, *, capture: dict[str, Any] | None = None,
) -> None:
    """Stub ``pipecat.services.whisper.stt`` with our fake Model + service."""

    class _WhisperSTTService:
        def __init__(self, **kwargs: Any) -> None:
            if capture is not None:
                capture.update(kwargs)

    for name in (
        "pipecat",
        "pipecat.services",
        "pipecat.services.whisper",
    ):
        mod = types.ModuleType(name)
        mod.__path__ = []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, name, mod)
    stt_mod = types.ModuleType("pipecat.services.whisper.stt")
    stt_mod.Model = _Model
    stt_mod.WhisperSTTService = _WhisperSTTService
    monkeypatch.setitem(sys.modules, "pipecat.services.whisper.stt", stt_mod)


# ---------------------------------------------------------------------------
# resolve_whisper_model
# ---------------------------------------------------------------------------


def test_resolve_whisper_model_accepts_value_and_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_whisper_stub(monkeypatch)
    from services.voice_pipecat import resolve_whisper_model

    assert resolve_whisper_model("base") is _Model.BASE
    assert resolve_whisper_model("BASE") is _Model.BASE
    assert resolve_whisper_model("large-v3") is _Model.LARGE_V3


def test_resolve_whisper_model_en_suffix_maps_to_base(monkeypatch: pytest.MonkeyPatch) -> None:
    # The 2026-06-02 bug: a seeded ``base.en`` (dropped from Pipecat's enum)
    # crashed the audio plane on connect. The shim maps it back to ``base``.
    _install_whisper_stub(monkeypatch)
    from services.voice_pipecat import resolve_whisper_model

    assert resolve_whisper_model("base.en") is _Model.BASE
    assert resolve_whisper_model("medium.en") is _Model.MEDIUM


def test_resolve_whisper_model_bogus_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_whisper_stub(monkeypatch)
    from services.voice_pipecat import resolve_whisper_model

    with pytest.raises(RuntimeError, match=r"not a\s+valid Pipecat Whisper model"):
        resolve_whisper_model("definitely-not-a-model")


def test_resolve_whisper_model_unknown_en_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``.en`` only maps when the base IS valid; ``huge.en`` has no base.
    _install_whisper_stub(monkeypatch)
    from services.voice_pipecat import resolve_whisper_model

    with pytest.raises(RuntimeError):
        resolve_whisper_model("huge.en")


# ---------------------------------------------------------------------------
# build_whisper_stt -- pinned to CPU on the host bridge
# ---------------------------------------------------------------------------


def test_build_whisper_stt_pins_cpu_int8(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _install_whisper_stub(monkeypatch, capture=captured)
    from services.voice_pipecat import build_whisper_stt

    build_whisper_stt("base")
    assert captured["device"] == "cpu"
    assert captured["compute_type"] == "int8"
    assert captured["model"] is _Model.BASE


# ---------------------------------------------------------------------------
# resolve_bridge_voice_settings -- default stt_model
# ---------------------------------------------------------------------------


def test_bridge_settings_default_stt_model_is_base() -> None:
    from services.voice_pipecat import resolve_bridge_voice_settings

    settings = resolve_bridge_voice_settings({})
    assert settings["stt_model"] == "base"  # not the dropped "base.en"
    assert settings["tts_voice"] == "af_bella"
    assert settings["default_room"] == "claude-bridge"
    # Turn-detection defaults raised from the bring-up's 0.2/0.8 (#1010).
    assert settings["vad_stop_secs"] == 0.5
    assert settings["user_speech_timeout"] == 1.5


def test_bridge_settings_explicit_values_win() -> None:
    from services.voice_pipecat import resolve_bridge_voice_settings

    settings = resolve_bridge_voice_settings(
        {
            "voice_bridge_stt_model": "medium",
            "voice_bridge_tts_voice": "bf_emma",
            "voice_bridge_user_speech_timeout": "2.5",
            "voice_bridge_vad_stop_secs": "0.4",
        },
    )
    assert settings["stt_model"] == "medium"
    assert settings["tts_voice"] == "bf_emma"
    assert settings["user_speech_timeout"] == 2.5
    assert settings["vad_stop_secs"] == 0.4


def test_bridge_settings_bad_float_falls_back_to_default() -> None:
    from services.voice_pipecat import resolve_bridge_voice_settings

    settings = resolve_bridge_voice_settings(
        {"voice_bridge_user_speech_timeout": "not-a-number"},
    )
    assert settings["user_speech_timeout"] == 1.5  # loud warning + default
