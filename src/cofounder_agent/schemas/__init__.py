"""Compatibility stub -- `schemas` now lives at `poindexter.schemas` (Glad-Labs/poindexter#1046, step 2).

Importing this installs the flat-import alias finder and replaces this module in
``sys.modules`` with the canonical package, so ``import schemas.x`` yields the SAME
object as ``import poindexter.schemas.x`` -- one module, two names (see
``poindexter/_flat_imports.py`` for why a ``__path__`` shim would not do). Deleted
in step 5 of the epic, once no flat spelling remains.
"""

import importlib as _importlib
import sys as _sys

from poindexter import _flat_imports as _flat_imports

_flat_imports.install()
# Keyed on the LAST segment: this same file also loads as
# `cofounder_agent.schemas` (the umbrella spelling the entry points use).
_sys.modules[__name__] = _importlib.import_module(
    f"poindexter.{__name__.rpartition('.')[2]}"
)
