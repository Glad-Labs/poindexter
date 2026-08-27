"""Contract tests for ``deploy-checkout-sync.sh``'s compose-apply + sweep.

The 2026-08-27 outage: compose recreated ``prometheus``/``brain-daemon``,
services that others declare ``depends_on: <it>: service_healthy`` (6 for
brain-daemon alone). The dependency wait raced its own recreate and died with
``No such container: <id>``, leaving ``worker`` and ``grafana`` CREATED and
never started. That failure never stamps the new config-hash either, so the
next pass re-ran the same recreate and lost the same race — a self-perpetuating
10-minute loop with the worker down the whole time.

Two guards, tested here:

- the apply is attempted TWICE (the second sees the dependency already
  recreated and healthy — verified live: attempt 1 failed, attempt 2 rc=0);
- any project container left in ``created`` is started. ``created`` means
  never-started, which is only ever an interrupted recreate — a deliberately
  stopped service is ``exited``. That filter is load-bearing: the parked
  ``voice-agent-livekit`` still shows up in ``compose ps -a`` as exited, so a
  broader filter would silently un-park voice.

Rig follows ``test_deploy_checkout_sync_mcp.py``: throwaway git origin +
deploy clone, recorders on PATH writing to one shared events file, and
``POINDEXTER_DEPLOY_ROOT`` pointed at the clone.
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


# ``up`` consults UP_FAIL_TIMES (how many leading attempts exit non-zero) via a
# counter file, so a single fake covers "fails once then recovers" and "always
# fails". ``ps`` prints STRANDED_NAMES only for the --status=created query —
# printing for any other filter would let a test pass while the script asked
# the wrong question.
_FAKE_START_STACK = """#!/usr/bin/env bash
echo "start-stack $*" >> "${EVENTS_FILE:-/dev/null}"
action="${1:-}"
if [ "$action" = "up" ]; then
  n=0
  [ -f "$UP_COUNT_FILE" ] && n="$(cat "$UP_COUNT_FILE")"
  n=$((n + 1)); echo "$n" > "$UP_COUNT_FILE"
  [ "$n" -le "${UP_FAIL_TIMES:-0}" ] && exit 1
  exit 0
fi
if [ "$action" = "ps" ]; then
  case "$*" in
    *--status=created*) printf '%s' "${STRANDED_NAMES:-}" \
      | tr ',' '\\n' | grep -v '^$' || true ;;
  esac
  exit 0
fi
exit 0
"""

_FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$EVENTS_FILE"
case "${1:-}" in
  start) exit "${FAKE_DOCKER_START_EXIT:-0}" ;;
  # `container inspect` gates the bounce loop; fail it so the loop skips
  # every RESTART_CONTAINERS entry and leaves the sweep as the only thing
  # under test here.
  container) exit 1 ;;
esac
exit 0
"""


def _build_rig(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    (home / ".poindexter").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def fake(name: str, body: str) -> None:
        f = bin_dir / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)

    fake("docker", _FAKE_DOCKER)
    fake("systemctl", '#!/usr/bin/env bash\nexit 1\n')  # unit absent -> step skipped
    fake("sudo", '#!/usr/bin/env bash\nwhile [[ "${1:-}" == -* ]]; do shift; done\nexec "$@"\n')

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)],
        check=True, timeout=60,
    )
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    stack = seed / "scripts" / "start-stack.sh"
    stack.parent.mkdir(parents=True)
    stack.write_text(_FAKE_START_STACK, encoding="utf-8")
    stack.chmod(0o755)
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
        ["git", "clone", "-q", str(origin), str(clone)], check=True, timeout=60,
    )
    (home / ".poindexter" / "deploy-last-restarted-sha").write_text(
        base_sha, encoding="utf-8",
    )
    return {
        "home": home, "bin": bin_dir, "events": tmp_path / "events",
        "seed": seed, "clone": clone, "counter": tmp_path / "upcount",
    }


def _advance_origin(rig: dict) -> str:
    p = rig["seed"] / "src" / "cofounder_agent" / "services" / "foo.py"
    p.write_text("X = 2\n", encoding="utf-8")
    _git(rig["seed"], "add", "-A")
    _git(rig["seed"], "commit", "-q", "-m", "B")
    _git(rig["seed"], "push", "-q", "origin", "main")
    return _git(rig["seed"], "rev-parse", "HEAD")


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
            "UP_COUNT_FILE": str(rig["counter"]),
            "SYNC_APPLY_RETRY_SETTLE_SEC": "0",  # keep the retry pause out of the test
            **env_extra,
        },
        capture_output=True, text=True, timeout=180,
    )


