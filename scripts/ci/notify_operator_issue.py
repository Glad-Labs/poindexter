#!/usr/bin/env python3
# scan-floor-exempt: a notifier, not a lint — it scans no source tree
"""Dedupe + self-heal for the operator issues that CI files on itself.

Three workflows auto-file a GitHub issue when ``main`` breaks
(``sync-to-public-poindexter``, ``semgrep``, ``unit-tests``). Each one called
``gh issue create`` unconditionally with the run id in the title, so a title was
unique per run and nothing could ever match anything: **one issue per failed
run**, not one per incident.

That is not a hypothetical cost. The mirror sync fires on every push to ``main``
and stays broken until the underlying cause is fixed, so a single incident files
an issue per push for as long as it lasts:

===========  =======  =========================================================
Incident     Issues   What it actually was
===========  =======  =========================================================
2026-06-13   11       one expired sync PAT (#1484-#1508, #1544)
2026-07-02   1        one failure (#2083)
2026-08-28   7        one sample webhook URL in a vendored semgrep rule
                      (#3436-#3448) — GitHub push protection rejected the push
===========  =======  =========================================================

Twenty issues for three incidents, and every one had to be closed by hand
afterwards. The backlog read as twenty bugs; there were three.

Two modes, and the second is the half that matters:

``failed``
    File the issue — or, when one is already open under the same title, add a
    comment recording this run. A storm becomes N comments on one issue.

``recovered``
    Close any open issue under that title, with a comment naming the run that
    went green. Called from an ``if: success()`` step, so the *fix* clears the
    issue rather than a human noticing weeks later. This is why the title must
    be stable: it is the dedupe key in both directions.

**Do not reach for ``gh issue list --search``.** The GitHub search index lags
behind writes by seconds to minutes — it kept reporting 30 open issues minutes
after all 30 were confirmed closed during the 2026-07-17 bandit triage. A
dedupe built on it would miss the issue it just created and file a duplicate,
which is the exact bug this script exists to fix. Plain ``gh issue list``
(no ``--search``) reads the issues API directly and is read-your-writes
consistent, so that is what ``find_open_issues`` uses — with an explicit
``--limit``, because the default silently truncates at 30.

Best-effort by design: every failure to reach ``gh`` is logged loudly and exits
0. In ``failed`` mode the job is already red and failing the notifier on top of
it adds nothing; in ``recovered`` mode the run genuinely succeeded, and turning
a green sync red because the bookkeeping call flaked would be a false alarm.
The log line is the signal — this script never fails silently, it just never
fails the build.

Usage::

    python3 scripts/ci/notify_operator_issue.py failed \
        --repo Glad-Labs/glad-labs-stack \
        --title "⚠️ poindexter mirror sync FAILED" \
        --label bug --body-file body.md \
        --run-url "$RUN_URL" --commit "$GITHUB_SHA"

    python3 scripts/ci/notify_operator_issue.py recovered \
        --repo Glad-Labs/glad-labs-stack \
        --title "⚠️ poindexter mirror sync FAILED" \
        --run-url "$RUN_URL" --commit "$GITHUB_SHA"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence

# The issues API pages at 30 by default and says nothing about it. Every open
# issue on the repo has to be visible for an exact-title match to be trustworthy
# — a miss here files a duplicate, which is the bug being fixed.
LIST_LIMIT = 500

LOG_PREFIX = "[notify-operator-issue]"

# A gh invocation and its result, so tests can drive this without a network or a
# GH_TOKEN. Returns (exit_code, stdout).
GhRunner = Callable[[Sequence[str]], "tuple[int, str]"]


def _default_runner(args: Sequence[str]) -> tuple[int, str]:
    """Run ``gh`` and return (exit code, stdout). Never raises."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print(f"{LOG_PREFIX} ERROR: `gh` not found on PATH", file=sys.stderr)
        return 127, ""
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        print(f"{LOG_PREFIX} ERROR: gh {' '.join(args)} -> rc={proc.returncode} {stderr}", file=sys.stderr)
    return proc.returncode, proc.stdout


