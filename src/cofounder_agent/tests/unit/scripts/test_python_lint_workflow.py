"""Guards on the ruff version ``.github/workflows/python-lint.yml`` installs (poindexter#1111).

The workflow pins ruff so CI and a contributor's ``poetry install`` cannot
disagree about what counts as a violation. The pin lives in an ``env:``
variable, and Dependabot cannot see one: it bumps ``poetry.lock`` and stops.
Four consecutive ruff bumps (0.15.20 -> 0.16.7) merged past a workflow that
still installed 0.15.20, so the ``backend-lint`` and ``syntax-check`` jobs ran
a different ruff from everyone else's.

``poetry.lock`` is the source of truth. This derives the expected version from
it, the way the bandit pin test does for ``unit-tests.yml``, so the next bump
fails here, inside its own PR, instead of drifting quietly.

If you are here because a ruff bump failed this test: set ``RUFF_VERSION`` in
``python-lint.yml`` to the version the lock now names, then confirm both jobs
stay green under it (``cd src/cofounder_agent && ruff check .``, and from the
repo root ``ruff check --select E9 .``).
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest
import tomllib
import yaml

pytestmark = pytest.mark.unit

WORKFLOW = Path(".github/workflows/python-lint.yml")
LOCKFILE = Path("src/cofounder_agent/poetry.lock")
PIN_VAR = "RUFF_VERSION"
# The one spelling a ruff install may use. A literal version here would be a
# second pin that the env variable, and so this test, never sees.
PINNED_SPEC = "ruff==${" + PIN_VAR + "}"
# ruff run as a command, however it is launched (`ruff check`, `python -m ruff
# format`, `poetry run ruff check`), so no job can drop out of the check by
# changing how it calls ruff.
RUNS_RUFF = re.compile(r"\bruff\s+(?:check|format)\b")
INSTALLS = re.compile(r"\binstall\b")


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / WORKFLOW).is_file() and (parent / LOCKFILE).is_file():
            return parent
    raise RuntimeError(f"could not find {WORKFLOW} and {LOCKFILE} above {__file__}")


def locked_version(lock_text: str, package: str) -> str:
    """The version poetry.lock resolves ``package`` to: exactly one entry, or fail."""
    entries = [p for p in tomllib.loads(lock_text).get("package", []) if p.get("name") == package]
    assert entries, f"{package} not found in poetry.lock"
    versions = [e["version"] for e in entries]
    assert len(entries) == 1, (
        f"poetry.lock resolves {package} {len(entries)} times ({versions}), "
        "so there is no single version to pin"
    )
    return versions[0]


def workflow_problems(workflow_text: str, locked: str) -> list[str]:
    """Every way the workflow's ruff can differ from ``locked``; empty when in step."""
    doc = yaml.safe_load(workflow_text)
    problems: list[str] = []

    pinned = (doc.get("env") or {}).get(PIN_VAR)
    if str(pinned) != locked:
        problems.append(f"env.{PIN_VAR} is {pinned!r}, but poetry.lock resolves ruff {locked}")

    jobs_running_ruff = 0
    for name, job in (doc.get("jobs") or {}).items():
        steps = job.get("steps") or []
        scopes = [("the job", job.get("env"))] + [
            (f"step {step.get('name') or step.get('run')!r}", step.get("env")) for step in steps
        ]
        problems += [
            f"{where} of job {name!r} redefines {PIN_VAR}, a second pin this test cannot follow"
            for where, env in scopes
            if PIN_VAR in (env or {})
        ]

        runs = [step.get("run") or "" for step in steps]
        if not any(RUNS_RUFF.search(run) for run in runs):
            continue
        jobs_running_ruff += 1
        installs = [run for run in runs if INSTALLS.search(run) and "ruff" in run]
        if not installs:
            problems.append(
                f"job {name!r} runs ruff but never installs it, so it gets whatever the runner has"
            )
        problems += [
            f"job {name!r} installs ruff with {run.strip()!r}; it must use {PINNED_SPEC!r} "
            "so the version comes from the one env pin"
            for run in installs
            if PINNED_SPEC not in run
        ]

    if not jobs_running_ruff:
        problems.append("no job in the workflow runs ruff, so there is nothing to keep in step")
    return problems


