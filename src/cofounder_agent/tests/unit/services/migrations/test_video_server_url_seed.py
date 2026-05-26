"""Contract test for the ``video_server_url`` baseline seed.

Pins the 2026-05-26 fix: the seed pointed at ``host.docker.internal:9837``
but the actual video generation server (``poindexter-wan-server``
container) listens on ``:9840``. The brain's ``operator_url_probe``
caught the drift and started paging operator alerts every 15 min.

Why a contract test instead of just fixing the seed: the baseline gets
regenerated periodically. Without a pin, a future regen reading from
a stale source could re-introduce ``:9837`` and the alert flood comes
back. This test reads the canonical seed file and asserts the
post-fix URL is what ships to fresh installs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    seeds_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "migrations"
        / "0000_baseline.seeds.sql"
    )
    return seeds_path.read_text(encoding="utf-8")


def _video_server_url_value(seeds_text: str) -> str | None:
    """Extract the seeded value for ``video_server_url`` from the SQL."""
    match = re.search(
        r"VALUES \('video_server_url', '([^']+)'",
        seeds_text,
    )
    return match.group(1) if match else None


def test_video_server_url_points_at_wan_server(baseline_seeds_text: str) -> None:
    """The seed must point at ``host.docker.internal:9840`` (poindexter-wan-server).

    If this fails because the seed was set to ``:9837`` again, the
    operator_url_probe will resume paging every 15 min after a fresh
    install. The wan-server container exposes 9840; nothing of ours
    listens on 9837 anymore.
    """
    value = _video_server_url_value(baseline_seeds_text)
    assert value is not None, "video_server_url seed row missing from baseline"
    assert value == "http://host.docker.internal:9840", (
        f"Expected video_server_url to seed at :9840 (wan-server), got {value!r}. "
        "If you intend to retire the surface, set the seed to '' (empty) — "
        "operator_url_probe skips empty URLs. Don't point at :9837 again "
        "(that port has no listener)."
    )


def test_video_server_url_seed_is_idempotent(baseline_seeds_text: str) -> None:
    """The seed must use ON CONFLICT DO NOTHING so an operator's runtime
    override (e.g. retiring the surface by setting it to '') survives a
    baseline replay."""
    match = re.search(
        r"INSERT INTO app_settings[^;]*?'video_server_url'[^;]*;",
        baseline_seeds_text,
    )
    assert match is not None, "video_server_url INSERT not found"
    assert "ON CONFLICT (key) DO NOTHING" in match.group(0), (
        "video_server_url INSERT missing ON CONFLICT clause — "
        "replaying the baseline would clobber operator overrides"
    )
