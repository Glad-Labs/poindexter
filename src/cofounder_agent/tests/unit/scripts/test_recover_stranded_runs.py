"""Guard for the stranded-run recovery in runner-healthcheck.

Unsetting ``CI_RUNNER`` only fails over runs created after the flip; a run's
``vars`` are fixed at creation, so jobs already queued for the self-hosted
labels wait forever. PR #4102 (2026-09-27) sat Queued 15+ hours that way.
These tests pin the four things that recovery gets wrong easily:

- which runs count as stranded (a queued SELF-HOSTED job, including inside an
  ``in_progress`` run — not hosted backlog, not running jobs);
- force-cancel, not cancel, then wait for ``completed`` before re-running
  (GitHub refuses to re-run an active run);
- never re-running a run a later push superseded: the seam workflows group
  on ``workflow + ref`` with ``cancel-in-progress`` for PRs, so that re-run
  would cancel the PR's current head run;
- the attempt cap, so a re-run that lands on self-hosted again cannot loop.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

_SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "ci" / "recover_stranded_runs.py"

SELF = ["self-hosted", "linux", "x64"]
HOSTED = ["ubuntu-latest"]


def _load():
    spec = importlib.util.spec_from_file_location(_SCRIPT.stem, _SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # Registered before exec: @dataclass resolves annotations via sys.modules.
    sys.modules[spec.name] = mod  # type: ignore[union-attr]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


mod = _load()


class FakeGh:
    """Records calls and answers GETs from a small in-memory repo state."""

    def __init__(
        self,
        runs: dict[str, list[dict[str, Any]]],
        jobs: dict[int, list[dict[str, Any]]],
        *,
        newest: dict[tuple[int, str], int] | None = None,
        fail: set[str] | None = None,
    ) -> None:
        self.runs = runs
        self.jobs = jobs
        self.newest = newest or {}
        self.fail = fail or set()
        self.calls: list[tuple[str, str]] = []

    def __call__(self, path: str, *, method: str = "GET", paginate: bool = False) -> Any:
        self.calls.append((method, path))
        for frag in self.fail:
            if frag in path:
                raise RuntimeError(f"boom {path}")
        if method == "POST":
            return None
        if "/actions/runs?status=" in path:
            status = path.split("status=")[1].split("&")[0]
            return [{"workflow_runs": self.runs.get(status, [])}]
        if "/jobs" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            return [{"jobs": self.jobs.get(run_id, [])}]
        if "/actions/workflows/" in path:
            workflow_id = int(path.split("/workflows/")[1].split("/")[0])
            branch = unquote(path.split("branch=")[1].split("&")[0])
            newest = self.newest.get((workflow_id, branch))
            return {"workflow_runs": [{"id": newest}] if newest else []}
        return {"status": "completed"}

    def posts(self) -> list[str]:
        return [p for m, p in self.calls if m == "POST"]


def _run(run_id: int, attempt: int = 1, branch: str | None = "feat") -> dict[str, Any]:
    return {
        "id": run_id,
        "name": f"wf{run_id}",
        "run_attempt": attempt,
        "html_url": f"https://x/{run_id}",
        "workflow_id": 10,
        "head_branch": branch,
    }


def _job(name: str, status: str, labels: list[str]) -> dict[str, Any]:
    return {"name": name, "status": status, "labels": labels}


class TestIsStrandedJob:
    def test_queued_self_hosted(self) -> None:
        assert mod.is_stranded_job(_job("t", "queued", SELF))

    def test_label_match_is_case_insensitive(self) -> None:
        assert mod.is_stranded_job(_job("t", "queued", ["Self-Hosted", "Linux"]))

    def test_queued_hosted_is_github_backlog(self) -> None:
        assert not mod.is_stranded_job(_job("t", "queued", HOSTED))

    def test_running_self_hosted_is_alive(self) -> None:
        assert not mod.is_stranded_job(_job("t", "in_progress", SELF))


class TestFindStrandedRuns:
    def test_finds_queued_job_inside_in_progress_run(self) -> None:
        # The common shape: a hosted `changes` job finished, the self-hosted
        # job behind it is queued, so the RUN reads in_progress, not queued.
        gh = FakeGh(
            runs={"in_progress": [_run(1)]},
            jobs={1: [_job("changes", "completed", HOSTED), _job("test", "queued", SELF)]},
        )
        found = mod.find_stranded_runs("o/r", gh=gh)
        assert [r.run_id for r in found] == [1]
        assert found[0].stranded_jobs == ["test"]

    def test_ignores_hosted_backlog_and_excludes_own_run(self) -> None:
        gh = FakeGh(
            runs={"queued": [_run(1), _run(2)], "in_progress": [_run(3)]},
            jobs={
                1: [_job("a", "queued", HOSTED)],
                2: [_job("b", "queued", SELF)],
                3: [_job("healthcheck", "queued", SELF)],
            },
        )
        found = mod.find_stranded_runs("o/r", gh=gh, exclude_run_id=3)
        assert [r.run_id for r in found] == [2]

    def test_reads_jobs_of_the_current_attempt(self) -> None:
        gh = FakeGh(runs={"queued": [_run(7, attempt=2)]}, jobs={7: []})
        mod.find_stranded_runs("o/r", gh=gh)
        assert ("GET", "/repos/o/r/actions/runs/7/attempts/2/jobs?per_page=100") in gh.calls

    def test_run_listed_under_both_statuses_counted_once(self) -> None:
        gh = FakeGh(
            runs={"queued": [_run(5)], "in_progress": [_run(5)]},
            jobs={5: [_job("t", "queued", SELF)]},
        )
        assert len(mod.find_stranded_runs("o/r", gh=gh)) == 1


class TestSuperseded:
    def _find(self, newest: dict[tuple[int, str], int], branch: str | None = "feat"):
        gh = FakeGh(
            runs={"queued": [_run(5, branch=branch)]},
            jobs={5: [_job("t", "queued", SELF)]},
            newest=newest,
        )
        return gh, mod.find_stranded_runs("o/r", gh=gh)

    def test_newer_run_on_same_workflow_and_branch_supersedes(self) -> None:
        _, found = self._find({(10, "feat"): 9})
        assert found[0].superseded_by == 9

    def test_newest_run_is_current(self) -> None:
        _, found = self._find({(10, "feat"): 5})
        assert found[0].superseded_by is None

    def test_no_branch_is_treated_as_current_without_a_query(self) -> None:
        gh, found = self._find({}, branch=None)
        assert found[0].superseded_by is None
        assert not any("/actions/workflows/" in p for _, p in gh.calls)

    def test_branch_is_url_encoded(self) -> None:
        gh, _ = self._find({}, branch="claude/fix+x")
        paths = [p for _, p in gh.calls if "/actions/workflows/" in p]
        assert paths == ["/repos/o/r/actions/workflows/10/runs?branch=claude%2Ffix%2Bx&per_page=1"]

    def test_hosted_backlog_costs_no_superseded_query(self) -> None:
        gh = FakeGh(runs={"queued": [_run(1)]}, jobs={1: [_job("a", "queued", HOSTED)]})
        mod.find_stranded_runs("o/r", gh=gh)
        assert not any("/actions/workflows/" in p for _, p in gh.calls)


def _stranded(run_id: int, attempt: int = 1, superseded_by: int | None = None):
    return mod.StrandedRun(run_id, f"wf{run_id}", attempt, "u", ["t"], superseded_by)


class TestRecover:
    def test_force_cancel_then_rerun_failed_jobs(self) -> None:
        gh = FakeGh(runs={}, jobs={})
        out = mod.recover(
            "o/r", [_stranded(1)], dry_run=False, max_attempt=3, gh=gh, wait=lambda *a, **k: True
        )
        # force-cancel, NOT cancel: a plain cancel leaves `if: always()`
        # gates queued and the run never completes.
        assert gh.posts() == [
            "/repos/o/r/actions/runs/1/force-cancel",
            "/repos/o/r/actions/runs/1/rerun-failed-jobs",
        ]
        assert [o.action for o in out] == ["recovered"]

    def test_all_cancels_precede_all_reruns(self) -> None:
        gh = FakeGh(runs={}, jobs={})
        mod.recover(
            "o/r",
            [_stranded(1), _stranded(2)],
            dry_run=False,
            max_attempt=3,
            gh=gh,
            wait=lambda *a, **k: True,
        )
        assert [p.rsplit("/", 1)[1] for p in gh.posts()] == [
            "force-cancel",
            "force-cancel",
            "rerun-failed-jobs",
            "rerun-failed-jobs",
        ]

    def test_dry_run_writes_nothing(self) -> None:
        gh = FakeGh(runs={}, jobs={})
        out = mod.recover("o/r", [_stranded(1)], dry_run=True, max_attempt=3, gh=gh)
        assert gh.posts() == []
        assert [o.action for o in out] == ["would-recover"]

    def test_superseded_run_is_cancelled_but_not_rerun(self) -> None:
        # Re-running it would rejoin the `workflow + ref` concurrency group
        # and, with cancel-in-progress, cancel the PR's current head run.
        gh = FakeGh(runs={}, jobs={})
        out = mod.recover(
            "o/r",
            [_stranded(1, superseded_by=4)],
            dry_run=False,
            max_attempt=3,
            gh=gh,
            wait=lambda *a, **k: True,
        )
        assert gh.posts() == ["/repos/o/r/actions/runs/1/force-cancel"]
        assert (out[0].action, out[0].warn) == ("cancelled", False)
        assert "#4" in out[0].detail

    def test_attempt_cap_cancels_but_does_not_rerun(self) -> None:
        # Still cancelled so it stops holding its concurrency group; not
        # re-run, because a re-run that strands again would only loop.
        gh = FakeGh(runs={}, jobs={})
        out = mod.recover(
            "o/r",
            [_stranded(1, attempt=3)],
            dry_run=False,
            max_attempt=3,
            gh=gh,
            wait=lambda *a, **k: True,
        )
        assert gh.posts() == ["/repos/o/r/actions/runs/1/force-cancel"]
        assert (out[0].action, out[0].warn) == ("cancelled", True)

    def test_dry_run_reports_the_cancel_only_plan(self) -> None:
        gh = FakeGh(runs={}, jobs={})
        out = mod.recover(
            "o/r", [_stranded(1, superseded_by=4)], dry_run=True, max_attempt=3, gh=gh
        )
        assert gh.posts() == []
        assert out[0].action == "would-cancel"

    def test_waits_share_one_budget(self) -> None:
        # Per-run timeouts would stack past the job's timeout-minutes; every
        # cancel was already issued, so the runs share one deadline.
        t = [0.0]
        budgets: list[float] = []

        def wait(repo: str, run_id: int, *, gh: Any, timeout_s: float) -> bool:
            budgets.append(timeout_s)
            t[0] += 100
            return True

        mod.recover(
            "o/r",
            [_stranded(1), _stranded(2), _stranded(3)],
            dry_run=False,
            max_attempt=3,
            gh=FakeGh(runs={}, jobs={}),
            wait=wait,
            wait_budget_s=180,
            clock=lambda: t[0],
        )
        assert budgets == [180, 80, 0]

    def test_no_rerun_if_run_never_completes(self) -> None:
        gh = FakeGh(runs={}, jobs={})
        out = mod.recover(
            "o/r", [_stranded(1)], dry_run=False, max_attempt=3, gh=gh, wait=lambda *a, **k: False
        )
        assert gh.posts() == ["/repos/o/r/actions/runs/1/force-cancel"]
        assert out[0].action == "failed"

    def test_one_failure_does_not_block_the_others(self) -> None:
        gh = FakeGh(runs={}, jobs={}, fail={"/runs/1/force-cancel"})
        out = mod.recover(
            "o/r",
            [_stranded(1), _stranded(2)],
            dry_run=False,
            max_attempt=3,
            gh=gh,
            wait=lambda *a, **k: True,
        )
        assert {o.run.run_id: o.action for o in out} == {1: "failed", 2: "recovered"}


class TestWaitCompleted:
    def test_times_out(self) -> None:
        t = [0.0]

        def clock() -> float:
            return t[0]

        def sleep(s: float) -> None:
            t[0] += s

        ok = mod.wait_completed(
            "o/r",
            1,
            gh=lambda *a, **k: {"status": "in_progress"},
            timeout_s=10,
            poll_s=4,
            sleep=sleep,
            clock=clock,
        )
        assert ok is False

    def test_returns_when_completed(self) -> None:
        states = iter([{"status": "in_progress"}, {"status": "completed"}])
        ok = mod.wait_completed(
            "o/r", 1, gh=lambda *a, **k: next(states), timeout_s=60, poll_s=1, sleep=lambda s: None
        )
        assert ok is True


class TestMain:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
        monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
        return tmp_path

    def test_exit_1_when_a_recovery_fails(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(
            mod, "find_stranded_runs", lambda repo, exclude_run_id=None: [_stranded(1)]
        )
        monkeypatch.setattr(
            mod, "recover", lambda repo, runs, **kw: [mod.Outcome(runs[0], "failed", "x")]
        )
        assert mod.main(["--repo", "o/r"]) == 1
        assert "recovered=0\ncancelled=0\n" in (tmp_path / "out").read_text()

    def test_counts_and_warnings(self, monkeypatch, tmp_path, capsys) -> None:
        runs = [_stranded(1), _stranded(2, superseded_by=3), _stranded(4, attempt=3)]
        monkeypatch.setattr(mod, "find_stranded_runs", lambda repo, exclude_run_id=None: runs)
        monkeypatch.setattr(
            mod,
            "recover",
            lambda repo, rs, **kw: [
                mod.Outcome(rs[0], "recovered"),
                mod.Outcome(rs[1], "cancelled", "superseded"),
                mod.Outcome(rs[2], "cancelled", "attempt cap", warn=True),
            ],
        )
        assert mod.main(["--repo", "o/r"]) == 0
        assert "recovered=1\ncancelled=2\n" in (tmp_path / "out").read_text()
        warnings = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::warning")]
        assert len(warnings) == 1 and "run 4" in warnings[0]

    def test_none_found_is_clean(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(mod, "find_stranded_runs", lambda repo, exclude_run_id=None: [])
        assert mod.main(["--repo", "o/r", "--dry-run"]) == 0
        assert "none found" in (tmp_path / "summary").read_text()
