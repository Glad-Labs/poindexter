"""Unit tests for the webhook dispatcher (Phase 1)."""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.integrations import registry as registry_module
from services.integrations import webhook_dispatcher


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self._row: dict[str, Any] | None = None

    def set_row(self, row: dict[str, Any] | None):
        self._row = row

    async def fetchrow(self, query, *args):
        return self._row

    async def execute(self, query, *args):
        self.executes.append((query, args))


class _FakeDBService:
    def __init__(self, pool: _FakePool):
        self.pool = pool


class _FakeSiteConfig:
    def __init__(self, secrets: dict[str, str]):
        self._secrets = secrets

    async def get_secret(self, key: str, default: str = "") -> str | None:
        return self._secrets.get(key)


class _FakeRequest:
    def __init__(self, body: bytes, headers: dict[str, str]):
        self._body = body
        self.headers = {k.lower(): v for k, v in headers.items()}

    async def body(self) -> bytes:
        return self._body


@pytest.fixture(autouse=True)
def _clear_registry():
    saved = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved)


def _row(**overrides) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "test_hook",
        "direction": "inbound",
        "handler_name": "echo",
        "path": None,
        "url": None,
        "signing_algorithm": "none",
        "secret_key_ref": None,
        "event_filter": {},
        "enabled": True,
        "config": {},
        "metadata": {},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_unknown_name_returns_404():
    pool = _FakePool()
    pool.set_row(None)
    db = _FakeDBService(pool)
    req = _FakeRequest(b"{}", {})
    with pytest.raises(HTTPException) as exc:
        await webhook_dispatcher.dispatch_inbound(
            "missing", req, db_service=db, site_config=_FakeSiteConfig({})
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_disabled_row_returns_404():
    pool = _FakePool()
    pool.set_row(_row(enabled=False))
    db = _FakeDBService(pool)
    req = _FakeRequest(b"{}", {})
    with pytest.raises(HTTPException) as exc:
        await webhook_dispatcher.dispatch_inbound(
            "test_hook", req, db_service=db, site_config=_FakeSiteConfig({})
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_outbound_row_rejected_on_inbound_path():
    pool = _FakePool()
    pool.set_row(_row(direction="outbound", url="https://example.com"))
    db = _FakeDBService(pool)
    req = _FakeRequest(b"{}", {})
    with pytest.raises(HTTPException) as exc:
        await webhook_dispatcher.dispatch_inbound(
            "test_hook", req, db_service=db, site_config=_FakeSiteConfig({})
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_none_signing_dispatches_happy_path():
    @registry_module.register_handler("webhook", "echo")
    async def echo(payload, *, site_config, row, pool):
        return {"echoed": payload}

    pool = _FakePool()
    pool.set_row(_row())
    db = _FakeDBService(pool)
    req = _FakeRequest(b'{"hello":"world"}', {})
    result = await webhook_dispatcher.dispatch_inbound(
        "test_hook", req, db_service=db, site_config=_FakeSiteConfig({})
    )
    assert result == {"ok": True, "name": "test_hook", "echoed": {"hello": "world"}}
    # One UPDATE for record_success (no pre-dispatch writes for none-signing path)
    assert len(pool.executes) == 1
    assert "total_success" in pool.executes[0][0]


@pytest.mark.asyncio
async def test_hmac_sha256_valid():
    @registry_module.register_handler("webhook", "echo")
    async def echo(payload, *, site_config, row, pool):
        return {}

    secret = "s3cr3t"
    body = b'{"x":1}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    pool = _FakePool()
    pool.set_row(
        _row(
            signing_algorithm="hmac-sha256",
            secret_key_ref="ls_secret",
        )
    )
    db = _FakeDBService(pool)
    req = _FakeRequest(body, {"X-Signature": sig})
    result = await webhook_dispatcher.dispatch_inbound(
        "test_hook", req,
        db_service=db,
        site_config=_FakeSiteConfig({"ls_secret": secret}),
    )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_hmac_sha256_tampered_body_rejected():
    @registry_module.register_handler("webhook", "echo")
    async def echo(payload, *, site_config, row, pool):
        return {}

    secret = "s3cr3t"
    good_body = b'{"x":1}'
    bad_body = b'{"x":2}'
    sig = hmac.new(secret.encode(), good_body, hashlib.sha256).hexdigest()

    pool = _FakePool()
    pool.set_row(_row(signing_algorithm="hmac-sha256", secret_key_ref="ls_secret"))
    db = _FakeDBService(pool)
    req = _FakeRequest(bad_body, {"X-Signature": sig})
    with pytest.raises(HTTPException) as exc:
        await webhook_dispatcher.dispatch_inbound(
            "test_hook", req,
            db_service=db,
            site_config=_FakeSiteConfig({"ls_secret": secret}),
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_secret_not_configured_returns_503():
    pool = _FakePool()
    pool.set_row(_row(signing_algorithm="hmac-sha256", secret_key_ref="missing"))
    db = _FakeDBService(pool)
    req = _FakeRequest(b"{}", {"X-Signature": "x"})
    with pytest.raises(HTTPException) as exc:
        await webhook_dispatcher.dispatch_inbound(
            "test_hook", req,
            db_service=db,
            site_config=_FakeSiteConfig({}),
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_bearer_auth_valid():
    @registry_module.register_handler("webhook", "echo")
    async def echo(payload, *, site_config, row, pool):
        return {}

    pool = _FakePool()
    pool.set_row(_row(signing_algorithm="bearer", secret_key_ref="am_token"))
    db = _FakeDBService(pool)
    req = _FakeRequest(b"{}", {"Authorization": "Bearer abc123"})
    result = await webhook_dispatcher.dispatch_inbound(
        "test_hook", req,
        db_service=db,
        site_config=_FakeSiteConfig({"am_token": "abc123"}),
    )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_svix_signature_with_v1_prefix():
    @registry_module.register_handler("webhook", "echo")
    async def echo(payload, *, site_config, row, pool):
        return {}

    secret = "svix_secret"
    body = b'{"type":"email.opened"}'
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    pool = _FakePool()
    pool.set_row(_row(signing_algorithm="svix", secret_key_ref="resend_secret"))
    db = _FakeDBService(pool)
    req = _FakeRequest(body, {"Svix-Signature": f"v1,{digest}"})
    result = await webhook_dispatcher.dispatch_inbound(
        "test_hook", req,
        db_service=db,
        site_config=_FakeSiteConfig({"resend_secret": secret}),
    )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_handler_exception_becomes_500_and_records_failure():
    @registry_module.register_handler("webhook", "exploder")
    async def exploder(payload, *, site_config, row, pool):
        raise RuntimeError("kaboom")

    pool = _FakePool()
    pool.set_row(_row(handler_name="exploder"))
    db = _FakeDBService(pool)
    req = _FakeRequest(b"{}", {})
    with pytest.raises(HTTPException) as exc:
        await webhook_dispatcher.dispatch_inbound(
            "test_hook", req, db_service=db, site_config=_FakeSiteConfig({})
        )
    assert exc.value.status_code == 500
    # Failure recorded: one UPDATE writing last_error
    assert any("last_error" in q for q, _ in pool.executes)


@pytest.mark.asyncio
async def test_unknown_handler_becomes_500():
    pool = _FakePool()
    pool.set_row(_row(handler_name="does_not_exist"))
    db = _FakeDBService(pool)
    req = _FakeRequest(b"{}", {})
    with pytest.raises(HTTPException) as exc:
        await webhook_dispatcher.dispatch_inbound(
            "test_hook", req, db_service=db, site_config=_FakeSiteConfig({})
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_row_counters_updated_on_success():
    @registry_module.register_handler("webhook", "echo")
    async def echo(payload, *, site_config, row, pool):
        return {}

    pool = _FakePool()
    pool.set_row(_row())
    db = _FakeDBService(pool)
    req = _FakeRequest(b"{}", {})
    await webhook_dispatcher.dispatch_inbound(
        "test_hook", req, db_service=db, site_config=_FakeSiteConfig({})
    )
    assert len(pool.executes) == 1
    q, args = pool.executes[0]
    assert "total_success" in q
    assert args[0] == "00000000-0000-0000-0000-000000000001"
