"""poindexter -- the open-source AI content pipeline, as one importable package.

Everything the backend ships lives under this root: ``poindexter.services``,
``poindexter.plugins``, ``poindexter.modules``, ``poindexter.utils``,
``poindexter.routes``, ``poindexter.schemas``, ``poindexter.config``,
``poindexter.tasks``, ``poindexter.brain`` (the standalone watchdog daemon),
``poindexter.cli`` and ``poindexter.memory``. The flat spellings those packages
had before Glad-Labs/poindexter#1046 (``import services.x``) are gone: no alias,
no stub, one module object per name.

Filesystem note: the package sits at ``src/cofounder_agent/poindexter/`` because
``src/cofounder_agent`` is the worker image's build context and the process
working directory (``main.py``, ``tests/``, ``skills/`` live beside it). The
import namespace does not depend on that -- callers always write
``from poindexter.services.x import ...``.
"""
