"""Handler modules for each integration surface.

Import side-effects register handlers via
``integrations.registry.register_handler`` decorators. Adding a new
handler = add a new module here and add an import line in :func:`load_all`.

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
    # during test collection unless load_all() is called. Keep this
    # list alphabetized by surface then handler name.

    # webhook.*
    from services.integrations.handlers import webhook_alertmanager  # noqa: F401
    from services.integrations.handlers import webhook_revenue  # noqa: F401
    from services.integrations.handlers import webhook_subscriber  # noqa: F401

    # outbound.*
    from services.integrations.handlers import outbound_discord  # noqa: F401
    from services.integrations.handlers import outbound_telegram  # noqa: F401
    from services.integrations.handlers import outbound_vercel_isr  # noqa: F401

    logger.info("integrations.handlers.load_all: handler modules imported")
