"""Unit tests for the chatterbox sidecar's idle-unload + /unload endpoint.

Glad-Labs/poindexter#940 — the sidecar used to cache its model forever, so
narration squatted VRAM through the video render that followed it.

**Dropping the model is not the whole fix (2026-09-25).** The first synthesis
creates a CUDA context that ``torch.cuda.memory_reserved()`` cannot see and
only a process exit returns: measured per PID from the host's nvidia-smi in a
throwaway container of this image, 3.9 GB with the model loaded, 660 MiB once
the idle unload dropped it (704 MiB on the live server, 20 MB reserved). The
exit gate (``_exit_gate``) now measures what the driver counts for this
process via NVML, and falls back to the reserved pool only when NVML is
unusable.

``chatterbox_server.py`` ships into the slim sidecar image, so it can't import
from the app package and we load it by file path (same approach as
``test_text_chunking.py``). ``soundfile`` is a sidecar-only dependency absent
from the backend env, so it's stubbed — nothing here exercises the encode path.
Each test gets a FRESH module (a new ``exec_module``, not a cached import), so
its globals — ``_model``, ``_nvml``/``_nvml_error``, ``_inflight`` — start
clean without a reset fixture.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.unit.scripts._fake_nvml import fake_pynvml

_SERVER = (
    Path(__file__).parents[6] / "scripts" / "tts_sidecars" / "chatterbox_server.py"
)


def _load_server(monkeypatch, *, idle_timeout: str = "120"):
    """Import the sidecar module fresh, with its sidecar-only deps stubbed.

    NVML is left unresolved (no ``sys.modules["pynvml"]`` entry): a test that
    wants the gate to see the driver's count installs a fake via ``_nvml``;
    one that doesn't gets the real ``import pynvml``, which fails in this env
    (nvidia-ml-py is not a backend dependency) and the gate falls back to
    torch's reserved pool, exactly as it does on an image built before this.
    """
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


def _fake_torch(monkeypatch, *, allocated_mb: int = 0, reserved_mb: int = 0):
    """Stub torch so _unload_model's empty_cache path, and _reserved_mb's
    fallback read, run without a GPU."""
    torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: True,
            empty_cache=lambda: None,
            memory_allocated=lambda _i=0: allocated_mb * 1024 * 1024,
            memory_reserved=lambda _i=0: reserved_mb * 1024 * 1024,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


def _nvml(monkeypatch, **kwargs):
    """Install a fake NVML the module's lazy ``import pynvml`` will pick up."""
    fake = fake_pynvml(**kwargs)
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    return fake


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

    def test_not_idle_while_a_request_is_counted_in(self, monkeypatch):
        """A request past ``speech()``'s entry but still waiting for
        ``_model_lock`` has not re-stamped ``_last_used`` yet (2026-09-25)."""
        mod = _load_server(monkeypatch, idle_timeout="120")
        mod._model = MagicMock()
        mod._last_used = time.time() - 300
        mod._inflight = 1
        assert mod._is_idle() is False

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
        _nvml(monkeypatch, own_mb=None)
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
        _nvml(monkeypatch, own_mb=None)
        mod._model = None
        assert mod.health()["status"] == "ok"

    def test_health_reports_what_the_driver_counts(self, monkeypatch):
        """With the model dropped the reserved pool reads 0 while the process
        still holds its context — this is the number that used to be
        unanswerable without nvidia-smi and a PID lookup."""
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch, reserved_mb=0)
        _nvml(monkeypatch, own_mb=660)
        mod._model = None

        body = mod.health()

        assert body["vram_process_mb"] == 660
        assert body["inflight"] == 0

    def test_soft_unload_frees_and_reports_what_it_left(self, monkeypatch):
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch, reserved_mb=20)
        _nvml(monkeypatch, own_mb=660)
        exits: list = []
        monkeypatch.setattr(mod.threading, "Timer", lambda *a, **k: exits.append(a))
        mod._model = MagicMock()

        body = mod.unload(mod.UnloadRequest(hard=False))

        assert body["status"] == "unloaded"
        assert body["released"] is True
        assert body["hard"] is False
        assert body["vram_process_mb"] == 660
        assert mod._model is None
        assert exits == [], "a soft unload must never schedule a process exit"

    def test_hard_unload_schedules_a_deferred_exit_when_it_holds_the_context(
        self, monkeypatch,
    ):
        """Deferred, not immediate: the caller treats 200 as 'reclaim
        accepted', so the response has to be delivered before the process
        dies. (image-gen's hard unload exits first and resets the connection;
        this one deliberately doesn't.) Gated on the process still holding
        VRAM once the model is dropped — here, the driver's count."""
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch, reserved_mb=0)
        _nvml(monkeypatch, own_mb=660)
        timers: list = []

        class _FakeTimer:
            def __init__(self, delay, fn):
                timers.append((delay, fn))

            def start(self):
                pass  # never actually exit the test runner

        monkeypatch.setattr(mod.threading, "Timer", _FakeTimer)
        mod._model = MagicMock()

        body = mod.unload(mod.UnloadRequest(hard=True))

        assert body["status"] == "exiting"
        assert body["hard"] is True
        assert body["released"] is True
        assert body["vram_process_mb"] == 660
        assert len(timers) == 1
        delay, _fn = timers[0]
        assert delay > 0, "exit must be deferred so the response can flush"

    def test_hard_unload_holding_nothing_declines_instead_of_exiting(self, monkeypatch):
        """Below the floor an exit reclaims nothing and buys a cold start —
        the image-gen lesson (~24 consecutive no-op exits before its gate)."""
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch, reserved_mb=0)
        _nvml(monkeypatch, own_mb=None)
        timers: list = []
        monkeypatch.setattr(mod.threading, "Timer", lambda *a, **k: timers.append(a))
        mod._model = MagicMock()

        body = mod.unload(mod.UnloadRequest(hard=True))

        assert body["status"] == "nothing_to_reclaim"
        assert body["vram_process_mb"] == 0
        assert body["vram_process_source"] == "nvml"
        assert timers == []

    def test_unload_with_no_body_is_soft(self, monkeypatch):
        """FastAPI passes None when the caller posts no JSON; that must not
        be read as a hard unload, however much VRAM the process holds."""
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch, reserved_mb=0)
        _nvml(monkeypatch, own_mb=660)
        monkeypatch.setattr(
            mod.threading, "Timer",
            lambda *a, **k: pytest.fail("no-body unload must not exit the process"),
        )
        mod._model = MagicMock()

        assert mod.unload(None)["hard"] is False

    # ---- in-flight guard: /unload must never cut a synthesis short ----

    def test_unload_declines_soft_and_hard_while_a_request_is_in_flight(self, monkeypatch):
        """The ladder wants VRAM to START work; the work already running is
        what the VRAM is for. Neither unload mode waits it out."""
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch, reserved_mb=0)
        _nvml(monkeypatch, own_mb=660)
        unload_calls: list = []
        monkeypatch.setattr(mod, "_unload_model", lambda: unload_calls.append(True))
        mod._model = MagicMock()
        mod._inflight = 1

        soft = mod.unload(mod.UnloadRequest(hard=False))
        hard = mod.unload(mod.UnloadRequest(hard=True))

        assert soft["status"] == hard["status"] == "busy"
        assert soft["inflight"] == hard["inflight"] == 1
        assert unload_calls == []
        assert mod._model is not None

    def test_speech_counts_itself_in_inflight_for_its_whole_stay(self, monkeypatch):
        mod = _load_server(monkeypatch)
        seen: list[int] = []

        def fake_inner(req):
            seen.append(mod._inflight)
            return "ok"

        monkeypatch.setattr(mod, "_speech", fake_inner)

        result = mod.speech(mod.SpeechRequest(input="hi"))

        assert result == "ok"
        assert seen == [1], "counted in before the handler body runs"
        assert mod._inflight == 0, "released once the handler returns"

    def test_speech_releases_inflight_even_when_it_raises(self, monkeypatch):
        mod = _load_server(monkeypatch)

        def fake_inner(req):
            raise RuntimeError("synthesis blew up")

        monkeypatch.setattr(mod, "_speech", fake_inner)

        with pytest.raises(RuntimeError):
            mod.speech(mod.SpeechRequest(input="hi"))

        assert mod._inflight == 0

    def test_deferred_exit_stands_down_if_a_request_arrived_after_the_unload_answered(
        self, monkeypatch,
    ):
        """The exit is irreversible. If a synthesis started (and holds the
        lock) between the unload's response and the deferred exit, the
        process must stay up. The lock is only TRIED, never waited for —
        otherwise this would block behind whatever synthesis is running."""
        mod = _load_server(monkeypatch)
        exits: list = []
        monkeypatch.setattr(mod.os, "_exit", lambda code: exits.append(code))

        with mod._model_lock:  # a synthesis in progress
            mod._deferred_exit()  # tries the lock, does not block, gives up

        assert exits == []

    def test_deferred_exit_stands_down_if_the_model_was_reloaded_meanwhile(self, monkeypatch):
        """The lock is free (the synthesis that reloaded the model has
        released it), but a fresh model is exactly what an exit would
        discard for nothing."""
        mod = _load_server(monkeypatch)
        mod._model = MagicMock()
        exits: list = []
        monkeypatch.setattr(mod.os, "_exit", lambda code: exits.append(code))

        mod._deferred_exit()

        assert exits == []

    def test_deferred_exit_runs_when_nothing_arrived(self, monkeypatch):
        mod = _load_server(monkeypatch)
        exits: list = []
        monkeypatch.setattr(mod.os, "_exit", lambda code: exits.append(code))

        mod._deferred_exit()

        assert exits == [0]