def _events(rig: dict) -> list[str]:
    f = rig["events"]
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def _status(rig: dict) -> dict:
    return json.loads(
        (rig["home"] / ".poindexter" / "deploy-checkout-sync.status.json")
        .read_text(encoding="utf-8")
    )


def _ups(rig: dict) -> list[str]:
    return [e for e in _events(rig) if e.startswith("start-stack up")]


class TestApplyRetry:
    def test_single_apply_when_it_succeeds_first_time(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig)
        assert len(_ups(rig)) == 1, "a healthy apply must not be run twice"
        assert _status(rig)["result"] == "deployed"

    def test_transient_failure_is_retried_and_recovers(self, tmp_path):
        """The live shape: attempt 1 loses the dependency race, attempt 2 wins.
        The pass must then be a normal success — marker recorded, no error."""
        rig = _build_rig(tmp_path)
        head = _advance_origin(rig)
        proc = _run_sync(rig, UP_FAIL_TIMES="1")
        assert len(_ups(rig)) == 2, "a failed apply must be retried once"
        assert proc.returncode == 0
        st = _status(rig)
        assert st["result"] == "deployed"
        assert st["head"] == head
        marker = (rig["home"] / ".poindexter" / "deploy-last-restarted-sha")
        assert marker.read_text(encoding="utf-8").strip() == head

    def test_persistent_failure_reports_and_withholds_marker(self, tmp_path):
        rig = _build_rig(tmp_path)
        head = _advance_origin(rig)
        proc = _run_sync(rig, UP_FAIL_TIMES="99")
        assert len(_ups(rig)) == 2, "retry is bounded at two attempts"
        assert proc.returncode == 1
        st = _status(rig)
        assert st["result"] == "error"
        assert "compose-apply" in st["detail"]
        marker = (rig["home"] / ".poindexter" / "deploy-last-restarted-sha")
        assert marker.read_text(encoding="utf-8").strip() != head, (
            "a failed pass must retry next cycle, not record the marker"
        )


class TestStrandedSweep:
    def test_created_containers_are_started(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig, STRANDED_NAMES="poindexter-worker,poindexter-grafana")
        started = [e for e in _events(rig) if e.startswith("docker start ")]
        assert started == [
            "docker start poindexter-worker",
            "docker start poindexter-grafana",
        ]

    def test_recovery_is_reported_even_on_a_clean_pass(self, tmp_path):
        """A silent self-heal is how a recurring fault stays invisible."""
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig, STRANDED_NAMES="poindexter-worker")
        st = _status(rig)
        assert st["result"] == "deployed"
        assert "recovered stranded: poindexter-worker" in st["detail"]

    def test_nothing_started_when_nothing_is_stranded(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig)
        assert not [e for e in _events(rig) if e.startswith("docker start ")]

    def test_sweep_asks_only_for_created_state(self, tmp_path):
        """Guards the safety filter itself. Parked services (voice-agent) are
        listed by ``compose ps -a`` as ``exited``; querying anything broader
        than ``--status=created`` would un-park them."""
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig)
        ps_calls = [e for e in _events(rig) if e.startswith("start-stack ps")]
        assert ps_calls, "the sweep must query compose for stranded containers"
        for call in ps_calls:
            assert "--status=created" in call
            assert "--status=exited" not in call

    def test_sweep_runs_even_when_apply_failed(self, tmp_path):
        """The sweep is the safety net FOR a failed apply — if it only ran on
        success it would be absent exactly when it is needed."""
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig, UP_FAIL_TIMES="99", STRANDED_NAMES="poindexter-worker")
        assert "docker start poindexter-worker" in _events(rig)

    def test_failed_start_is_surfaced(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        proc = _run_sync(
            rig, STRANDED_NAMES="poindexter-worker", FAKE_DOCKER_START_EXIT="1",
        )
        assert proc.returncode == 1
        assert "stranded-start" in _status(rig)["detail"]
