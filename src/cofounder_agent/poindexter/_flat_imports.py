"""Alias the pre-namespace flat import roots onto ``poindexter.*`` -- one module
object, two names.

Step 2 of Glad-Labs/poindexter#1046. The backend's packages are moving from
flat top-level names (``services``, ``plugins``, ``modules``, ``utils``,
``routes``, ``schemas``, ``config``, ``tasks``, and ``brain``) to
``poindexter.<name>``. ~6,400 ``import`` statements and ~4,000
``mock.patch("services.x")`` strings still spell the flat names, and they
cannot all change in the same commit as the move. This finder makes the flat
spelling keep working during the transition **without duplicating modules**:

    import services.taps.memory                  # flat, legacy
    import cofounder_agent.services.taps.memory  # umbrella (entry points)
    import poindexter.services.taps.memory       # canonical, new

all yield the SAME object -- ``sys.modules["services.taps.memory"] is
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
flat root, :meth:`FlatImportAliasFinder.find_spec` LOCATES the canonical
``poindexter.<name>`` with :func:`importlib.util.find_spec` (which imports the
parent packages but never executes the module itself) and returns a spec whose
loader does the real work at ``exec_module`` time: import the canonical module,
then swap the canonical object into ``sys.modules`` under the flat name. CPython
re-reads ``sys.modules[spec.name]`` after ``exec_module`` precisely to support
modules that replace themselves, so the import statement receives the canonical
object. The canonical module's own ``__name__`` / ``__spec__`` are never touched
(a throwaway module absorbs the attribute initialisation), so ``pickle``,
``dataclasses`` and ``inspect`` see the canonical identity.

WHY THE IMPORT HAPPENS IN THE LOADER, NOT THE FINDER
----------------------------------------------------
The first cut imported the canonical module inside ``find_spec``. That is a
side effect inside ``importlib._bootstrap._find_spec``, and it interacts with
circular imports: when the canonical module's execution re-enters the SAME flat
name (``routes/task_routes.py`` imports ``task_publishing_routes``, which does
``from routes.task_routes import ...``; ``services/integrations/__init__.py``
imports its own children by their flat names), the nested import populates
``sys.modules[flat]`` while the outer ``_find_spec`` is still running -- and
``_find_spec`` then IGNORES the spec our finder returns in favour of
``sys.modules[flat].__spec__``, the canonical spec, so ``_load_unlocked``
executes the file a SECOND time under the canonical key. Flat name = copy one,
canonical name = copy two: exactly the double-import this module exists to
prevent, surfacing as ``mock.patch("routes.task_routes.x")`` patching the copy
the app does not run. Locating in ``find_spec`` and importing in ``exec_module``
keeps ``_find_spec`` side-effect free; during the ``exec_module`` window the
placeholder that sits under the flat name forwards attribute access to the
canonical module, so a circular import sees exactly what a plain one would (the
names defined so far). ``tests/unit/poindexter/test_flat_imports.py`` pins both
cycle shapes with an execution counter.

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

__all__ = ["FLAT_ROOTS", "ROOT", "UMBRELLA", "FlatImportAliasFinder", "install", "uninstall"]

#: The canonical root every flat name maps under.
ROOT: str = "poindexter"

#: The flat top-level names being retired. Must equal
#: ``services.module_paths.PROJECT_ROOTS`` -- pinned by a test rather than an
#: import, because this module has to stay dependency-free: it runs from
#: ``poindexter/__init__.py`` before anything else in the package exists.
FLAT_ROOTS: frozenset[str] = frozenset(
    {"services", "plugins", "modules", "utils", "routes", "schemas", "config", "tasks", "brain"}
)

#: The umbrella package the backend is ALSO importable through: ``src/cofounder_agent``
#: is itself a package (the poetry manifest ships it as ``cofounder_agent``), and the
#: pyproject entry points are spelled ``cofounder_agent.plugins.samples.hello_tap:HelloTap``.
#: ``cofounder_agent.<flat root>.x`` is therefore a THIRD spelling of the same module,
#: aliased onto the canonical object too. (Before step 2 that spelling loaded a second
#: copy of every module it touched -- a pre-existing double import this closes.)
UMBRELLA: str = "cofounder_agent"


class _AliasLoader(importlib.abc.Loader):
    """Import the canonical module, then swap it in under the flat name.

    ``create_module`` returns ``None`` so Python builds a throwaway module and
    initialises *its* attributes; ``exec_module`` imports the canonical module
    (the throwaway sits under the flat name meanwhile, forwarding attribute
    access to the canonical object so a circular re-entry behaves like a plain
    circular import) and then replaces the throwaway in ``sys.modules`` with the
    canonical object. The canonical module's ``__spec__`` is never rewritten.
    """

    def __init__(self, canonical: str) -> None:
        self._canonical = canonical

    def create_module(self, spec):  # noqa: ANN001 -- importlib protocol
        return None

    def exec_module(self, module: ModuleType) -> None:
        canonical = self._canonical
        flat = module.__spec__.name  # type: ignore[union-attr]

        def _forward(name: str):  # noqa: ANN202 -- PEP 562 module __getattr__
            target = sys.modules.get(canonical)
            if target is None:
                raise AttributeError(name)
            return getattr(target, name)

        module.__getattr__ = _forward  # type: ignore[attr-defined]
        importlib.import_module(canonical)
        sys.modules[flat] = sys.modules[canonical]


class FlatImportAliasFinder(importlib.abc.MetaPathFinder):
    """``sys.meta_path`` finder: ``<flat root>.*`` -> the ``<root>.<flat root>.*`` object."""

    def __init__(
        self,
        root: str = ROOT,
        flat_roots: Iterable[str] = FLAT_ROOTS,
        umbrella: str | None = UMBRELLA,
    ) -> None:
        self.root = root
        self.flat_roots = frozenset(flat_roots)
        self.umbrella = umbrella

    def _flat_name(self, fullname: str) -> str | None:
        """``services.x`` or ``<umbrella>.services.x`` -> ``services.x``; else None."""
        head, _, rest = fullname.partition(".")
        if head == self.umbrella and rest:
            head, _, _ = rest.partition(".")
            fullname = rest
        return fullname if head in self.flat_roots else None

    def find_spec(self, fullname: str, path=None, target=None):  # noqa: ANN001 -- protocol
        flat = self._flat_name(fullname)
        if flat is None:
            return None
        canonical = f"{self.root}.{flat}"
        # LOCATE only -- importlib.util.find_spec imports the canonical module's
        # parents but never executes the module itself, so this stays free of the
        # side effect that made _bootstrap._find_spec discard our spec (see the
        # module docstring). The import happens in _AliasLoader.exec_module.
        try:
            cspec = importlib.util.find_spec(canonical)
        except ModuleNotFoundError as exc:
            missing = exc.name or ""
            # A parent of the canonical module does not exist: not our case --
            # let normal resolution find the flat package, or fail normally.
            if missing == canonical or canonical.startswith(missing + "."):
                return None
            # A dependency missing INSIDE a parent package. Propagate: falling
            # through would report the wrong missing module.
            raise
        except ValueError:
            # sys.modules[canonical].__spec__ is None (a hand-built module):
            # nothing to alias onto.
            return None
        if cspec is None:
            return None
        spec = importlib.util.spec_from_loader(
            fullname, _AliasLoader(canonical), origin=cspec.origin
        )
        # Keep package-ness so `import <flat>.<sub>` consults a parent __path__ --
        # including while the placeholder sits under the flat name mid-import.
        if cspec.submodule_search_locations is not None:
            spec.submodule_search_locations = list(cspec.submodule_search_locations)
        return spec

    def __repr__(self) -> str:
        return f"FlatImportAliasFinder(root={self.root!r}, flat_roots={sorted(self.flat_roots)})"


def install(
    root: str = ROOT,
    flat_roots: Iterable[str] = FLAT_ROOTS,
    umbrella: str | None = UMBRELLA,
) -> FlatImportAliasFinder:
    """Install the finder at the front of ``sys.meta_path``. Idempotent per root."""
    for existing in sys.meta_path:
        if isinstance(existing, FlatImportAliasFinder) and existing.root == root:
            return existing
    finder = FlatImportAliasFinder(root, flat_roots, umbrella)
    sys.meta_path.insert(0, finder)
    return finder


def uninstall(root: str = ROOT) -> None:
    """Remove the finder for ``root`` (tests; step 5)."""
    sys.meta_path[:] = [
        f for f in sys.meta_path if not (isinstance(f, FlatImportAliasFinder) and f.root == root)
    ]
