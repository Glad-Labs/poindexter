"""``deploy-checkout-sync.sh`` bounces the app containers ONCE per tree (stack#3661).

The 2026-09-11 incident: three sidecar Dockerfiles broke (``chown … /brain``
after brain moved), so every ten-minute pass failed at image-rebuild and
withheld the deploy marker -- correct -- but each retrying pass ALSO ran the
bounce loop. Its redundancy guard compares container ``StartedAt`` to
``reset_at_epoch``, which is set only when the same pass performed the
``git reset``; a retry finds the clone already at HEAD, sets nothing, and
restarted ``poindexter-worker`` + ``poindexter-pipeline-bot`` + the mcp-http
unit unconditionally: ~70 worker restarts in 12 hours.

Contract, tested here against the real script in a throwaway origin + clone
with recorder fakes on PATH (rig follows ``test_deploy_checkout_sync_apply.py``):

- a pass that resets onto a new tree bounces the containers and records the
  tree in ``~/.poindexter/deploy-last-bounced-sha`` even when another step of
  the same pass fails;
- a retrying pass (no reset, HEAD already recorded) bounces NOTHING and says so;
- once the failed step succeeds the marker is recorded without a further bounce;
- a new tree bounces again;
- no bounce record (first pass after this change) means bounce-once-and-record;
- the connector step keeps its own record (``deploy-last-connector-sha``) so a
  retry does not ``uv sync`` + ``systemctl restart`` the mcp-http unit again.
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
_UNIT = "poindexter-mcp-http.service"


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


# `build` exits FAKE_BUILD_EXIT so a test can make the image-rebuild step fail
# (the real incident's failing step) and withhold the deploy marker.
_FAKE_START_STACK = """#!/usr/bin/env bash
echo "start-stack $*" >> "${EVENTS_FILE:-/dev/null}"
case "${1:-}" in
  build) exit "${FAKE_BUILD_EXIT:-0}" ;;
  ps) exit 0 ;;
