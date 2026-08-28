"""Guards on ``.github/workflows/grafana-panels-lint.yml`` (poindexter#1029).

The panel-query lint exists to catch the class of bug PR #308 cleaned up:
panels referencing renamed or dropped tables/columns, silently broken until
an operator opened the dashboard.

It spent three weeks not catching anything. A macro-expansion defect
(``$__timeFrom()``/``$__timeTo()`` rendered as bigint-ms when Grafana's
Postgres datasource emits quoted timestamp literals) false-failed 58 panels,
so the step was softened with ``continue-on-error: true``. The defect was
fixed 2026-08-07; the soft-fail was not removed, and a green job nobody reads
the log of is indistinguishable from a passing one.

These tests pin the two properties that keep it honest:

1. the lint step actually fails the job, and
2. the ramp ratchet next to it stays wired.

If you are here because a test failed and you were adding
``continue-on-error`` back: prefer fixing the lint, or narrowing what it
hard-fails on (it already downgrades network/connection problems to WARN and
exits 0). Softening the whole step is how this rotted the first time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit


def _repo_root() -> Path:
    return next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _workflow() -> dict:
    path = _repo_root() / ".github" / "workflows" / "grafana-panels-lint.yml"
    if not path.exists():
        pytest.skip("workflow absent (public mirror strips some CI)")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(job: str) -> list[dict]:
    return _workflow()["jobs"][job].get("steps") or []


def test_panel_query_lint_is_a_hard_failure() -> None:
    """A soft-failed lint reports nothing anyone reads. See module docstring."""
    lint_steps = [
        s
        for s in _steps("grafana-panels-lint")
        if "grafana_panels_lint.py" in (s.get("run") or "")
    ]
    assert lint_steps, "no step runs grafana_panels_lint.py"
    for step in lint_steps:
        assert not step.get("continue-on-error"), (
            f"step {step.get('name')!r} is continue-on-error, so a broken panel "
            "query merges green — the exact #308 bug class this workflow exists "
            "to catch. Fix the lint or narrow what it hard-fails on instead."
        )


def test_threshold_ramp_ratchet_stays_wired() -> None:
    """The ramp lint is the ratchet from poindexter#1028; unwiring it silently
    re-opens the fleet to invisible threshold escalations."""
    steps = _steps("grafana-threshold-ramp-lint")
    assert any(
        "grafana_threshold_ramp_lint.py" in (s.get("run") or "") for s in steps
    ), "the grafana-threshold-ramp-lint job no longer runs the ramp lint"
    for step in steps:
        assert not step.get("continue-on-error"), "the ramp ratchet must hard-fail"


def test_workflow_is_path_filtered_so_it_must_not_be_a_required_check() -> None:
    """A required status check that is path-filtered never reports on PRs
    outside those paths and leaves them pending forever. This asserts the
    filter exists so the comment warning against making it required stays
    true; if the filter is ever dropped, revisit that decision deliberately."""
    wf = _workflow()
    # PyYAML parses a bare `on:` key as the boolean True.
    triggers = wf.get("on") or wf.get(True)
    assert triggers["pull_request"].get("paths"), "pull_request paths filter gone"
    assert "infrastructure/grafana/**" in triggers["pull_request"]["paths"]
