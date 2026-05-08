"""Unit tests for the outbound webhook dispatcher (Phase 1b)."""

from __future__ import annotations

from typing import Any

import pytest

from services.integrations import outbound_dispatcher
from services.integrations import registry as registry_module


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self._row: dict[str, Any] | None = None

    def set_row(self, row):
        self._row = row

    async def fetchrow(self, query, *args):
        return self._row

    async def execute(self, query, *args):
        self.executes.append((query, args))


class _FakeDBService:
    def __init__(self, pool):
        self.pool = pool


class _FakeSiteConfig:
    async def get_secret(self, key, default=""):
        return None


def _row(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000099",
        "name": "test_out",
        "direction": "outbound",
        "handler_name": "echo_out",
        "path": None,
        "url": "https://example.com/hook",
        "signing_algorithm": "none",
        "secret_key_ref": None,
        "event_filter": {},
        "enabled": True,
        "config": {},
        "metadata": {},
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clear_registry():
    saved = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved)


@pytest.mark.asyncio
async def test_unknown_name_raises():
    pool = _FakePool()
    pool.set_row(None)
    db = _FakeDBService(pool)
    with pytest.raises(outbound_dispatcher.OutboundWebhookError):
        await outbound_dispatcher.deliver(
            "missing", {"x": 1}, db_service=db, site_config=_FakeSiteConfig()
        )


@pytest.mark.asyncio
async def test_disabled_raises():
    pool = _FakePool()
    pool.set_row(_row(enabled=False))
    db = _FakeDBService(pool)
    with pytest.raises(outbound_dispatcher.OutboundWebhookError):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )


@pytest.mark.asyncio
async def test_inbound_row_rejected():
    pool = _FakePool()
    pool.set_row(_row(direction="inbound", url=None))
    db = _FakeDBService(pool)
    with pytest.raises(outbound_dispatcher.OutboundWebhookError):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )


@pytest.mark.asyncio
async def test_happy_path_dispatches_and_records_success():
    calls: list[dict[str, Any]] = []

    @registry_module.register_handler("outbound", "echo_out")
    async def echo_out(payload, *, site_config, row, pool):
        calls.append(
            {"payload": payload, "row_name": row["name"], "pool": pool}
        )
        return {"delivered": True}

    pool = _FakePool()
    pool.set_row(_row())
    db = _FakeDBService(pool)
    result = await outbound_dispatcher.deliver(
        "test_out", {"content": "hi"}, db_service=db, site_config=_FakeSiteConfig()
    )
    assert result == {"ok": True, "name": "test_out", "delivered": True}
    assert calls[0]["row_name"] == "test_out"
    # One UPDATE for success
    assert len(pool.executes) == 1
    assert "total_success" in pool.executes[0][0]


@pytest.mark.asyncio
async def test_handler_exception_records_failure_and_reraises():
    @registry_module.register_handler("outbound", "boom")
    async def boom(payload, *, site_config, row, pool):
        raise RuntimeError("delivery failed")

    pool = _FakePool()
    pool.set_row(_row(handler_name="boom"))
    db = _FakeDBService(pool)
    with pytest.raises(RuntimeError, match="delivery failed"):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )
    assert any("last_error" in q for q, _ in pool.executes)


@pytest.mark.asyncio
async def test_unknown_handler_records_failure():
    pool = _FakePool()
    pool.set_row(_row(handler_name="does_not_exist"))
    db = _FakeDBService(pool)
    with pytest.raises(registry_module.HandlerRegistrationError):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )
    # failure recorded on the row
    assert any("last_error" in q for q, _ in pool.executes)


@pytest.mark.asyncio
async def test_string_payload_accepted_by_contract_passthrough():
    """The dispatcher itself is payload-shape-agnostic; handlers validate."""
    @registry_module.register_handler("outbound", "passthru")
    async def passthru(payload, *, site_config, row, pool):
        return {"received_type": type(payload).__name__}

    pool = _FakePool()
    pool.set_row(_row(handler_name="passthru"))
    db = _FakeDBService(pool)
    result = await outbound_dispatcher.deliver(
        "test_out", "plain string", db_service=db, site_config=_FakeSiteConfig()
    )
    assert result["received_type"] == "str"


