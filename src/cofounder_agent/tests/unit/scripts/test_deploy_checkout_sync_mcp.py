"""Contract tests for ``deploy-checkout-sync.sh``'s claude.ai-connector step.

The other half of the 2026-08-15 deploy gap (see
``test_install_session_timers.py`` for the session-unit half): the phone
connector ``poindexter-mcp-http.service`` used to run from the operator
checkout, OUTSIDE the deploy clone the sync resets — so a merged change under
``mcp-server/**`` silently never reached the phone surface (PR #3247 needed a
manual FF + restart). The fix repoints the unit at the deploy clone and
teaches the sync to (a) ``uv sync`` the clone's ``mcp-server/.venv`` when the
dependency set changed or the venv is missing, and (b) restart the unit
whenever ``mcp-server/**`` changed at all.

Driven the same way as the session-timer tests: fake ``sudo`` (flag-stripping
exec-through) plus ``systemctl`` / ``uv`` / ``docker`` recorders on PATH, a
throwaway git origin + deploy clone, and ``POINDEXTER_DEPLOY_ROOT`` pointed at
the clone. All fakes append to ONE shared events file so call order (venv
synced before the process reloads) is assertable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash + git",
)

_UNIT = "poindexter-mcp-http.service"
_GIT_ID = ("-c", "user.name=t", "-c", "user.email=t@example.com")


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "deploy-checkout-sync.sh").exists()
    )


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *_GIT_ID, *args],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


def _build_rig(tmp_path: Path) -> dict:
    """Origin repo + deploy clone one commit ahead-able, fakes on PATH."""
    home = tmp_path / "home"
    (home / ".poindexter").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    events = tmp_path / "events"

    def fake(name: str, body: str) -> None:
        f = bin_dir / name
        f.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
        f.chmod(0o755)

    fake("sudo", 'while [[ "${1:-}" == -* ]]; do shift; done\nexec "$@"\n')
    fake(
        "systemctl",
        'echo "systemctl $*" >> "$EVENTS_FILE"\n'
        'if [[ "${1:-}" == show ]]; then echo "${FAKE_LOADSTATE:-loaded}"; fi\n'
        "exit 0\n",
    )
    fake("uv", 'echo "uv $*" >> "$EVENTS_FILE"\nexit "${FAKE_UV_EXIT:-0}"\n')
    fake("docker", "exit 1\n")  # no containers present -> bounce loop skips all

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)],
        check=True, timeout=60,
    )

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    # .venv/ must be ignored in the fixture exactly like the real repo — the
    # sync's `clean -fd` relies on it to spare the clone's venv.
    (seed / ".gitignore").write_text(".venv/\n__pycache__/\n", encoding="utf-8")
    stack = seed / "scripts" / "start-stack.sh"
    stack.parent.mkdir(parents=True)
    stack.write_text(
        '#!/usr/bin/env bash\necho "start-stack $*" >> "${EVENTS_FILE:-/dev/null}"\nexit 0\n',
        encoding="utf-8",
    )
    stack.chmod(0o755)
    mcp = seed / "mcp-server"
    mcp.mkdir()
    (mcp / "server.py").write_text("BASE = 1\n", encoding="utf-8")
    (mcp / "pyproject.toml").write_text('[project]\nname = "m"\n', encoding="utf-8")
    (mcp / "uv.lock").write_text("lock-v1\n", encoding="utf-8")
    svc = seed / "src" / "cofounder_agent" / "services"
    svc.mkdir(parents=True)
    (svc / "foo.py").write_text("X = 1\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "A")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    base_sha = _git(seed, "rev-parse", "HEAD")

    clone = tmp_path / "deploy-clone"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)], check=True, timeout=60
    )
    (home / ".poindexter" / "deploy-last-restarted-sha").write_text(
        base_sha, encoding="utf-8"
    )
    return {
        "home": home, "bin": bin_dir, "events": events,
        "seed": seed, "clone": clone, "base_sha": base_sha,
    }


def _advance_origin(rig: dict, paths: dict[str, str]) -> str:
    """Commit ``paths`` to origin/main so the clone is one commit behind."""
    seed = rig["seed"]
    for rel, content in paths.items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "B")
    _git(seed, "push", "-q", "origin", "main")
    return _git(seed, "rev-parse", "HEAD")


def _seed_venv(rig: dict) -> None:
    py = rig["clone"] / "mcp-server" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    py.chmod(0o755)


def _run_sync(rig: dict, **env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash",
            str(_repo_root() / "scripts" / "linux" / "deploy-checkout-sync.sh"),
            "--no-flow-check",
        ],
        env={
            "PATH": f"{rig['bin']}:/usr/bin:/bin",
            "HOME": str(rig["home"]),
            "POINDEXTER_DEPLOY_ROOT": str(rig["clone"]),
            "EVENTS_FILE": str(rig["events"]),
            **env_extra,
        },
        capture_output=True, text=True, timeout=120,
    )


def _events(rig: dict) -> list[str]:
    f = rig["events"]
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def _marker(rig: dict) -> str:
    return (
        (rig["home"] / ".poindexter" / "deploy-last-restarted-sha")
        .read_text(encoding="utf-8").strip()
    )


def _status(rig: dict) -> dict:
    return json.loads(
        (rig["home"] / ".poindexter" / "deploy-checkout-sync.status.json")
        .read_text(encoding="utf-8")
    )


def test_mcp_code_change_restarts_connector(tmp_path):
    rig = _build_rig(tmp_path)
    _seed_venv(rig)
    new_sha = _advance_origin(rig, {"mcp-server/server.py": "BASE = 2\n"})
    proc = _run_sync(rig)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    ev = _events(rig)
    assert f"systemctl restart {_UNIT}" in ev
    assert not [ln for ln in ev if ln.startswith("uv ")], (
        "code-only change must not touch the venv"
    )
    assert _marker(rig) == new_sha
    st = _status(rig)
    assert st["result"] == "deployed"
    assert _UNIT in st["restarted"]


def test_lockfile_change_syncs_venv_before_restart(tmp_path):
    rig = _build_rig(tmp_path)
    _seed_venv(rig)
    _advance_origin(rig, {"mcp-server/uv.lock": "lock-v2\n"})
    proc = _run_sync(rig)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    ev = _events(rig)
    uv_line = f"uv sync --directory {rig['clone']}/mcp-server"
    restart_line = f"systemctl restart {_UNIT}"
    assert uv_line in ev
    assert restart_line in ev
    assert ev.index(uv_line) < ev.index(restart_line), (
        "deps must land before the process reloads"
    )


def test_non_mcp_change_leaves_connector_alone(tmp_path):
    rig = _build_rig(tmp_path)
    _seed_venv(rig)
    new_sha = _advance_origin(
        rig, {"src/cofounder_agent/services/foo.py": "X = 2\n"}
    )
    proc = _run_sync(rig)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    ev = _events(rig)
    assert not [ln for ln in ev if f"restart {_UNIT}" in ln]
    assert not [ln for ln in ev if ln.startswith("uv ")]
    assert _marker(rig) == new_sha, "the deploy itself must still complete"


def test_missing_venv_self_heals_even_without_mcp_diff(tmp_path):
    """First pass after setup-deploy-checkout.sh on a host with the unit:
    the clone has no .venv yet, so the sync must create it and reload."""
    rig = _build_rig(tmp_path)  # no venv seeded
    _advance_origin(rig, {"src/cofounder_agent/services/foo.py": "X = 2\n"})
    proc = _run_sync(rig)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    ev = _events(rig)
    assert any(ln.startswith("uv sync") for ln in ev)
    assert f"systemctl restart {_UNIT}" in ev


def test_unit_not_installed_skips_gracefully(tmp_path):
    """Hosts that never enabled the connector (consumer installs) must not
    fail the pass, run uv, or attempt a restart — even with no venv."""
    rig = _build_rig(tmp_path)
    new_sha = _advance_origin(rig, {"mcp-server/server.py": "BASE = 2\n"})
    proc = _run_sync(rig, FAKE_LOADSTATE="not-found")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    ev = _events(rig)
    assert not [ln for ln in ev if f"restart {_UNIT}" in ln]
    assert not [ln for ln in ev if ln.startswith("uv ")]
    assert _marker(rig) == new_sha
    assert _status(rig)["result"] == "deployed"


def test_uv_failure_withholds_marker(tmp_path):
    rig = _build_rig(tmp_path)
    _seed_venv(rig)
    _advance_origin(rig, {"mcp-server/uv.lock": "lock-v2\n"})
    proc = _run_sync(rig, FAKE_UV_EXIT="1")
    assert proc.returncode == 1
    assert _marker(rig) == rig["base_sha"], (
        "marker must be withheld so the next cycle retries"
    )
    st = _status(rig)
    assert st["result"] == "error"
    assert "mcp-connector" in st["detail"]
    assert not [ln for ln in _events(rig) if f"restart {_UNIT}" in ln], (
        "a failed venv sync must not reload the process onto broken deps"
    )


def test_repo_unit_template_points_at_deploy_clone():
    """Pin the design decision: the connector runs from the deploy clone
    (generic template rendered per-host at install, like the session units)."""
    unit = (
        _repo_root() / "infrastructure" / "systemd" / _UNIT
    ).read_text(encoding="utf-8")
    assert "User=poindexter" in unit
    assert (
        "WorkingDirectory=/home/poindexter/.poindexter/deploy/glad-labs-stack/mcp-server"
        in unit
    )
    assert (
        "ExecStart=/home/poindexter/.poindexter/deploy/glad-labs-stack"
        "/mcp-server/.venv/bin/python http_server.py" in unit
    )
