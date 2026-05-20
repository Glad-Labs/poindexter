"""notify_operator must fall back to lifespan-bound SiteConfig.

Pinning the wiring fix: when callers don't have a SiteConfig in scope
(e.g. brain probes, scheduler jobs, the alert dispatcher's path) they
pass ``site_config=None``. Without a fallback, ``secret_resolver`` then
treats the row as unconfigured and every operator notification fails to
authenticate even when the bot token is correctly seeded.

The fallback consults :func:`_resolve_site_config` which reads from
``services.integrations.shared_context.get_site_config`` (the lifespan-
bound SiteConfig). Pinning this so a future refactor that moves the
fallback can't silently regress the live-fire behavior.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_notify_operator_falls_back_to_lifespan_site_config_when_caller_passes_none():
    """site_config=None must be resolved from shared_context, not propagated as None.

    Without this, ``outbound_dispatcher.deliver`` receives ``site_config=None``
    and ``secret_resolver`` short-circuits with "no site_config in scope",
    causing telegram_post / discord_post handlers to fail auth even when
    the bot token is correctly seeded in ``app_settings``.
    """
    from services.integrations import operator_notify

    lifespan_site_config = MagicMock(name="lifespan_site_config")
    db_service = MagicMock(name="db_service")
    db_service.pool = MagicMock(name="pool")

    deliver_mock = AsyncMock()

    with patch(
        "services.integrations.operator_notify._resolve_site_config",
        return_value=lifespan_site_config,
    ) as resolve_mock, patch(
        "services.integrations.shared_context.get_database_service",
        return_value=db_service,
    ), patch(
        "services.integrations.outbound_dispatcher.deliver",
        deliver_mock,
    ):
        await operator_notify.notify_operator("test message", critical=False)

    resolve_mock.assert_called_once()
    deliver_mock.assert_called_once()
    forwarded = deliver_mock.await_args.kwargs.get("site_config")
    assert forwarded is lifespan_site_config, (
        "notify_operator must forward the lifespan-bound SiteConfig to the "
        "outbound dispatcher; otherwise secret_resolver short-circuits and "
        "the notification fails auth despite the bot token being seeded."
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_notify_operator_does_not_clobber_explicit_caller_supplied_site_config():
    """A caller that passes its own site_config keeps that one (no surprise overwrite)."""
    from services.integrations import operator_notify

    explicit_site_config = MagicMock(name="explicit_site_config")
    db_service = MagicMock(name="db_service")
    db_service.pool = MagicMock(name="pool")

    deliver_mock = AsyncMock()

    with patch(
        "services.integrations.operator_notify._resolve_site_config",
    ) as resolve_mock, patch(
        "services.integrations.shared_context.get_database_service",
        return_value=db_service,
    ), patch(
        "services.integrations.outbound_dispatcher.deliver",
        deliver_mock,
    ):
        await operator_notify.notify_operator(
            "test message", critical=False, site_config=explicit_site_config,
        )

    resolve_mock.assert_not_called()
    forwarded = deliver_mock.await_args.kwargs.get("site_config")
    assert forwarded is explicit_site_config
