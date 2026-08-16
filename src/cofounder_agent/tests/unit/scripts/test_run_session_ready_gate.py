"""Contract tests for run-session.sh's boot readiness gate (stack#3033).

``Persistent=true`` timers replay missed fires at boot, seconds into a
half-ready environment: Postgres still starting, gh's keyring not yet
unlocked (pre-login). After the 2026-08-04→05 downtime both catch-up runs
failed exactly that way (dependency-review 401'd GitHub, alert-triage got
connection-refused on :5433) — failure pages for runs that would have
succeeded minutes later, plus a full day's deferral of the catch-up work.

The gate waits (bounded) for Postgres + gh auth and then DEFERS — exit 0,
no session dispatch — because every session self-heals at its next
scheduled fire. These tests drive the real script with a fake ``gh`` on
PATH and a real TCP listener standing in for Postgres.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _script() -> Path:
    root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "run-session.sh").exists()
    )
    return root / "scripts" / "linux" / "run-session.sh"


class _Harness:
    def __init__(self, tmp_path: Path, *, gh_ok: bool):
        self.home = tmp_path / "home"
        self.bin = tmp_path / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        # gh that reports auth ready (or not).
        (self.bin / "gh").write_text(
            f"#!/usr/bin/env bash\nexit {0 if gh_ok else 1}\n", encoding="utf-8"
        )
        (self.bin / "gh").chmod(0o755)
        # A git whose fetch FAILS: any run that makes it past the gate dies
        # at the fetch with rc!=0, which is how the tests below distinguish
        # "deferred at the gate" (rc 0) from "proceeded into the session".
        (self.bin / "git").write_text(
            '#!/usr/bin/env bash\nfor a in "$@"; do [ "$a" = fetch ] && exit 128; done\nexit 0\n',
            encoding="utf-8",
        )
        (self.bin / "git").chmod(0o755)

    def run(self, **env_extra: str) -> tuple[subprocess.CompletedProcess, str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "POINDEXTER_REPO": str(self.home / "repo"),
            "OPS_GIT_FETCH_RETRY_SECONDS": "0",
            "OPS_GIT_FETCH_ATTEMPTS": "1",
            "OPS_CHECKOUT_SYNC": "0",
            **env_extra,
        }
        proc = subprocess.run(
            ["bash", str(_script()), "test-health"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        logs = sorted(
            (self.home / ".poindexter" / "logs" / "claude-sessions").glob("*.log")
        )
        return proc, (logs[0].read_text(encoding="utf-8") if logs else "")


def _listening_port() -> tuple[socket.socket, int]:
    """A real localhost listener standing in for Postgres."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]

    def _drain():
        while True:
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                return

    threading.Thread(target=_drain, daemon=True).start()
    return srv, port


def _closed_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_half_ready_environment_defers_with_exit_zero(tmp_path):
    """Pre-login boot shape: no keyring (gh fails), Postgres down. The run
    must defer cleanly — exit 0, no session dispatch, no failure latch."""
    h = _Harness(tmp_path, gh_ok=False)
    proc, log = h.run(
        OPS_READY_WAIT_SECONDS="1",
        OPS_READY_PG_PORT=str(_closed_port()),
    )
    assert proc.returncode == 0
    assert "deferring to the next scheduled fire" in log
    assert "deferred (environment not ready)" in log
    # The gate must report WHICH half wasn't ready.
    assert "postgres=0" in log
    assert "gh=0" in log


def test_ready_environment_passes_straight_through(tmp_path):
    """Both halves up → the gate is invisible and the session proceeds
    (into the harness's failing fetch, which proves dispatch happened)."""
    h = _Harness(tmp_path, gh_ok=True)
    srv, port = _listening_port()
    try:
        proc, log = h.run(
            OPS_READY_WAIT_SECONDS="5",
            OPS_READY_PG_PORT=str(port),
        )
    finally:
        srv.close()
    assert "deferring" not in log
    # Proceeded past the gate: the fake git fetch failure is the next stop.
    assert proc.returncode != 0
    assert "git fetch origin failed" in log


def test_gate_waits_for_postgres_to_come_up(tmp_path):
    """Postgres arriving within the budget unblocks the run — the gate turns
    a boot race into latency, not a deferral."""
    import time

    h = _Harness(tmp_path, gh_ok=True)
    port = _closed_port()

    # Bring the listener up shortly after the script starts polling. The
    # thread MUST be a daemon: a plain thread parked in accept() outlives the
    # test on any teardown race and then blocks interpreter exit — pytest
    # printed "20 passed" and hung until the CI job timeout, which GitHub
    # reports as CANCELLED (the three ghost-cancels on stack#3252's PR).
    started: list[socket.socket] = []

    def _late_bind():
        time.sleep(2.0)
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(8)
        started.append(srv)
        while True:
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                return

    threading.Thread(target=_late_bind, daemon=True).start()
    try:
        proc, log = h.run(
            OPS_READY_WAIT_SECONDS="30",
            OPS_READY_PG_PORT=str(port),
        )
    finally:
        for s in started:
            s.close()
    assert "readiness gate: waiting" in log      # it did poll at least once
    assert "deferring" not in log
    assert "git fetch origin failed" in log      # then proceeded


def test_kill_switch_disables_the_gate(tmp_path):
    """OPS_READY_GATE=0 skips the gate entirely (the seam the other
    run-session test suites use)."""
    h = _Harness(tmp_path, gh_ok=False)
    proc, log = h.run(
        OPS_READY_GATE="0",
        OPS_READY_PG_PORT=str(_closed_port()),
    )
    assert "readiness gate disabled" in log
    assert "deferring" not in log
    assert "git fetch origin failed" in log
