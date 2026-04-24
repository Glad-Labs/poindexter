"""Unit tests for the integrations framework scaffolding (Phase 0)."""

from __future__ import annotations

from typing import Any

import pytest

from services.integrations import (
    HandlerRegistrationError,
    dispatch,
    lookup,
    register_handler,
    registered_names,
    resolve_secret,
)
from services.integrations import registry as registry_module


@pytest.fixture(autouse=True)
def _clear_registry():
    """Isolate tests — framework handlers register at import in real code."""
    saved = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved)


class TestHandlerRegistry:
    def test_register_and_lookup(self):
        @register_handler("webhook", "echo")
        async def echo(payload, *, site_config, row, pool):
            return ("echo", payload)

        resolved = lookup("webhook", "echo")
        assert resolved is echo

    def test_lookup_unknown_raises(self):
        with pytest.raises(HandlerRegistrationError):
            lookup("webhook", "does_not_exist")

    def test_duplicate_registration_raises(self):
        @register_handler("retention", "ttl_prune")
        async def first(payload, *, site_config, row, pool):
            return 1

        with pytest.raises(HandlerRegistrationError):

            @register_handler("retention", "ttl_prune")
            async def second(payload, *, site_config, row, pool):
                return 2

    def test_registering_same_function_twice_is_idempotent(self):
        async def handler(payload, *, site_config, row, pool):
            return "ok"

        register_handler("tap", "noop")(handler)
        register_handler("tap", "noop")(handler)  # same fn, not an error

    def test_surface_namespace_isolation(self):
        @register_handler("webhook", "shared_name")
        async def webhook_version(payload, *, site_config, row, pool):
            return "webhook"

        @register_handler("tap", "shared_name")
        async def tap_version(payload, *, site_config, row, pool):
            return "tap"

        assert lookup("webhook", "shared_name") is webhook_version
        assert lookup("tap", "shared_name") is tap_version

    def test_registered_names_filters_by_surface(self):
        @register_handler("webhook", "a")
        async def a(payload, *, site_config, row, pool):
            pass

        @register_handler("retention", "b")
        async def b(payload, *, site_config, row, pool):
            pass

        assert registered_names("webhook") == ["webhook.a"]
        assert registered_names("retention") == ["retention.b"]
        assert sorted(registered_names()) == ["retention.b", "webhook.a"]

    def test_surface_name_validation(self):
        with pytest.raises(HandlerRegistrationError):
            register_handler("", "x")
        with pytest.raises(HandlerRegistrationError):
            register_handler("web.hook", "x")
        with pytest.raises(HandlerRegistrationError):
            register_handler("webhook", "")
        with pytest.raises(HandlerRegistrationError):
            register_handler("webhook", "has.dot")


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_passes_payload_and_context(self):
        captured: dict[str, Any] = {}

        @register_handler("webhook", "capture")
        async def capture(payload, *, site_config, row, pool):
            captured["payload"] = payload
            captured["site_config"] = site_config
            captured["row"] = row
            captured["pool"] = pool
            return "ok"

        result = await dispatch(
            "webhook",
            "capture",
            {"event": "test"},
            site_config="site_config_sentinel",
            row={"name": "test_row"},
            pool="pool_sentinel",
        )
        assert result == "ok"
        assert captured["payload"] == {"event": "test"}
        assert captured["site_config"] == "site_config_sentinel"
        assert captured["row"] == {"name": "test_row"}
        assert captured["pool"] == "pool_sentinel"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_raises(self):
        with pytest.raises(HandlerRegistrationError):
            await dispatch(
                "webhook",
                "does_not_exist",
                {},
                site_config=None,
                row={},
            )


class _FakeSiteConfig:
    def __init__(self, secrets: dict[str, str]):
        self._secrets = secrets

    async def get_secret(self, key: str, default: str = "") -> str | None:
        return self._secrets.get(key, default if default else None)


class TestSecretResolver:
    @pytest.mark.asyncio
    async def test_resolves_from_secret_key_ref(self):
        sc = _FakeSiteConfig({"ls_webhook_secret": "plaintext-value"})
        row = {"secret_key_ref": "ls_webhook_secret"}
        assert await resolve_secret(row, sc) == "plaintext-value"

    @pytest.mark.asyncio
    async def test_resolves_from_credentials_ref(self):
        sc = _FakeSiteConfig({"store_creds": "json-blob"})
        row = {"credentials_ref": "store_creds"}
        assert await resolve_secret(row, sc) == "json-blob"

    @pytest.mark.asyncio
    async def test_resolves_from_auth_ref(self):
        sc = _FakeSiteConfig({"mcp_auth": "token"})
        row = {"auth_ref": "mcp_auth"}
        assert await resolve_secret(row, sc) == "token"

    @pytest.mark.asyncio
    async def test_priority_order(self):
        """secret_key_ref wins over credentials_ref wins over auth_ref."""
        sc = _FakeSiteConfig({"a": "A", "b": "B", "c": "C"})
        row = {"secret_key_ref": "a", "credentials_ref": "b", "auth_ref": "c"}
        assert await resolve_secret(row, sc) == "A"

        row = {"credentials_ref": "b", "auth_ref": "c"}
        assert await resolve_secret(row, sc) == "B"

    @pytest.mark.asyncio
    async def test_no_ref_returns_none(self):
        sc = _FakeSiteConfig({})
        assert await resolve_secret({}, sc) is None
        assert await resolve_secret({"name": "row"}, sc) is None

    @pytest.mark.asyncio
    async def test_missing_app_settings_row_returns_empty_string(self):
        sc = _FakeSiteConfig({})  # does not contain "missing_key"
        row = {"secret_key_ref": "missing_key"}
        assert await resolve_secret(row, sc) == ""
