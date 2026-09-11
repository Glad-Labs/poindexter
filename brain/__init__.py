"""Compatibility stub -- the brain daemon lives at ``poindexter.brain``
(Glad-Labs/poindexter#1046, step 2).

Importing this installs the flat-import alias finder and replaces this module in
``sys.modules`` with the canonical package, so ``import brain.x`` yields the SAME
object as ``import poindexter.brain.x``. Unlike the other flat stubs this one sits
at the repo root (where ``brain/`` used to be), so a host process that only has
the repo root on ``sys.path`` -- a script, a systemd session -- still resolves;
it puts the backend root on ``sys.path`` itself when ``poindexter`` is not
importable yet. Deleted in step 5 of the epic, once no flat spelling remains.
"""

import importlib as _importlib
import pathlib as _pathlib
import sys as _sys

try:
    from poindexter import _flat_imports as _flat_imports
except ModuleNotFoundError:
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1] / "src" / "cofounder_agent"))
    from poindexter import _flat_imports as _flat_imports

_flat_imports.install()
# Keyed on the LAST segment: this same file also loads as `cofounder_agent.brain`
# is NOT a thing (brain never lived under the umbrella), but the shape matches
# the other stubs so a reader recognises it.
_sys.modules[__name__] = _importlib.import_module(f"poindexter.{__name__.rpartition('.')[2]}")
