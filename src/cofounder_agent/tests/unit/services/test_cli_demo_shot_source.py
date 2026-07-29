"""Tests for the ``cli_demo`` shot source (Glad-Labs/poindexter#937 PR2).

Covers the three things that make this source different from every other one:
it is SELECTED not generated, its id becomes a filesystem path, and its clip
has a fixed length the director does not control.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas.video_shot_list import Shot, VideoShotList
from services.demo_clips import (
    BakeResult,
    available_demos,
    load_manifest,
    write_manifest,
)
from services.video_renderers.shot_list_renderer import (
    _REGENERABLE_SOURCES,
    _resolve_demo_clip,
)


class _Cfg:
    def __init__(self, data: dict):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def _shot(**kw):
    base = dict(
        idx=0, duration_s=6.0, intent="show the CLI",
        source="cli_demo", demo_id="posts-list", narration_offset_s=0.0,
    )
    base.update(kw)
    return Shot(**base)


# ---------------------------------------------------------------------------
# Schema — the director selects, it never generates
# ---------------------------------------------------------------------------


def test_valid_cli_demo_shot() -> None:
    assert _shot().demo_id == "posts-list"


def test_demo_id_is_required() -> None:
    with pytest.raises(ValidationError, match="requires a ``demo_id``"):
        _shot(demo_id=None)


@pytest.mark.parametrize("field", ["prompt", "query"])
def test_prompt_or_query_on_cli_demo_is_rejected(field: str) -> None:
    """A prompt here means the model tried to GENERATE rather than choose."""
    with pytest.raises(ValidationError, match="must not have a prompt or query"):
        _shot(**{field: "a terminal window"})


@pytest.mark.parametrize("bad", [
    "../../etc/passwd",
    "/absolute/path",
    "has spaces",
    "UPPERCASE",
    "trailing/slash",
    "..",
])
def test_demo_id_rejects_path_traversal_and_junk(bad: str) -> None:
    """``demo_id`` is LLM-authored and becomes a filename."""
    with pytest.raises(ValidationError, match="must be a lowercase slug"):
        _shot(demo_id=bad)


def test_pacing_guard_limits_consecutive_demo_shots() -> None:
    """Demo footage cannot take over a video.

    The existing max-2-consecutive rule applies to ``cli_demo`` like any other
    source, so a director cannot emit an all-terminal video.
    """
    shots = [
        _shot(idx=i, narration_offset_s=float(i * 5), duration_s=5.0)
        for i in range(3)
    ]
    with pytest.raises(ValidationError, match="more than 2 consecutive"):
        VideoShotList(
            total_duration_s=15.0, shots=shots, director_model="m",
            director_prompt_version="v1",
            director_decided_at=datetime.now(timezone.utc),
        )


def test_cli_demo_is_not_regenerable() -> None:
    """Re-rolling a deterministic recording replays identical frames."""
    assert "cli_demo" not in _REGENERABLE_SOURCES


# ---------------------------------------------------------------------------
# _resolve_demo_clip — the path boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_returns_error_for_unbaked_clip(tmp_path: Path) -> None:
    path, dur, err = await _resolve_demo_clip(
        _shot(demo_id="never-baked"), _Cfg({"demo_clip_dir": str(tmp_path)})
    )
    assert path == "" and dur == 0.0
    assert "not baked" in err
    assert "demos bake --slug never-baked" in err  # actionable remediation


@pytest.mark.asyncio
async def test_resolve_rejects_traversal_at_the_render_site(tmp_path: Path) -> None:
    """Defence in depth: the schema guards too, but a frozen or hand-edited
    shot list can reach the renderer without re-validation."""
    bad = Shot.model_construct(
        idx=0, duration_s=5.0, intent="x", source="cli_demo",
        demo_id="../../../etc/passwd", narration_offset_s=0.0,
    )
    path, _, err = await _resolve_demo_clip(bad, _Cfg({"demo_clip_dir": str(tmp_path)}))
    assert path == ""
    assert "not a valid slug" in err


@pytest.mark.asyncio
async def test_resolve_rejects_empty_demo_id(tmp_path: Path) -> None:
    blank = Shot.model_construct(
        idx=0, duration_s=5.0, intent="x", source="cli_demo",
        demo_id="", narration_offset_s=0.0,
    )
    _, _, err = await _resolve_demo_clip(blank, _Cfg({"demo_clip_dir": str(tmp_path)}))
    assert "no demo_id" in err


# ---------------------------------------------------------------------------
# Manifest — how the director learns a clip's length
# ---------------------------------------------------------------------------


def test_manifest_round_trip(tmp_path: Path) -> None:
    write_manifest(tmp_path, [
        BakeResult("posts-list", True, duration_s=6.52),
        BakeResult("ops-sweep", True, duration_s=20.0),
        BakeResult("broken", False, error="boom"),
    ])
    loaded = load_manifest(_Cfg({"demo_clip_dir": str(tmp_path)}))
    assert loaded == {"posts-list": 6.52, "ops-sweep": 20.0}
    assert "broken" not in loaded  # a failed bake has no duration to record


def test_manifest_merges_rather_than_overwrites(tmp_path: Path) -> None:
    """Baking one --slug must not erase the other entries."""
    write_manifest(tmp_path, [BakeResult("a", True, duration_s=1.0)])
    write_manifest(tmp_path, [BakeResult("b", True, duration_s=2.0)])
    assert load_manifest(_Cfg({"demo_clip_dir": str(tmp_path)})) == {"a": 1.0, "b": 2.0}


def test_corrupt_manifest_does_not_block_a_bake(tmp_path: Path) -> None:
    """It is a rebuildable cache of durations, not a source of truth."""
    (tmp_path / "manifest.json").write_text("{not json")
    write_manifest(tmp_path, [BakeResult("a", True, duration_s=1.0)])
    assert load_manifest(_Cfg({"demo_clip_dir": str(tmp_path)})) == {"a": 1.0}


def test_missing_manifest_is_empty_not_an_error(tmp_path: Path) -> None:
    assert load_manifest(_Cfg({"demo_clip_dir": str(tmp_path)})) == {}


def test_available_demos_excludes_unbaked(tmp_path: Path) -> None:
    """Offering an unbaked demo guarantees a fallback fill + a warn finding."""
    cfg = _Cfg({"demo_clip_dir": str(tmp_path)})
    assert available_demos(cfg) == []

    (tmp_path / "posts-list.mp4").write_bytes(b"x")
    (tmp_path / "manifest.json").write_text(json.dumps({"posts-list": 6.5}))
    got = available_demos(cfg)
    assert [t.slug for t, _ in got] == ["posts-list"]
    assert got[0][1] == 6.5
