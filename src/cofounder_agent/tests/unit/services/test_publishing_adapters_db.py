"""Unit tests for ``services.publishing_adapters_db`` (poindexter#112)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from services.publishing_adapters_db import (
    PublishingAdapterRow,
    _parse_jsonb,
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

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self):
        # An enabled-publishers query that legitimately returns zero rows
        # (table present, but every adapter is disabled) must still hand
        # back an empty list — not raise, not crash on the loop body.
        pool = _FakePool([])
        assert await load_enabled_publishers(pool) == []

    @pytest.mark.asyncio
    async def test_preserves_db_row_order(self):
        # The query orders by name ASC. The loader must walk rows in the
        # order the DB hands them back — the dispatch loop relies on that
        # ordering to be deterministic across runs.
        pool = _FakePool([
            _bluesky_row(name="alpha"),
            _bluesky_row(name="bravo", id=_MASTODON_ID, platform="mastodon"),
            _bluesky_row(name="charlie"),
        ])
        rows = await load_enabled_publishers(pool)
        assert [r.name for r in rows] == ["alpha", "bravo", "charlie"]
        # Per-row fields hydrate independently — the second row's platform
        # override survives intact.
        assert rows[1].platform == "mastodon"

    @pytest.mark.asyncio
    async def test_null_credentials_ref_passes_through(self):
        # credentials_ref is nullable in the schema (an adapter can be
        # seeded ahead of the secrets it'll eventually read). The loader
        # must not coerce None to "" — downstream code distinguishes the
        # two when deciding whether to prompt for setup.
        pool = _FakePool([_bluesky_row(credentials_ref=None)])
        rows = await load_enabled_publishers(pool)
        assert rows[0].credentials_ref is None


class TestParseJsonb:
    """Direct coverage for the JSONB normalization helper.

    asyncpg's jsonb decoder is configurable per-pool; some setups hand
    back native dicts, others raw JSON strings. The helper has to absorb
    both — plus a handful of "DB returned something weird" cases — and
    always yield a dict so the frozen dataclass init doesn't blow up.
    """

    def test_none_returns_empty_dict(self):
        assert _parse_jsonb(None) == {}

    def test_empty_string_returns_empty_dict(self):
        # An empty jsonb cell shouldn't trip json.loads (which would
        # raise on ''). Early-exit on the empty case.
        assert _parse_jsonb("") == {}

    def test_invalid_json_returns_empty_dict(self):
        # Defensive: a corrupt jsonb cell falls back to {} rather than
        # raising — distribution should keep running even if one row's
        # metadata is garbage.
        assert _parse_jsonb("{not valid json") == {}

    def test_non_dict_json_returns_empty_dict(self):
        # JSON arrays / scalars are syntactically valid but not what the
        # dataclass wants. Coerce to {} so the row still hydrates.
        assert _parse_jsonb("[1, 2, 3]") == {}
        assert _parse_jsonb("42") == {}
        assert _parse_jsonb('"a string"') == {}
        assert _parse_jsonb("null") == {}

    def test_unsupported_type_returns_empty_dict(self):
        # An int/bytes/etc. column type leaking through (driver bug,
        # custom codec) shouldn't raise — fall through to {}.
        assert _parse_jsonb(42) == {}
        assert _parse_jsonb(b"{}") == {}

    def test_dict_input_returns_independent_copy(self):
        # The helper hands back a new dict so the frozen row can't be
        # mutated via the caller's reference (e.g., asyncpg's decoded
        # value cache).
        original = {"k": "v"}
        out = _parse_jsonb(original)
        assert out == {"k": "v"}
        out["k"] = "changed"
        assert original == {"k": "v"}


class TestAsDictMetadataIndependence:
    def test_as_dict_metadata_is_independent_copy(self):
        # Parallels the existing config-independence test — metadata gets
        # the same dict() copy treatment in as_dict so callers can't
        # mutate the frozen row through the returned mapping.
        row = PublishingAdapterRow(
            id=UUID("00000000-0000-0000-0000-000000000002"),
            name="mastodon_main",
            platform="mastodon",
            handler_name="mastodon",
            credentials_ref=None,
            enabled=True,
            config={},
            metadata={"seeded_by": "test"},
        )
        d = row.as_dict()
        d["metadata"]["seeded_by"] = "mutated"
        assert row.metadata == {"seeded_by": "test"}
