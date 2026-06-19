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
