"""``deploy-sync-launcher.sh`` runs MERGED driver code and can't be bricked by it.

Glad-Labs/poindexter#4172. ``poindexter-deploy-sync.service`` ran
``deploy-checkout-sync.sh`` out of the operator's working checkout "so a broken
merge can't brick the syncer that would fix it". Nothing kept that checkout
current: ``run-session.sh``'s ff-only pre-flight skipped a dirty tree 34 runs in
a row, the checkout sat 148 commits behind, and four merged driver fixes
(#3984, #4001, #4085, #4144) never ran.

The launcher runs the deploy clone's committed driver every fire and keeps the
last copy that completed a clean pass. Contract, tested against the real
launcher running the real driver in a throwaway origin + clone, with recorder
fakes on PATH (rig from ``test_deploy_checkout_sync_retry``):

- merged == last-known-good: one run, labelled ``merged``;
- a merged driver that differs is promoted after a clean pass, and it is the
  bytes that RAN that are kept, not whatever the pass reset the clone onto;
- a merged driver that fails ``bash -n``, is missing, crashes or hangs before
  moving the clone makes the last-known-good copy run in the same fire, and
  that copy advances the clone onto the fix, which is promoted next fire;
- a deferral, a failure after the clone moved, and an unreachable origin do NOT
  fall back;
- every fallback is visible: status file, heartbeat and log say which copy ran.
"""

from __future__ import annotations

import fcntl
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.scripts.test_deploy_checkout_sync_retry import (
    _advance_origin,
    _build_rig,
    _clear_events,
    _git,
    _repo_root,
    _restarts,
    _status,
)

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None or shutil.which("timeout") is None,
    reason="needs bash + git + timeout",
)

DRIVER_REL = "scripts/linux/deploy-checkout-sync.sh"
LAUNCHER_REL = "scripts/linux/deploy-sync-launcher.sh"

# The retry rig's docker fake (StartedAt per container) plus an answer for the
# busy probes' `count(*)`, so FAKE_BUSY_COUNT=1 makes the pass defer.
_FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$EVENTS_FILE"
case "${1:-} ${2:-}" in
  "container inspect")
    if [[ "$*" == *"-f"* ]]; then
      c="${@: -1}"
      if [ -n "${STARTED_DIR:-}" ] && [ -f "$STARTED_DIR/$c" ]; then cat "$STARTED_DIR/$c"
      else echo "${FAKE_STARTED_AT:-2020-01-01T00:00:00.000000000Z}"; fi
    fi
    exit 0 ;;
