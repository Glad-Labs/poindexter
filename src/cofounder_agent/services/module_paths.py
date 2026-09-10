"""The one place a project module is named by string.

Every dynamic import of our own code -- the plugin registry's core samples,
``atom_registry``'s package walk, ``http_client``'s wiring list, route
registration, the import audit, the CLI's lazy provider load -- goes through
this module instead of calling ``importlib.import_module("services.x")``
directly. That is step 1 of Glad-Labs/poindexter#1046.

WHY
---
``src/cofounder_agent`` is on ``sys.path`` as a *root* today, so the backend's
packages import as flat top-level names: ``services``, ``plugins``,
``modules``, ``utils``, ``routes``, ``schemas``, ``config``, ``tasks``. That is
why ``pip install poindexter`` has never worked -- a wheel cannot install those
names without shadowing the real PyPI packages ``utils`` and ``config`` (and
vice versa), and a wheel that nests them under a package cannot satisfy the
code's own ``from services import ...``. The fix is to move the tree under
``poindexter.*``. A mechanical import rewrite handles the ~6,400 ``import``
statements; it cannot see the ~250 places a module is named by *string*.
Those are the ones that fail at runtime, not import time, so they are moved
behind this seam first, while the tree is still flat and every path is
trivially testable.

CONTRACT
--------
* Both spellings are accepted everywhere, today: ``"services.x"`` and
  ``"poindexter.services.x"`` resolve to whatever is importable in this
  process. Callers may adopt the new spelling before the move.
* :data:`ROOT_PACKAGE` is the single switch. It is ``""`` while the tree is
  flat and becomes ``"poindexter"`` in step 2 of the epic. Nothing else
  changes.
* Only *project* paths are touched. A first segment outside
  :data:`PROJECT_ROOTS` (``os.path``, ``langchain_core.x``, a third-party
  plugin's ``acme_taps.slack``) passes through untouched -- and so does the
  CLI's own ``poindexter.cli.app``, because ``cli`` is not a project root.
* ``brain`` IS a project root (decided 2026-09-10: it moves to
  ``poindexter.brain``). It is a repo-root sibling of ``src/cofounder_agent``
  today; the CLI imports it at 12 sites and the backend at 25, ``brain`` is a
  taken name on PyPI, and the dependency is bidirectional -- so it ships inside
  the one distribution, under the namespace.

``tests/unit/services/test_module_paths.py`` walks every string-named
project module in the wired call sites and asserts each resolves and
imports under BOTH spellings, so a new string path in those files is covered
the moment it is added.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

__all__ = [
    "PROJECT_ROOTS",
    "ROOT_PACKAGE",
    "flat_module_path",
    "import_module_path",
    "import_object_path",
    "is_project_module_path",
    "resolve_module_path",
    "resolve_object_path",
]

#: The package the backend will live under after step 2 of poindexter#1046.
#: ``""`` while the tree is flat. Flipping this is the whole of that step's
#: runtime change; every string path in the codebase already routes here.
ROOT_PACKAGE: str = ""

#: The future root's name. Recognised on input at all times so callers can
#: write ``poindexter.services.x`` before the move.
FUTURE_ROOT: str = "poindexter"

#: The flat top-level packages of ``src/cofounder_agent`` -- the set that
#: moves. Keep this in step with the tree; ``test_module_paths`` asserts every
#: one is a real package.
PROJECT_ROOTS: frozenset[str] = frozenset(
    {"services", "plugins", "modules", "utils", "routes", "schemas", "config", "tasks", "brain"}
)


def _split(dotted: str) -> list[str]:
    if not dotted or not isinstance(dotted, str):
        raise ValueError(f"module path must be a non-empty string, got {dotted!r}")
    return dotted.split(".")


def flat_module_path(dotted: str) -> str:
    """Return the canonical *flat* spelling of a project module path.

    ``"poindexter.services.x"`` -> ``"services.x"``; ``"services.x"`` is
    returned unchanged; anything that is not a project path is returned
    unchanged. Use this for keys that must be stable across the migration
    (``sys.modules`` lookups, baseline files, log filters).
    """
    parts = _split(dotted)
    if len(parts) >= 2 and parts[0] == FUTURE_ROOT and parts[1] in PROJECT_ROOTS:
        return ".".join(parts[1:])
    return dotted


def is_project_module_path(dotted: str) -> bool:
    """True when ``dotted`` names one of our own modules, in either spelling."""
    try:
        return _split(flat_module_path(dotted))[0] in PROJECT_ROOTS
    except ValueError:
        return False


def resolve_module_path(dotted: str) -> str:
    """Return the spelling of ``dotted`` that is importable in THIS process.

    Accepts both spellings. Non-project paths pass through untouched.
    Idempotent: resolving a resolved path is a no-op.
    """
    flat = flat_module_path(dotted)
    if _split(flat)[0] not in PROJECT_ROOTS:
        return dotted
    return f"{ROOT_PACKAGE}.{flat}" if ROOT_PACKAGE else flat


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
    """``importlib.import_module`` for a project path, in either spelling.

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
