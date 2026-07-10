from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import triage_sweep as ts  # noqa: E402


def test_single_area_match():
    assert ts.pick_area_label("The Grafana dashboard panel is broken") == "monitoring"


def test_cross_cutting_returns_none():
    # mentions both frontend and backend -> ambiguous -> bare
    assert ts.pick_area_label("The Next.js page calls the FastAPI backend route") is None


def test_no_signal_returns_none():
    assert ts.pick_area_label("Please improve this") is None
