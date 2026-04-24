"""Handler modules for each integration surface.

Import side-effects register handlers via
``integrations.handlers.register_handler`` decorators. Adding a new
handler = add a new module here and import it in :func:`load_all`.

We do eager imports rather than entry_point discovery because:

- Handlers are first-party Poindexter code, not third-party plugins.
- Explicit import is faster and easier to reason about than
  ``importlib.metadata.entry_points()`` + ``__import__``.
- Third-party plugin handlers (when they arrive) can register
  themselves via the public ``services.integrations.register_handler``
  decorator from their own ``pyproject.toml`` entry_point target.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def load_all() -> None:
    """Import every handler module so their decorators fire.

    Called once at FastAPI startup (and at scheduler startup in the
    worker process). Idempotent — subsequent calls no-op because the
    registry enforces single-registration.
    """
    # Import inside the function so module import doesn't load handlers
    # during test collection unless load_all() is called.
    # Phase 1 handlers land in follow-up commits.
    # Keep this list alphabetized.
    pass
