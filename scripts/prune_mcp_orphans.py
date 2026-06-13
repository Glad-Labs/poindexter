"""
MCP Orphan Sweep — reaps abandoned Claude Code MCP-server processes.

When a Claude Code session is force-killed, crashes, or OOMs, Windows does NOT
reap its child process tree (there are no process groups like Unix). The
session's MCP servers — npx/node launchers, python servers, bun, uv/uvx,
mcp-grafana, mcp-server-docker, Serena's pyright LSP — can linger, holding RAM.
This sweep finds and kills *genuine orphans only*.

Safety — a process is reaped ONLY if ALL of these hold:
  1. its executable name is in ALLOWLIST (MCP-server runtimes only)
  2. its command line carries an MCP signature (MCP_MARKERS) — this is what
     excludes the worker, brain daemon, dev servers, and any non-MCP use of
     those runtimes (none of which carry an MCP marker)
  3. its immediate parent PID is absent from the live-process snapshot. A live
     parent is, by definition, present in the snapshot — so this rule can NEVER
     false-positive onto a process whose parent is still alive. The only way to
     be flagged is a parent that has genuinely exited.
  4. it is older than GRACE_SECONDS (never races a just-spawned server whose
     parent is mid-handoff)
  5. it is not this sweep process itself

Consequently the Docker stack (vmmem / com.docker), anything with a live parent,
anything outside the allowlist, and your live Claude Code sessions are never
touched.

Cadence is owned by Windows Task Scheduler (see prune-mcp-orphans.ps1) — this
script is a one-shot: it sweeps once and exits. Default is ENFORCE; pass
--dry-run to log would-be kills without killing anything.

Usage:
    python  scripts/prune_mcp_orphans.py --dry-run   # log only, kill nothing
    pythonw scripts/prune_mcp_orphans.py             # enforce (windowless)
    python  scripts/prune_mcp_orphans.py --dry-run --verbose

Log: ~/.poindexter/mcp-orphan-sweep.log
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

# A just-spawned server may briefly outlive the npx/uvx launcher that started
# it while the parent claude process re-parents it; wait this long before a
# dead parent counts as "orphaned".
GRACE_SECONDS = 120

# Executable names we are ever willing to kill (lowercased). These are the
# runtimes Claude Code MCP servers run under. Notably ABSENT: pythonw.exe (this
# script's own runner), conhost.exe and cmd.exe (Windows reaps orphaned console
# wrappers on its own), and every host daemon under scripts/ (no MCP marker).
ALLOWLIST = frozenset(
    {
        "node.exe",
        "python.exe",
        "python3.13.exe",
        "bun.exe",
        "uv.exe",
        "uvx.exe",
        "mcp-grafana.exe",
        "mcp-server-docker.exe",
        "deno.exe",
    }
)

# Substrings (lowercased) that mark a process as an MCP server. Matched against
# "<name> <cmdline>". This is the gate that protects the worker / brain / dev
# servers: they run the same runtimes but carry none of these markers.
MCP_MARKERS = (
    "mcp",  # mcp-server, mcp-grafana, @playwright/mcp, context7-mcp, ollama-mcp, ...
    "modelcontextprotocol",
    "pyright",  # Serena's bundled language server
    "serena",
)

LOG_DIR = Path.home() / ".poindexter"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "mcp-orphan-sweep.log", encoding="utf-8"),
        # pythonw.exe (windowless) has no stdout — guard like the other daemons.
        *([] if sys.stdout is None else [logging.StreamHandler(sys.stdout)]),
    ],
)
logger = logging.getLogger("mcp-orphan-sweep")


@dataclass(frozen=True)
class ProcInfo:
    """Flat, psutil-free snapshot of one process — keeps should_reap() pure."""

    pid: int
    ppid: int
    name: str  # lowercased executable name
    cmdline: str  # full command line, lowercased
    create_time: float  # epoch seconds


def should_reap(
    p: ProcInfo,
    live_pids: frozenset[int] | set[int],
    now: float,
    self_pid: int,
    grace_s: int = GRACE_SECONDS,
) -> bool:
    """Pure predicate: True iff `p` is a genuine, reapable MCP orphan.

    See the module docstring for the five conditions. This function takes no
    psutil objects so it can be unit-tested against synthetic process records.
    """
    if p.pid == self_pid:
        return False
    if p.name not in ALLOWLIST:
        return False
    haystack = f"{p.name} {p.cmdline}"
    if not any(marker in haystack for marker in MCP_MARKERS):
        return False
    if p.ppid in live_pids:
        # Parent is alive -> not an orphan. This is the provable-safety gate.
        return False
    if (now - p.create_time) < grace_s:
        return False
    return True


def collect() -> tuple[list[ProcInfo], frozenset[int]]:
    """Snapshot every process once. Returns (infos, live_pids).

    The full iteration completes before any predicate runs, so `live_pids` is a
    complete set when should_reap() consults it.
    """
    infos: list[ProcInfo] = []
    live: set[int] = set()
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        try:
            info = proc.info
            pid = info["pid"]
            live.add(pid)
            infos.append(
                ProcInfo(
                    pid=pid,
                    ppid=info["ppid"] or 0,
                    name=(info["name"] or "").lower(),
                    cmdline=" ".join(info["cmdline"] or []).lower(),
                    create_time=info["create_time"] or 0.0,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return infos, frozenset(live)


def sweep(dry_run: bool) -> int:
    """Run one sweep. Returns the number of orphan candidates found."""
    infos, live = collect()
    now = time.time()
    self_pid = os.getpid()

    candidates = [p for p in infos if should_reap(p, live, now, self_pid)]
    reaped = 0
    for p in candidates:
        age = int(now - p.create_time)
        verb = "WOULD-REAP" if dry_run else "REAP"
        logger.info(
            "%s pid=%d name=%s age=%ds ppid=%d(dead) cmd=%s",
            verb,
            p.pid,
            p.name,
            age,
            p.ppid,
            p.cmdline[:160],
        )
        if dry_run:
            continue
        try:
            proc = psutil.Process(p.pid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
            reaped += 1
        except psutil.NoSuchProcess:
            pass  # already gone between snapshot and kill — fine
        except psutil.AccessDenied:
            logger.warning("access denied killing pid=%d name=%s", p.pid, p.name)

    logger.info(
        "sweep complete: scanned=%d candidates=%d reaped=%d mode=%s",
        len(infos),
        len(candidates),
        reaped,
        "dry-run" if dry_run else "enforce",
    )
    return len(candidates)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reap orphaned Claude Code MCP-server processes."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log what would be reaped; kill nothing",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="raise log level to DEBUG (more detail on stdout)",
    )
    args = parser.parse_args(argv)
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    sweep(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
