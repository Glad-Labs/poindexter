"""Tests for ``scripts/ci/grafana_threshold_ramp_lint.py`` (poindexter#1028).

The lint exists because a threshold ramp whose adjacent steps share a colour
renders an **invisible** severity escalation: the panel advertises N levels
and delivers N-1. Nothing errors and the numbers are real, so it survives
review indefinitely — 23 such ramps accumulated across 6 boards before
anyone noticed.

These tests pin the properties that make the lint trustworthy:

1. it is green on the real checked-in dashboards (so it can be a hard gate),
2. it walks ``fieldConfig.overrides`` and row-nested panels, not just
   top-level ``defaults`` — an earlier ad-hoc scan missed both and
   under-reported the fleet by six ramps, and
3. a duplicate colour is a non-zero exit naming the board, panel and step.
"""

from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _repo_root() -> Path:
    return next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _load_lint():
    path = _repo_root() / "scripts" / "ci" / "grafana_threshold_ramp_lint.py"
    spec = spec_from_file_location("grafana_threshold_ramp_lint", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _board(panels: list[dict]) -> dict:
    return {"title": "t", "uid": "u", "panels": panels}


def _steps(*colors: str) -> dict:
    return {
        "mode": "absolute",
        "steps": [{"color": c, "value": None if i == 0 else i} for i, c in enumerate(colors)],
    }


def _write(tmp_path: Path, name: str, board: dict) -> Path:
    (tmp_path / name).write_text(json.dumps(board), encoding="utf-8")
    return tmp_path / name


def _run(module, tmp_path: Path, argv_dir: Path) -> int:
    old = sys.argv
    sys.argv = ["grafana_threshold_ramp_lint.py", str(argv_dir)]
    try:
        return module.main()
    finally:
        sys.argv = old


def test_real_dashboards_are_clean() -> None:
    """The lint must be green on main, or it cannot be a hard CI gate."""
    module = _load_lint()
    dashboards = _repo_root() / "infrastructure" / "grafana" / "dashboards"
    if not dashboards.is_dir():  # public mirror strips some boards
        pytest.skip("dashboards directory absent")
    assert _run(module, dashboards, dashboards) == 0


def test_clean_ramp_passes(tmp_path: Path) -> None:
    module = _load_lint()
    _write(
        tmp_path,
        "ok.json",
        _board([{"id": 1, "title": "fine", "fieldConfig": {"defaults": {
            "thresholds": _steps("blue", "yellow", "orange", "dark-red")}, "overrides": []}}]),
    )
    assert _run(module, tmp_path, tmp_path) == 0


def test_duplicate_in_defaults_fails(tmp_path: Path, capsys) -> None:
    module = _load_lint()
    _write(
        tmp_path,
        "bad.json",
        _board([{"id": 7, "title": "Approval Queue", "fieldConfig": {"defaults": {
            "thresholds": _steps("blue", "yellow", "orange", "orange")}, "overrides": []}}]),
    )
    assert _run(module, tmp_path, tmp_path) == 1
    out = capsys.readouterr().out
    assert "bad.json" in out and "panel=7" in out and "Approval Queue" in out


def test_duplicate_in_an_override_fails(tmp_path: Path, capsys) -> None:
    """Overrides carry their own thresholds — an earlier scan missed these."""
    module = _load_lint()
    _write(
        tmp_path,
        "ov.json",
        _board([{"id": 9, "title": "Latest 20 QA Passes", "fieldConfig": {
            "defaults": {},
            "overrides": [{
                "matcher": {"id": "byName", "options": "Final Score"},
                "properties": [{"id": "thresholds",
                                "value": _steps("orange", "orange", "yellow", "blue")}],
            }],
        }}]),
    )
    assert _run(module, tmp_path, tmp_path) == 1
    out = capsys.readouterr().out
    assert "Final Score" in out


def test_duplicate_nested_under_a_row_fails(tmp_path: Path) -> None:
    """Collapsed rows nest their panels — walk_panels must recurse."""
    module = _load_lint()
    _write(
        tmp_path,
        "row.json",
        _board([{"id": 1, "type": "row", "collapsed": True, "panels": [
            {"id": 2, "title": "buried", "fieldConfig": {"defaults": {
                "thresholds": _steps("blue", "blue")}, "overrides": []}}]}]),
    )
    assert _run(module, tmp_path, tmp_path) == 1


def test_single_step_ramp_is_not_a_duplicate(tmp_path: Path) -> None:
    """A one-step ramp is the honest shape for a panel with no severity."""
    module = _load_lint()
    _write(
        tmp_path,
        "one.json",
        _board([{"id": 3, "title": "Active gen", "fieldConfig": {"defaults": {
            "thresholds": _steps("blue")}, "overrides": []}}]),
    )
    assert _run(module, tmp_path, tmp_path) == 0


def test_missing_directory_is_a_hard_error(tmp_path: Path) -> None:
    module = _load_lint()
    assert _run(module, tmp_path, tmp_path / "nope") == 2
