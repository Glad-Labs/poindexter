"""Contract tests for ``run-session.sh``'s pre-flight checkout self-update.

The deploy gap (found 2026-08-15): the systemd session units run
``run-session.sh`` out of the operator's **working checkout**, and nothing
auto-updated that tree — ``poindexter-deploy-sync`` serves a *dedicated* deploy
clone and must never point at a working checkout (its step 4 is
``git reset --hard`` + ``git clean -fd``, and the working checkout holds
stashes and agent worktrees). So PR #3228's fetch retry merged but the
deployed wrapper kept running the old code, 3 commits behind, until a human
fast-forwarded it.

The fix is a guarded ``merge --ff-only`` pre-flight in the wrapper itself.
These tests drive the real script against **real git repos** (upstream bare +
working clone) because the whole point is git's merge semantics — what gets
merged, and above all what never does:

- ff-only, never ``reset --hard``: local WIP is structurally unreachable.
- Skip (don't abort) on: non-``main`` branch, dirty *tracked* files,
  divergence. A stale wrapper is the status quo, not a new failure mode.
- Untracked files do NOT block the sync (``status --porcelain -uno``) — a
  stray scratch file must not wedge self-deployment forever.
- ``OPS_CHECKOUT_SYNC=0`` opts out entirely.
- The four worktree sessions reuse the pre-flight's fetch (no double fetch)
  and still get a fresh worktree off the advanced ``origin/main``.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash + git",
)

_GIT_ID = ["-c", "user.email=ops@test", "-c", "user.name=ops-test"]


def _script() -> Path:
    root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "run-session.sh").exists()
    )
    return root / "scripts" / "linux" / "run-session.sh"


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *_GIT_ID, *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _commit_file(repo: Path, relpath: str, content: str, msg: str) -> None:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


class _World:
    """An upstream bare repo, an 'author' clone that advances it, and the
    operator's working clone that run-session.sh will (maybe) self-update."""

    def __init__(self, tmp_path: Path):
        self.upstream = tmp_path / "upstream.git"
        self.author = tmp_path / "author"
        self.work = tmp_path / "work"
        self.home = tmp_path / "home"
        self.bin = tmp_path / "bin"
        for d in (self.author, self.home, self.bin):
            d.mkdir(parents=True)

        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(self.upstream)],
            check=True, capture_output=True,
        )
        _git(self.author, "init", "-q", "-b", "main")
        _git(self.author, "remote", "add", "origin", str(self.upstream))
        # run-session.sh cd's into src/cofounder_agent before `poetry run`, so
        # the tree must carry that directory; the fake poetry below no-ops.
        _commit_file(self.author, "src/cofounder_agent/README.md", "pkg\n", "seed")
        _git(self.author, "push", "-q", "origin", "main")
        subprocess.run(
            ["git", "clone", "-q", str(self.upstream), str(self.work)],
            check=True, capture_output=True,
        )

        fake_poetry = self.bin / "poetry"
        fake_poetry.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_poetry.chmod(0o755)

    def advance_upstream(self, n: int = 1) -> str:
        for i in range(n):
            _commit_file(self.author, "scripts/note.txt", f"rev {i}\n", f"upstream {i}")
        _git(self.author, "push", "-q", "origin", "main")
        return _git(self.author, "rev-parse", "origin/main")

    def head(self) -> str:
        return _git(self.work, "rev-parse", "HEAD")

    def run(self, session: str = "alert-triage", **env_extra: str) -> _Result:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "POINDEXTER_REPO": str(self.work),
            "OPS_GIT_FETCH_RETRY_SECONDS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            # These tests pin the checkout-sync contract; the boot readiness
            # gate (stack#3033) has its own suite in
            # test_run_session_ready_gate.py.
            "OPS_READY_GATE": "0",
            **env_extra,
        }
        proc = subprocess.run(
            ["bash", str(_script()), session],
            env=env, capture_output=True, text=True, timeout=120,
        )
        logs = sorted((self.home / ".poindexter" / "logs" / "claude-sessions").glob("*.log"))
        log = logs[0].read_text(encoding="utf-8") if logs else ""
        return _Result(proc, log)