esac
exit 0
"""

# Containers are present; `container inspect -f` reports a StartedAt far in the
# past so the same-pass StartedAt guard never fires and only the cross-pass
# record under test can suppress a restart.
_FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$EVENTS_FILE"
case "${1:-} ${2:-}" in
  "container inspect")
    [[ "$*" == *"-f"* ]] && echo "${FAKE_STARTED_AT:-2020-01-01T00:00:00.000000000Z}"
    exit 0 ;;
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
    fake("sudo", '#!/usr/bin/env bash\nwhile [[ "${1:-}" == -* ]]; do shift; done\nexec "$@"\n')
    fake(
        "systemctl",
        '#!/usr/bin/env bash\necho "systemctl $*" >> "$EVENTS_FILE"\n'
        'if [[ "${1:-}" == show ]]; then echo "${FAKE_LOADSTATE:-not-found}"; fi\nexit 0\n',
    )
    fake("uv", '#!/usr/bin/env bash\necho "uv $*" >> "$EVENTS_FILE"\nexit 0\n')

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, timeout=60)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / ".gitignore").write_text(".venv/\n__pycache__/\n", encoding="utf-8")
    stack = seed / "scripts" / "start-stack.sh"
    stack.parent.mkdir(parents=True)
    stack.write_text(_FAKE_START_STACK, encoding="utf-8")
    stack.chmod(0o755)
    svc = seed / "src" / "cofounder_agent" / "poindexter" / "services"
    svc.mkdir(parents=True)
    (svc / "foo.py").write_text("X = 1\n", encoding="utf-8")
    mcp = seed / "mcp-server"
    mcp.mkdir()
    (mcp / "server.py").write_text("BASE = 1\n", encoding="utf-8")
    (mcp / "pyproject.toml").write_text('[project]\nname = "m"\n', encoding="utf-8")
    (mcp / "uv.lock").write_text("lock-v1\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "A")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    base_sha = _git(seed, "rev-parse", "HEAD")

    clone = tmp_path / "deploy-clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, timeout=60)
    (home / ".poindexter" / "deploy-last-restarted-sha").write_text(base_sha, encoding="utf-8")
    return {"home": home, "bin": bin_dir, "events": tmp_path / "events", "seed": seed, "clone": clone, "base_sha": base_sha}


def _advance_origin(rig: dict, paths: dict[str, str] | None = None) -> str:
    """Commit a change to origin/main so the clone is one commit behind."""
    seed = rig["seed"]
    n = len(_git(seed, "rev-list", "HEAD").splitlines())
    for rel, content in (paths or {"src/cofounder_agent/poindexter/services/foo.py": f"X = {n + 1}\n"}).items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", f"C{n + 1}")
    _git(seed, "push", "-q", "origin", "main")
    return _git(seed, "rev-parse", "HEAD")


def _run_sync(rig: dict, **env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_repo_root() / "scripts" / "linux" / "deploy-checkout-sync.sh"), "--no-flow-check"],
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


def _clear_events(rig: dict) -> None:
    if rig["events"].exists():
        rig["events"].unlink()


def _restarts(rig: dict) -> list[str]:
    return [e for e in _events(rig) if e.startswith("docker restart ")]


def _status(rig: dict) -> dict:
    return json.loads((rig["home"] / ".poindexter" / "deploy-checkout-sync.status.json").read_text(encoding="utf-8"))


def _record(rig: dict, name: str) -> str | None:
    p = rig["home"] / ".poindexter" / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


class TestBounceOncePerTree:
    def test_new_tree_bounces_and_records_even_when_another_step_fails(self, tmp_path):
        rig = _build_rig(tmp_path)
        new = _advance_origin(rig)
        proc = _run_sync(rig, FAKE_BUILD_EXIT="1")  # the image-rebuild step fails
        assert proc.returncode == 1
        assert _status(rig)["result"] == "error"
        assert "image-rebuild" in _status(rig)["detail"]
        assert sorted(_restarts(rig)) == ["docker restart poindexter-pipeline-bot", "docker restart poindexter-worker"]
        assert _record(rig, "deploy-last-bounced-sha") == new, "the bounce is recorded the moment it succeeds"
        assert _record(rig, "deploy-last-restarted-sha") == rig["base_sha"], "the deploy marker is still withheld"

    def test_retrying_pass_does_not_bounce_again(self, tmp_path):
        """The stack#3661 shape: same HEAD, marker withheld, the failed step retried."""
        rig = _build_rig(tmp_path)
        new = _advance_origin(rig)
        _run_sync(rig, FAKE_BUILD_EXIT="1")
        assert len(_restarts(rig)) == 2
        _clear_events(rig)

        proc = _run_sync(rig, FAKE_BUILD_EXIT="1")  # retry: clone already at HEAD, build still broken
        assert proc.returncode == 1
        assert _restarts(rig) == [], f"a retrying pass must not restart anything: {_restarts(rig)}"
        assert any(e.startswith("start-stack build") for e in _events(rig)), "the failed step itself IS retried"
        assert "restarts skipped" in _status(rig)["detail"] and new[:9] in _status(rig)["detail"]
        assert "already restarted onto" in proc.stdout

    def test_marker_recorded_without_a_further_bounce_once_the_failed_step_passes(self, tmp_path):
        rig = _build_rig(tmp_path)
        new = _advance_origin(rig)
        _run_sync(rig, FAKE_BUILD_EXIT="1")
        _clear_events(rig)
        proc = _run_sync(rig, FAKE_BUILD_EXIT="0")  # the sidecar Dockerfile got fixed
        assert proc.returncode == 0
        assert _status(rig)["result"] == "deployed"
        assert _restarts(rig) == []
        assert _record(rig, "deploy-last-restarted-sha") == new
        assert "restarts skipped" in _status(rig)["detail"]

    def test_a_new_tree_bounces_again(self, tmp_path):
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        _run_sync(rig)
        assert len(_restarts(rig)) == 2
        _clear_events(rig)
        newer = _advance_origin(rig)
        proc = _run_sync(rig)
        assert proc.returncode == 0
        assert len(_restarts(rig)) == 2, "code advanced again -> the containers re-import it"
        assert _record(rig, "deploy-last-bounced-sha") == newer

    def test_no_bounce_record_means_bounce_once_and_record(self, tmp_path):
        """First pass after this change (or the file was removed): the clone is
        already at HEAD with the marker behind -- the exact pre-fix retry shape --
        so the containers are bounced ONCE (the old behaviour) and the tree is
        recorded, which is what stops the loop on the pass after."""
        rig = _build_rig(tmp_path)
        new = _advance_origin(rig)
        _git(rig["clone"], "fetch", "-q", "origin")
        _git(rig["clone"], "reset", "-q", "--hard", "origin/main")  # "Already at origin/main" next pass
        assert _record(rig, "deploy-last-bounced-sha") is None
        proc = _run_sync(rig, FAKE_BUILD_EXIT="1")
        assert proc.returncode == 1
        assert len(_restarts(rig)) == 2
        assert _record(rig, "deploy-last-bounced-sha") == new
        _clear_events(rig)
        _run_sync(rig, FAKE_BUILD_EXIT="1")
        assert _restarts(rig) == []

    def test_same_pass_started_at_guard_still_holds(self, tmp_path):
        """A container compose-apply recreated moments ago (StartedAt after the
        reset) is skipped per container, as before."""
        rig = _build_rig(tmp_path)
        _advance_origin(rig)
        proc = _run_sync(rig, FAKE_STARTED_AT="2099-01-01T00:00:00.000000000Z")
        assert proc.returncode == 0
        assert _restarts(rig) == []
        assert "already-fresh" in _status(rig)["detail"]


