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
    # during test collection unless load_all() is called. Keep the
    # list alphabetized inside the import block; surface comments below
    # provide a quick reference for which handler owns which surface.

    # webhook.* surface: webhook_alertmanager, webhook_revenue, webhook_subscriber
    # outbound.* surface: outbound_discord, outbound_telegram, outbound_vercel_isr
    # publishing.* surface: publishing_bluesky, publishing_mastodon
    # retention.* surface: retention_downsample, retention_summarize_to_table, retention_ttl_prune
    # tap.* surface: tap_builtin_topic_source, tap_corsair_csv,
    #                tap_external_metrics_writer, tap_singer_subprocess
    from services.integrations.handlers import (  # noqa: F401
        outbound_discord,
        outbound_telegram,
        outbound_vercel_isr,
        publishing_bluesky,
        publishing_mastodon,
        retention_downsample,
        retention_summarize_to_table,
        retention_ttl_prune,
        tap_builtin_topic_source,
        tap_corsair_csv,
        tap_external_metrics_writer,
        tap_singer_subprocess,
        webhook_alertmanager,
        webhook_revenue,
        webhook_subscriber,
    )

    # Reference the imported names so static analyzers don't drop them
    # — the import is purely for the @register_handler side effects.
    _ = (
        webhook_alertmanager, webhook_revenue, webhook_subscriber,
        outbound_discord, outbound_telegram, outbound_vercel_isr,
        publishing_bluesky, publishing_mastodon,
        retention_downsample, retention_summarize_to_table, retention_ttl_prune,
        tap_builtin_topic_source, tap_corsair_csv,
        tap_external_metrics_writer, tap_singer_subprocess,
    )

    logger.info("integrations.handlers.load_all: handler modules imported")