# ---------------------------------------------------------------------------
# Pool / DB availability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_pool_raises_outbound_error_before_handler_runs():
    """db_service.pool is None must surface as OutboundWebhookError, not
    AttributeError. The check guards the row lookup so a startup race
    (deliver called before pool init) gives the operator a clear message
    rather than a stack trace deep inside asyncpg.
    """
    handler_calls: list[Any] = []

    @registry_module.register_handler("outbound", "echo_out")
    async def echo_out(payload, *, site_config, row, pool):
        handler_calls.append(payload)
        return {}

    class _NoPoolDB:
        pool = None

    with pytest.raises(
        outbound_dispatcher.OutboundWebhookError, match="database pool unavailable"
    ):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=_NoPoolDB(), site_config=_FakeSiteConfig()
        )
    assert handler_calls == [], "handler must not run when pool is missing"


# ---------------------------------------------------------------------------
# JSONB column parsing (asyncpg returns raw JSON strings without a codec)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jsonb_string_columns_parsed_to_dicts_for_handler():
    """asyncpg returns JSONB columns as raw strings unless a codec is
    registered; the dispatcher json.loads event_filter/config/metadata
    so handlers can read them as dicts (e.g. config['chat_id']).
    """
    seen_rows: list[dict[str, Any]] = []

    @registry_module.register_handler("outbound", "captures_row")
    async def captures_row(payload, *, site_config, row, pool):
        seen_rows.append(row)
        return {}

    pool = _FakePool()
    pool.set_row(_row(
        handler_name="captures_row",
        event_filter='{"event": "post.published"}',
        config='{"chat_id": "12345", "parse_mode": "MarkdownV2"}',
        metadata='{"owner": "ops"}',
    ))
    db = _FakeDBService(pool)
    await outbound_dispatcher.deliver(
        "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
    )

    assert len(seen_rows) == 1
    row = seen_rows[0]
    assert row["event_filter"] == {"event": "post.published"}
    assert row["config"] == {"chat_id": "12345", "parse_mode": "MarkdownV2"}
    assert row["metadata"] == {"owner": "ops"}


@pytest.mark.asyncio
async def test_invalid_json_in_jsonb_column_left_as_raw_string():
    """A malformed JSONB string falls through silently — the dispatcher
    leaves the raw string so the handler raises a clearer error than a
    silent type confusion. We pin the fallback so a future tightening
    (e.g. fail-closed on parse error) is an explicit decision.
    """
    seen_rows: list[dict[str, Any]] = []

    @registry_module.register_handler("outbound", "captures_row")
    async def captures_row(payload, *, site_config, row, pool):
        seen_rows.append(row)
        return {}

    pool = _FakePool()
    pool.set_row(_row(
        handler_name="captures_row",
        config="{not valid json",
    ))
    db = _FakeDBService(pool)
    await outbound_dispatcher.deliver(
        "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
    )

    assert seen_rows[0]["config"] == "{not valid json"


@pytest.mark.asyncio
async def test_empty_string_jsonb_column_left_unchanged():
    """`if isinstance(v, str) and v` short-circuits on empty string —
    we don't call json.loads("") (which would raise) and the handler
    sees an empty string, not None.
    """
    seen_rows: list[dict[str, Any]] = []

    @registry_module.register_handler("outbound", "captures_row")
    async def captures_row(payload, *, site_config, row, pool):
        seen_rows.append(row)
        return {}

    pool = _FakePool()
    pool.set_row(_row(handler_name="captures_row", config=""))
    db = _FakeDBService(pool)
    await outbound_dispatcher.deliver(
        "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
    )
    assert seen_rows[0]["config"] == ""


