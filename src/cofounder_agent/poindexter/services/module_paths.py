"""The one seam a project module named by *string* goes through.

Glad-Labs/poindexter#1046 moved the backend under ``poindexter.*``. The plugin
registry's core samples, ``atom_registry``'s package walk, ``http_client``'s
wiring list, route registration, the import audit and the CLI's lazy provider
load all name modules as strings -- paths that fail at runtime rather than
import time -- so step 1 of the epic routed them through here before anything
moved. Since step 5 there is exactly one spelling, and this module's job is to
keep it that way:

* :func:`resolve_module_path` returns a canonical path unchanged, passes
  third-party paths through, and REFUSES the retired flat spelling
  (``services.x``) with a ``ModuleNotFoundError`` that names the fix. A stale
  string in a config row or a third-party plugin's entry point fails at the
  seam with an actionable message instead of a bare ``No module named
  'services'`` from deep inside importlib.
* :func:`import_module_path` / :func:`import_object_path` are ``importlib`` for
  such a path (``"module.path"`` / ``"module.path:attr"``), raising exactly what
  importlib raises so callers keep their existing ``except`` policy.

``tests/unit/services/test_module_paths.py`` AST-walks the wired files and
imports every string path they name (with a floor so a refactor cannot blind
it), and pins the epic's definition of done on the real tree: no flat root is
importable as a top-level package.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

__all__ = [
    "PROJECT_ROOTS",
    "ROOT_PACKAGE",
    "import_module_path",
    "import_object_path",
    "resolve_module_path",
    "resolve_object_path",
]

#: The package every backend module lives under.
ROOT_PACKAGE: str = "poindexter"

#: The subpackages of :data:`ROOT_PACKAGE` that used to be flat top-level
#: packages of ``src/cofounder_agent``. A dotted path whose FIRST segment is one
#: of these is the retired flat spelling. ``test_module_paths`` asserts that none
#: of them is importable as a top-level package and that each imports under the
#: root.
PROJECT_ROOTS: frozenset[str] = frozenset(
    {"services", "plugins", "modules", "utils", "routes", "schemas", "config", "tasks", "brain"}
)


def _split(dotted: str) -> list[str]:
    if not dotted or not isinstance(dotted, str):
        raise ValueError(f"module path must be a non-empty string, got {dotted!r}")
    return dotted.split(".")


def resolve_module_path(dotted: str) -> str:
    """Return ``dotted`` when it is importable as spelled; refuse the flat spelling.

    Canonical (``poindexter.services.x``) and third-party paths pass through
    untouched. A path whose first segment is a retired flat root raises
    ``ModuleNotFoundError`` -- so an existing ``except ImportError`` policy still
    applies -- with the canonical spelling in the message.
    """
    head = _split(dotted)[0]
    if head in PROJECT_ROOTS:
        raise ModuleNotFoundError(
            f"{dotted!r} spells a retired flat import root; the backend lives under "
            f"{ROOT_PACKAGE!r} since Glad-Labs/poindexter#1046 -- use "
            f"'{ROOT_PACKAGE}.{dotted}'",
            name=dotted,
        )
    return dotted


def resolve_object_path(spec: str) -> tuple[str, str]:
    """Split an entry-point style ``"module.path:attr"`` into a resolved pair.

    Raises ``ValueError`` when the ``:attr`` half is missing -- a spec that
    names no attribute is a data error, never something to guess at.
    """
    module_path, sep, attr = spec.partition(":")
    if not sep or not attr or not module_path:
        raise ValueError(f"expected 'module.path:attr', got {spec!r}")
    return resolve_module_path(module_path), attr


def import_module_path(dotted: str) -> ModuleType:
    """``importlib.import_module`` for a project path.

    Raises exactly what ``importlib.import_module`` raises -- callers keep
    their existing ``except`` policy.
    """
    return importlib.import_module(resolve_module_path(dotted))


def import_object_path(spec: str) -> Any:
    """Import ``"module.path:attr"`` and return the attribute.

    ``AttributeError`` propagates when the module imports but lacks ``attr``:
    that is the stale-class-name failure the registry completeness test exists
    to catch, and it must stay loud.
    """
    module_path, attr = resolve_object_path(spec)
    return getattr(importlib.import_module(module_path), attr)
