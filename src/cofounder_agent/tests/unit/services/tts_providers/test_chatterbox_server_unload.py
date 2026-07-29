"""Unit tests for the chatterbox sidecar's idle-unload + /unload endpoint.

Glad-Labs/poindexter#940 — the sidecar used to cache its model forever, so
narration squatted VRAM through the video render that followed it.

``chatterbox_server.py`` ships into the slim sidecar image, so it can't import
from the app package and we load it by file path (same approach as
``test_text_chunking.py``). ``soundfile`` is a sidecar-only dependency absent
from the backend env, so it's stubbed — nothing here exercises the encode path.
"""

from __future__ import annotations

import importlib.util
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SERVER = (
    Path(__file__).parents[6] / "scripts" / "tts_sidecars" / "chatterbox_server.py"
)


def _load_server(monkeypatch, *, idle_timeout: str = "120"):
    """Import the sidecar module fresh, with its sidecar-only deps stubbed."""
    if not _SERVER.exists():
        pytest.skip(f"sidecar not present at {_SERVER}")

    monkeypatch.setenv("CHATTERBOX_IDLE_TIMEOUT_S", idle_timeout)
    # soundfile: sidecar-only dep, used solely by _encode (not under test).
    monkeypatch.setitem(sys.modules, "soundfile", MagicMock())
    # text_chunking is a sibling file the sidecar imports flat.
    monkeypatch.syspath_prepend(str(_SERVER.parent))

    spec = importlib.util.spec_from_file_location("chatterbox_server_ut", _SERVER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_torch(monkeypatch, *, allocated_mb: int = 0):
    """Stub torch so _unload_model's empty_cache path runs without a GPU."""
    torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: True,
            empty_cache=lambda: None,
            memory_allocated=lambda _i: allocated_mb * 1024 * 1024,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


@pytest.mark.unit
class TestChatterboxUnload:
    def test_unload_is_a_noop_when_nothing_loaded(self, monkeypatch):
        """Reclaim runs opportunistically against a possibly-cold sidecar, so
        'nothing to free' must be a clean False, not an error."""
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch)
        assert mod._model is None
        assert mod._unload_model() is False

    def test_unload_drops_the_model(self, monkeypatch):
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch)
        mod._model = MagicMock()

        assert mod._unload_model() is True
        assert mod._model is None

    def test_unload_survives_a_torch_failure(self, monkeypatch):
        """The model reference is dropped before empty_cache is attempted, so
        a torch problem must not strand a loaded model NOR take the sidecar
        down — Python frees it regardless."""
        mod = _load_server(monkeypatch)
        broken = types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: True,
                empty_cache=lambda: (_ for _ in ()).throw(RuntimeError("cuda gone")),
                memory_allocated=lambda _i: 0,
            )
        )
        monkeypatch.setitem(sys.modules, "torch", broken)
        mod._model = MagicMock()

        assert mod._unload_model() is True
        assert mod._model is None

    # ---- idle detection ----

    def test_not_idle_when_no_model_loaded(self, monkeypatch):
        mod = _load_server(monkeypatch)
        mod._model = None
        mod._last_used = 0.0
        assert mod._is_idle() is False

    def test_not_idle_before_the_timeout(self, monkeypatch):
        mod = _load_server(monkeypatch, idle_timeout="120")
        mod._model = MagicMock()
        mod._last_used = time.time() - 10
        assert mod._is_idle() is False

    def test_idle_after_the_timeout(self, monkeypatch):
        mod = _load_server(monkeypatch, idle_timeout="120")
        mod._model = MagicMock()
        mod._last_used = time.time() - 300
        assert mod._is_idle() is True

    def test_zero_timeout_disables_idle_unload(self, monkeypatch):
        """0 is the documented 'keep the model hot' escape hatch — it must
        never idle out, however long the model sits."""
        mod = _load_server(monkeypatch, idle_timeout="0")
        mod._model = MagicMock()
        mod._last_used = time.time() - 86400
        assert mod._is_idle() is False
        assert mod._maybe_idle_unload() is False
        assert mod._model is not None

    def test_maybe_idle_unload_frees_an_idle_model(self, monkeypatch):
        mod = _load_server(monkeypatch, idle_timeout="120")
        _fake_torch(monkeypatch)
        mod._model = MagicMock()
        mod._last_used = time.time() - 300

        assert mod._maybe_idle_unload() is True
        assert mod._model is None

    def test_maybe_idle_unload_keeps_a_busy_model(self, monkeypatch):
        mod = _load_server(monkeypatch, idle_timeout="120")
        _fake_torch(monkeypatch)
        mod._model = MagicMock()
        mod._last_used = time.time()

        assert mod._maybe_idle_unload() is False
        assert mod._model is not None

    # ---- HTTP surface ----

    def test_health_reports_residency(self, monkeypatch):
        """The reclaim path and operators both need to see whether VRAM is
        actually held; a bare {"status": "ok"} can't answer that."""
        mod = _load_server(monkeypatch)
        mod._model = None
        assert mod.health()["model_loaded"] is False

        mod._model = MagicMock()
        body = mod.health()
        assert body["model_loaded"] is True
        assert body["idle_timeout_s"] == 120

    def test_health_stays_ok_while_idle(self, monkeypatch):
        """Docker's healthcheck greps this endpoint. An unloaded model is a
        normal resting state, so reporting anything but ok would flap the
        container every time the idle timer fired."""
        mod = _load_server(monkeypatch)
        mod._model = None
        assert mod.health()["status"] == "ok"

    def test_soft_unload_frees_without_exiting(self, monkeypatch):
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch)
        exits: list = []
        monkeypatch.setattr(mod.threading, "Timer", lambda *a, **k: exits.append(a))
        mod._model = MagicMock()

        body = mod.unload(mod.UnloadRequest(hard=False))
        assert body == {"status": "unloaded", "released": True, "hard": False}
        assert mod._model is None
        assert exits == [], "a soft unload must never schedule a process exit"

    def test_hard_unload_schedules_a_deferred_exit(self, monkeypatch):
        """Deferred, not immediate: the caller treats 200 as 'reclaim
        accepted', so the response has to be delivered before the process
        dies. (image-gen's hard unload exits first and resets the connection;
        this one deliberately doesn't.)"""
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch)
        timers: list = []

        class _FakeTimer:
            def __init__(self, delay, fn):
                timers.append((delay, fn))

            def start(self):
                pass  # never actually exit the test runner

        monkeypatch.setattr(mod.threading, "Timer", _FakeTimer)
        mod._model = MagicMock()

        body = mod.unload(mod.UnloadRequest(hard=True))
        assert body["hard"] is True
        assert body["released"] is True
        assert len(timers) == 1
        delay, _fn = timers[0]
        assert delay > 0, "exit must be deferred so the response can flush"

    def test_unload_with_no_body_is_soft(self, monkeypatch):
        """FastAPI passes None when the caller posts no JSON; that must not
        be read as a hard unload."""
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch)
        monkeypatch.setattr(
            mod.threading, "Timer",
            lambda *a, **k: pytest.fail("no-body unload must not exit the process"),
        )
        mod._model = MagicMock()

        assert mod.unload(None)["hard"] is False
