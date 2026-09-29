"""``deploy-checkout-sync.sh`` step 8b restarts host daemons whose files changed.

Glad-Labs/poindexter#4188. ``poindexter-gpu-scraper`` and
``poindexter-recovery-agent`` are long-running host systemd services. Each reads
its code once, at start, so a merged change reaches it only through a restart:
the recovery agent already ran from the deploy clone and still sat on
pre-#4158 code for a day, because nothing restarted it.

Contract, tested against the real script in a throwaway origin + clone with
recorder fakes on PATH (the rig from ``test_deploy_checkout_sync_retry.py``; the
fake ``systemctl`` answers per-unit properties from ``FAKE_UNITS/<unit>/<Prop>``
and falls back to ``FAKE_LOADSTATE`` like ``test_deploy_checkout_sync_mcp.py``):

- a change to a file a daemon loads at start restarts that daemon and no other;
- the step is FAIL-SOFT: a failed restart amends the status detail and never
  withholds the deploy marker, and the unit's own record (not the marker) keeps
  the restart owed, so the next pass retries it, no-change passes included;
- a retrying pass does not restart a daemon again (once per tree);
- a unit is left alone when it is not installed, not running, not run from the
  deploy clone (noted), already started after the clone reached HEAD, or has a
  child process running (held for the next pass);
- no record yet means restart once and record;
- the table covers every repo file each daemon loads at start. That set is
  DERIVED by loading the daemon's module level, never hand-listed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash + git",
)

_GIT_ID = ("-c", "user.name=t", "-c", "user.email=t@example.com")
_SCRAPER = "poindexter-gpu-scraper.service"
_AGENT = "poindexter-recovery-agent.service"
_SCRIPT_OF = {_SCRAPER: "scripts/gpu-scraper.py", _AGENT: "scripts/recovery-agent.py"}
# The generic deploy-clone path the unit templates ship with.
_TEMPLATE_CLONE = "/home/poindexter/.poindexter/deploy/glad-labs-stack"


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


# As in the retry rig: `build` exits FAKE_BUILD_EXIT so a test can fail another
# step and withhold the marker, and every rebuilt service reads as running.
_FAKE_START_STACK = """#!/usr/bin/env bash
echo "start-stack $*" >> "${EVENTS_FILE:-/dev/null}"
case "${1:-}" in
  build) exit "${FAKE_BUILD_EXIT:-0}" ;;
  ps) [[ " $* " == *" -q "* ]] && echo 0123456789abcdef; exit 0 ;;
esac
exit 0
"""

# Containers present and started long ago; every other docker call succeeds.
_FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$EVENTS_FILE"
case "${1:-} ${2:-}" in
  "inspect -f") echo true; exit 0 ;;
  "container inspect") [[ "$*" == *"-f"* ]] && echo 2020-01-01T00:00:00.000000000Z; exit 0 ;;
esac
exit 0
"""

