"""``deploy-checkout-sync.sh`` keeps the host CLI's venv on the deployed lockfile.

Glad-Labs/poindexter#4156: the host ``poindexter`` command runs out of
``~/.poindexter/cli-venv``, editable-installed from the deploy clone
(``scripts/linux/cli-venv-sync.sh``). Code reaches it for free, since each CLI
call is a fresh process. A ``poetry.lock`` change does not, so every sync pass
runs the CLONE's ``cli-venv-sync.sh``. Pinned here:

- on a deploy pass the step runs LAST, after the container steps AND after the
  marker is recorded. A lockfile change triggers both the worker-image rebuild
  and this sync, and the unit kills the pass at TimeoutStartSec, so an earlier
  slot could cost the marker and a second round of force-recreates;
- it runs on no-change passes too, which is how a failed sync heals;
- it can never fail the pass or withhold the marker. A failure amends the
  status detail, and the pass still emits exactly one heartbeat;
- a host that never installed the host CLI (exit 3) stays silent;
- a hung sync is bounded by ``SYNC_CLI_VENV_TIMEOUT_SEC``;
- a clone that predates the script is skipped.

Rig follows ``test_deploy_checkout_sync_retry.py``: the real script against a
throwaway origin + deploy clone, recorder fakes on PATH, and a fake
``cli-venv-sync.sh`` committed INTO the fixture repo, because the sync calls the
clone's copy, not the operator checkout's.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash + git",
)

_GIT_ID = ("-c", "user.name=t", "-c", "user.email=t@example.com")


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "deploy-checkout-sync.sh").exists()
    )


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *_GIT_ID, *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


_FAKE_START_STACK = """#!/usr/bin/env bash
echo "start-stack $*" >> "${EVENTS_FILE:-/dev/null}"
exit 0
"""

_FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$EVENTS_FILE"
case "${1:-} ${2:-}" in
  "container inspect")
    [[ "$*" == *"-f"* ]] && echo "2020-01-01T00:00:00.000000000Z"
    exit 0 ;;
esac
exit 0
"""

_FAKE_CLI_VENV_SYNC = """#!/usr/bin/env bash
echo "cli-venv-sync $*" >> "$EVENTS_FILE"
echo "marker-at-cli-sync=$(cat "$HOME/.poindexter/deploy-last-restarted-sha" 2>/dev/null)" >> "$EVENTS_FILE"
if [[ "${1:-}" == --status ]]; then echo "host CLI environment: current"; exit 0; fi
echo "cli-venv-sync: progress goes to the deploy log" >&2
sleep "${FAKE_CLI_SYNC_SLEEP:-0}"
exit "${FAKE_CLI_SYNC_EXIT:-0}"
"""


def _build_rig(tmp_path: Path, *, with_cli_sync: bool = True) -> dict:
    home = tmp_path / "home"
    (home / ".poindexter").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def fake(name: str, body: str) -> None:
        f = bin_dir / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)

    fake("docker", _FAKE_DOCKER)
    fake("sudo", '#!/usr/bin/env bash\nwhile [[ "${1:-}" == -* ]]; do shift; done\nexec "$@"\n')
    fake(
        "systemctl",
        '#!/usr/bin/env bash\necho "systemctl $*" >> "$EVENTS_FILE"\n'
        'if [[ "${1:-}" == show ]]; then echo not-found; fi\nexit 0\n',
    )

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, timeout=60)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    stack = seed / "scripts" / "start-stack.sh"
    stack.parent.mkdir(parents=True)
    stack.write_text(_FAKE_START_STACK, encoding="utf-8")
    stack.chmod(0o755)
    if with_cli_sync:
        cli_sync = seed / "scripts" / "linux" / "cli-venv-sync.sh"
        cli_sync.parent.mkdir(parents=True)
        cli_sync.write_text(_FAKE_CLI_VENV_SYNC, encoding="utf-8")
        cli_sync.chmod(0o755)
    svc = seed / "src" / "cofounder_agent" / "poindexter" / "services"
    svc.mkdir(parents=True)
    (svc / "foo.py").write_text("X = 1\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "A")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    base_sha = _git(seed, "rev-parse", "HEAD")

    clone = tmp_path / "deploy-clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, timeout=60)
    (home / ".poindexter" / "deploy-last-restarted-sha").write_text(base_sha, encoding="utf-8")
    return {"home": home, "bin": bin_dir, "events": tmp_path / "events", "seed": seed,
            "clone": clone, "base_sha": base_sha}


def _advance_origin(rig: dict) -> str:
    seed = rig["seed"]
    n = len(_git(seed, "rev-list", "HEAD").splitlines())
    (seed / "src" / "cofounder_agent" / "poetry.lock").write_text(f"lock-v{n + 1}\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", f"C{n + 1}")
    _git(seed, "push", "-q", "origin", "main")
    return _git(seed, "rev-parse", "HEAD")


def _run_sync(rig: dict, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_repo_root() / "scripts" / "linux" / "deploy-checkout-sync.sh"),
         "--no-flow-check", *args],
        env={
            "PATH": f"{rig['bin']}:/usr/bin:/bin",
            "HOME": str(rig["home"]),
            "POINDEXTER_DEPLOY_ROOT": str(rig["clone"]),
            "EVENTS_FILE": str(rig["events"]),
            "SYNC_APPLY_RETRY_SETTLE_SEC": "0",
            **env_extra,
        },
        capture_output=True, text=True, timeout=180,
    )


