"""Unit tests for plugins.registry entry_points discovery.

Tests use a synthetic EntryPoint returned from a monkeypatched
`importlib.metadata.entry_points` so we exercise the discovery path
without having to pip-install a test package.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint
from typing import Any

import pytest

from plugins import Document, get_llm_providers, get_taps
from plugins.registry import clear_registry_cache


class _FakeTap:
    """Minimal Tap-shaped test double used by registry discovery tests."""

    name = "fake"
    interval_seconds = 3600

    async def extract(self, pool: Any, config: dict[str, Any]):
        yield Document(source_id="x", source_table="memory", text="hello")


class _BrokenTap:
    """A class that can be loaded but blows up during instantiation."""

    def __init__(self) -> None:
        raise RuntimeError("intentionally broken")


class _FakeLLMProvider:
    """Minimal LLMProvider-shaped double."""

    name = "fake-llm"
    supports_streaming = False
    supports_embeddings = False


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    """Clear the registry cache before and after each test.

    The registry caches results for the process lifetime; tests that
    monkeypatch entry_points need a clean slate.
    """
    clear_registry_cache()
    yield
    clear_registry_cache()


def _make_entry_point(name: str, group: str, target: type) -> EntryPoint:
    """Build an EntryPoint with .load() returning a pre-computed target.

    The real EntryPoint.load() reads from installed distributions; for
    tests we subclass and override load() to return our fake class
    directly.
    """
    ep = EntryPoint(name=name, value=f"test_module:{target.__name__}", group=group)
    # EntryPoint is a NamedTuple, so override via closure on _load wrapper.
    return _EntryPointWithFixedLoad(ep, target)


class _EntryPointWithFixedLoad:
    """EntryPoint wrapper whose load() returns a pre-supplied target.

    Duck-typed to satisfy the Iterable[EntryPoint] contract used by
    plugins.registry._load_group. Not a subclass because EntryPoint is
    a frozen NamedTuple on some Python versions.
    """

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


def test_discovers_registered_tap(monkeypatch):
    """A Tap with an entry_point in the right group is discovered."""
    def fake_entry_points(group: str | None = None):
        if group == "poindexter.taps":
            return [_make_entry_point("fake", "poindexter.taps", _FakeTap)]
        return []

    monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
    clear_registry_cache()

    taps = get_taps()
    assert len(taps) == 1
    assert taps[0].name == "fake"
    assert taps[0].interval_seconds == 3600


def test_broken_plugin_skipped_not_fatal(monkeypatch, caplog):
    """A plugin that raises during instantiation is logged + skipped.

    One broken plugin must not block discovery of the others.
    """
    def fake_entry_points(group: str | None = None):
        if group == "poindexter.taps":
            return [
                _make_entry_point("broken", "poindexter.taps", _BrokenTap),
                _make_entry_point("fake", "poindexter.taps", _FakeTap),
            ]
        return []

    monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
    clear_registry_cache()

    with caplog.at_level("ERROR"):
        taps = get_taps()

    # Broken one skipped; working one still discovered.
    assert len(taps) == 1
    assert taps[0].name == "fake"
    assert any("broken" in rec.message for rec in caplog.records)


def test_isolated_group_registration(monkeypatch):
    """LLMProvider registered under its own group doesn't leak into taps."""
    def fake_entry_points(group: str | None = None):
        if group == "poindexter.llm_providers":
            return [_make_entry_point("fake-llm", "poindexter.llm_providers", _FakeLLMProvider)]
        return []

    monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
    clear_registry_cache()

    assert len(get_llm_providers()) == 1
    assert get_llm_providers()[0].name == "fake-llm"
    assert get_taps() == [], "Taps group must be empty"


def test_cache_reuses_load_result(monkeypatch):
    """Two calls to get_taps hit the entry_points loader once."""
    call_count = 0

    def fake_entry_points(group: str | None = None):
        nonlocal call_count
        if group == "poindexter.taps":
            call_count += 1
            return [_make_entry_point("fake", "poindexter.taps", _FakeTap)]
        return []

    monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
    clear_registry_cache()

    get_taps()
    get_taps()
    get_taps()
    assert call_count == 1, "registry should cache entry_points() calls"


def test_clear_cache_forces_rediscovery(monkeypatch):
    """clear_registry_cache() makes the next get_* call re-query entry_points."""
    call_count = 0

    def fake_entry_points(group: str | None = None):
        nonlocal call_count
        if group == "poindexter.taps":
            call_count += 1
            return [_make_entry_point("fake", "poindexter.taps", _FakeTap)]
        return []

    monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
    clear_registry_cache()

    get_taps()
    clear_registry_cache()
    get_taps()
    assert call_count == 2, "clear_registry_cache must invalidate the cache"