# `show -p <Prop> ... <unit>` prints FAKE_UNITS/<unit>/<Prop> when it exists,
# else FAKE_LOADSTATE for LoadState and nothing for anything else. `restart`
# exits FAKE_RESTART_EXIT (a missing sudo grant fails right here too, and the
# fake cannot tell root from sudo, which CI's root runner needs) and, when it
# succeeds, stamps the unit's new start time the way systemd would.
_FAKE_SYSTEMCTL = r"""#!/usr/bin/env bash
echo "systemctl $*" >> "$EVENTS_FILE"
units="${FAKE_UNITS:-/nonexistent}"
case "${1:-}" in
  show)
    prop=""; args=("$@"); unit="${args[-1]}"
    for ((i = 0; i < ${#args[@]}; i++)); do [ "${args[$i]}" = -p ] && prop="${args[$((i + 1))]}"; done
    if [ -f "$units/$unit/$prop" ]; then cat "$units/$unit/$prop"
    elif [ "$prop" = LoadState ]; then echo "${FAKE_LOADSTATE:-not-found}"; fi
    exit 0 ;;
  restart)
    rc="${FAKE_RESTART_EXIT:-0}"
    [ "$rc" = 0 ] && [ -d "$units/${2:-}" ] && echo "@$(date +%s)" > "$units/$2/ExecMainStartTimestamp"
    exit "$rc" ;;
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
    fake("systemctl", _FAKE_SYSTEMCTL)
    fake("uv", '#!/usr/bin/env bash\necho "uv $*" >> "$EVENTS_FILE"\nexit 0\n')

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, timeout=60)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    files = {
        "scripts/start-stack.sh": _FAKE_START_STACK,
        "scripts/gpu-scraper.py": "INTERVAL = 60\n",
        "scripts/recovery-agent.py": "PORT = 9841\n",
        "src/cofounder_agent/poindexter/__init__.py": "V = 1\n",
        "src/cofounder_agent/poindexter/brain/__init__.py": '"""brain"""\n',
        "src/cofounder_agent/poindexter/brain/bootstrap.py": "B = 1\n",
        "src/cofounder_agent/poindexter/brain/health_probes.py": "P = 1\n",
        "src/cofounder_agent/poindexter/services/foo.py": "X = 1\n",
    }
    for rel, text in files.items():
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (seed / "scripts" / "start-stack.sh").chmod(0o755)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "A")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    base_sha = _git(seed, "rev-parse", "HEAD")

    clone = tmp_path / "deploy-clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, timeout=60)
    (home / ".poindexter" / "deploy-last-restarted-sha").write_text(base_sha, encoding="utf-8")
    return {
        "home": home, "bin": bin_dir, "events": tmp_path / "events", "seed": seed,
        "clone": clone, "base_sha": base_sha, "units": tmp_path / "units",
        "cgroup": tmp_path / "cgroup", "records": home / ".poindexter" / "deploy-host-daemons",
    }


def _install(rig: dict, unit: str, *, active: str = "active", started_ago: int = 86400,
             procs: int = 1, exec_start: str | None = None) -> None:
    """The unit is installed on the fake host, in the given state."""
    d = rig["units"] / unit
    d.mkdir(parents=True, exist_ok=True)
    exec_start = exec_start or f"/usr/bin/python3 {rig['clone']}/{_SCRIPT_OF[unit]}"
    props = {
        "LoadState": "loaded",
        "ActiveState": active,
        # systemctl's own rendering of an ExecStart= line.
        "ExecStart": f"{{ path=/usr/bin/python3 ; argv[]={exec_start} ; ignore_errors=no ; "
                     "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }",
        "ExecMainStartTimestamp": f"@{int(time.time()) - started_ago}",
        "ControlGroup": f"/system.slice/{unit}",
    }
    for prop, value in props.items():
        (d / prop).write_text(value + "\n", encoding="utf-8")
    _set_procs(rig, unit, procs)


def _set_procs(rig: dict, unit: str, n: int) -> None:
    cg = rig["cgroup"] / "system.slice" / unit
    cg.mkdir(parents=True, exist_ok=True)
    (cg / "cgroup.procs").write_text("".join(f"{2000 + i}\n" for i in range(n)), encoding="utf-8")


def _record(rig: dict, unit: str) -> str | None:
    p = rig["records"] / unit
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _set_record(rig: dict, unit: str, sha: str) -> None:
    rig["records"].mkdir(parents=True, exist_ok=True)
    (rig["records"] / unit).write_text(sha, encoding="utf-8")


def _both_installed_and_current(rig: dict) -> None:
    """Steady state: both daemons installed, running since long ago, and
    recorded on the tree the clone is on."""
    for unit in (_SCRAPER, _AGENT):
        _install(rig, unit)
        _set_record(rig, unit, rig["base_sha"])


def _advance_origin(rig: dict, *paths: str) -> str:
    """Commit a change to each path on origin/main (the clone is then behind)."""
    seed = rig["seed"]
    n = len(_git(seed, "rev-list", "HEAD").splitlines())
    for rel in paths or ("src/cofounder_agent/poindexter/services/foo.py",):
        p = seed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# change {n + 1}\n", encoding="utf-8")
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
            "FAKE_UNITS": str(rig["units"]),
            "SYNC_CGROUP_ROOT": str(rig["cgroup"]),
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


def _daemon_restarts(rig: dict) -> list[str]:
    return [e.split()[-1] for e in _events(rig)
            if e.startswith("systemctl restart ") and e.split()[-1] in (_SCRAPER, _AGENT)]


def _status(rig: dict) -> dict:
    return json.loads(
        (rig["home"] / ".poindexter" / "deploy-checkout-sync.status.json").read_text(encoding="utf-8"))


def _marker(rig: dict) -> str:
    return (rig["home"] / ".poindexter" / "deploy-last-restarted-sha").read_text(encoding="utf-8").strip()


class TestRestartOnChange:
    @pytest.mark.parametrize(("path", "unit"), [
        ("scripts/gpu-scraper.py", _SCRAPER),
        ("src/cofounder_agent/poindexter/brain/bootstrap.py", _SCRAPER),
        ("src/cofounder_agent/poindexter/__init__.py", _SCRAPER),
        ("src/cofounder_agent/poindexter/brain/__init__.py", _SCRAPER),
        ("scripts/recovery-agent.py", _AGENT),
    ])
    def test_a_file_the_daemon_loads_at_start_restarts_it_and_no_other(self, tmp_path, path, unit):
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        new = _advance_origin(rig, path)
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _daemon_restarts(rig) == [unit]
        assert _record(rig, _SCRAPER) == new and _record(rig, _AGENT) == new, (
            "the restarted unit is on HEAD, and the other one's files did not change"
        )
        st = _status(rig)
        assert st["result"] == "deployed"
        assert unit in st["restarted"]
        assert f"restarted {unit} onto {new[:9]} ({path} changed)" in proc.stdout

    def test_other_changes_restart_no_daemon_and_advance_the_records(self, tmp_path):
        """Other brain modules are not in the scraper's process: only the ones it
        imports at start are."""
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        new = _advance_origin(rig, "src/cofounder_agent/poindexter/brain/health_probes.py",
                              "src/cofounder_agent/poindexter/services/foo.py")
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _daemon_restarts(rig) == []
        assert _record(rig, _SCRAPER) == new and _record(rig, _AGENT) == new

    def test_units_that_are_not_installed_are_skipped_silently(self, tmp_path):
        """Consumer hosts never installed these units: nothing to restart, no
        record, nothing in the status."""
        rig = _build_rig(tmp_path)  # no FAKE_UNITS dirs: every LoadState is FAKE_LOADSTATE
        new = _advance_origin(rig, "scripts/gpu-scraper.py", "scripts/recovery-agent.py")
        proc = _run_sync(rig, FAKE_LOADSTATE="not-found")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _daemon_restarts(rig) == []
        assert not rig["records"].exists() or not any(rig["records"].iterdir())
        st = _status(rig)
        assert st["result"] == "deployed" and _marker(rig) == new
        assert "host daemon" not in st["detail"]

    def test_status_names_the_tree_each_daemon_runs(self, tmp_path):
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        new = _advance_origin(rig, "scripts/gpu-scraper.py")
        assert _run_sync(rig).returncode == 0
        out = _run_sync(rig, "--status").stdout
        assert f"host daemon {_SCRAPER} runs: {new[:9]}" in out
        assert f"host daemon {_AGENT} runs: {new[:9]}" in out


class TestFailSoft:
    def test_a_failed_restart_amends_the_detail_and_never_withholds_the_marker(self, tmp_path):
        """No passwordless sudo on this host: a withheld marker would error every
        pass, so the pass is recorded and the miss rides in the detail."""
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        new = _advance_origin(rig, "scripts/gpu-scraper.py")
        proc = _run_sync(rig, FAKE_RESTART_EXIT="1")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        st = _status(rig)
        assert st["result"] == "deployed"
        assert _marker(rig) == new, "a missed daemon restart must not withhold the deploy marker"
        assert f"could not restart {_SCRAPER}" in st["detail"]
        assert _SCRAPER not in st["restarted"]
        assert _record(rig, _SCRAPER) == rig["base_sha"], "the restart is still owed"

    def test_a_missed_restart_is_retried_on_the_next_no_change_pass(self, tmp_path):
        """The marker moved on, so the next pass is a no-change pass and the next
        merge's diff would not contain the change. The record keeps it owed."""
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        new = _advance_origin(rig, "scripts/gpu-scraper.py")
        _run_sync(rig, FAKE_RESTART_EXIT="1")
        _clear_events(rig)

        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        st = _status(rig)
        assert st["result"] == "synced-no-change"
        assert _daemon_restarts(rig) == [_SCRAPER]
        assert _SCRAPER in st["restarted"]
        assert "could not restart" not in st["detail"]
        assert _record(rig, _SCRAPER) == new
        _clear_events(rig)

        _run_sync(rig)
        assert _daemon_restarts(rig) == [], "done: the next pass owes nothing"

    def test_a_restart_held_for_a_mid_action_unit_happens_on_the_next_pass(self, tmp_path):
        """A restart kills the unit's whole cgroup. The recovery agent's compose
        reapply runs as a fire-and-forget child, and killing it mid-recreate
        can strand containers."""
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        _set_procs(rig, _AGENT, 2)
        new = _advance_origin(rig, "scripts/recovery-agent.py")
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _daemon_restarts(rig) == []
        assert f"{_AGENT} is mid-action (2 processes)" in _status(rig)["detail"]
        assert _record(rig, _AGENT) == rig["base_sha"]
        _clear_events(rig)

        _set_procs(rig, _AGENT, 1)
        _run_sync(rig)
        assert _daemon_restarts(rig) == [_AGENT]
        assert _record(rig, _AGENT) == new


