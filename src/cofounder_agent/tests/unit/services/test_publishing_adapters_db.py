"""Unit tests for ``services.publishing_adapters_db`` (poindexter#112)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from services.publishing_adapters_db import (
    PublishingAdapterRow,
    load_enabled_publishers,
)


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]] | Exception):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def fetch(self, query, *args):
        if isinstance(self._rows, Exception):
            raise self._rows
        return self._rows


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]] | Exception):
        self._rows = rows

    def acquire(self):
        return _FakeConn(self._rows)


_BLUESKY_ID = uuid4()
_MASTODON_ID = uuid4()


def _bluesky_row(**overrides) -> dict[str, Any]:
    base = {
        "id": _BLUESKY_ID,
        "name": "bluesky_main",
        "platform": "bluesky",
        "handler_name": "bluesky",
        "credentials_ref": "bluesky_",
        "enabled": True,
        "config": {},
        "metadata": {"seeded_by": "poindexter#112"},
    }
    base.update(overrides)
    return base


class TestLoadEnabledPublishers:
    @pytest.mark.asyncio
    async def test_pool_none_returns_empty(self):
        assert await load_enabled_publishers(None) == []

    @pytest.mark.asyncio
    async def test_happy_path_returns_frozen_rows(self):
        pool = _FakePool([_bluesky_row()])
        rows = await load_enabled_publishers(pool)
        assert len(rows) == 1
        row = rows[0]
        assert isinstance(row, PublishingAdapterRow)
        assert row.name == "bluesky_main"
        assert row.platform == "bluesky"
        assert row.handler_name == "bluesky"
        assert row.enabled is True
        assert row.config == {}
        assert row.metadata == {"seeded_by": "poindexter#112"}
        # frozen — mutating the dataclass should fail.
        with pytest.raises(Exception):
            row.name = "x"  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_jsonb_string_parsed(self):
        # Some asyncpg codec setups return jsonb columns as raw JSON
        # strings — the loader must coerce them to dicts.
        pool = _FakePool(
            [_bluesky_row(config=json.dumps({"foo": "bar"}), metadata="{}")]
        )
        rows = await load_enabled_publishers(pool)
        assert rows[0].config == {"foo": "bar"}
        assert rows[0].metadata == {}

    @pytest.mark.asyncio
    async def test_db_failure_returns_empty(self):
        pool = _FakePool(RuntimeError("table does not exist"))
        rows = await load_enabled_publishers(pool)
        assert rows == []

    @pytest.mark.asyncio
    async def test_as_dict_round_trip(self):
        row = PublishingAdapterRow(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            name="bluesky_main",
            platform="bluesky",
            handler_name="bluesky",
            credentials_ref="bluesky_",
            enabled=True,
            config={"k": "v"},
            metadata={},
        )
        d = row.as_dict()
        assert d["name"] == "bluesky_main"
        assert d["handler_name"] == "bluesky"
        assert d["config"] == {"k": "v"}
        # mutating the returned dict must not affect the frozen row.
        d["config"]["k"] = "changed"
        assert row.config == {"k": "v"}