class TestConnectorOncePerTree:
    @staticmethod
    def _seed_venv(rig: dict) -> None:
        py = rig["clone"] / "mcp-server" / ".venv" / "bin" / "python"
        py.parent.mkdir(parents=True)
        py.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        py.chmod(0o755)

    def test_retrying_pass_does_not_restart_the_connector_again(self, tmp_path):
        rig = _build_rig(tmp_path)
        self._seed_venv(rig)
        # An mcp change AND a backend change in one commit, as in the real incident:
        # the backend path matches REBUILD_MAP (auto-embed), so FAKE_BUILD_EXIT can fail
        # the pass and withhold the marker; mcp-server/ alone matches no rebuild entry.
        new = _advance_origin(rig, {
            "mcp-server/server.py": "BASE = 2\n", "mcp-server/uv.lock": "lock-v2\n",
            "src/cofounder_agent/poindexter/services/foo.py": "X = 9\n",
        })
        _run_sync(rig, FAKE_BUILD_EXIT="0", FAKE_LOADSTATE="loaded")
        events = _events(rig)
        assert any(e.startswith("uv sync") for e in events) and f"systemctl restart {_UNIT}" in events
        assert _record(rig, "deploy-last-connector-sha") == new
        _clear_events(rig)

        # Make the pass FAIL elsewhere so the marker stays withheld, then retry.
        (rig["home"] / ".poindexter" / "deploy-last-restarted-sha").write_text(rig["base_sha"], encoding="utf-8")
        proc = _run_sync(rig, FAKE_BUILD_EXIT="1", FAKE_LOADSTATE="loaded")
        assert proc.returncode == 1
        events = _events(rig)
        assert not any(e.startswith("uv sync") for e in events), events
        assert f"systemctl restart {_UNIT}" not in events, events
        assert "already synced+restarted" in proc.stdout
        assert _restarts(rig) == []

    def test_missing_venv_still_self_heals_on_a_retry(self, tmp_path):
        rig = _build_rig(tmp_path)
        new = _advance_origin(rig, {"mcp-server/server.py": "BASE = 2\n"})
        (rig["home"] / ".poindexter" / "deploy-last-connector-sha").write_text(new, encoding="utf-8")
        (rig["home"] / ".poindexter" / "deploy-last-bounced-sha").write_text(new, encoding="utf-8")
        _git(rig["clone"], "fetch", "-q", "origin")
        _git(rig["clone"], "reset", "-q", "--hard", "origin/main")
        proc = _run_sync(rig, FAKE_LOADSTATE="loaded")  # no venv in the clone
        assert proc.returncode == 0
        events = _events(rig)
        assert any(e.startswith("uv sync") for e in events), "a missing venv is healed regardless of the record"
        assert f"systemctl restart {_UNIT}" in events, "fresh deps reload the process"
