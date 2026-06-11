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

    def test_discover_is_idempotent(self):
        """Calling discover() twice does not double-walk packages."""
        import services.atom_registry as ar

        ar._DISCOVERED = False
        ar._ATOMS.clear()
        ar._RUNNERS.clear()

        mod = _make_module("modules.content.atoms")
        walk_mock = MagicMock()

        with (
            patch("plugins.registry.get_modules", return_value=[mod]),
            patch.object(ar, "_walk_package", walk_mock),
            patch.object(ar, "_surface_stages_as_atoms"),
        ):
            ar.discover()
            ar.discover()  # second call must be a no-op

        assert walk_mock.call_count == 1
