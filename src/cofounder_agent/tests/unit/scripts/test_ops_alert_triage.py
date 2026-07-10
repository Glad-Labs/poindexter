from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import alert_triage as at  # noqa: E402


def test_prompt_includes_alert_and_probe():
    p = at.build_classification_prompt("BrainDaemonStale", "dispatched ok", "def probe(): ...")
    assert "BrainDaemonStale" in p
    assert "def probe()" in p


def test_parse_classification_valid_json():
    raw = '{"classification": "probe_bug", "reason": "dedup broken", "suspect_file": "brain/x_probe.py"}'
    out = at.parse_classification(raw)
    assert out["classification"] == "probe_bug"
    assert out["suspect_file"] == "brain/x_probe.py"


def test_parse_classification_normalizes_and_defaults():
    out = at.parse_classification('{"classification": "REAL_FAILURE"}')
    assert out["classification"] == "real_failure"
    assert out["reason"] == ""


def test_parse_classification_bad_json_raises():
    with pytest.raises(ValueError):
        at.parse_classification("not json")
