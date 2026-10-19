"""The nightly sleep window's decisions (scripts/linux/night_sleep.py).

2026-10-18: the operator's box sleeps 01:00-08:00 to stop paying for 365 W around
the clock. The I/O (psql, rtcwake, docker) is exercised by hand on the host;
pinned here is the part that decides WHEN: inside the window or not, within
the defer limit or past it, the wake instant, a window that crosses midnight,
and how a container's Ollama URL is reached from the host.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_PATH = next(p for p in Path(__file__).resolve().parents if (p / "scripts" / "linux").is_dir()) \
    / "scripts" / "linux" / "night_sleep.py"
_spec = importlib.util.spec_from_file_location("night_sleep", _PATH)
ns = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ns)

NY = ZoneInfo("America/New_York")


def _at(hh, mm, day=19):
    return datetime(2026, 10, day, hh, mm, tzinfo=NY)


@pytest.mark.parametrize(("now", "state"), [
    (_at(0, 50), "outside"), (_at(1, 0), "due"), (_at(2, 59), "due"),
    (_at(3, 0), "too_late"), (_at(7, 59), "too_late"), (_at(8, 0), "outside"), (_at(14, 0), "outside"),
])
def test_where_now_sits_in_the_window(now, state):
    assert ns.window(now, "01:00", "08:00", 120)["state"] == state


def test_the_wake_instant_is_the_same_morning():
    win = ns.window(_at(1, 20), "01:00", "08:00", 120)
    assert win["start_at"] == _at(1, 0) and win["wake_at"] == _at(8, 0)


def test_a_window_that_crosses_midnight():
    win = ns.window(_at(23, 30, day=18), "23:00", "07:00", 120)
    assert win["state"] == "due" and win["wake_at"] == _at(7, 0)
    assert ns.window(_at(6, 0), "23:00", "07:00", 120)["state"] == "too_late"
    assert ns.window(_at(12, 0), "23:00", "07:00", 120)["state"] == "outside"


def test_a_container_ollama_url_as_the_host_reaches_it():
    assert ns.host_url("http://host.docker.internal:11435/") == "http://127.0.0.1:11435"
    assert ns.host_url("") == ""
    assert ns.host_url("file:///etc/passwd") == ""  # only http(s) is ever opened


def test_the_gpu_holder_pattern_matches_what_the_scheduler_writes():
    from poindexter.services import gpu_scheduler as gs

    prefix = ns.GPU_HOLDER_PATTERN.rstrip("%")
    for owner, phase in (("video", "media_render"), ("ollama", "draft"), ("image_gen", "hero")):
        assert gs._holder_tag(owner, phase, None)["application_name"].startswith(prefix)
    assert not "some-other-app".startswith(prefix)


class TestGpusSettle:
    """2026-10-18: the first test suspend aborted because an Ollama runner told
    to unload seconds earlier was still releasing 21 GB, and the kernel's
    freezer gave up on it. Sleep waits until no model is loaded and GPU memory
    holds still."""

    def _run(self, monkeypatch, readings, loaded):
        readings, loaded = iter(readings), iter(loaded)
        monkeypatch.setattr(ns, "_gpu_memory_used", lambda: next(readings))
        monkeypatch.setattr(ns, "_ollama_loaded", lambda _s: next(loaded))
        monkeypatch.setattr(ns.time, "sleep", lambda _s: None)
        return ns.wait_gpus_settled({}, timeout_s=3600, interval_s=0)

    def test_settles_once_memory_holds_still_with_nothing_loaded(self, monkeypatch):
        # Ollama is only asked once two readings agree: here at readings 4 and 5.
        mem = [(2900, 21000), (2900, 9000), (2629, 2), (2629, 2), (2629, 2)]
        assert self._run(monkeypatch, mem, [False, False]) is True

    def test_a_model_still_loaded_at_steady_memory_resets_the_count(self, monkeypatch):
        mem = [(2629, 2)] * 5
        assert self._run(monkeypatch, mem, [True, False, False, False]) is True

    def test_a_loaded_model_keeps_it_waiting_until_the_timeout(self, monkeypatch):
        monkeypatch.setattr(ns.time, "monotonic", iter([0, 1, 2, 3, 4, 99999]).__next__)
        assert self._run(monkeypatch, [(1, 1)] * 10, [True] * 10) is False


class _Run:
    """A subprocess.run stand-in answering by the command's first two words."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, cmd, **_kw):
        self.calls.append(cmd)
        key = " ".join(cmd[:2]) if cmd[0] == "loginctl" else cmd[0]
        if cmd[0] == "loginctl" and cmd[1] == "show-session":
            key = f"show {cmd[2]}"
        out = self.answers.get(key, "")
        return type("R", (), {"stdout": out() if callable(out) else out, "returncode": 0})()