# ---------------------------------------------------------------------------
# Result envelope handling (non-dict returns)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_returning_none_yields_bare_ok_envelope():
    """`if isinstance(result, dict)` guards the .update() call — handlers
    that return None still get a clean {ok, name} envelope back.
    Without the guard, .update(None) would raise TypeError.
    """
    @registry_module.register_handler("outbound", "void")
    async def void(payload, *, site_config, row, pool):
        return None

    pool = _FakePool()
    pool.set_row(_row(handler_name="void"))
    db = _FakeDBService(pool)
    result = await outbound_dispatcher.deliver(
        "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
    )
    assert result == {"ok": True, "name": "test_out"}


@pytest.mark.asyncio
async def test_handler_returning_list_skips_envelope_merge():
    """Same guard — a list return doesn't crash .update() and doesn't
    bleed list entries into the envelope. Operators get the bare ok
    envelope; the list is silently dropped (handlers should return dicts).
    """
    @registry_module.register_handler("outbound", "lister")
    async def lister(payload, *, site_config, row, pool):
        return ["a", "b", "c"]

    pool = _FakePool()
    pool.set_row(_row(handler_name="lister"))
    db = _FakeDBService(pool)
    result = await outbound_dispatcher.deliver(
        "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
    )
    assert result == {"ok": True, "name": "test_out"}


# ---------------------------------------------------------------------------
# Counter-update SQL shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_sql_clears_last_error_and_increments_counter():
    """The success update must reset last_error and bump total_success —
    otherwise a stale failure message would haunt healthy rows in the
    operator dashboard. Pin the SQL contract."""
    @registry_module.register_handler("outbound", "ok")
    async def ok(payload, *, site_config, row, pool):
        return {}

    pool = _FakePool()
    pool.set_row(_row(handler_name="ok"))
    db = _FakeDBService(pool)
    await outbound_dispatcher.deliver(
        "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
    )

    assert len(pool.executes) == 1
    sql, args = pool.executes[0]
    assert "UPDATE webhook_endpoints" in sql
    assert "total_success = total_success + 1" in sql
    assert "last_error = NULL" in sql
    assert "last_success_at = now()" in sql
    # Row id is the only positional param to the success update
    assert args == ("00000000-0000-0000-0000-000000000099",)


@pytest.mark.asyncio
async def test_handler_exception_failure_message_prefixes_with_handler_exception():
    """The recorded error string must match the documented prefix so
    the operator dashboard's filter ('handler exception:%') keeps
    working. Plain text contract — pin it."""
    @registry_module.register_handler("outbound", "boom2")
    async def boom2(payload, *, site_config, row, pool):
        raise RuntimeError("kaboom")

    pool = _FakePool()
    pool.set_row(_row(handler_name="boom2"))
    db = _FakeDBService(pool)
    with pytest.raises(RuntimeError, match="kaboom"):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )

    # Find the failure UPDATE — there should be exactly one (no success).
    failure_updates = [
        (sql, args) for sql, args in pool.executes if "last_error" in sql
    ]
    assert len(failure_updates) == 1
    sql, args = failure_updates[0]
    assert "total_failure = total_failure + 1" in sql
    # last_error message is the second positional arg
    assert args[1] == "handler exception: kaboom"


@pytest.mark.asyncio
async def test_unknown_handler_failure_message_names_the_handler():
    """When handler_name doesn't resolve, the recorded last_error must
    actually name the missing handler — not just say 'unknown handler'
    generically — so operators can spot a typo without re-querying.
    """
    pool = _FakePool()
    pool.set_row(_row(handler_name="ghost_handler"))
    db = _FakeDBService(pool)
    with pytest.raises(registry_module.HandlerRegistrationError):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )

    failure_updates = [
        (sql, args) for sql, args in pool.executes if "last_error" in sql
    ]
    assert len(failure_updates) == 1
    _, args = failure_updates[0]
    assert args[1] == "unknown handler: ghost_handler"