class _Result:
    def __init__(self, proc: subprocess.CompletedProcess, log: str):
        self.proc, self.log = proc, log


def test_behind_and_clean_fast_forwards_to_origin_main(tmp_path):
    """The deploy gap itself: a merged change reaches the checkout unattended."""
    w = _World(tmp_path)
    target = w.advance_upstream(3)
    res = w.run()
    assert res.proc.returncode == 0, res.log
    assert w.head() == target, f"working checkout should be at origin/main:\n{res.log}"
    assert "fast-forwarded" in res.log
    assert "3 commit(s)" in res.log


def test_dirty_tracked_file_skips_the_merge(tmp_path):
    """Never merge onto WIP — the invariant that rules out reset --hard here."""
    w = _World(tmp_path)
    before = w.head()
    w.advance_upstream()
    (w.work / "src" / "cofounder_agent" / "README.md").write_text("edited\n", encoding="utf-8")
    res = w.run()
    assert res.proc.returncode == 0
    assert w.head() == before, "dirty checkout must not be advanced"
    assert "uncommitted tracked changes" in res.log
    # and the WIP itself must survive byte-for-byte
    assert (w.work / "src" / "cofounder_agent" / "README.md").read_text() == "edited\n"


def test_untracked_files_do_not_block_the_sync(tmp_path):
    """-uno: a stray scratch file must not wedge self-deployment forever."""
    w = _World(tmp_path)
    target = w.advance_upstream()
    (w.work / "scratch.tmp").write_text("wip notes\n", encoding="utf-8")
    res = w.run()
    assert w.head() == target, f"untracked file wrongly blocked the sync:\n{res.log}"
    assert (w.work / "scratch.tmp").read_text() == "wip notes\n"


def test_non_main_branch_is_left_alone(tmp_path):
    w = _World(tmp_path)
    _git(w.work, "checkout", "-q", "-b", "feature/wip")
    before = w.head()
    w.advance_upstream()
    res = w.run()
    assert res.proc.returncode == 0
    assert w.head() == before
    assert "not main" in res.log


def test_diverged_checkout_warns_and_still_runs(tmp_path):
    """Divergence is an anomaly to surface, not a reason to kill the session."""
    w = _World(tmp_path)
    _commit_file(w.work, "local-only.txt", "unpushed\n", "local divergence")
    local = w.head()
    w.advance_upstream()
    res = w.run()
    assert res.proc.returncode == 0, "session must still run on the stale wrapper"
    assert w.head() == local, "diverged checkout must not be touched"
    assert "cannot fast-forward" in res.log
    assert "STALE" in res.log


def test_opt_out_knob_disables_the_sync(tmp_path):
    w = _World(tmp_path)
    before = w.head()
    w.advance_upstream()
    res = w.run(OPS_CHECKOUT_SYNC="0")
    assert res.proc.returncode == 0
    assert w.head() == before
    assert "checkout sync disabled" in res.log


def test_worktree_session_syncs_then_builds_worktree_off_new_main(tmp_path):
    """The committing sessions get both halves: current wrapper checkout AND a
    fresh worktree off the advanced origin/main — with the worktree cleaned up."""
    w = _World(tmp_path)
    target = w.advance_upstream(2)
    res = w.run(session="test-health")
    assert res.proc.returncode == 0, res.log
    assert w.head() == target
    wt_root = w.home / ".poindexter" / "worktrees"
    leftovers = [p for p in wt_root.glob("*") if p.is_dir()]
    assert leftovers == [], f"worktree should be removed on cleanup: {leftovers}"


def test_already_current_checkout_noop_still_leaves_a_breadcrumb(tmp_path):
    """No merge happens, but the log must still prove the pre-flight ran —
    this script's own 2026-08-14 lesson: silence is indistinguishable from
    breakage."""
    w = _World(tmp_path)
    before = w.head()
    res = w.run()
    assert res.proc.returncode == 0
    assert w.head() == before
    assert "checkout sync: already at origin/main" in res.log
    assert "fast-forwarded" not in res.log
    assert "WARNING" not in res.log
