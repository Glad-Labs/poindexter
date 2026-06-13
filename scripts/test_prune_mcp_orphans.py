"""Unit tests for the MCP orphan-sweep kill predicate.

The predicate (`should_reap`) is pure logic with no psutil dependency, so we
exercise every gate against synthetic process records. The load-bearing test is
`test_runtime_without_mcp_marker_is_never_reaped` — proof that the worker /
brain / dev servers cannot be killed even when orphaned.

Run:  python -m pytest scripts/test_prune_mcp_orphans.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

# The script uses an underscore name precisely so it can be imported here.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prune_mcp_orphans as sweep  # noqa: E402

NOW = 1_000_000.0
LIVE_PIDS = frozenset({1, 4, 555})  # pretend these PIDs are currently alive
DEAD_PPID = 999  # not in LIVE_PIDS -> parent has exited
SELF_PID = 4242


def mk(
    *,
    pid: int = 100,
    ppid: int = DEAD_PPID,
    name: str = "node.exe",
    cmd: str = r"node c:\users\dev\appdata\local\npm-cache\_npx\x\@playwright\mcp\cli.js",
    age_s: float = 600.0,
) -> sweep.ProcInfo:
    return sweep.ProcInfo(
        pid=pid,
        ppid=ppid,
        name=name.lower(),
        cmdline=cmd.lower(),
        create_time=NOW - age_s,
    )


def reap(p: sweep.ProcInfo) -> bool:
    return sweep.should_reap(p, LIVE_PIDS, NOW, SELF_PID)


# --- the happy path: genuine orphans get reaped ----------------------------


def test_dead_parent_mcp_runtime_old_enough_is_reaped():
    assert reap(mk()) is True


def test_python_mcp_server_is_reaped():
    cmd = r"c:\repo\mcp-server\.venv\scripts\python.exe http_server.py"
    assert reap(mk(name="python.exe", cmd=cmd)) is True


def test_uvx_launched_mcp_server_is_reaped():
    assert reap(mk(name="uvx.exe", cmd="uvx some-mcp-server --stdio")) is True


def test_grafana_mcp_is_reaped():
    assert reap(mk(name="mcp-grafana.exe", cmd=r"c:\tools\mcp-grafana.exe")) is True


def test_pyright_lsp_is_reaped_when_orphaned():
    assert reap(mk(name="python.exe", cmd="python pyright-langserver --stdio")) is True


# --- the safety gates: nothing important gets reaped -----------------------


def test_live_parent_is_never_reaped():
    # ppid 555 IS in LIVE_PIDS -> parent alive -> protected (provable-safety gate)
    assert reap(mk(ppid=555)) is False


def test_runtime_without_mcp_marker_is_never_reaped():
    # The worker: same python.exe runtime, orphaned, old — but NO MCP marker.
    worker = mk(name="python.exe", cmd="python -m cofounder_agent.worker_service")
    assert reap(worker) is False
    # The brain daemon, likewise.
    brain = mk(name="python.exe", cmd=r"python c:\...\brain\brainstem.py")
    assert reap(brain) is False
    # A host daemon from scripts/ (gpu-scraper), likewise.
    daemon = mk(name="pythonw.exe", cmd="pythonw scripts/gpu-scraper.py")
    assert reap(daemon) is False


def test_too_young_is_not_reaped():
    assert reap(mk(age_s=30.0)) is False


def test_non_allowlisted_name_is_not_reaped():
    # chrome with "mcp" in its cmdline and a dead parent — still protected.
    assert reap(mk(name="chrome.exe", cmd="chrome --type=renderer mcp")) is False


def test_self_is_never_reaped():
    assert reap(mk(pid=SELF_PID)) is False


def test_exactly_at_grace_boundary_is_reaped():
    # age == grace is allowed (the guard is strictly "< grace").
    assert reap(mk(age_s=sweep.GRACE_SECONDS)) is True
