#!/usr/bin/env python3
# scan-floor-exempt: operational GitHub API client, not a source-tree lint
"""Recover workflow runs stranded on dead self-hosted runners.

``runner-healthcheck`` fails the ``vars.CI_RUNNER`` seam over to
``ubuntu-latest`` by unsetting the variable when no self-hosted runner is
online. That only helps runs created AFTER the flip: a run's ``vars`` context
is fixed when the run is created, so a job that was already queued with
``runs-on: [self-hosted, linux, x64]`` stays queued for a runner that is never
coming back.

That is not hypothetical. On 2026-09-27 both runners went offline while PR
#4102's runs were queued for them. The healthcheck unset ``CI_RUNNER`` on
schedule, and the PR's jobs sat "Queued" for 15+ hours anyway. Two things
made it worse than a plain queue:

- A plain cancel does not clear it. Gate jobs carry ``if: always()``, which
  re-evaluates true on cancel, so the cancelled run keeps a queued job and
  never completes. ``force-cancel`` is the endpoint that bypasses ``always()``.
- ``concurrency: cancel-in-progress`` workflows (benchmarks,
  cloudflare-workers, docker-build, security) then queue every NEW run behind
  the stuck one, so fresh pushes were blocked too, not just the old run.

The recovery: force-cancel every stranded run (nothing can pick it up, and it
may be holding its concurrency group), wait for it to reach ``completed``,
then re-run its failed/cancelled jobs. A re-run attempt reads the CURRENT
``vars``, so with ``CI_RUNNER`` unset it lands on ``ubuntu-latest``; jobs that
already succeeded on a hosted runner are not repeated.

A cancelled run is NOT re-run in two cases:

- **A newer run of the same workflow exists on the same branch.** Nearly every
  seam workflow groups on ``workflow + ref`` with ``cancel-in-progress`` for
  pull requests. Re-running a run that a later push superseded would rejoin
  that group and cancel the PR's CURRENT head run in favour of a stale commit.
  Where cancel-in-progress is off, it would still displace a newer run that is
  pending in the group.
- **It is already at ``--max-attempt``.** A run that strands again after being
  re-run is landing on self-hosted again (usually the dormant
  ``CI_RUNNER_DOCKER`` seam still being set), so another re-run would only
  loop. It is reported with a warning.

"Stranded" is deliberately narrow: a job whose status is ``queued`` AND whose
labels include ``self-hosted``. A run whose queued jobs are all hosted is
GitHub's own backlog and is left alone.

Only call this when no self-hosted runner is online and ``CI_RUNNER`` is
unset — the workflow enforces that. With a runner online the job would just
be picked up, and with ``CI_RUNNER`` still set the re-run would queue on the
same dead label again.

Pure stdlib + the ``gh`` CLI (present on every hosted runner and already how
the healthcheck talks to the API), so it needs no ``setup-python`` step.

Run::

    GH_TOKEN=... python scripts/ci/recover_stranded_runs.py --repo OWNER/NAME --dry-run
    GH_TOKEN=... python scripts/ci/recover_stranded_runs.py --repo OWNER/NAME

Exit 0 when every stranded run was handled (or there were none, or
``--dry-run``); 1 when any cancel or re-run failed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

SELF_HOSTED_LABEL = "self-hosted"

# A run with a job waiting for a runner is ``queued`` when nothing in it has
# started, but ``in_progress`` once an earlier job has run — e.g. a hosted
# `changes` job finished and the self-hosted `test` job behind it is queued.
# Querying ``queued`` alone would miss exactly the common shape.
RUN_STATUSES = ("queued", "in_progress")

GhFn = Callable[..., Any]


def gh_api(path: str, *, method: str = "GET", paginate: bool = False) -> Any:
    """Call ``gh api`` and return parsed JSON (``None`` for an empty body).

    With ``paginate`` the pages come back as a list (``--slurp``) so the
    caller can merge them; ``gh`` concatenates raw pages otherwise.
    """
    cmd = ["gh", "api", "-X", method, path]
    if paginate:
        cmd += ["--paginate", "--slurp"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh api {method} {path} failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:500]}"
        )
    body = proc.stdout.strip()
    return json.loads(body) if body else None


@dataclass
class StrandedRun:
    run_id: int
    name: str
    run_attempt: int
    html_url: str
    stranded_jobs: list[str] = field(default_factory=list)
    # A newer run of the same workflow on the same branch, when one exists.
    # Such a run is cancelled but never re-run (see the module docstring).
    superseded_by: int | None = None


def _merge_pages(pages: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(pages, dict):
        pages = [pages]
    out: list[dict[str, Any]] = []
    for page in pages or []:
        out.extend(page.get(key) or [])
    return out


def is_stranded_job(job: dict[str, Any]) -> bool:
    """A job waiting for a self-hosted runner.

    Labels are compared case-insensitively: GitHub normalises runner labels
    that way when matching, and the seam value is authored lowercase.
    """
    if job.get("status") != "queued":
        return False
    labels = {str(label).lower() for label in job.get("labels") or []}
    return SELF_HOSTED_LABEL in labels


def newest_run_id(repo: str, workflow_id: int, branch: str, *, gh: GhFn = gh_api) -> int | None:
    """Id of the newest run of ``workflow_id`` on ``branch``, any event.

    Any event, not just the stranded run's own: push and schedule runs on
    ``main`` share one ``workflow + ref`` concurrency group, so a newer run of
    either kind is what a re-run would collide with.
    """
    page = (
        gh(
            f"/repos/{repo}/actions/workflows/{workflow_id}/runs"
            f"?branch={quote(branch, safe='')}&per_page=1"
        )
        or {}
    )
    runs = page.get("workflow_runs") or []
    return int(runs[0]["id"]) if runs else None


def find_stranded_runs(
    repo: str, *, gh: GhFn = gh_api, exclude_run_id: int | None = None
) -> list[StrandedRun]:
    """Every active run holding at least one job queued for self-hosted."""
    seen: set[int] = set()
    stranded: list[StrandedRun] = []
    for status in RUN_STATUSES:
        runs = _merge_pages(
            gh(f"/repos/{repo}/actions/runs?status={status}&per_page=100", paginate=True),
            "workflow_runs",
        )
        for run in runs:
            run_id = int(run["id"])
            if run_id in seen or run_id == exclude_run_id:
                continue
            seen.add(run_id)
            attempt = int(run.get("run_attempt") or 1)
            jobs = _merge_pages(
                gh(
                    f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100",
                    paginate=True,
                ),
                "jobs",
            )
            names = [j.get("name", "?") for j in jobs if is_stranded_job(j)]
            if not names:
                continue
            # Run ids only grow, so a larger id on the same workflow + branch
            # is a later run. No branch (rare: e.g. a deleted ref) means no
            # way to tell, and the run is treated as current.
            superseded_by = None
            branch, workflow_id = run.get("head_branch"), run.get("workflow_id")
            if branch and workflow_id:
                newest = newest_run_id(repo, int(workflow_id), str(branch), gh=gh)
                if newest is not None and newest > run_id:
                    superseded_by = newest
            stranded.append(
                StrandedRun(
                    run_id=run_id,
                    name=str(run.get("name") or run.get("display_title") or "?"),
                    run_attempt=attempt,
                    html_url=str(run.get("html_url") or ""),
                    stranded_jobs=names,
                    superseded_by=superseded_by,
                )
            )
    return stranded


def wait_completed(
    repo: str,
    run_id: int,
    *,
    gh: GhFn = gh_api,
    timeout_s: float = 180.0,
    poll_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll until the run reports ``completed``. False on timeout."""
    deadline = clock() + timeout_s
    while True:
        run = gh(f"/repos/{repo}/actions/runs/{run_id}") or {}
        if run.get("status") == "completed":
            return True
        if clock() >= deadline:
            return False
        sleep(poll_s)