class TestRuffVersionPinnedForReproducibility:
    """The workflow's pinned ruff must be the one poetry.lock resolves. A
    dependency bump that moves the lock has to fail loudly here, not leave CI
    running a ruff nobody else has."""

    def test_workflow_pin_matches_poetry_lock(self):
        root = _repo_root()
        locked = locked_version((root / LOCKFILE).read_text(encoding="utf-8"), "ruff")
        problems = workflow_problems((root / WORKFLOW).read_text(encoding="utf-8"), locked)
        assert problems == [], (
            f"{WORKFLOW} is out of step with poetry.lock (ruff {locked}):\n  - "
            + "\n  - ".join(problems)
            + f"\nSet {PIN_VAR} to {locked}, then confirm both jobs stay green under it: "
            "`cd src/cofounder_agent && ruff check .` and, from the repo root, "
            "`ruff check --select E9 .`."
        )


_LOCK = textwrap.dedent(
    """\
    [[package]]
    name = "ruff"
    version = "0.16.7"
    """
)


def _workflow(
    pin: str = "0.16.7",
    install: str = 'pip install "ruff==${RUFF_VERSION}"',
    job_env: str = "",
    step_env: str = "",
) -> str:
    # Assembled by hand: the shell's ${...} would need doubled braces in an f-string.
    return (
        f"env:\n  {PIN_VAR}: '{pin}'\n"
        "jobs:\n"
        "  lint:\n"
        f"{job_env}"
        "    steps:\n"
        f"      - name: Install ruff\n        run: {install}\n{step_env}"
        "      - run: ruff check .\n"
    )


class TestTheGuardCanFail:
    """A pin test that has never been seen failing has not been shown to work.
    Each case is a way this guard could pass while CI still runs the wrong ruff."""

    def test_a_workflow_in_step_with_the_lock_is_clean(self):
        assert workflow_problems(_workflow(), "0.16.7") == []

    def test_the_original_drift_is_caught(self):
        (problem,) = workflow_problems(_workflow(pin="0.15.20"), "0.16.7")
        assert "0.15.20" in problem and "0.16.7" in problem

    @pytest.mark.parametrize(
        "install",
        [
            "pip install ruff==0.16.7",  # right today, but a second pin the env var never moves
            "pip install ruff",  # unpinned
            'pip install "ruff>=0.16"',  # a range
            "pipx install ruff==0.16.7",  # not spelled `pip install`, still an install
        ],
    )
    def test_an_install_that_bypasses_the_pin_is_caught(self, install):
        problems = workflow_problems(_workflow(install=install), "0.16.7")
        assert any("must use" in p for p in problems), install

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"job_env": "    env:\n      RUFF_VERSION: '0.15.20'\n"},
            {"step_env": "        env:\n          RUFF_VERSION: '0.15.20'\n"},
        ],
        ids=["job", "step"],
    )
    def test_a_second_pin_at_a_narrower_scope_is_caught(self, kwargs):
        problems = workflow_problems(_workflow(**kwargs), "0.16.7")
        assert any("redefines" in p for p in problems)

    @pytest.mark.parametrize(
        "command",
        ["ruff check .", "python -m ruff check .", "poetry run ruff format --check ."],
    )
    def test_a_job_that_runs_ruff_without_installing_it_is_caught(self, command):
        workflow = (
            f"env:\n  {PIN_VAR}: '0.16.7'\njobs:\n  lint:\n    steps:\n      - run: {command}\n"
        )
        assert any("never installs" in p for p in workflow_problems(workflow, "0.16.7")), command

    def test_a_workflow_that_no_longer_runs_ruff_is_caught(self):
        workflow = (
            f"env:\n  {PIN_VAR}: '0.16.7'\njobs:\n  lint:\n    steps:\n      - run: echo hi\n"
        )
        assert any("nothing to keep in step" in p for p in workflow_problems(workflow, "0.16.7"))


class TestLockedVersion:
    def test_reads_the_single_entry(self):
        assert locked_version(_LOCK, "ruff") == "0.16.7"

    def test_a_package_missing_from_the_lock_fails_loudly(self):
        with pytest.raises(AssertionError, match="not found"):
            locked_version(_LOCK, "bandit")

    def test_an_ambiguous_lock_fails_loudly(self):
        with pytest.raises(AssertionError, match="2 times"):
            locked_version(_LOCK + _LOCK.replace("0.16.7", "0.15.20"), "ruff")
