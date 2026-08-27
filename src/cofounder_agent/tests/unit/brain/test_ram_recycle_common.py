"""Shared RAM-recycle primitives (brain/ram_recycle_common.py).

Extracted from comfyui_ram_watch when the mechanism was generalised to the
queue-less GPU sidecars after the 2026-08-27 freeze. The parser test moved
here with the parser it covers.
"""
from __future__ import annotations

import pytest
from brain import ram_recycle_common as rc


def test_parse_status_rss_swap_gb():
    """VmRSS/VmSwap kB lines -> GB; missing VmSwap = 0; no VmRSS = None."""
    blob = (
        "Name:\tpython\n"
        "VmPeak:\t30000000 kB\n"
        "VmRSS:\t14574940 kB\n"
        "VmSwap:\t15518925 kB\n"
    )
    parsed = rc.parse_status_rss_swap_gb(blob)
    assert parsed is not None
    rss_gb, swap_gb = parsed
    assert rss_gb == pytest.approx(13.9, abs=0.01)
    assert swap_gb == pytest.approx(14.8, abs=0.01)

    swapless = rc.parse_status_rss_swap_gb("VmRSS:\t1048576 kB\n")
    assert swapless == (1.0, 0.0)

    assert rc.parse_status_rss_swap_gb("Name:\tpython\n") is None
    assert rc.parse_status_rss_swap_gb("") is None
    assert rc.parse_status_rss_swap_gb("VmRSS:\tgarbage kB\n") is None


def test_swap_is_counted_not_just_rss():
    """The 2026-08-27 regression in one assertion.

    chatterbox read 1.2 GB RSS + 8.9 GB swap. A probe watching RSS alone
    would have called it healthy while it held 10 GB of the host's memory,
    which is exactly how four sidecars filled 48 GB of swap unnoticed.
    """
    rss_gb, swap_gb = rc.parse_status_rss_swap_gb(
        "VmRSS:\t1258291 kB\nVmSwap:\t9332326 kB\n"
    )
    assert rss_gb == pytest.approx(1.2, abs=0.05)
    assert swap_gb == pytest.approx(8.9, abs=0.05)
    assert rss_gb + swap_gb > 10.0


@pytest.mark.parametrize(
    "raw,default,expected",
    [
        ("true", False, True),
        ("TRUE", False, True),
        ("1", False, True),
        ("yes", False, True),
        ("on", False, True),
        ("false", True, False),
        ("nonsense", True, False),
        (None, True, True),
        (None, False, False),
    ],
)
def test_coerce_bool(raw, default, expected):
    assert rc.coerce_bool(raw, default) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [("6", 6.0), (" 4.5 ", 4.5), ("garbage", 9.0), (None, 9.0), ("", 9.0)],
)
def test_coerce_float_falls_back_on_garbage(raw, expected):
    assert rc.coerce_float(raw, 9.0) == expected


@pytest.mark.parametrize(
    "raw,expected", [("60", 60), (" 30 ", 30), ("garbage", 7), (None, 7)]
)
def test_coerce_int_falls_back_on_garbage(raw, expected):
    assert rc.coerce_int(raw, 7) == expected


class _Boom:
    async def fetchval(self, *a, **k):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_read_setting_returns_default_when_db_fails():
    """A DB blip must not crash a brain cycle — it degrades to the default."""
    assert await rc.read_setting(_Boom(), "any_key", "fallback") == "fallback"


def test_read_container_cpu_percent_parses_docker_stats(monkeypatch):
    class _R:
        returncode = 0
        stdout = "1.21%\n"
        stderr = ""

    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: _R())
    assert rc.read_container_cpu_percent("c") == pytest.approx(1.21)


@pytest.mark.parametrize(
    "returncode,stdout", [(1, ""), (0, ""), (0, "  \n"), (0, "notanumber%")]
)
def test_read_container_cpu_percent_unknown_is_none_never_zero(
    monkeypatch, returncode, stdout
):
    """Unknown MUST be None, not 0.0.

    Callers treat None as "cannot prove idle" (busy). If this returned 0.0
    for an unreadable stat, a blind probe would read it as perfectly idle
    and restart a sidecar mid-inference.
    """

    class _R:
        pass

    _R.returncode = returncode
    _R.stdout = stdout
    _R.stderr = ""
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: _R())
    assert rc.read_container_cpu_percent("c") is None


def test_restart_container_reports_failure_without_raising(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(rc.subprocess, "run", _boom)
    ok, msg = rc.restart_container("c")
    assert ok is False
    assert "docker CLI not on PATH" in msg
