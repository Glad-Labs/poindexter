"""Unit tests for the ``Module`` Protocol + ``get_modules()`` registry.

Phase 1 of Glad-Labs/poindexter#490 — Module v1. The tests pin the
contract; they do NOT exercise actual entry-point discovery (which
needs an installed package) — instead they patch
``plugins.registry._cached`` to return synthetic modules, which is
the same shape every other ``get_*`` test uses in this repo.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from plugins.module import Module, ModuleManifest
from plugins.registry import get_modules


def _make_module(
    name: str = "test_mod",
    version: str = "1.0.0",
    visibility: str = "public",
    description: str = "",
    requires: tuple[str, ...] = (),
):
    """Minimal Module-shaped stub. Implements ``manifest()`` and the
    lifecycle methods as no-ops. Uses a plain class so each test can
    return different stubs to ``_cached``."""

    class _StubModule:
        def manifest(self) -> ModuleManifest:
            return ModuleManifest(
                name=name,
                version=version,
                visibility=visibility,  # type: ignore[arg-type]
                description=description,
                requires=requires,
            )

        async def migrate(self, pool: object) -> None:  # pragma: no cover
            pass

        def register_routes(self, app: object) -> None:  # pragma: no cover
            pass

        def register_cli(self, parser: object) -> None:  # pragma: no cover
            pass

        def register_dashboards(self, grafana: object) -> None:  # pragma: no cover
            pass

        def register_probes(self, brain: object) -> None:  # pragma: no cover
            pass

        async def healthcheck(self, pool: object) -> object:  # pragma: no cover
            return None

    return _StubModule()


@pytest.fixture(autouse=True)
def _clear_registry_cache():
    """Every test starts with a fresh _cached() result. The registry
    caches via functools.cache, so we clear before AND after."""
    from plugins.registry import clear_registry_cache
    clear_registry_cache()
    yield
    clear_registry_cache()


@pytest.mark.unit
def test_get_modules_empty_when_no_entry_points():
    """A fresh install with no module packages installed returns
    an empty list, not an error. Critical because this is the
    base case for the substrate-without-modules deployment."""
    with patch("plugins.registry._cached", return_value=()):
        result = get_modules()
    assert result == []


@pytest.mark.unit
def test_get_modules_returns_valid_module():
    """A module whose manifest passes every validation rule comes
    through as-is. Also checks isinstance(mod, Module) since the
    Protocol is runtime_checkable."""
    mod = _make_module(name="content", version="0.1.0", visibility="public")
    with patch("plugins.registry._cached", return_value=(mod,)):
        result = get_modules()
    assert len(result) == 1
    assert result[0] is mod
    # runtime_checkable Protocol — isinstance should succeed
    assert isinstance(mod, Module)
    # Manifest accessor returns what we built
    m = result[0].manifest()
    assert m.name == "content"
    assert m.version == "0.1.0"
    assert m.visibility == "public"


@pytest.mark.unit
def test_get_modules_drops_module_with_invalid_name(caplog):
    """A module whose name doesn't match ^[a-z][a-z0-9_]*$ is
    dropped with a warning, not raised. Caller experience:
    poindexter still boots; the bad module just doesn't appear
    in get_modules()."""
    bad_uppercase = _make_module(name="Content")  # uppercase rejected
    bad_dash = _make_module(name="my-module")  # dash rejected
    bad_leading_digit = _make_module(name="1content")  # leading digit rejected
    with patch(
        "plugins.registry._cached",
        return_value=(bad_uppercase, bad_dash, bad_leading_digit),
    ):
        with caplog.at_level("WARNING", logger="plugins.registry"):
            result = get_modules()
    assert result == []
    # One warning per dropped module
    warnings = [
        r for r in caplog.records
        if r.name == "plugins.registry" and r.levelname == "WARNING"
    ]
    assert len(warnings) == 3
    for w in warnings:
        assert "does not match" in w.message


@pytest.mark.unit
def test_get_modules_drops_duplicate_names(caplog):
    """When two installed packages both register a module named
    e.g. 'content', the first one wins and the second is dropped
    with a warning. Deterministic precedence: the order
    entry_points() returns them in, which is itself a function of
    package install order + the platform's directory listing
    (entry_points() does NOT sort). This is documented as 'first-
    discovered wins' on purpose — any other policy needs a
    user-configurable precedence map and that's out of scope for
    Phase 1."""
    first = _make_module(name="content", version="1.0.0")
    second = _make_module(name="content", version="2.0.0")
    with patch("plugins.registry._cached", return_value=(first, second)):
        with caplog.at_level("WARNING", logger="plugins.registry"):
            result = get_modules()
    assert len(result) == 1
    assert result[0] is first
    assert result[0].manifest().version == "1.0.0"
    # One warning for the dropped duplicate
    dup_warnings = [
        r for r in caplog.records
        if r.name == "plugins.registry" and "duplicate module" in r.message
    ]
    assert len(dup_warnings) == 1


@pytest.mark.unit
def test_get_modules_drops_module_whose_manifest_raises(caplog):
    """A third-party module package whose manifest() raises at
    discovery time must NOT crash the host — drop it with a warning
    and continue. The blast radius of one bad manifest is exactly
    one module."""

    class _ExplodingModule:
        def manifest(self) -> ModuleManifest:
            raise RuntimeError("kaboom — package corrupted")

        async def migrate(self, pool):  # pragma: no cover
            pass

        def register_routes(self, app):  # pragma: no cover
            pass

        def register_cli(self, parser):  # pragma: no cover
            pass

        def register_dashboards(self, grafana):  # pragma: no cover
            pass

        def register_probes(self, brain):  # pragma: no cover
            pass

        async def healthcheck(self, pool):  # pragma: no cover
            return None

    exploding = _ExplodingModule()
    healthy = _make_module(name="content")
    with patch("plugins.registry._cached", return_value=(exploding, healthy)):
        with caplog.at_level("WARNING", logger="plugins.registry"):
            result = get_modules()
    # Healthy module survives
    assert len(result) == 1
    assert result[0] is healthy
    # Exploding module dropped with the cause logged
    raise_warnings = [
        r for r in caplog.records
        if r.name == "plugins.registry"
        and "manifest() raised" in r.message
    ]
    assert len(raise_warnings) == 1
    assert "kaboom" in raise_warnings[0].message