# ---------------------------------------------------------------------------
# The exit gate: what the driver counts for this process (2026-09-25)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExitGate:
    def test_gate_prefers_nvml_over_the_reserved_pool(self, monkeypatch):
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch, reserved_mb=999_999)  # would gate reclaimable=True
        _nvml(monkeypatch, own_mb=None)  # but the driver says: nothing held

        gate = mod._exit_gate()

        assert gate["vram_process_source"] == "nvml"
        assert gate["reclaimable"] is False, "the driver's answer wins, not the stale reserved figure"

    def test_gate_falls_back_to_reserved_pool_when_nvml_is_unusable(self, monkeypatch, caplog):
        mod = _load_server(monkeypatch)
        _fake_torch(monkeypatch, reserved_mb=700)
        monkeypatch.setitem(sys.modules, "pynvml", None)  # import raises

        with caplog.at_level("WARNING", logger="chatterbox-server"):
            gates = [mod._exit_gate() for _ in range(3)]

        assert all(g["vram_process_source"] is None for g in gates)
        assert all(g["vram_reserved_mb"] == 700 and g["reclaimable"] for g in gates)
        assert sum("NVML unusable" in r.getMessage() for r in caplog.records) == 1

    def test_nvml_initialises_once_and_reads_every_time(self, monkeypatch):
        mod = _load_server(monkeypatch)
        fake = _nvml(monkeypatch, own_mb=660)

        for _ in range(3):
            mod._process_vram_mb()

        assert fake.calls["init"] == 1
        assert fake.calls["procs"] == 3

    def test_process_listed_without_its_memory_falls_back_rather_than_reading_zero(
        self, monkeypatch,
    ):
        """Where per-process accounting is unavailable (WDDM, WSL2) NVML lists
        the process with no figure; reading that as 0 would call a
        context-holding process empty."""
        mod = _load_server(monkeypatch)
        fake = _nvml(monkeypatch)
        fake.own_unavailable = True

        assert mod._process_vram_mb() is None
        assert "per-process accounting" in mod._nvml_error

    def test_the_count_is_this_pid_summed_over_every_visible_gpu(self, monkeypatch):
        mod = _load_server(monkeypatch)
        _nvml(monkeypatch, own_mb=660, own_device=1, devices=2, others=[(4242, 10_952)])

        assert mod._process_vram_mb() == 660


