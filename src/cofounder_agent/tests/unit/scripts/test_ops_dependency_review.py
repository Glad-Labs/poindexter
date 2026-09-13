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


def test_dev_majors_and_production_titles_are_not_dev_tooling():
    assert dr.is_dev_tooling_bump("deps:(deps-dev): bump typescript from 5.9.3 to 7.0.2 in /web/starter") is False
    assert dr.is_dev_tooling_bump("deps:(deps-dev): bump tailwindcss from 3.4.19 to 4.3.3 in /web/starter") is False
    assert dr.is_dev_tooling_bump("deps:(deps): bump fastapi from 0.138.2 to 0.141.1 in /src/cofounder_agent") is False
    assert dr.is_dev_tooling_bump("deps:(deps): bump the production group across 1 directory with 3 updates") is False
    assert dr.is_dev_tooling_bump("ci: bump python from 3.13-slim to 3.14-slim in /src/cofounder_agent") is False


def test_auto_mergeable_is_the_union():
    assert dr.auto_mergeable("deps:(deps): bump markdown from 3.10.2 to 3.10.3") is True   # patch, any scope
    assert dr.auto_mergeable("deps:(deps-dev): bump the development group across 1 directory with 16 updates") is True
    assert dr.auto_mergeable("deps:(deps): bump litellm from 1.89.2 to 1.100.1 in /src/cofounder_agent") is False  # held minor
    assert dr.auto_mergeable("deps:(deps): bump fastapi from 0.138.2 to 0.141.1 in /src/cofounder_agent") is True  # production minor
    assert dr.auto_mergeable("deps:(deps): bump fastapi from 0.141.1 to 1.0.0 in /src/cofounder_agent") is False  # major


def test_production_minors_auto_merge_unless_held():
    assert dr.is_production_minor_bump("deps:(deps): bump fastapi from 0.138.2 to 0.141.1 in /src/cofounder_agent") is True
    assert dr.is_production_minor_bump("deps:(deps): bump starlette from 1.3.1 to 1.6.0 in /src/cofounder_agent") is True
    assert dr.is_production_minor_bump("deps:(deps): bump the production group across 1 directory with 3 updates") is True
    assert dr.is_production_minor_bump("deps:(deps): bump the web-starter-minor-patch group across 1 directory with 2 updates") is True
    assert dr.is_production_minor_bump("deps:(deps): bump the mcp-server-minor-patch group in /mcp-server with 4 updates") is True
    # held: the unit suite cannot see what these change
    for pkg, frm, to in (("litellm", "1.89.2", "1.100.1"), ("prefect", "3.6.0", "3.7.0"), ("torch", "2.13.0", "2.14.0"),
                         ("llama-index-core", "0.14.24", "0.15.0"), ("langchain-core", "1.2.0", "1.3.0"),
                         ("ragas", "0.4.1", "0.5.0"), ("next", "16.3.1", "16.4.0")):
        assert dr.is_production_minor_bump(f"deps:(deps): bump {pkg} from {frm} to {to} in /x") is False, pkg
    # never: majors, dev scope, docker tags, unparsable
    assert dr.is_production_minor_bump("deps:(deps): bump protobuf from 6.33.6 to 7.36.1 in /src/cofounder_agent/poindexter/brain") is False
    assert dr.is_production_minor_bump("deps:(deps-dev): bump ipython from 9.16.1 to 9.17.1 in /src/cofounder_agent") is False
    assert dr.is_production_minor_bump("ci: bump python from 3.13-slim to 3.14-slim in /src/cofounder_agent") is False
    assert dr.is_production_minor_bump("ci: bump alpine from 3.20 to 3.24 in /scripts") is False
    assert dr.is_production_minor_bump("chore: tidy the lockfile") is False


def test_held_list_is_by_name_or_family_prefix():
    assert dr.bumped_package("deps:(deps): bump @types/node from 20.19.35 to 22.20.2 in /web/starter") == "@types/node"
    assert dr.bumped_package("deps:(deps): bump the production group across 1 directory with 3 updates") is None
    assert dr.is_held_package("litellm") and dr.is_held_package("torchvision") and dr.is_held_package("llama-index-embeddings-ollama")
    assert not dr.is_held_package("fastapi") and not dr.is_held_package("starlette")
