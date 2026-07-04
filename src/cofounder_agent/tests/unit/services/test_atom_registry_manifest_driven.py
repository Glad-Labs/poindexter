"""Tests for manifest-driven atom discovery (Glad-Labs/poindexter#754).

Verifies that ``atom_registry.discover()`` iterates module manifests and
calls ``_walk_package`` for each non-None ``atoms_package`` field, rather
than relying on a hardcoded ``"modules.content.atoms"`` path.

No real filesystem imports are needed — both ``get_modules`` and
``_walk_package`` are patched so the test is hermetic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — minimal Module + ModuleManifest stubs
# ---------------------------------------------------------------------------


def _make_manifest(atoms_package: str | None) -> Any:
    """Return a minimal manifest-like object with an atoms_package attr."""
    m = MagicMock()
    m.atoms_package = atoms_package
    return m


def _make_module(atoms_package: str | None) -> Any:
    """Return a minimal module stub whose manifest() returns the manifest."""
    mod = MagicMock()
    mod.manifest.return_value = _make_manifest(atoms_package)
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestManifestDrivenAtomDiscovery:
    """atom_registry.discover() iterates manifests, calls _walk_package per pkg."""

    @pytest.fixture(autouse=True)
    def _restore_atom_registry(self):
        """Snapshot + restore the global atom registry around each test.

        These tests deliberately ``_ATOMS.clear()`` / ``_RUNNERS.clear()`` and
        flip ``_DISCOVERED`` to exercise discovery against patched modules. The
        patched ``_walk_package`` is a no-op, so the registry is left EMPTY with
        ``_DISCOVERED=True`` — which short-circuits every later real
        ``discover()`` to a no-op. That pollution leaked into any test that runs
        afterwards and relies on a populated catalog (``test_canonical_blog_spec``
        / ``test_podcast_pipeline_spec`` saw ``available: []``; masked in CI only
        because pytest-xdist happened to distribute the files apart). Save the
        three module globals, yield, then restore them.
        """
        import services.atom_registry as ar

        saved_atoms = dict(ar._ATOMS)
        saved_runners = dict(ar._RUNNERS)
        saved_discovered = ar._DISCOVERED
        try:
            yield
        finally:
            ar._ATOMS.clear()
            ar._ATOMS.update(saved_atoms)
            ar._RUNNERS.clear()
            ar._RUNNERS.update(saved_runners)
            ar._DISCOVERED = saved_discovered

    def _run_discover(
        self,
        modules: list[Any],
        *,
        walk_side_effect: Any = None,
    ) -> MagicMock:
        """Patch get_modules + _walk_package, reset _DISCOVERED, run discover().

        Returns the MagicMock for _walk_package so callers can assert on it.
        """
        import services.atom_registry as ar

        # Force rediscovery.
        ar._DISCOVERED = False
        ar._ATOMS.clear()
        ar._RUNNERS.clear()

        walk_mock = MagicMock(side_effect=walk_side_effect)

        with (
            patch("plugins.registry.get_modules", return_value=modules),
            patch.object(ar, "_walk_package", walk_mock),
            patch.object(ar, "_surface_stages_as_atoms"),  # keep tests fast
        ):
            ar.discover()

        return walk_mock

    def test_single_module_with_atoms_package(self):
        """A module with atoms_package set causes _walk_package to be called."""
        mod = _make_module("modules.foo.atoms")
        walk = self._run_discover([mod])
        walk.assert_called_once_with("modules.foo.atoms")

    def test_module_without_atoms_package_is_skipped(self):
        """A module with atoms_package=None does not trigger _walk_package."""
        mod = _make_module(None)
        walk = self._run_discover([mod])
        walk.assert_not_called()

    def test_multiple_modules_each_walked(self):
        """Every module that declares atoms_package gets its own _walk_package call."""
        mods = [
            _make_module("modules.content.atoms"),
            _make_module(None),
            _make_module("modules.hr.atoms"),
        ]
        walk = self._run_discover(mods)
        assert walk.call_count == 2
        walk.assert_any_call("modules.content.atoms")
        walk.assert_any_call("modules.hr.atoms")

    def test_manifest_error_skips_module(self):
        """If manifest() raises, the module is skipped and discovery continues."""
        bad_mod = MagicMock()
        bad_mod.manifest.side_effect = RuntimeError("oops")
        good_mod = _make_module("modules.good.atoms")

        walk = self._run_discover([bad_mod, good_mod])
        walk.assert_called_once_with("modules.good.atoms")

    def test_fallback_when_get_modules_raises(self):
        """If get_modules() itself raises, discover() falls back to the hardcoded path."""
        import services.atom_registry as ar

        ar._DISCOVERED = False
        ar._ATOMS.clear()
        ar._RUNNERS.clear()

        walk_mock = MagicMock()

        with (
            patch(
                "plugins.registry.get_modules",
                side_effect=ImportError("registry broken"),
            ),
            patch.object(ar, "_walk_package", walk_mock),
            patch.object(ar, "_surface_stages_as_atoms"),
        ):
            ar.discover()

        # Fallback path must still walk the content atoms so existing prod
        # behaviour is preserved even when the module registry is broken.
        walk_mock.assert_called_once_with("modules.content.atoms")

    @staticmethod
    def _walk_that_registers(name: str = "atoms.fake"):
        """A _walk_package stand-in that actually populates the registry —
        successful discovery, so caching applies."""
        import services.atom_registry as ar

        def _walk(_pkg: str) -> None:
            meta = MagicMock()
            meta.name = name
            ar._ATOMS[name] = meta
            ar._RUNNERS[name] = MagicMock()

        return MagicMock(side_effect=_walk)

    def test_discover_is_idempotent(self):
        """Calling discover() twice after a SUCCESSFUL (non-empty) discovery
        does not double-walk packages."""
        import services.atom_registry as ar

        ar._DISCOVERED = False
        ar._ATOMS.clear()
        ar._RUNNERS.clear()

        mod = _make_module("modules.content.atoms")
        walk_mock = self._walk_that_registers()

        with (
            patch("plugins.registry.get_modules", return_value=[mod]),
            patch.object(ar, "_walk_package", walk_mock),
            patch.object(ar, "_surface_stages_as_atoms"),
        ):
            ar.discover()
            ar.discover()  # second call must be a no-op

        assert walk_mock.call_count == 1

    def test_empty_discovery_is_not_cached(self):
        """2026-07-03 (task ba4d627a): a Prefect subprocess whose discovery
        yielded ZERO atoms (transient import failure — e.g. deploy-clone
        mid-sync) cached the empty registry via _DISCOVERED=True, and every
        graph_def load in that process then failed the drift gate with
        'atom no longer exists in the registry' for EVERY node, fataling the
        task. An empty discovery must not latch — the next call retries."""
        import services.atom_registry as ar

        ar._DISCOVERED = False
        ar._ATOMS.clear()
        ar._RUNNERS.clear()

        mod = _make_module("modules.content.atoms")
        walk_mock = MagicMock()  # no-op: discovery yields nothing

        with (
            patch("plugins.registry.get_modules", return_value=[mod]),
            patch.object(ar, "_walk_package", walk_mock),
            patch.object(ar, "_surface_stages_as_atoms"),
        ):
            ar.discover()
            assert ar._DISCOVERED is False
            ar.discover()  # empty result was not cached → retries the walk

        assert walk_mock.call_count == 2

    def test_empty_then_successful_discovery_heals(self):
        """A transient empty discovery followed by a successful one leaves a
        populated, cached registry (self-healing within the process)."""
        import services.atom_registry as ar

        ar._DISCOVERED = False
        ar._ATOMS.clear()
        ar._RUNNERS.clear()

        mod = _make_module("modules.content.atoms")

        with (
            patch("plugins.registry.get_modules", return_value=[mod]),
            patch.object(ar, "_walk_package", MagicMock()),
            patch.object(ar, "_surface_stages_as_atoms"),
        ):
            ar.discover()  # transient failure: nothing registered
        assert ar._DISCOVERED is False

        walk_ok = self._walk_that_registers()
        with (
            patch("plugins.registry.get_modules", return_value=[mod]),
            patch.object(ar, "_walk_package", walk_ok),
            patch.object(ar, "_surface_stages_as_atoms"),
        ):
            ar.discover()  # retry succeeds
            assert ar._DISCOVERED is True
            assert not ar.registry_is_empty()
