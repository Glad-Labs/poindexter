from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import dependency_review as dr  # noqa: E402


def test_patch_bump_true():
    assert dr.is_patch_bump("Bump lodash from 4.17.20 to 4.17.21") is True
    assert dr.is_patch_bump("chore(deps): bump urllib3 from 2.1.0 to 2.1.2") is True


def test_minor_and_major_bumps_false():
    assert dr.is_patch_bump("Bump react from 18.2.0 to 18.3.0") is False
    assert dr.is_patch_bump("Bump next from 15.0.0 to 16.0.0") is False


def test_non_version_title_false():
    assert dr.is_patch_bump("Update the CI workflow") is False


def test_checks_green():
    assert dr.all_checks_green([{"state": "SUCCESS"}, {"conclusion": "SUCCESS"}]) is True
    assert dr.all_checks_green([{"state": "SUCCESS"}, {"conclusion": "FAILURE"}]) is False
    assert dr.all_checks_green([]) is False


def test_older_than_hours():
    now = dt.datetime(2026, 7, 9, 12, 0, tzinfo=dt.UTC)
    old = "2026-07-09T05:00:00Z"
    fresh = "2026-07-09T11:30:00Z"
    assert dr.older_than_hours(old, 6, now=now) is True
    assert dr.older_than_hours(fresh, 6, now=now) is False