def find_open_issues(repo: str, title: str, *, runner: GhRunner) -> list[int]:
    """Return the numbers of every OPEN issue whose title matches exactly.

    Exact match, not substring: two guards under near-identical titles must not
    collapse into each other. Returns all matches rather than the first so that
    ``recovered`` can clear duplicates which slipped through a race (workflows
    without a ``concurrency`` group can run two failure handlers at once).
    """
    rc, stdout = runner(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(LIST_LIMIT),
            "--json",
            "number,title",
        ]
    )
    if rc != 0:
        return []
    try:
        rows = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        print(f"{LOG_PREFIX} ERROR: could not parse `gh issue list` output as JSON", file=sys.stderr)
        return []
    return [int(row["number"]) for row in rows if row.get("title") == title]


def cmd_failed(
    *,
    repo: str,
    title: str,
    label: str,
    body: str,
    run_url: str,
    commit: str,
    runner: GhRunner,
) -> int:
    """File the issue, or comment on the one already tracking this incident."""
    existing = find_open_issues(repo, title, runner=runner)
    if existing:
        number = existing[0]
        comment = f"Still failing — run {run_url} on commit `{commit}`."
        rc, _ = runner(
            ["issue", "comment", str(number), "--repo", repo, "--body", comment]
        )
        if rc == 0:
            print(f"{LOG_PREFIX} recorded another failure on existing issue #{number}")
        return 0

    rc, stdout = runner(
        [
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            title,
            "--label",
            label,
            "--body",
            f"{body}\n\n**Run:** {run_url}\n**Commit:** `{commit}`",
        ]
    )
    if rc == 0:
        print(f"{LOG_PREFIX} filed {stdout.strip() or 'a new issue'}")
    return 0


def cmd_recovered(
    *, repo: str, title: str, run_url: str, commit: str, runner: GhRunner
) -> int:
    """Close every open issue under this title — the failure is over."""
    existing = find_open_issues(repo, title, runner=runner)
    if not existing:
        return 0

    comment = f"Recovered — run {run_url} on commit `{commit}` succeeded. Closing automatically."
    for number in existing:
        runner(["issue", "comment", str(number), "--repo", repo, "--body", comment])
        rc, _ = runner(["issue", "close", str(number), "--repo", repo])
        if rc == 0:
            print(f"{LOG_PREFIX} closed #{number} — recovered")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    failed = sub.add_parser("failed", help="file, or comment on the open issue")
    failed.add_argument("--repo", required=True)
    failed.add_argument("--title", required=True, help="STABLE title — the dedupe key. No run id.")
    failed.add_argument("--label", default="bug")
    failed.add_argument("--body-file", required=True)
    failed.add_argument("--run-url", required=True)
    failed.add_argument("--commit", required=True)

    recovered = sub.add_parser("recovered", help="close the open issue — it is fixed")
    recovered.add_argument("--repo", required=True)
    recovered.add_argument("--title", required=True)
    recovered.add_argument("--run-url", required=True)
    recovered.add_argument("--commit", required=True)

    return parser


def main(argv: Sequence[str] | None = None, *, runner: GhRunner = _default_runner) -> int:
    args = build_parser().parse_args(argv)

    if "\n" in args.title:
        print(f"{LOG_PREFIX} ERROR: --title must be a single line", file=sys.stderr)
        return 0

    if args.mode == "failed":
        try:
            with open(args.body_file, encoding="utf-8") as handle:
                body = handle.read()
        except OSError as exc:
            print(f"{LOG_PREFIX} ERROR: could not read --body-file: {exc}", file=sys.stderr)
            return 0
        return cmd_failed(
            repo=args.repo,
            title=args.title,
            label=args.label,
            body=body,
            run_url=args.run_url,
            commit=args.commit,
            runner=runner,
        )

    return cmd_recovered(
        repo=args.repo,
        title=args.title,
        run_url=args.run_url,
        commit=args.commit,
        runner=runner,
    )


if __name__ == "__main__":
    raise SystemExit(main())
