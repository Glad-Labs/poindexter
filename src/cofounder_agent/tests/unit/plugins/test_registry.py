"""Unit tests for plugins.registry entry_points discovery.

Tests use a synthetic EntryPoint returned from a monkeypatched
`importlib.metadata.entry_points` so we exercise the discovery path
without having to pip-install a test package.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint
from typing import Any

import pytest

from plugins import Document, get_all_llm_providers, get_llm_providers, get_taps
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

    GH#311: also re-binds the original ``plugins.registry`` submodule
    onto the parent ``plugins`` package before the test runs. A handful
    of service tests stub ``sys.modules["plugins"]`` with a bare module
    to avoid dragging in heavyweight imports; if that stub leaks, every
    ``monkeypatch.setattr("plugins.registry.entry_points", ...)`` in
    this file dies with ``no attribute 'registry'``. We rebind rather
    than re-import so the patches still hit the same module object
    referenced by this file's top-level imports of ``get_taps`` etc.
    """
    import sys

    registry = sys.modules.get("plugins.registry")
    if registry is None:
        import importlib
        registry = importlib.import_module("plugins.registry")
    plugins_pkg = sys.modules.get("plugins")
    if plugins_pkg is not None and getattr(plugins_pkg, "registry", None) is not registry:
        plugins_pkg.registry = registry  # type: ignore[attr-defined]

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


# ---------------------------------------------------------------------------
# get_all_llm_providers — Glad-Labs/poindexter#376 regression
# ---------------------------------------------------------------------------


class TestGetAllLlmProviders:
    """``get_all_llm_providers`` must surface ``ollama_native`` even on a
    fresh boot where the package isn't ``pip install``ed.

    Glad-Labs/poindexter#376: dev_diary's TWO_PASS writer mode calls
    ``services.topic_ranking.embed_text`` which looks up the
    ``ollama_native`` provider by name. Before this fix that lookup went
    through ``get_llm_providers()`` (entry_points-only), which returns
    an empty list in every dev/test checkout because poetry-build of the
    backend package is never run — leading to ``RuntimeError:
    ollama_native provider not registered — cannot generate embeddings``
    on every dev_diary run.
    """

    def test_returns_ollama_native_with_no_entry_points(self, monkeypatch):
        """Bare boot — no entry_points present — must still expose
        ``ollama_native`` via the imperatively-loaded core samples.
        """
        def fake_entry_points(group: str | None = None):
            return []  # entry_points genuinely empty (the prod reality)

        monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
        clear_registry_cache()

        names = {p.name for p in get_all_llm_providers()}
        assert "ollama_native" in names, (
            "ollama_native must be discoverable on a bare checkout. "
            f"Got: {sorted(names)}"
        )
        # The other in-tree providers should also be there.
        assert "openai_compat" in names
        assert "litellm" in names

    def test_entry_point_plugin_overrides_core_sample(self, monkeypatch):
        """When an entry_point and a core_sample share a name, the
        installed plugin distribution wins. This lets operators ship a
        replacement ``ollama_native`` via an installed package without
        editing the core repo.
        """
        class _OverrideOllama:
            name = "ollama_native"
            supports_streaming = True
            supports_embeddings = True
            sentinel = "from-entry-point"

        def fake_entry_points(group: str | None = None):
            if group == "poindexter.llm_providers":
                return [
                    _make_entry_point(
                        "ollama_native", "poindexter.llm_providers", _OverrideOllama,
                    ),
                ]
            return []

        monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
        clear_registry_cache()

        by_name = {p.name: p for p in get_all_llm_providers()}
        assert getattr(by_name["ollama_native"], "sentinel", None) == "from-entry-point"

    def test_get_llm_providers_alone_is_empty_on_fresh_boot(self, monkeypatch):
        """Documents the Glad-Labs/poindexter#376 trap: the
        entry_points-only ``get_llm_providers`` returns nothing in a
        dev checkout. Production code MUST use ``get_all_llm_providers``
        instead.
        """
        def fake_entry_points(group: str | None = None):
            return []

        monkeypatch.setattr("plugins.registry.entry_points", fake_entry_points)
        clear_registry_cache()

        assert get_llm_providers() == [], (
            "get_llm_providers() (entry_points-only) is expected to be "
            "empty on a fresh boot — that's exactly why callers should "
            "use get_all_llm_providers() instead."
        )