esac
if [[ "${1:-}" == exec ]]; then echo "${FAKE_BUSY_COUNT:-0}"; fi
exit 0
"""


def _driver_source() -> str:
    return (_repo_root() / DRIVER_REL).read_text(encoding="utf-8")


def _older_driver() -> str:
    """A valid driver that is not byte-identical to the merged one."""
    return _driver_source() + "\n# an older copy\n"


def _broken(kind: str) -> str:
    src = _driver_source()
    anchor = "set -uo pipefail\n"
    assert anchor in src, "the driver's `set -uo pipefail` line moved; update this test"
    if kind == "syntax error":
        return src.replace(anchor, anchor + "if then fi\n", 1)
    if kind == "early exit":
        return src.replace(anchor, anchor + "exit 3\n", 1)
    if kind == "unbound variable":
        return src.replace(anchor, anchor + 'echo "$NOT_SET_ANYWHERE_4172"\n', 1)
    if kind == "hang":
        return src.replace(anchor, anchor + "sleep 300\n", 1)
    raise AssertionError(kind)


def _rig(tmp_path: Path) -> dict:
    """The retry rig, with the real driver and launcher committed into the tree
    the clone deploys, the clone on that commit, and the launcher installed."""
    rig = _build_rig(tmp_path)
    for name, body in (("docker", _FAKE_DOCKER), ("curl", "#!/usr/bin/env bash\necho '[]'\n")):
        f = rig["bin"] / name
        f.write_text(body, encoding="utf-8")
        f.chmod(0o755)
    launcher_src = (_repo_root() / LAUNCHER_REL).read_text(encoding="utf-8")
    sha = _advance_origin(rig, {DRIVER_REL: _driver_source(), LAUNCHER_REL: launcher_src})
    _put_clone_on(rig, sha)
    state = rig["home"] / ".poindexter" / "deploy-sync"
    state.mkdir(parents=True)
    launcher = state / "deploy-sync-launcher.sh"
    launcher.write_text(launcher_src, encoding="utf-8")
    launcher.chmod(0o755)
    rig.update(state=state, launcher=launcher, lkg=state / "last-known-good.sh",
               meta=state / "last-known-good.meta", deployed=sha)
    return rig


def _put_clone_on(rig: dict, sha: str) -> None:
    """A previous pass already reset the clone onto <sha> and deployed it."""
    _git(rig["clone"], "fetch", "-q", "origin")
    _git(rig["clone"], "reset", "-q", "--hard", sha)
    (rig["home"] / ".poindexter" / "deploy-last-restarted-sha").write_text(sha, encoding="utf-8")
    (rig["home"] / ".poindexter" / "deploy-last-bounced-sha").write_text(sha, encoding="utf-8")


def _set_lkg(rig: dict, text: str, *, commit: str = "0123456789abcdef") -> None:
    rig["lkg"].write_text(text, encoding="utf-8")
    rig["lkg"].chmod(0o755)
    rig["meta"].write_text(
        f"commit={commit}\nblob=unknown\nhow=seeded by the test\nat=2026-09-28T00:00:00Z\n",
        encoding="utf-8",
    )


def _launch(rig: dict, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(rig["launcher"]), *args],
        env={
            "PATH": f"{rig['bin']}:/usr/bin:/bin",
            "HOME": str(rig["home"]),
            "POINDEXTER_DEPLOY_ROOT": str(rig["clone"]),
            "EVENTS_FILE": str(rig["events"]),
            "STARTED_DIR": str(rig["started"]),
            "SYNC_APPLY_RETRY_SETTLE_SEC": "0",
            "SYNC_FLOW_WAIT_MAX_SEC": "0",
            **env_extra,
        },
        capture_output=True, text=True, timeout=180,
    )


def _driver_runs(proc: subprocess.CompletedProcess) -> list[str]:
    """One `Syncing …` line per driver run, naming the copy that ran."""
    return [ln for ln in proc.stdout.splitlines() if ln.startswith("[deploy-checkout-sync] Syncing ")]


def _clone_head(rig: dict) -> str:
    return _git(rig["clone"], "rev-parse", "HEAD")


def _heartbeat_sql(rig: dict) -> str:
    """Every recorded `docker …` call; the heartbeat INSERT spans several lines."""
    f = rig["events"]
    return f.read_text(encoding="utf-8") if f.exists() else ""


def _log(rig: dict) -> str:
    p = rig["home"] / ".poindexter" / "deploy-checkout-sync.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


class TestSteadyStateAndPromotion:
    def test_identical_copies_run_once_as_merged(self, tmp_path):
        rig = _rig(tmp_path)
        _set_lkg(rig, _driver_source())
        proc = _launch(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        runs = _driver_runs(proc)
        assert len(runs) == 1 and f"(driver: merged from {rig['deployed'][:9]})" in runs[0], runs
        status = _status(rig)
        assert (status["driver"], status["driverCommit"]) == ("merged", rig["deployed"])
        assert "promoted" not in proc.stdout, "nothing to promote when the copies are identical"
        assert "'driver', $$merged$$" in _heartbeat_sql(rig)

    def test_first_clean_pass_creates_the_last_known_good_copy(self, tmp_path):
        rig = _rig(tmp_path)
        assert not rig["lkg"].exists()
        proc = _launch(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert rig["lkg"].read_text(encoding="utf-8") == _driver_source()
        meta = rig["meta"].read_text(encoding="utf-8")
        assert f"commit={rig['deployed']}" in meta and "promoted after a clean pass" in meta
        assert "promoted the merged driver" in proc.stdout

    def test_a_changed_driver_replaces_the_old_copy_after_a_clean_pass(self, tmp_path):
        rig = _rig(tmp_path)
        _set_lkg(rig, _older_driver())
        proc = _launch(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert len(_driver_runs(proc)) == 1
        assert rig["lkg"].read_text(encoding="utf-8") == _driver_source()

    def test_the_bytes_that_ran_are_promoted_not_what_the_pass_reset_onto(self, tmp_path):
        """The pass resets the clone onto a NEWER driver. That one has not run
        yet, so it must not become last-known-good until its own clean pass."""
        rig = _rig(tmp_path)
        _set_lkg(rig, _older_driver())
        newer = _driver_source() + "\n# the next merged change\n"
        newer_sha = _advance_origin(rig, {DRIVER_REL: newer})
        proc = _launch(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _clone_head(rig) == newer_sha, "the pass reset the clone onto the newer commit"
        assert rig["lkg"].read_text(encoding="utf-8") == _driver_source(), "the copy that ran"
        proc = _launch(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert rig["lkg"].read_text(encoding="utf-8") == newer, "promoted on its own first clean pass"


class TestABrokenMergeCannotBrickTheSyncer:
    @pytest.mark.parametrize("kind", ["syntax error", "missing"])
    def test_an_unusable_merged_driver_runs_the_last_known_good_copy(self, tmp_path, kind):
        rig = _rig(tmp_path)
        _set_lkg(rig, _driver_source(), commit=rig["deployed"])
        if kind == "missing":
            (rig["seed"] / DRIVER_REL).unlink()
            _git(rig["seed"], "add", "-A")
            _git(rig["seed"], "commit", "-q", "-m", "lose the driver")
            _git(rig["seed"], "push", "-q", "origin", "main")
            bad = _git(rig["seed"], "rev-parse", "HEAD")
        else:
            bad = _advance_origin(rig, {DRIVER_REL: _broken(kind)})
        _put_clone_on(rig, bad)
        fixed = _driver_source() + "\n# fixed\n"
        fix = _advance_origin(rig, {DRIVER_REL: fixed})

        proc = _launch(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        runs = _driver_runs(proc)
        assert len(runs) == 1 and "(driver: last-known-good" in runs[0], runs
        assert _clone_head(rig) == fix, "the last-known-good copy advanced the clone onto the fix"
        status = _status(rig)
        assert status["result"] == "deployed" and status["driver"] == "last-known-good"
        assert "fallback:" in status["detail"]
        assert ("fails bash -n" if kind == "syntax error" else f"has no {DRIVER_REL}") in status["detail"]
        assert "'driver', $$last-known-good$$" in _heartbeat_sql(rig)
        assert "FALLBACK" in _log(rig)
        assert rig["lkg"].read_text(encoding="utf-8") == _driver_source(), "a fallback never promotes"

        # Next fire: the fix is the merged driver, runs as such, and is promoted.
        proc = _launch(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _status(rig)["driver"] == "merged"
        assert rig["lkg"].read_text(encoding="utf-8") == fixed

    @pytest.mark.parametrize("kind", ["early exit", "unbound variable"])
    def test_a_driver_that_dies_before_syncing_falls_back_in_the_same_fire(self, tmp_path, kind):
        rig = _rig(tmp_path)
        _set_lkg(rig, _driver_source(), commit=rig["deployed"])
        bad = _advance_origin(rig, {DRIVER_REL: _broken(kind)})
        _put_clone_on(rig, bad)
        fix = _advance_origin(rig, {DRIVER_REL: _driver_source() + "\n# fixed\n"})

        proc = _launch(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert [r.split("(driver: ")[1].split(" ")[0] for r in _driver_runs(proc)] == ["last-known-good"], (
            "the broken copy died before its first log line; only the fallback got that far"
        )
        assert _clone_head(rig) == fix
        status = _status(rig)
        assert status["driver"] == "last-known-good"
        assert f"did not reach origin/main (rc={3 if kind == 'early exit' else 1})" in status["detail"]
        assert len(_restarts(rig)) == 2, "the fallback pass deployed the fix like any pass"

    def test_a_driver_that_hangs_before_syncing_is_killed_and_replaced(self, tmp_path):
        rig = _rig(tmp_path)
        _set_lkg(rig, _driver_source(), commit=rig["deployed"])
        bad = _advance_origin(rig, {DRIVER_REL: _broken("hang")})
        _put_clone_on(rig, bad)
        fix = _advance_origin(rig, {DRIVER_REL: _driver_source() + "\n# fixed\n"})

        proc = _launch(rig, SYNC_DRIVER_TIMEOUT_SEC="6")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _clone_head(rig) == fix
        assert "timed out after 6s" in _status(rig)["detail"]

    def test_with_no_last_known_good_copy_a_broken_driver_is_reported_not_run(self, tmp_path):
        rig = _rig(tmp_path)
        bad = _advance_origin(rig, {DRIVER_REL: _broken("syntax error")})
        _put_clone_on(rig, bad)
        proc = _launch(rig)
        assert proc.returncode == 1
        assert _driver_runs(proc) == []
        assert "no last-known-good copy" in proc.stdout


class TestWhenNotToFallBack:
    def test_a_deferral_is_trusted(self, tmp_path):
        """A busy stack: the merged driver's busy guard is the newer one, and a
        fallback would let an older guard bounce the worker through a render."""
        rig = _rig(tmp_path)
        _set_lkg(rig, _older_driver())
        _advance_origin(rig)  # the clone is one commit behind
        before = _clone_head(rig)
        proc = _launch(rig, FAKE_BUSY_COUNT="1")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert len(_driver_runs(proc)) == 1, "no fallback run"
        assert _status(rig)["result"] == "deferred-active-flow"
        assert _clone_head(rig) == before
        assert rig["lkg"].read_text(encoding="utf-8") == _older_driver(), "not promoted either"
        assert "a deferral" in proc.stdout

    def test_a_failure_after_the_clone_moved_does_not_fall_back(self, tmp_path):
        """The clone is current, so a fix can still arrive: no fallback, and no
        promotion of a driver that did not finish cleanly."""
        rig = _rig(tmp_path)
        _set_lkg(rig, _older_driver())
        new = _advance_origin(rig)  # a backend change: REBUILD_MAP -> auto-embed
        proc = _launch(rig, FAKE_BUILD_EXIT="1")
        assert proc.returncode == 1
        assert len(_driver_runs(proc)) == 1
        assert _clone_head(rig) == new
        status = _status(rig)
        assert (status["result"], status["driver"]) == ("error", "merged")
        assert rig["lkg"].read_text(encoding="utf-8") == _older_driver()
        assert "failed after reaching origin/main" in proc.stdout

    def test_an_unreachable_origin_does_not_fall_back(self, tmp_path):
        """Neither copy could reach it, so a fallback run would only fail again."""
        rig = _rig(tmp_path)
        _set_lkg(rig, _older_driver())
        _git(rig["clone"], "remote", "set-url", "origin", str(tmp_path / "gone.git"))
        proc = _launch(rig)
        assert proc.returncode == 1
        assert len(_driver_runs(proc)) == 1
        assert _status(rig)["driver"] == "merged"
        assert "does not answer" in proc.stdout


class TestVisibilityAndHousekeeping:
    def test_a_direct_run_says_so(self, tmp_path):
        rig = _rig(tmp_path)
        proc = subprocess.run(
            ["bash", str(rig["clone"] / DRIVER_REL), "--no-flow-check"],
            env={"PATH": f"{rig['bin']}:/usr/bin:/bin", "HOME": str(rig["home"]),
                 "POINDEXTER_DEPLOY_ROOT": str(rig["clone"]), "EVENTS_FILE": str(rig["events"]),
                 "STARTED_DIR": str(rig["started"])},
            capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (_status(rig)["driver"], _status(rig)["driverCommit"]) == ("direct", "")

    def test_an_out_of_date_launcher_says_so_every_pass(self, tmp_path):
        rig = _rig(tmp_path)
        _set_lkg(rig, _driver_source())
        launcher = (_repo_root() / LAUNCHER_REL).read_text(encoding="utf-8")
        sha = _advance_origin(rig, {LAUNCHER_REL: launcher + "\n# a newer launcher\n"})
        _put_clone_on(rig, sha)
        proc = _launch(rig)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "launcher out of date" in _status(rig)["detail"]
        assert "OUT OF DATE" in _launch(rig, "--report").stdout

    def test_one_pass_at_a_time(self, tmp_path):
        rig = _rig(tmp_path)
        lock = rig["state"] / "launcher.lock"
        with open(lock, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            proc = _launch(rig)
        assert proc.returncode == 0
        assert _driver_runs(proc) == []
        assert "another deploy-sync pass" in proc.stdout

    def test_report_and_status_are_read_only_views(self, tmp_path):
        rig = _rig(tmp_path)
        _launch(rig)  # creates the last-known-good copy
        _clear_events(rig)
        report = _launch(rig, "--report")
        assert report.returncode == 0
        assert "identical to the merged driver" in report.stdout
        assert "promoted the merged driver" in report.stdout, "recent decisions come from the log"
        status = _launch(rig, "--status")
        assert status.returncode == 0
        assert "--- deploy driver ---" in status.stdout and "last-known-good" in status.stdout
        assert json.loads((rig["home"] / ".poindexter" / "deploy-checkout-sync.status.json")
                          .read_text(encoding="utf-8"))["driver"] == "merged"
        assert _driver_runs(report) == [] and _driver_runs(status) == []
        assert _restarts(rig) == []


class TestTheUnit:
    """The unit template that puts the launcher in charge (stack#4172)."""

    @staticmethod
    def _read(rel: str) -> str:
        return (_repo_root() / rel).read_text(encoding="utf-8")

    def test_it_execs_the_installed_launcher_outside_every_git_tree(self):
        unit = self._read("infrastructure/systemd/poindexter-deploy-sync.service")
        exec_start = re.search(r"^ExecStart=(\S+)", unit, re.M).group(1)
        assert exec_start.endswith("/.poindexter/deploy-sync/deploy-sync-launcher.sh"), exec_start
        # Not the working checkout (it only changes when pulled) and not the
        # deploy clone (a broken launcher merge would brick the syncer).
        assert "/glad-labs-website/" not in exec_start
        assert "/.poindexter/deploy/" not in exec_start

    def test_its_timeout_covers_a_fire_that_falls_back(self):
        """Two driver runs, each with its kill grace, plus the ls-remote check.
        Derived from the launcher, so a longer per-run budget fails here."""
        launcher = self._read(LAUNCHER_REL)
        per_run = int(re.search(r"SYNC_DRIVER_TIMEOUT_SEC:-(\d+)", launcher).group(1))
        grace = int(re.search(r"--kill-after=(\d+)", launcher).group(1))
        ls_remote = int(re.search(r"timeout (\d+) git -C \S+ ls-remote", launcher).group(1))
        unit = self._read("infrastructure/systemd/poindexter-deploy-sync.service")
        total = int(re.search(r"^TimeoutStartSec=(\d+)", unit, re.M).group(1))
        assert total >= 2 * (per_run + grace) + ls_remote, (total, per_run, grace, ls_remote)