# ---------------------------------------------------------------------------
# The idle pass: drop the model, then exit for the context it leaves
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdlePass:
    def _idle(self, mod):
        mod._last_used = time.time() - mod._IDLE_TIMEOUT_S - 1

    def test_idle_pass_exits_once_the_model_is_dropped_and_a_context_is_left(
        self, monkeypatch,
    ):
        mod = _load_server(monkeypatch, idle_timeout="120")
        _fake_torch(monkeypatch, reserved_mb=0)
        fake = _nvml(monkeypatch, own_mb=3900)
        mod._model = MagicMock()
        self._idle(mod)
        exits: list = []
        monkeypatch.setattr(mod.os, "_exit", lambda code: exits.append(code))

        def drop():
            mod._model = None
            fake.own_mb = 660  # what the driver counts once the model is gone
            return True

        monkeypatch.setattr(mod, "_unload_model", drop)

        mod._idle_pass()

        assert exits == [0]

    def test_idle_pass_exits_for_the_context_a_soft_unload_left(self, monkeypatch):
        """The ladder's soft /unload drops the model before the timer does and
        leaves the context. The idle pass exits for it once the timeout has
        run, without anything left to unload."""
        mod = _load_server(monkeypatch, idle_timeout="120")
        _fake_torch(monkeypatch, reserved_mb=0)
        _nvml(monkeypatch, own_mb=660)
        mod._model = None
        self._idle(mod)
        exits: list = []
        monkeypatch.setattr(mod.os, "_exit", lambda code: exits.append(code))

        mod._idle_pass()

        assert exits == [0]

    def test_idle_pass_keeps_a_warm_process_inside_the_timeout(self, monkeypatch):
        mod = _load_server(monkeypatch, idle_timeout="120")
        _nvml(monkeypatch, own_mb=660)
        mod._model = None
        mod._last_used = time.time()  # just used
        exits: list = []
        monkeypatch.setattr(mod.os, "_exit", lambda code: exits.append(code))

        mod._idle_pass()

        assert exits == []

    def test_idle_pass_on_a_process_that_has_served_nothing_reads_nothing(self, monkeypatch):
        """Every exit starts a fresh process with ``_last_used`` 0 and no
        context; the pass does not even ask the driver."""
        mod = _load_server(monkeypatch, idle_timeout="120")
        fake = _nvml(monkeypatch, own_mb=None)
        assert mod._last_used == 0.0
        exits: list = []
        monkeypatch.setattr(mod.os, "_exit", lambda code: exits.append(code))

        mod._idle_pass()

        assert exits == []
        assert fake.calls["procs"] == 0

    def test_idle_pass_leaves_a_counted_in_request_alone(self, monkeypatch):
        """``_inflight`` catches a window ``_last_used`` cannot: a request
        that has entered ``speech()`` but is still waiting for
        ``_model_lock`` (real synthesis holds that lock for its whole run, so
        this is brief) has not yet re-stamped ``_last_used``. Neither stage —
        the model drop nor the process exit — must act on it."""
        mod = _load_server(monkeypatch, idle_timeout="120")
        _nvml(monkeypatch, own_mb=3900)
        mod._model = MagicMock()
        mod._inflight = 1
        self._idle(mod)
        exits: list = []
        monkeypatch.setattr(mod.os, "_exit", lambda code: exits.append(code))

        mod._idle_pass()

        assert mod._model is not None, "counted in must not lose its model either"
        assert exits == []

    def test_zero_timeout_never_exits_however_long_it_sits(self, monkeypatch):
        mod = _load_server(monkeypatch, idle_timeout="0")
        _nvml(monkeypatch, own_mb=660)
        mod._model = None
        mod._last_used = time.time() - 86400
        exits: list = []
        monkeypatch.setattr(mod.os, "_exit", lambda code: exits.append(code))

        mod._idle_pass()

        assert exits == []


# ---------------------------------------------------------------------------
# Boot log names what the gate measures (mirrors wan / stable-audio / RIFE)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_boot_log_names_the_exit_gates_measure(monkeypatch, caplog):
    mod = _load_server(monkeypatch, idle_timeout="120")
    _nvml(monkeypatch, own_mb=None)

    async def body():
        # asyncio.run cancels the idle_unloader task this creates once body()
        # returns (same idiom as test_wan_server_health_nvml.py) — nothing
        # here needs it to actually run.
        await mod._start_idle_unloader()

    with caplog.at_level("INFO", logger="chatterbox-server"):
        asyncio.run(body())

    assert any(
        "exit gate measures this process through NVML (0 MiB held now" in r.getMessage()
        for r in caplog.records
    )
