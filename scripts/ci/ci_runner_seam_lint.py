#!/usr/bin/env python3
"""Guard the ``vars.CI_RUNNER`` runs-on seam against Docker-needing jobs.

The self-hosted runners are containers that deliberately do NOT mount
``/var/run/docker.sock``. They execute raw PR code (unit-tests runs
``poetry install`` on dependabot branches), so handing them the daemon that
runs the production stack would let one compromised dependency own the host.
See the SECURITY note on ``github-runner-1`` in ``docker-compose.local.yml``.

Consequence: a job on the seam cannot use anything that needs Docker —
``services:`` containers, ``container:``, or the docker CLI. GitHub starts
service containers through the daemon, so such a job dies at
"Initialize containers" before a single step runs.

This has bitten twice, which is why it is a lint and not a comment:

- **#2920** put ``docker-build``'s ``build-worker`` on the seam. Every push to
  main went red the moment the runner fleet came online.
- **benchmarks** carried ``services: postgres`` on the seam behind a stale
  comment claiming the runner was "a host process on Matt's PC with Docker
  access". It had been failing its nightly run for weeks — invisible because
  it gates nothing.

Both are the same mistake: the seam's capability model is not obvious from
the job definition, so it has to be checked mechanically.

**Pure stdlib on purpose — no PyYAML.** It rides in security.yml's
``action-pins`` job, which has no ``setup-python`` step and runs under the
runner's system interpreter. The self-hosted runner image ships no PyYAML
(verified: ``ModuleNotFoundError: No module named 'yaml'``), so importing it
would make this lint fail on exactly the fleet it exists to protect. The
scanner below extracts only the four things the rule needs — ``runs-on``,
``services:``, ``container:``, and step ``uses:``/``run:`` — which does not
require a general YAML parser. ``check-action-pins.py`` sets the same
precedent.

Run: ``python scripts/ci/ci_runner_seam_lint.py``
Exit 0 = clean, 1 = a seam job needs Docker.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_WORKFLOW_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# The shared pytest-shaped seam. ``CI_RUNNER_DOCKER`` is a SEPARATE, dormant
# seam for jobs that do need the daemon — it is intentionally not checked
# here, because a runner registered against it would be one that mounts
# docker.sock.
_SEAM_VAR = "vars.CI_RUNNER"
_DOCKER_SEAM_VAR = "vars.CI_RUNNER_DOCKER"

# Shell invocations that need a daemon. Deliberately narrow: ``docker`` must
# sit in COMMAND position (line start or after a shell separator) AND be
# followed by a real subcommand. A looser ``\bdocker\s+\w`` matched
# ``emit docker true`` — a shell helper call inside security.yml's path
# classifier — and grep patterns like ``'dockerfile|docker-compose|...'``.
# Blocking a legitimate job is worse than the rare miss here, because
# ``services:``/``container:``/Docker-actions are the reliable signals and
# they are checked structurally above.
_DOCKER_CMD = re.compile(
    r"(?:^[ \t]*|[\n;&|(][ \t]*)(?:sudo\s+)?docker(?:-compose)?\s+"
    r"(?:build|buildx|compose|run|exec|push|pull|save|load|tag|login|logout"
    r"|images|ps|start|stop|rm|rmi|inspect|volume|network|system)\b",
    re.MULTILINE,
)

# Actions that run as Docker container actions or shell out to the daemon.
# Kept as a small explicit list rather than a guess: an action not listed
# here is assumed native, which is what every currently-seamed job uses
# (checkout / setup-python / setup-node / upload-artifact /
# create-github-app-token — all proven on the fleet).
_DOCKER_ACTIONS = (
    "aquasecurity/trivy-action",
    "anchore/sbom-action",
    "anchore/scan-action",
    "docker/setup-buildx-action",
    "docker/build-push-action",
    "docker/login-action",
)


def _uses_seam(runs_on: Any) -> bool:
    return _SEAM_VAR in str(runs_on) and _DOCKER_SEAM_VAR not in str(runs_on)


def _bare_python_reasons(job: dict[str, Any]) -> list[str]:
    """Flag ``python`` where only ``python3`` is guaranteed.

    GitHub's hosted images put a ``python`` shim on PATH; the self-hosted
    runner image does not. A job that shells out to bare ``python`` without
    an ``actions/setup-python`` step therefore works hosted and dies on the
    fleet with ``python: command not found`` (exit 127) — which is exactly
    how ``security.yml``'s ``action-pins`` failed the moment it moved onto
    the seam.

    ``actions/setup-python`` installs the shim, so a job that uses it is
    fine either way. This only fires on the unguarded case.
    """
    if any("actions/setup-python" in str(s.get("uses") or "") for s in job.get("steps") or []):
        return []

    reasons: list[str] = []
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for line in str(step.get("run") or "").splitlines():
            if _BARE_PYTHON.search("\n" + line):
                reasons.append(f"invokes bare `python` (use python3): {line.strip()[:56]}")
    return reasons


def _docker_reasons(job: dict[str, Any]) -> list[str]:
    """Every reason this job needs a Docker daemon. Empty = seam-safe."""
    reasons: list[str] = []

    if job.get("services"):
        names = ", ".join(sorted(job["services"])) if isinstance(job["services"], dict) else "?"
        reasons.append(f"declares services: ({names}) — started via the daemon")
    if job.get("container"):
        reasons.append("declares container: — the job body runs in a container")

    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        uses = str(step.get("uses") or "").split("@")[0]
        for action in _DOCKER_ACTIONS:
            if uses == action:
                reasons.append(f"uses {action}")
        run = str(step.get("run") or "")
        if _DOCKER_CMD.search(run):
            snippet = next(
                (ln.strip() for ln in run.splitlines() if _DOCKER_CMD.search(ln)), ""
            )
            reasons.append(f"runs the docker CLI: {snippet[:60]}")

    return reasons


# Bare ``python`` in command position. ``python3`` and ``python -V`` inside a
# quoted string are not the target; a real invocation at the start of a
# command is.
_BARE_PYTHON = re.compile(
    r"(?:^[ \t]*|[\n;&|(][ \t]*)(?:sudo\s+)?python(?![0-9._-])\s", re.MULTILINE
)

_KEY_ONLY = re.compile(r"^(\s*)([A-Za-z0-9_.\-]+):\s*(?:#.*)?$")
_KEY_VALUE = re.compile(r"^(\s*)([A-Za-z0-9_.\-]+):\s*(.*?)\s*$")
_STEP_USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)")
_STEP_RUN = re.compile(r"^(\s*)-?\s*run:\s*(.*)$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def scan_jobs(text: str) -> dict[str, dict[str, Any]]:
    """Extract the job fields this lint needs, without a YAML parser.

    Returns ``{job_name: {"runs-on", "services", "container", "steps"}}`` —
    the same shape :func:`_docker_reasons` takes, so the rule logic is
    parser-agnostic and unit-testable against plain dicts.

    Indentation is **inferred, not assumed**. Most workflows here indent jobs
    by 2, but ``release-mirror-to-public.yml`` and ``release-please.yml`` use
    4 — a hardcoded depth silently skipped both, which is the same
    fails-quietly shape this lint exists to prevent.
    """
    lines = text.splitlines()
    jobs: dict[str, dict[str, Any]] = {}

    start = next((i for i, ln in enumerate(lines) if re.match(r"^jobs:\s*$", ln)), None)
    if start is None:
        return jobs

    # The jobs: block runs until the next top-level key.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and not ln.startswith("#") and _indent(ln) == 0:
            end = i
            break
    block = lines[start + 1 : end]

    body_lines = [ln for ln in block if ln.strip() and not ln.lstrip().startswith("#")]
    if not body_lines:
        return jobs
    job_indent = min(_indent(ln) for ln in body_lines)

    # Split the block into per-job line ranges.
    headers: list[tuple[int, str]] = []
    for i, ln in enumerate(block):
        m = _KEY_ONLY.match(ln)
        if m and _indent(ln) == job_indent:
            headers.append((i, m.group(2)))

    for n, (idx, name) in enumerate(headers):
        stop = headers[n + 1][0] if n + 1 < len(headers) else len(block)
        body = block[idx + 1 : stop]
        job: dict[str, Any] = {"runs-on": "", "services": {}, "container": "", "steps": []}

        real = [ln for ln in body if ln.strip() and not ln.lstrip().startswith("#")]
        key_indent = min((_indent(ln) for ln in real), default=job_indent + 2)

        for i, ln in enumerate(body):
            if not ln.strip():
                continue
            kv = _KEY_VALUE.match(ln)
            if kv and _indent(ln) == key_indent:
                key, value = kv.group(2), kv.group(3)
                if key == "runs-on":
                    job["runs-on"] = value
                elif key == "container":
                    job["container"] = value or "declared"
                elif key == "services":
                    for nxt in body[i + 1 :]:
                        if nxt.strip() and _indent(nxt) <= key_indent:
                            break
                        svc = _KEY_ONLY.match(nxt)
                        if svc and _indent(nxt) == key_indent + (key_indent - job_indent):
                            job["services"][svc.group(2)] = {}
                continue

            uses = _STEP_USES.match(ln)
            if uses:
                job["steps"].append({"uses": uses.group(1)})
                continue

            run = _STEP_RUN.match(ln)
            if run:
                run_indent, inline = len(run.group(1)), run.group(2)
                collected = (
                    [inline] if inline and inline not in ("|", ">", "|-", ">-") else []
                )
                for nxt in body[i + 1 :]:
                    if nxt.strip() and _indent(nxt) <= run_indent:
                        break
                    collected.append(nxt)
                job["steps"].append({"run": "\n".join(collected)})

        jobs[name] = job

    return jobs


def main() -> int:
    if not _WORKFLOW_DIR.is_dir():
        print(f"[ci-runner-seam] no workflow dir at {_WORKFLOW_DIR} — nothing to check.")
        return 0

    violations: list[tuple[str, str, list[str]]] = []
    seam_jobs = 0

    for path in sorted(_WORKFLOW_DIR.glob("*.yml")):
        for job_name, job in scan_jobs(path.read_text(encoding="utf-8")).items():
            if not _uses_seam(job.get("runs-on", "")):
                continue
            seam_jobs += 1
            reasons = _docker_reasons(job) + _bare_python_reasons(job)
            if reasons:
                violations.append((path.name, str(job_name), reasons))

    if violations:
        print("[ci-runner-seam] FAIL — these jobs are on the vars.CI_RUNNER seam")
        print("                 but need a Docker daemon the runners do not have:\n")
        for wf, job, reasons in violations:
            print(f"  {wf} :: {job}")
            for reason in reasons:
                print(f"      - {reason}")
        print(
            "\n  The self-hosted runners mount no /var/run/docker.sock on purpose —\n"
            "  they execute raw PR code, so the daemon that runs the production\n"
            "  stack is deliberately out of reach. Such a job dies at\n"
            '  "Initialize containers" before any step runs.\n\n'
            "  Fix by pinning the job to `runs-on: ubuntu-latest`, or move it to\n"
            "  the separate `vars.CI_RUNNER_DOCKER` seam if a daemon-capable\n"
            "  runner is ever provisioned."
        )
        return 1

    print(f"[ci-runner-seam] OK — {seam_jobs} seam job(s), all fleet-compatible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
