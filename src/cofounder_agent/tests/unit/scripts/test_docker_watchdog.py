"""``docker-watchdog.sh``: restart a dead engine, raise a dead stack, and nothing else.

It had no tests while it ran out of the operator's working checkout, where a
merged change reached it only when someone pulled. Since
Glad-Labs/poindexter#4172 the unit runs the deploy clone's copy, so a
merged change runs within one deploy pass, and these pin the behaviour that
change has to keep (both properties from the 2026-08-27 incident, see the
script header):

- a healthy stack is a no-op;
- a dead engine is restarted (``sudo systemctl restart docker``);
- an unhealthy worker is confirmed ``CONFIRM_ATTEMPTS`` times before the stack is
  brought up, and a worker that recovers meanwhile (a deploy restart) is left
  alone;
- the stack is brought up FROM THE DEPLOY CLONE, whose relative bind mounts
  resolve to the paths the running containers were created with.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _repo_root() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "docker-watchdog.sh").exists()
    )


def _rig(tmp_path: Path, *, deploy_clone: bool = True) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    events = tmp_path / "events"
    health = tmp_path / "health-answers"  # one line per probe: 0 = healthy
    fakes = {
        "docker": 'echo "docker $*" >> "$EVENTS"; [ "${1:-}" = info ] && exit "${FAKE_DOCKER_INFO_EXIT:-0}"; exit 0',
        # Pops the next answer off HEALTH_ANSWERS; out of answers = unhealthy.
        "curl": 'echo "curl $*" >> "$EVENTS"; a="$(head -n1 "$HEALTH_ANSWERS" 2>/dev/null)"; '
                'sed -i 1d "$HEALTH_ANSWERS" 2>/dev/null; exit "${a:-7}"',
        "sudo": 'echo "sudo $*" >> "$EVENTS"; exit 0',
        "sleep": 'echo "sleep $*" >> "$EVENTS"; exit 0',
    }
    for name, body in fakes.items():
        f = bin_dir / name
        f.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
        f.chmod(0o755)
    trees = {}
    # "glad-labs-website" under HOME is the script's no-clone fallback.
    for name in ("deploy-clone", "glad-labs-website"):
        tree = tmp_path / name
        (tree / "scripts").mkdir(parents=True)
        stack = tree / "scripts" / "start-stack.sh"
        stack.write_text('#!/usr/bin/env bash\necho "start-stack $* (in $PWD)" >> "$EVENTS"\n', encoding="utf-8")
        stack.chmod(0o755)
        trees[name] = tree
    if deploy_clone:
        (trees["deploy-clone"] / "docker-compose.local.yml").write_text("services: {}\n", encoding="utf-8")
    return {"bin": bin_dir, "events": events, "health": health, **trees}


def _run(rig: dict, health: list[int], **env_extra: str) -> subprocess.CompletedProcess:
    rig["health"].write_text("".join(f"{h}\n" for h in health), encoding="utf-8")
    return subprocess.run(
        ["bash", str(_repo_root() / "scripts/linux/docker-watchdog.sh")],
        env={
            "PATH": f"{rig['bin']}:/usr/bin:/bin",
            "HOME": str(rig["glad-labs-website"].parent),
            "EVENTS": str(rig["events"]),
            "HEALTH_ANSWERS": str(rig["health"]),
            "POINDEXTER_DEPLOY_ROOT": str(rig["deploy-clone"]),
            "POINDEXTER_WATCHDOG_CONFIRM_INTERVAL": "0",
            **env_extra,
        },
        capture_output=True, text=True, timeout=60,
    )


def _events(rig: dict) -> list[str]:
    return rig["events"].read_text(encoding="utf-8").splitlines() if rig["events"].exists() else []


def test_a_healthy_stack_is_left_alone(tmp_path):
    rig = _rig(tmp_path)
    proc = _run(rig, [0])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not any(e.startswith(("sudo ", "start-stack ")) for e in _events(rig))


def test_a_dead_engine_is_restarted(tmp_path):
    rig = _rig(tmp_path)
    proc = _run(rig, [0], FAKE_DOCKER_INFO_EXIT="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "sudo systemctl restart docker" in _events(rig)


def test_a_worker_that_recovers_while_being_confirmed_is_not_acted_on(tmp_path):
    """The worker bounces on ordinary deploys; that is not a dead stack."""
    rig = _rig(tmp_path)
    proc = _run(rig, [7, 7, 0])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not any(e.startswith("start-stack ") for e in _events(rig))
    assert "recovered on its own" in proc.stdout


def test_a_confirmed_dead_stack_is_brought_up_from_the_deploy_clone(tmp_path):
    rig = _rig(tmp_path)
    proc = _run(rig, [7, 7, 7, 7])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ups = [e for e in _events(rig) if e.startswith("start-stack ")]
    assert ups == [f"start-stack up -d (in {rig['deploy-clone']})"], _events(rig)
    probes = [e for e in _events(rig) if e.startswith("curl ")]
    assert len(probes) == 4, "one probe, then CONFIRM_ATTEMPTS (3) confirmations"


def test_without_a_deploy_clone_it_falls_back_to_the_checkout(tmp_path):
    rig = _rig(tmp_path, deploy_clone=False)
    proc = _run(rig, [7, 7, 7, 7])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [e for e in _events(rig) if e.startswith("start-stack ")] == [
        f"start-stack up -d (in {rig['glad-labs-website']})"
    ]


def test_the_unit_runs_the_deploy_clones_copy():
    """stack#4172: a working checkout only changes when someone pulls it."""
    unit = (_repo_root() / "infrastructure/systemd/poindexter-docker-watchdog.service").read_text(encoding="utf-8")
    exec_start = re.search(r"^ExecStart=(\S+)", unit, re.M).group(1)
    assert exec_start.endswith("/.poindexter/deploy/glad-labs-stack/scripts/linux/docker-watchdog.sh"), exec_start
