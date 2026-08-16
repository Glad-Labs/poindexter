"""Contract tests for ``install-session-timers.sh``'s unit-template render.

Half of the 2026-08-15 deploy gap: ``run-session.sh`` now self-updates via its
ff-only pre-flight, but the systemd **unit file** cannot — the installed copy
under /etc/systemd/system is a render, and writing /etc needs sudo. The fix is
making the installer the one idempotent deploy path for the unit too: it renders
``User=`` and the ``ExecStart`` path from the repo template (which ships a
generic ``poindexter`` / /home/poindexter placeholder) onto the current host.

Driven with a fake ``sudo`` (exec-through) and fake ``systemctl`` (call
recorder) on PATH, plus ``POINDEXTER_UNIT_DIR`` pointed at a tmp dir — the
same seam a non-standard install would use.
"""
from __future__ import annotations

import getpass
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

_SESSIONS = (
    "dependency-review", "codebase-audit", "doc-sync", "claude-md-sync",
    "triage-sweep", "alert-triage", "test-health", "pro-freshness",
)


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "install-session-timers.sh").exists()
    )


def _run_installer(
    tmp_path: Path, **env_extra: str
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    bin_dir = tmp_path / "bin"
    unit_dir = tmp_path / "units"
    bin_dir.mkdir()
    unit_dir.mkdir()

    fake_sudo = bin_dir / "sudo"
    fake_sudo.write_text('#!/usr/bin/env bash\nexec "$@"\n', encoding="utf-8")
    fake_sudo.chmod(0o755)

    calls = tmp_path / "systemctl-calls"
    fake_systemctl = bin_dir / "systemctl"
    fake_systemctl.write_text(
        f'#!/usr/bin/env bash\necho "$@" >> "{calls}"\nexit 0\n', encoding="utf-8"
    )
    fake_systemctl.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(_repo_root() / "scripts" / "linux" / "install-session-timers.sh")],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "POINDEXTER_UNIT_DIR": str(unit_dir),
            **env_extra,
        },
        capture_output=True, text=True, timeout=60,
    )
    return proc, unit_dir, calls


def test_service_template_is_rendered_for_this_host(tmp_path):
    proc, unit_dir, _ = _run_installer(tmp_path)
    assert proc.returncode == 0, proc.stderr
    unit = (unit_dir / "poindexter-session@.service").read_text(encoding="utf-8")
    assert f"User={getpass.getuser()}" in unit, "placeholder user must be replaced"
    assert "poindexter\n" not in unit.split("User=", 1)[1].split("\n", 1)[0]
    expected_exec = f"ExecStart={_repo_root()}/scripts/linux/run-session.sh %i"
    assert expected_exec in unit, "ExecStart must point at the installing checkout"
    directives = [ln for ln in unit.splitlines() if ln and not ln.startswith("#")]
    assert not any("/home/poindexter" in ln for ln in directives), (
        "placeholder path must not survive the render outside comments"
    )


def test_all_eight_timers_are_written_and_enabled(tmp_path):
    proc, unit_dir, calls = _run_installer(tmp_path)
    assert proc.returncode == 0, proc.stderr
    for name in _SESSIONS:
        timer = unit_dir / f"poindexter-session@{name}.timer"
        assert timer.exists(), f"missing timer for {name}"
        assert "OnCalendar=" in timer.read_text(encoding="utf-8")
    recorded = calls.read_text(encoding="utf-8")
    for name in _SESSIONS:
        assert f"enable --now poindexter-session@{name}.timer" in recorded
    assert "daemon-reload" in recorded


def test_sudo_user_wins_over_effective_user(tmp_path):
    """The documented invocation is `sudo bash install-session-timers.sh`, so the
    whole script runs as root and `id -un` answers root. Caught live on the first
    deploy (2026-08-16): the unit rendered User=root, which would have run every
    session against root's HOME — no poetry env, no ~/.poindexter. SUDO_USER
    (set by sudo to the invoking login) must win."""
    proc, unit_dir, _ = _run_installer(tmp_path, SUDO_USER="operator-login")
    assert proc.returncode == 0, proc.stderr
    unit = (unit_dir / "poindexter-session@.service").read_text(encoding="utf-8")
    assert "User=operator-login" in unit
    assert f"User={getpass.getuser()}" not in unit


def test_repo_template_keeps_generic_placeholder(tmp_path):
    """The repo copy must stay generic — the render happens at install time,
    so a committed render (one host's login/path) would break every other host."""
    template = (
        _repo_root() / "infrastructure" / "systemd" / "poindexter-session@.service"
    ).read_text(encoding="utf-8")
    assert "User=poindexter" in template
    assert "ExecStart=/home/poindexter/glad-labs-website/scripts/linux/run-session.sh %i" in template
