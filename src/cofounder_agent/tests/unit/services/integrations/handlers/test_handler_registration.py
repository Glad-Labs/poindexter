"""Registration surface after the Apprise cutover."""

from __future__ import annotations

import importlib

import pytest

from services.integrations import registry
from services.integrations.handlers import load_all


def test_apprise_handler_registered_and_legacy_gone():
    load_all()
    outbound = registry.registered_names("outbound")
    assert "outbound.apprise_notify" in outbound
    assert "outbound.discord_post" not in outbound
    assert "outbound.telegram_post" not in outbound


def test_all_retention_handlers_registered_via_load_all():
    """load_all() must wire every retention handler the runtime relies on.

    Regression guard: ``retention.checkpoint_prune`` shipped with a correct
    ``@register_handler`` decorator and full unit coverage, but was omitted
    from ``load_all()``'s import list, so it was never registered in the
    worker process. Its own unit test imports the handler module *directly*
    (firing the decorator in-process), which masked the gap — ``run_retention``
    failed in production with ``HandlerRegistrationError: no handler
    registered under 'retention.checkpoint_prune'`` while every test stayed
    green. Asserting the ``load_all()`` surface directly is what catches a
    handler file that exists but isn't wired.
    """
    load_all()
    retention = registry.registered_names("retention")
    assert "retention.checkpoint_prune" in retention
    assert "retention.downsample" in retention
    assert "retention.summarize_to_table" in retention
    assert "retention.ttl_prune" in retention


def test_discord_module_deleted():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(
            "services.integrations.handlers.outbound_discord"
        )


def test_telegram_streaming_helpers_retained():
    mod = importlib.import_module(
        "services.integrations.handlers.outbound_telegram"
    )
    assert hasattr(mod, "send_telegram_message")
    assert hasattr(mod, "edit_telegram_message")
    assert not hasattr(mod, "telegram_post")


def test_outbound_discord_not_in_http_client_wiring():
    from services.http_client import WIRED_HTTP_CLIENT_MODULES

    assert (
        "services.integrations.handlers.outbound_discord"
        not in WIRED_HTTP_CLIENT_MODULES
    )