class TestOncePerTree:
    def test_a_retrying_pass_does_not_restart_a_daemon_again(self, tmp_path):
        """Another step fails and withholds the marker, so the next pass retries
        the whole deploy. The daemon already runs HEAD and is left alone."""
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        # services/foo.py matches REBUILD_MAP (auto-embed), so FAKE_BUILD_EXIT
        # can fail the pass; gpu-scraper.py matches no rebuild entry.
        new = _advance_origin(rig, "scripts/gpu-scraper.py", "src/cofounder_agent/poindexter/services/foo.py")
        proc = _run_sync(rig, FAKE_BUILD_EXIT="1")
        assert proc.returncode == 1
        assert "image-rebuild" in _status(rig)["detail"]
        assert _daemon_restarts(rig) == [_SCRAPER], "fail-soft and independent: it ran despite the failure"
        assert _record(rig, _SCRAPER) == new
        _clear_events(rig)

        proc = _run_sync(rig, FAKE_BUILD_EXIT="1")
        assert proc.returncode == 1
        assert _daemon_restarts(rig) == []


class TestLeftAlone:
    @pytest.mark.parametrize("state", ["inactive", "failed"])
    def test_a_unit_that_is_not_running_is_not_started(self, tmp_path, state):
        """`systemctl restart` STARTS a stopped unit. Stopped, it loads the
        clone's code whenever it next starts, so nothing is owed."""
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        _install(rig, _SCRAPER, active=state)
        new = _advance_origin(rig, "scripts/gpu-scraper.py")
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _daemon_restarts(rig) == []
        assert f"{_SCRAPER} is {state}; not starting it" in proc.stdout
        assert _record(rig, _SCRAPER) == new

    def test_a_unit_started_after_the_clone_reached_head_is_not_restarted(self, tmp_path):
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        _install(rig, _SCRAPER, started_ago=-3600)  # the installer restarted it just now
        new = _advance_origin(rig, "scripts/gpu-scraper.py")
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _daemon_restarts(rig) == []
        assert f"{_SCRAPER} started after the clone reached" in proc.stdout
        assert _record(rig, _SCRAPER) == new

    def test_a_unit_that_runs_another_tree_is_reported_and_not_restarted(self, tmp_path):
        """A restart would reload the OTHER tree's code, so it proves nothing.
        The note stays until the installer re-renders the unit onto the clone."""
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        _install(rig, _SCRAPER, exec_start="/usr/bin/python3 /home/someone/glad-labs-website/scripts/gpu-scraper.py")
        _advance_origin(rig, "scripts/gpu-scraper.py")
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _daemon_restarts(rig) == []
        st = _status(rig)
        assert st["result"] == "deployed"
        assert f"{_SCRAPER} does not run from the deploy clone" in st["detail"]
        assert "install-deploy-sync.sh" in st["detail"]
        assert _record(rig, _SCRAPER) == rig["base_sha"]

        _run_sync(rig)  # no-change pass: still said
        assert f"{_SCRAPER} does not run from the deploy clone" in _status(rig)["detail"]

    def test_no_restart_leaves_the_daemons_alone(self, tmp_path):
        rig = _build_rig(tmp_path)
        _both_installed_and_current(rig)
        _advance_origin(rig, "scripts/gpu-scraper.py", "scripts/recovery-agent.py")
        proc = _run_sync(rig, "--no-restart")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _daemon_restarts(rig) == []
        assert _record(rig, _SCRAPER) == rig["base_sha"]


