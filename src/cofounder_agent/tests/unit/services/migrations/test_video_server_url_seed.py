"""Contract test for the ``video_server_url`` baseline seed.

Pins the 2026-05-28 fix (Glad-Labs/poindexter#649 PR 2): the
seed had pointed at ``host.docker.internal:9840`` but the
slideshow path that ``services/video_service.py::generate_video_for_post``
drives expects the ffmpeg slideshow server on ``:9837``. The
T2V Wan 2.1 model server (``:9840``) only accepts a ``prompt``
field — POSTing ``image_paths`` / ``audio_path`` / ``ken_burns``
to it returns 422 on every call.

The 2026-05-26 repoint to ``:9840`` was a mistake — it conflated
two different services on adjacent ports. The slideshow server
(ffmpeg, port 9837) is the one ``video_service`` calls; the
T2V model server (Wan 2.1, port 9840) is addressed by the
``Wan21Provider`` plugin via ``wan_server_url`` /
``plugin.video_provider.wan2.1-1.3b.server_url`` and sends a
``prompt`` body.

Why a contract test: the baseline gets regenerated periodically.
Without a pin, a future regen reading from a stale source could
re-introduce ``:9840`` and the 422 storm comes back.
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


def test_video_server_url_points_at_slideshow_server(baseline_seeds_text: str) -> None:
    """The seed must point at ``host.docker.internal:9837`` (slideshow server).

    If this fails because the seed was set to ``:9840`` again, every
    ``generate_video_for_post`` call 422s — the wan-server is text-to-
    video and rejects ``image_paths`` / ``audio_path`` / ``ken_burns``.
    """
    value = _video_server_url_value(baseline_seeds_text)
    assert value is not None, "video_server_url seed row missing from baseline"
    assert value == "http://host.docker.internal:9837", (
        f"Expected video_server_url to seed at :9837 (slideshow server), got {value!r}. "
        "If you intend to retire the surface, set the seed to '' (empty) — "
        "operator_url_probe skips empty URLs. Don't point at :9840 — the "
        "wan-server is a different service (T2V model) addressed by "
        "Wan21Provider via wan_server_url / plugin.video_provider.wan2.1-1.3b.server_url."
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