def test_the_graphical_console_is_the_active_local_desktop_session(monkeypatch):
    run = _Run({
        "loginctl list-sessions": "  3 1000 mattm - pts/0 active no -\n  4 1000 mattm seat0 tty1 active no -\n",
        "show 3": "Type=tty\nVTNr=0\nActive=yes\nRemote=no\n",
        "show 4": "Type=wayland\nVTNr=1\nActive=yes\nRemote=no\n",
    })
    monkeypatch.setattr(ns.subprocess, "run", run)
    assert ns.graphical_vt() == 1


def test_no_graphical_session_means_no_console_switch(monkeypatch):
    run = _Run({"loginctl list-sessions": "  3 1000 mattm - pts/0 active no -\n",
                "show 3": "Type=tty\nVTNr=0\nActive=yes\nRemote=no\n"})
    monkeypatch.setattr(ns.subprocess, "run", run)
    assert ns.recover_display(settle_s=0, wait_s=0) == {"vt": None, "ok": None, "cycles": 0}
    assert not any(c[0] == "chvt" for c in run.calls)


def test_the_compositor_failure_line_is_what_counts(monkeypatch):
    monkeypatch.setattr(ns.subprocess, "run", _Run({
        "journalctl": "cosmic-comp: Unable to load output config: Failed to reset config\n"}))
    assert ns.display_failed_since(datetime(2026, 10, 18, 19, 35))
    monkeypatch.setattr(ns.subprocess, "run", _Run({"journalctl": "cosmic-comp: output enabled\n"}))
    assert not ns.display_failed_since(datetime(2026, 10, 18, 19, 35))


def test_the_console_cycle_retries_once_then_reports_failure(monkeypatch):
    monkeypatch.setattr(ns.time, "sleep", lambda _s: None)
    session = {"loginctl list-sessions": "  4 1000 mattm seat0 tty1 active no -\n",
               "show 4": "Type=wayland\nVTNr=1\nActive=yes\nRemote=no\n"}
    run = _Run({**session, "journalctl": "Failed to reset config"})
    monkeypatch.setattr(ns.subprocess, "run", run)
    assert ns.recover_display(settle_s=0) == {"vt": 1, "ok": False, "cycles": 2}
    assert [c for c in run.calls if c[0] == "chvt"] == [["chvt", "2"], ["chvt", "1"]] * 2

    run = _Run({**session, "journalctl": ""})
    monkeypatch.setattr(ns.subprocess, "run", run)
    assert ns.recover_display(settle_s=0) == {"vt": 1, "ok": True, "cycles": 1}


def test_an_inactive_desktop_session_still_gets_its_console_back(monkeypatch):
    """Right after resume logind may report the desktop inactive: the moment it needs the switch."""
    monkeypatch.setattr(ns.subprocess, "run", _Run({
        "loginctl list-sessions": "  4 1000 mattm seat0 tty1 active no -\n",
        "show 4": "Type=wayland\nVTNr=1\nActive=no\nRemote=no\n",
    }))
    assert ns.graphical_vt(wait_s=0) == 1


def test_a_remote_session_is_never_switched_to(monkeypatch):
    monkeypatch.setattr(ns.subprocess, "run", _Run({
        "loginctl list-sessions": "  9 1000 mattm - - active no -\n",
        "show 9": "Type=x11\nVTNr=7\nActive=yes\nRemote=yes\n",
    }))
    assert ns.graphical_vt(wait_s=0) is None