@dataclass
class Outcome:
    run: StrandedRun
    action: str  # would-recover | would-cancel | recovered | cancelled | failed
    detail: str = ""
    warn: bool = False  # surfaced as a ::warning:: annotation


def _no_rerun_reason(run: StrandedRun, max_attempt: int) -> tuple[str, bool] | None:
    """Why a stranded run is cancelled but not re-run, and whether to warn."""
    if run.superseded_by is not None:
        return f"superseded by run #{run.superseded_by}, so not re-run", False
    if run.run_attempt >= max_attempt:
        return (
            f"attempt {run.run_attempt} >= {max_attempt}, so not re-run: re-runs keep "
            "landing on self-hosted — check CI_RUNNER and CI_RUNNER_DOCKER",
            True,
        )
    return None


def recover(
    repo: str,
    runs: list[StrandedRun],
    *,
    dry_run: bool,
    max_attempt: int,
    gh: GhFn = gh_api,
    wait: Callable[..., bool] = wait_completed,
    wait_budget_s: float = 180.0,
    clock: Callable[[], float] = time.monotonic,
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    to_rerun: list[StrandedRun] = []

    # Pass 1: force-cancel everything first, so the waits below overlap
    # instead of serialising one run's shutdown after another's.
    for run in runs:
        no_rerun = _no_rerun_reason(run, max_attempt)
        if dry_run:
            if no_rerun:
                outcomes.append(Outcome(run, "would-cancel", no_rerun[0], warn=no_rerun[1]))
            else:
                outcomes.append(Outcome(run, "would-recover"))
            continue
        try:
            gh(f"/repos/{repo}/actions/runs/{run.run_id}/force-cancel", method="POST")
        except RuntimeError as exc:
            outcomes.append(Outcome(run, "failed", f"force-cancel: {exc}", warn=True))
            continue
        if no_rerun:
            outcomes.append(Outcome(run, "cancelled", no_rerun[0], warn=no_rerun[1]))
        else:
            to_rerun.append(run)

    # Pass 2: a run can only be re-run once it has completed. One budget for
    # all of them, not one each: every cancel was issued above, so the runs
    # have all been shutting down since, and per-run timeouts would stack
    # past the job's timeout-minutes.
    deadline = clock() + wait_budget_s
    for run in to_rerun:
        try:
            if not wait(repo, run.run_id, gh=gh, timeout_s=max(0.0, deadline - clock())):
                outcomes.append(
                    Outcome(
                        run,
                        "failed",
                        "force-cancelled but not completed in time; re-run it by hand "
                        "once it completes (it is no longer queued, so the next "
                        "healthcheck will not find it)",
                        warn=True,
                    )
                )
                continue
            # rerun-failed-jobs re-runs failed AND cancelled jobs (plus their
            # dependents) and keeps the hosted jobs that already passed.
            gh(f"/repos/{repo}/actions/runs/{run.run_id}/rerun-failed-jobs", method="POST")
        except RuntimeError as exc:
            outcomes.append(Outcome(run, "failed", str(exc), warn=True))
            continue
        outcomes.append(Outcome(run, "recovered"))
    return outcomes


def render_summary(outcomes: list[Outcome], *, dry_run: bool) -> str:
    head = "#### stranded self-hosted runs" + (" (dry run)" if dry_run else "")
    if not outcomes:
        return f"{head}\n- none found\n"
    lines = [head]
    for o in outcomes:
        jobs = ", ".join(o.run.stranded_jobs)
        detail = f" — {o.detail}" if o.detail else ""
        lines.append(
            f"- **{o.action}**: [{o.run.name} #{o.run.run_id}]({o.run.html_url}) "
            f"(attempt {o.run.run_attempt}; queued: {jobs}){detail}"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--max-attempt",
        type=int,
        default=3,
        help="cancel but do not re-run runs already at this attempt (loop backstop)",
    )
    args = parser.parse_args(argv)
    if not args.repo:
        parser.error("--repo (or GITHUB_REPOSITORY) is required")

    own = os.environ.get("GITHUB_RUN_ID")
    runs = find_stranded_runs(args.repo, exclude_run_id=int(own) if own else None)
    outcomes = recover(args.repo, runs, dry_run=args.dry_run, max_attempt=args.max_attempt)

    summary = render_summary(outcomes, dry_run=args.dry_run)
    print(summary)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(summary)

    recovered = sum(o.action == "recovered" for o in outcomes)
    cancelled = sum(o.action == "cancelled" for o in outcomes)
    # Machine-readable counts for the workflow's Discord ping.
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(f"recovered={recovered}\ncancelled={cancelled}\n")

    for o in outcomes:
        if o.warn:
            print(f"::warning::run {o.run.run_id} ({o.run.name}): {o.action} — {o.detail}")
    return 1 if any(o.action == "failed" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
