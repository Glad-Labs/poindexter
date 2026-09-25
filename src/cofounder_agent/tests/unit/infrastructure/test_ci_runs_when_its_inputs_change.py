"""A PR that changes only what these tests read must still run them.

test-backend's "Detect whether this change touches tested code" step skips
every pytest step when a PR changes nothing its pattern matches. The tests in
this directory read dashboards and alert rules under infrastructure/, not
backend code. If the pattern misses those files, a dashboard-only PR merges
without the tests that check dashboards. The first backend PR after it then
goes red for a panel it never touched.

That was the state when this directory first got a CI step (2026-09-25): the
step existed, and the PRs it guards would have skipped it. In the month
before, 7 of 561 merges changed files under infrastructure/grafana/ and
nothing else the pattern matched.

The inputs are not listed here. They are the module-level paths the sibling
test modules declare (``ALERT_RULES_YML``, ``DASHBOARDS_DIR``, ...), so a new
test here that reads a new tree fails this until the pattern covers it.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
WORKFLOW = (".github", "workflows", "unit-tests.yml")
_TRIGGER = re.compile(r"backend_hits=\$\(grep -cE '([^']+)'")


def _trigger_pattern(repo_root: Path) -> re.Pattern[str]:
    """The ERE the detect-changes step matches each changed path against."""
    path = repo_root.joinpath(*WORKFLOW)
    steps = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["test-backend"]["steps"]
    script = next((s.get("run") or "" for s in steps if s.get("id") == "changes"), "")
    found = _TRIGGER.search(script)
    assert found, (
        f"no `backend_hits=$(grep -cE '...')` line in the `changes` step of {path}. "
        "The trigger moved; point this test at it rather than deleting the check."
    )
    return re.compile(found.group(1))


def _declared_inputs(repo_root: Path) -> set[Path]:
    """Every file the sibling test modules name through a module-level Path."""
    declared: set[Path] = set()
    for module_path in sorted(HERE.glob("test_*.py")):
        if module_path.resolve() == Path(__file__).resolve():
            continue
        module = importlib.import_module(f"{__package__}.{module_path.stem}")
        declared |= {
            value.resolve()
            for value in vars(module).values()
            if isinstance(value, Path)
            and value.resolve() != repo_root
            and value.resolve().is_relative_to(repo_root)
        }
    files: set[Path] = set()
    for path in declared:
        files |= {p for p in path.rglob("*") if p.is_file()} if path.is_dir() else {path}
    return files


def test_every_file_these_tests_read_triggers_the_suite(repo_root: Path) -> None:
    pattern = _trigger_pattern(repo_root)
    inputs = sorted(p.relative_to(repo_root).as_posix() for p in _declared_inputs(repo_root))
    assert inputs, (
        f"found no module-level input paths in the test modules under {HERE}. "
        "A check that scanned nothing has not passed."
    )

    missed = [path for path in inputs if not pattern.search(path)]
    if missed:
        pytest.fail(
            "A PR that changes only these files skips every pytest step in "
            "test-backend, including the tests that read them:\n  "
            + "\n  ".join(missed)
            + f"\n\nAdd their tree to the detect-changes pattern in {'/'.join(WORKFLOW)} "
            f"(currently {pattern.pattern!r}).",
            pytrace=False,
        )
