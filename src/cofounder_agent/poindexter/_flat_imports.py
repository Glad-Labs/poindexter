"""Alias the pre-namespace flat import roots onto ``poindexter.*`` -- one module
object, two names.

Step 2 of Glad-Labs/poindexter#1046. The backend's packages are moving from
flat top-level names (``services``, ``plugins``, ``modules``, ``utils``,
``routes``, ``schemas``, ``config``, ``tasks``, and ``brain``) to
``poindexter.<name>``. ~6,400 ``import`` statements and ~4,000
``mock.patch("services.x")`` strings still spell the flat names, and they
cannot all change in the same commit as the move. This finder makes the flat
spelling keep working during the transition **without duplicating modules**:

    import services.taps.memory            # flat, legacy
    import poindexter.services.taps.memory # canonical, new

both yield the SAME object -- ``sys.modules["services.taps.memory"] is
sys.modules["poindexter.services.taps.memory"]``.

WHY NOT A ``__path__`` SHIM
---------------------------
The tempting alternative -- a stub ``services/__init__.py`` whose ``__path__``
points at ``poindexter/services/`` -- executes each file TWICE, once per
spelling. Two module objects, two copies of every module-level singleton
(settings caches, the plugin registry, the AppContainer), two ``class Foo``
objects that fail ``isinstance`` against each other, and a
``mock.patch("services.x.y")`` that patches a copy the code under test never
looks at. That is the failure ``reference_importlib_reload_breaks_class_identity``
describes, at repo scale. Aliasing on ``sys.meta_path`` keeps one object.

HOW IT WORKS
------------
``sys.meta_path`` finders are consulted for EVERY import -- top-level and
submodule -- before the path-based finder. For a name whose first segment is a
flat root, this finder imports the canonical ``poindexter.<name>`` (normal
resolution; the head is ``poindexter``, so no recursion) and returns a spec
whose loader swaps the canonical object into ``sys.modules`` under the flat
name during ``exec_module``. CPython re-reads ``sys.modules[spec.name]`` after
``exec_module`` precisely to support modules that replace themselves, so the
import statement receives the canonical object. The canonical module's own
``__name__`` / ``__spec__`` are never touched (a throwaway module absorbs the
attribute initialisation), so ``pickle``, ``dataclasses`` and ``inspect`` see
the canonical identity.

TWO STATES, ONE FINDER
----------------------
* Before the move (canonical package absent): the finder returns ``None`` and
  the flat package on ``sys.path`` imports normally. Installing it is a no-op.
* After the move (flat packages reduced to stubs): every flat import is
  aliased. An ``ImportError`` raised *inside* the canonical module -- a missing
  third-party dependency -- propagates unchanged; only "the canonical module
  itself does not exist" falls through, so a real error is never masked by a
  misleading ``No module named services.x``.

WHO INSTALLS IT
---------------
``poindexter/__init__.py`` (so the wheel and ``python -m poindexter`` get it
the moment the root package is imported), and the stub ``__init__.py`` left at
each old flat root (so a process whose FIRST import is a flat one -- ``main.py``
does ``from services import ...`` before anything touches ``poindexter`` -- is
covered too). Both call :func:`install`, which is idempotent.

REMOVAL
-------
Step 5 of the epic deletes the flat stubs and this module once no flat
spelling remains. The epic's definition of done includes ``import services``
NOT working. Until then, ``tests/unit/poindexter/test_flat_imports.py`` pins
identity, patchability, error propagation and idempotence against a synthetic
package tree.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from collections.abc import Iterable
from types import ModuleType

__all__ = ["FLAT_ROOTS", "ROOT", "FlatImportAliasFinder", "install", "uninstall"]

#: The canonical root every flat name maps under.
ROOT: str = "poindexter"

#: The flat top-level names being retired. Must equal
#: ``services.module_paths.PROJECT_ROOTS`` -- pinned by a test rather than an
#: import, because this module has to stay dependency-free: it runs from
#: ``poindexter/__init__.py`` before anything else in the package exists.
FLAT_ROOTS: frozenset[str] = frozenset(
    {"services", "plugins", "modules", "utils", "routes", "schemas", "config", "tasks", "brain"}
)


class _AliasLoader(importlib.abc.Loader):
    """Swap the canonical module in under the flat name.

    ``create_module`` returns ``None`` so Python builds a throwaway module and
    initialises *its* attributes; ``exec_module`` then replaces that throwaway
    in ``sys.modules`` with the canonical object. The canonical module's
    ``__spec__`` is therefore never rewritten to the flat name.
    """

    def __init__(self, canonical: str) -> None:
        self._canonical = canonical

    def create_module(self, spec):  # noqa: ANN001 -- importlib protocol
        return None

    def exec_module(self, module: ModuleType) -> None:
        sys.modules[module.__spec__.name] = sys.modules[self._canonical]  # type: ignore[union-attr]


class FlatImportAliasFinder(importlib.abc.MetaPathFinder):
    """``sys.meta_path`` finder: ``<flat root>.*`` -> the ``<root>.<flat root>.*`` object."""

    def __init__(self, root: str = ROOT, flat_roots: Iterable[str] = FLAT_ROOTS) -> None:
        self.root = root
        self.flat_roots = frozenset(flat_roots)

    def find_spec(self, fullname: str, path=None, target=None):  # noqa: ANN001 -- protocol
        head = fullname.partition(".")[0]
        if head not in self.flat_roots:
            return None
        canonical = f"{self.root}.{fullname}"
        if canonical not in sys.modules:
            try:
                importlib.import_module(canonical)
            except ModuleNotFoundError as exc:
                missing = exc.name or ""
                # The canonical module (or one of its parents) does not exist:
                # not our case -- let normal resolution find the flat package,
                # or fail with the ordinary message.
                if missing == canonical or canonical.startswith(missing + "."):
                    return None
                # A dependency missing INSIDE the canonical module. Propagate:
                # falling through would report the wrong missing module.
                raise
        module = sys.modules[canonical]
        spec = importlib.util.spec_from_loader(
            fullname, _AliasLoader(canonical), origin=getattr(module, "__file__", None)
        )
        # Keep package-ness so `import <flat>.<sub>` consults a parent __path__.
        search = getattr(module, "__path__", None)
        if search is not None:
            spec.submodule_search_locations = list(search)
        return spec

    def __repr__(self) -> str:
        return f"FlatImportAliasFinder(root={self.root!r}, flat_roots={sorted(self.flat_roots)})"


def install(root: str = ROOT, flat_roots: Iterable[str] = FLAT_ROOTS) -> FlatImportAliasFinder:
    """Install the finder at the front of ``sys.meta_path``. Idempotent per root."""
    for existing in sys.meta_path:
        if isinstance(existing, FlatImportAliasFinder) and existing.root == root:
            return existing
    finder = FlatImportAliasFinder(root, flat_roots)
    sys.meta_path.insert(0, finder)
    return finder


def uninstall(root: str = ROOT) -> None:
    """Remove the finder for ``root`` (tests; step 5)."""
    sys.meta_path[:] = [
        f for f in sys.meta_path if not (isinstance(f, FlatImportAliasFinder) and f.root == root)
    ]
