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
