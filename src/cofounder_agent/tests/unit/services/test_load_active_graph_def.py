"""Unit tests for load_active_graph_def (atom-cutover Plan 4, #355).
Pool stub — no live DB."""

from __future__ import annotations

import json

import pytest

from services.pipeline_templates import load_active_graph_def


class _Conn:
    def __init__(self, row):
        self._row = row

    async def fetchrow(self, sql, *args):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_a):
        return False


class _Pool:
    def __init__(self, row):
        self._conn = _Conn(row)

    def acquire(self):
        return _Acquire(self._conn)


@pytest.mark.unit
class TestLoadActiveGraphDef:
    async def test_returns_parsed_dict_from_str_jsonb(self):
        spec = {"name": "x", "entry": "a", "nodes": [{"id": "a", "atom": "qa.aggregate"}], "edges": []}
        pool = _Pool({"graph_def": json.dumps(spec)})
        out = await load_active_graph_def(pool, "canonical_blog")
        assert out == spec

    async def test_returns_dict_when_already_decoded(self):
        spec = {"name": "x", "nodes": [{"id": "a", "atom": "q"}]}
        pool = _Pool({"graph_def": spec})
        out = await load_active_graph_def(pool, "canonical_blog")
        assert out == spec

    async def test_no_row_returns_none(self):
        pool = _Pool(None)
        assert await load_active_graph_def(pool, "canonical_blog") is None

    async def test_empty_graph_def_returns_none(self):
        # The column default is '{}' — a node-less spec is treated as absent.
        pool = _Pool({"graph_def": "{}"})
        assert await load_active_graph_def(pool, "canonical_blog") is None

    async def test_none_pool_returns_none(self):
        assert await load_active_graph_def(None, "canonical_blog") is None

    async def test_unparseable_json_returns_none(self):
        pool = _Pool({"graph_def": "{not json"})
        assert await load_active_graph_def(pool, "canonical_blog") is None