def _events(rig: dict) -> list[str]:
    f = rig["events"]
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def _cli_calls(rig: dict) -> list[str]:
    return [e for e in _events(rig) if e.startswith("cli-venv-sync")]


def _heartbeats(rig: dict) -> list[str]:
    """The audit_log rows the brain's deploy_sync probe reads (poindexter#977).

    The INSERT is multi-line, so the fake docker's record of it spans lines;
    the VALUES line appears exactly once per heartbeat."""
    return [e for e in _events(rig) if "VALUES ('deploy_sync_run'" in e]


def _status(rig: dict) -> dict:
    return json.loads(
        (rig["home"] / ".poindexter" / "deploy-checkout-sync.status.json").read_text(encoding="utf-8")
    )


def _marker(rig: dict) -> str:
    return (rig["home"] / ".poindexter" / "deploy-last-restarted-sha").read_text(encoding="utf-8").strip()


def _log(rig: dict) -> str:
    return (rig["home"] / ".poindexter" / "deploy-checkout-sync.log").read_text(encoding="utf-8")


def test_deploy_pass_syncs_the_cli_env_after_the_container_steps(tmp_path):
    rig = _build_rig(tmp_path)
    new_sha = _advance_origin(rig)
    proc = _run_sync(rig)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    ev = _events(rig)
    assert _cli_calls(rig) == ["cli-venv-sync "], "background mode: no --ensure, no creating a venv"
    cli_at = ev.index("cli-venv-sync ")
    assert cli_at > ev.index("docker restart poindexter-worker"), (
        "a lockfile sync must not widen the reset->bounce window"
    )
    assert cli_at > max(i for i, e in enumerate(ev) if e.startswith("start-stack up -d"))
    assert f"marker-at-cli-sync={new_sha}" in ev, (
        "the pass must be recorded before the sync: a TimeoutStartSec kill "
        "during it must not cost the marker"
    )
    assert _marker(rig) == new_sha
    st = _status(rig)
    assert st["result"] == "deployed"
    assert "host CLI" not in st["detail"]
    assert "progress goes to the deploy log" in _log(rig)


def test_a_failed_cli_sync_is_reported_but_never_withholds_the_marker(tmp_path):
    """Withholding the marker would make every later pass re-run the rebuilds
    and force-recreates, a container outage caused by an unreachable PyPI."""
    rig = _build_rig(tmp_path)
    new_sha = _advance_origin(rig)
    proc = _run_sync(rig, FAKE_CLI_SYNC_EXIT="1")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert _marker(rig) == new_sha
    st = _status(rig)
    assert st["result"] == "deployed"
    assert "host CLI env sync failed (rc=1)" in st["detail"]
    assert "[ERROR] host CLI env sync failed" in _log(rig)
    assert len(_heartbeats(rig)) == 1, "the amendment rewrites the file, not the heartbeat"


def test_a_host_without_the_host_cli_stays_silent(tmp_path):
    rig = _build_rig(tmp_path)
    _advance_origin(rig)
    proc = _run_sync(rig, FAKE_CLI_SYNC_EXIT="3")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "host CLI" not in _status(rig)["detail"]
    assert "host CLI env" not in _log(rig)


def test_no_change_passes_still_heal_the_cli_env(tmp_path):
    rig = _build_rig(tmp_path)
    _advance_origin(rig)
    assert _run_sync(rig).returncode == 0
    rig["events"].unlink()

    proc = _run_sync(rig, FAKE_CLI_SYNC_EXIT="1")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert _cli_calls(rig) == ["cli-venv-sync "]
    assert not [e for e in _events(rig) if e.startswith("docker restart")]
    st = _status(rig)
    assert st["result"] == "synced-no-change"
    assert "host CLI env sync failed (rc=1)" in st["detail"]


def test_the_step_is_bounded_by_its_timeout(tmp_path):
    rig = _build_rig(tmp_path)
    new_sha = _advance_origin(rig)
    started = time.monotonic()
    proc = _run_sync(rig, FAKE_CLI_SYNC_SLEEP="30", SYNC_CLI_VENV_TIMEOUT_SEC="1")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert time.monotonic() - started < 25, "a hung sync must not hold the deploy pass"
    assert _marker(rig) == new_sha
    assert "host CLI env sync timed out after 1s" in _status(rig)["detail"]


def test_a_clone_that_predates_the_script_is_skipped(tmp_path):
    rig = _build_rig(tmp_path, with_cli_sync=False)
    new_sha = _advance_origin(rig)
    proc = _run_sync(rig)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert _cli_calls(rig) == []
    assert _marker(rig) == new_sha
    assert _status(rig)["result"] == "deployed"


def test_status_flag_includes_the_cli_env(tmp_path):
    rig = _build_rig(tmp_path)
    proc = _run_sync(rig, "--status")
    assert proc.returncode == 0
    assert "--- host CLI env ---" in proc.stdout
    assert "host CLI environment: current" in proc.stdout
    assert _cli_calls(rig) == ["cli-venv-sync --status"]
