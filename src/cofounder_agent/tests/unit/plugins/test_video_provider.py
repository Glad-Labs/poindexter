"""Unit tests for ``plugins/video_provider.py``.

GitHub #124 — adds the VideoProvider Protocol + VideoResult dataclass
that mirrors the ImageProvider shape. Tests:

- VideoResult dataclass shape + field defaults + serialization
- VideoProvider runtime_checkable conformance
- Registry exposes ``get_video_providers()`` and the
  ``poindexter.video_providers`` group is wired in
  ``ENTRY_POINT_GROUPS``.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint
from typing import Any

import pytest

from plugins import VideoProvider, VideoResult, get_video_providers
from plugins.registry import ENTRY_POINT_GROUPS, clear_registry_cache


# ---------------------------------------------------------------------------
# VideoResult dataclass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVideoResultDataclass:
    def test_minimal_construction(self):
        r = VideoResult(file_url="file:///tmp/x.mp4")
        assert r.file_url == "file:///tmp/x.mp4"
        # Defaults align with what generation providers can't always
        # know up-front.
        assert r.file_path is None
        assert r.duration_s == 0
        assert r.width is None
        assert r.height is None
        assert r.fps is None
        assert r.codec == ""
        assert r.format == ""
        assert r.source == "unknown"
        assert r.prompt == ""
        assert r.metadata == {}

    def test_full_construction(self):
        r = VideoResult(
            file_url="https://cdn/x.mp4",
            file_path="/tmp/x.mp4",
            duration_s=5,
            width=832,
            height=480,
            fps=16,
            codec="h264",
            format="mp4",
            source="wan2.1-1.3b",
            prompt="a cat surfing",
            metadata={"file_size_bytes": 1024, "license": "apache-2.0"},
        )
        assert r.duration_s == 5
        assert r.metadata["license"] == "apache-2.0"

    def test_to_dict_round_trip(self):
        r = VideoResult(
            file_url="file:///t.mp4",
            file_path="/t.mp4",
            duration_s=3,
            source="wan2.1-1.3b",
            metadata={"a": 1},
        )
        d = r.to_dict()
        assert d["file_url"] == "file:///t.mp4"
        assert d["file_path"] == "/t.mp4"
        assert d["duration_s"] == 3
        assert d["source"] == "wan2.1-1.3b"
        assert d["metadata"] == {"a": 1}

    def test_to_legacy_dict_maps_to_legacy_field_names(self):
        """legacy ``services.video_service.VideoResult`` field shape is
        what publish_service / backfill_videos / routes consume; the
        adapter must keep them working without code changes."""
        r = VideoResult(
            file_url="file:///t.mp4",
            file_path="/t.mp4",
            duration_s=42,
            metadata={"file_size_bytes": 1024, "images_used": 8},
        )
        legacy = r.to_legacy_dict()
        assert legacy["success"] is True
        assert legacy["file_path"] == "/t.mp4"
        assert legacy["duration_seconds"] == 42
        assert legacy["file_size_bytes"] == 1024
        assert legacy["images_used"] == 8
        assert legacy["error"] is None

    def test_to_legacy_dict_handles_missing_metadata_keys(self):
        r = VideoResult(file_url="", file_path=None)
        legacy = r.to_legacy_dict()
        assert legacy["success"] is False
        assert legacy["file_size_bytes"] == 0
        assert legacy["images_used"] == 0


# ---------------------------------------------------------------------------
# VideoProvider Protocol conformance
# ---------------------------------------------------------------------------


class _OkVideoProvider:
    """Minimal Protocol-conformant double — name + kind + async fetch."""

    name = "ok"
    kind = "generate"

    async def fetch(
        self, query_or_prompt: str, config: dict[str, Any],
    ) -> list[VideoResult]:
        return [VideoResult(file_url="file:///x.mp4", source=self.name)]


class _MissingFetch:
    name = "broken"
    kind = "generate"


@pytest.mark.unit
class TestVideoProviderProtocol:
    def test_runtime_checkable_accepts_conforming_class(self):
        assert isinstance(_OkVideoProvider(), VideoProvider)

    def test_runtime_checkable_rejects_missing_fetch(self):
        # Note: runtime_checkable Protocols don't check method
        # signatures, only attribute presence — so this verifies the
        # most basic shape contract.
        assert not isinstance(_MissingFetch(), VideoProvider)

    @pytest.mark.asyncio
    async def test_fetch_returns_video_result_list(self):
        provider = _OkVideoProvider()
        results = await provider.fetch("a prompt", {})
        assert len(results) == 1
        assert isinstance(results[0], VideoResult)
        assert results[0].source == "ok"


# ---------------------------------------------------------------------------
# Registry wiring — ``poindexter.video_providers`` entry-point group
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    clear_registry_cache()
    yield
    clear_registry_cache()


@pytest.mark.unit
class TestVideoProviderRegistry:
    def test_entry_point_group_registered(self):
        assert ENTRY_POINT_GROUPS["video_providers"] == (
            "poindexter.video_providers"
        )

    def test_get_video_providers_callable(self):
        # Even when no providers are pip-installed, the function must
        # return a list (possibly empty) — never raise.
        result = get_video_providers()
        assert isinstance(result, list)

    def test_discovers_registered_video_provider(self, monkeypatch):
        """A VideoProvider with an entry_point in the right group is
        discovered. Mirrors test_registry.py:test_discovers_registered_tap."""

        class _EntryPointWithFixedLoad:
            def __init__(self, ep: EntryPoint, target: type):
                self._ep = ep
                self._target = target

            @property
            def name(self) -> str:
                return self._ep.name

            @property
            def group(self) -> str:
                return self._ep.group

            def load(self) -> type:
                return self._target

        ep = EntryPoint(
            name="ok",
            value="t:_OkVideoProvider",
            group="poindexter.video_providers",
        )
        wrapped = _EntryPointWithFixedLoad(ep, _OkVideoProvider)

        def fake_entry_points(group: str | None = None):
            if group == "poindexter.video_providers":
                return [wrapped]
            return []

        monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
        clear_registry_cache()

        providers = get_video_providers()
        assert len(providers) == 1
        assert providers[0].name == "ok"
        assert providers[0].kind == "generate"

    def test_get_core_samples_includes_video_providers_key(self):
        """``get_core_samples()`` must surface the video_providers list
        so dispatchers can merge entry-point + core-sample sources."""
        from plugins.registry import get_core_samples

        samples = get_core_samples()
        assert "video_providers" in samples
        # Wan21Provider + KenBurnsSlideshowProvider both ship as
        # imperative core samples until the packaging issue is
        # resolved.
        names = sorted(p.__class__.__name__ for p in samples["video_providers"])
        assert "Wan21Provider" in names
        assert "KenBurnsSlideshowProvider" in names
