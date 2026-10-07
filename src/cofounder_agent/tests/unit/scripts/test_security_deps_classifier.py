"""Pins the ``deps`` path classifier that gates the Trivy CVE scan.

Why this file exists
--------------------
``security.yml`` runs ``Trivy — filesystem vuln scan`` on a push or PR only
when its ``changes`` job emits ``deps=true``. Until 2026-10-07 that pattern
named ``src/cofounder_agent``'s Poetry pair and the root ``package*.json``
and nothing else. A dependency bump in ``mcp-server*/uv.lock``, the brain's
``poetry.lock`` or any Cloudflare Worker's ``package-lock.json`` therefore
merged without the CVE scan ever running on it, and the only remaining
check was the Monday baseline.

The expectation is derived, not hand-listed: every lockfile in the tree must
match, so a new project directory is covered the day its lockfile lands.
The pattern is read straight out of the workflow, so the test can't drift
from what CI runs. ``grep -E`` (POSIX ERE) and Python's ``re`` agree on it:
anchors, alternation, groups and a bracket class, nothing else.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "security.yml"

LOCKFILE_NAMES = {"package-lock.json", "poetry.lock", "uv.lock"}
# Build caches and vendored installs hold lockfiles that are not ours.
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
    ".vercel", "dist", "build", ".pytest_cache", ".claude", ".mypy_cache",
}

_DEPS_STEP = re.compile(
    r"if grep -qE \\\s*'(?P<pattern>[^']+)'\s*\\\s*"
    r"<<<\"\$changed\"; then emit deps true",
)


@pytest.fixture(scope="module")
def deps_pattern() -> re.Pattern[str]:
    assert WORKFLOW.is_file(), f"security workflow missing at {WORKFLOW}"
    match = _DEPS_STEP.search(WORKFLOW.read_text(encoding="utf-8"))
    assert match, "could not find the `emit deps` classifier in security.yml"
    return re.compile(match.group("pattern"))


def _tracked_lockfiles() -> list[str]:
    found = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name in LOCKFILE_NAMES:
                found.append(Path(root, name).relative_to(REPO_ROOT).as_posix())
    return sorted(found)


def test_every_lockfile_triggers_the_cve_scan(deps_pattern: re.Pattern[str]) -> None:
    lockfiles = _tracked_lockfiles()
    # Scan floor: the backend's own lock always exists in this tree, so an
    # empty or tiny list means the walk is broken, not that all is covered.
    assert "src/cofounder_agent/poetry.lock" in lockfiles, lockfiles
    missed = [p for p in lockfiles if not deps_pattern.search(p)]
    assert not missed, (
        "these lockfiles can change without the Trivy scan running on the PR; "
        f"widen the `deps` pattern in security.yml: {missed}"
    )


# Example paths, deliberately NOT real files: test_ci_runs_when_its_inputs_
# change.py reads every parametrize string as a file this suite depends on,
# and these are only strings fed to the regex.
@pytest.mark.parametrize(
    "path",
    [
        "mcp-server-example/pyproject.toml",
        "infrastructure/cloudflare/example-worker/package.json",
        "src/cofounder_agent/example/poetry.lock",
        "scripts/requirements-example.txt",
        "web/example-site/app/page.js",
    ],
)
def test_manifests_anywhere_trigger(deps_pattern: re.Pattern[str], path: str) -> None:
    assert deps_pattern.search(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/cofounder_agent/example/module.py",
        "docs/operations/example.md",
        "package-lock.json.bak",
        "scripts/my-package.json.tmpl",
    ],
)
def test_ordinary_files_do_not_trigger(deps_pattern: re.Pattern[str], path: str) -> None:
    assert not deps_pattern.search(path)