class TestFirstContact:
    def test_no_record_restarts_a_running_unit_once_then_records_it(self, tmp_path):
        """What the process runs is unknown, and unknown restarts, as the bounce
        does for a missing record. This is the first pass after this change: it
        brings the recovery agent onto the clone's current file."""
        rig = _build_rig(tmp_path)  # clone current, marker == HEAD: a no-change pass
        for unit in (_SCRAPER, _AGENT):
            _install(rig, unit)
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _status(rig)["result"] == "synced-no-change"
        assert sorted(_daemon_restarts(rig)) == [_SCRAPER, _AGENT]
        assert _record(rig, _SCRAPER) == rig["base_sha"] == _record(rig, _AGENT)
        # A missing record is the normal first-contact state, not an error: a
        # dry run on the operator host printed "No such file or directory" to
        # the unit's journal for each daemon before this was pinned.
        assert "No such file" not in proc.stderr, proc.stderr
        _clear_events(rig)

        _run_sync(rig)
        assert _daemon_restarts(rig) == []

    def test_no_record_skips_a_unit_that_started_after_the_clone_moved(self, tmp_path):
        rig = _build_rig(tmp_path)
        _install(rig, _SCRAPER, started_ago=-3600)
        proc = _run_sync(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _daemon_restarts(rig) == []
        assert _record(rig, _SCRAPER) == rig["base_sha"]


# --- the table itself ---------------------------------------------------------

def _host_daemon_map() -> dict[str, str]:
    """{path regex: unit}, parsed from the script like test_deploy_rebuild_map_coverage."""
    text = (_repo_root() / "scripts" / "linux" / "deploy-checkout-sync.sh").read_text(encoding="utf-8")
    block = text.split("declare -A HOST_DAEMON_MAP=(", 1)[1].split("\n)", 1)[0]
    out = {m.group(1): m.group(2) for m in re.finditer(r"^\s*\['([^']+)'\]=\"([^\"]+)\"", block, re.M)}
    assert out, "HOST_DAEMON_MAP parsed empty: the check would pass on nothing"
    return out


def _template_exec_script(unit: str) -> str:
    """The repo-relative script the unit template runs, from its ExecStart."""
    text = (_repo_root() / "infrastructure" / "systemd" / unit).read_text(encoding="utf-8")
    exec_start = next(ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("ExecStart="))
    script = exec_start.split()[-1]
    assert script.startswith(_TEMPLATE_CLONE + "/"), f"{unit} must run from the deploy clone: {exec_start}"
    return script[len(_TEMPLATE_CLONE) + 1:]


def _start_time_files(script: Path, home: Path) -> list[str]:
    """Every repo file in the daemon's process once it is up.

    Runs the script's module level (its main loop is behind __name__) in a clean
    interpreter with the environment the unit gives it: no DATABASE_URL, so the
    scraper resolves its DSN through poindexter.brain.bootstrap, exactly as on
    the host.
    """
    probe = (
        "import importlib.util, json, os, sys\n"
        "path, root = sys.argv[1], sys.argv[2]\n"
        "spec = importlib.util.spec_from_file_location('_host_daemon_probe', path)\n"
        "spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
        "files = {os.path.realpath(path)} | {os.path.realpath(m.__file__)\n"
        "         for m in list(sys.modules.values()) if getattr(m, '__file__', None)}\n"
        "print(json.dumps(sorted(os.path.relpath(f, root) for f in files if f.startswith(root + os.sep))))\n"
    )
    root = str(_repo_root().resolve())
    proc = subprocess.run(
        [sys.executable, "-c", probe, str(script), root],
        env={"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
        cwd=home, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"loading {script.name} failed: {proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestTheTable:
    def test_the_scraper_unit_template_runs_from_the_deploy_clone(self):
        text = (_repo_root() / "infrastructure" / "systemd" / _SCRAPER).read_text(encoding="utf-8")
        assert "User=poindexter" in text
        assert f"WorkingDirectory={_TEMPLATE_CLONE}\n" in text
        assert f"ExecStart=/usr/bin/python3 {_TEMPLATE_CLONE}/scripts/gpu-scraper.py\n" in text

    def test_every_pattern_matches_a_tracked_file(self):
        """A renamed file would leave an entry that can never fire."""
        root = _repo_root()
        tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, timeout=60)
        if tracked.returncode != 0:
            pytest.skip("not a git checkout")
        paths = tracked.stdout.splitlines()
        assert len(paths) > 100
        for pattern in _host_daemon_map():
            assert any(re.search(pattern, p) for p in paths), f"{pattern} matches no tracked file"

    def test_the_table_covers_every_repo_file_each_daemon_loads_at_start(self, tmp_path):
        """Derived, not listed: a hand-kept list agrees with whoever forgot the
        entry. The first draft of this table named bootstrap.py and missed the
        two package __init__ files Python runs to import it."""
        pytest.importorskip("asyncpg")
        pytest.importorskip("httpx")
        table = _host_daemon_map()
        units = sorted(set(table.values()))
        assert units == sorted([_SCRAPER, _AGENT])
        for unit in units:
            script = _template_exec_script(unit)
            loaded = _start_time_files(_repo_root() / script, tmp_path)
            assert script in loaded, f"the probe did not load {script}: {loaded}"
            patterns = [p for p, u in table.items() if u == unit]
            missing = [f for f in loaded if not any(re.search(p, f) for p in patterns)]
            assert not missing, f"{unit} loads {missing} at start, and HOST_DAEMON_MAP has no entry for them"
            if unit == _SCRAPER:
                # Floor: the probe really followed the DSN import.
                assert "src/cofounder_agent/poindexter/brain/bootstrap.py" in loaded, loaded
