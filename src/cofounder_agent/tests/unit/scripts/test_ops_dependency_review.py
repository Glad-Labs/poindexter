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



def test_dev_tooling_minor_and_groups_are_auto_mergeable():
    assert dr.is_dev_tooling_bump("deps:(deps-dev): bump pytest-benchmark from 5.2.3 to 5.3.0 in /src/cofounder_agent") is True
    assert dr.is_dev_tooling_bump("deps:(deps-dev): bump the development group across 1 directory with 16 updates") is True
    assert dr.is_dev_tooling_bump("deps:(deps-dev): bump the web-starter-minor-patch group across 1 directory with 2 updates") is True
    assert dr.is_dev_tooling_bump("deps:(deps-dev): bump esbuild from 0.28.1 to 0.28.2 in /infrastructure/cloudflare/page-views-beacon in the beacon-minor-patch group") is True


def test_dev_majors_and_production_minors_still_wait():
    assert dr.is_dev_tooling_bump("deps:(deps-dev): bump typescript from 5.9.3 to 7.0.2 in /web/starter") is False
    assert dr.is_dev_tooling_bump("deps:(deps-dev): bump tailwindcss from 3.4.19 to 4.3.3 in /web/starter") is False
    assert dr.is_dev_tooling_bump("deps:(deps): bump fastapi from 0.138.2 to 0.141.1 in /src/cofounder_agent") is False
    assert dr.is_dev_tooling_bump("deps:(deps): bump the production group across 1 directory with 3 updates") is False
    assert dr.is_dev_tooling_bump("ci: bump python from 3.13-slim to 3.14-slim in /src/cofounder_agent") is False


def test_auto_mergeable_is_the_union():
    assert dr.auto_mergeable("deps:(deps): bump markdown from 3.10.2 to 3.10.3") is True   # patch, any scope
    assert dr.auto_mergeable("deps:(deps-dev): bump the development group across 1 directory with 16 updates") is True
    assert dr.auto_mergeable("deps:(deps): bump litellm from 1.89.2 to 1.100.1 in /src/cofounder_agent") is False
