"""Boot-time graph_def self-heal — baseline-stamp only fully-unstamped rows.

poindexter#755: graph_def *reseed* migrations write the raw spec via
``json.dumps(CANONICAL_BLOG_GRAPH_DEF)`` with no per-node ``_contract_fp``
(so they stay importable in the migrations-smoke env, which has no atom
registry). That un-stamps the active row, which then trips the load-time
drift gate (``assert_graph_def_current``) on the next worker boot and halts
every pipeline run.

The durable fix is a boot-time idempotent step
(``ensure_active_graph_defs_stamped``) that baseline-stamps ONLY rows that are
fully unstamped, leaving already-stamped rows untouched so genuine
atom-contract drift in a stamped row is still caught by the gate.

This module tests two units:

* ``is_graph_def_fully_unstamped`` — the pure decision predicate. The critical
  case is *partial* stamping → ``False`` (never overwrite a row that carries
  any fingerprint, or we'd mask the drift the gate exists to find).
* ``ensure_active_graph_defs_stamped`` — the DB orchestration: stamp the
  fully-unstamped row, skip the stamped one (no UPDATE), and no-op when the
  registry is unavailable.
"""

from __future__ import annotations

import json

import pytest

import services.pipeline_architect as pa
from plugins.atom import AtomMeta


def _meta(name: str) -> AtomMeta:
    return AtomMeta(
        name=name,
        type="atom",
        version="1.0.0",
        description="d",
        requires=(),
        produces=(),
        inputs=(),
        outputs=(),
    )


@pytest.fixture
def registry(monkeypatch):
    """Patch the atom-meta lookup so ``stamp_graph_def`` resolves against a
    fixed fake table instead of the live registry."""
    table = {
        "atoms.draft": _meta("atoms.draft"),
        "atoms.title": _meta("atoms.title"),
    }
    monkeypatch.setattr(pa, "get_atom_meta", lambda n: table.get(n))
    return table


def _unstamped_spec() -> dict:
    return {
        "name": "canonical_blog",
        "nodes": [
            {"id": "a", "atom": "atoms.draft", "config": {}},
            {"id": "b", "atom": "atoms.title", "config": {}},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "END"}],
    }


class TestIsFullyUnstamped:
    def test_true_when_no_node_stamped(self):
        assert pa.is_graph_def_fully_unstamped(_unstamped_spec()) is True

    def test_false_when_all_nodes_stamped(self, registry):
        stamped = pa.stamp_graph_def(_unstamped_spec())
        assert pa.is_graph_def_fully_unstamped(stamped) is False

    def test_false_when_partially_stamped(self):
        # The safety case: a single stamped node means the row is NOT eligible
        # for a baseline stamp — re-stamping it would overwrite (and thus mask)
        # whatever the stamped node's true contract drift is.
        spec = _unstamped_spec()
        spec["nodes"][0]["_contract_fp"] = "abc123deadbe"
        assert pa.is_graph_def_fully_unstamped(spec) is False

    def test_false_when_no_nodes(self):
        assert pa.is_graph_def_fully_unstamped({"name": "x", "nodes": []}) is False
        assert pa.is_graph_def_fully_unstamped({"name": "x"}) is False

    def test_false_when_falsy_fp_is_treated_as_unstamped(self):
        # An empty-string / null fp is "unstamped" per assert_graph_def_current
        # (it rejects falsy _contract_fp), so a node carrying one keeps the row
        # eligible for a baseline stamp.
        spec = _unstamped_spec()
        spec["nodes"][0]["_contract_fp"] = ""
        assert pa.is_graph_def_fully_unstamped(spec) is True


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.executed: list[tuple] = []

    async def fetch(self, _sql, *_args):
        return self._rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _FakePool:
    def __init__(self, rows):
        self.conn = _FakeConn(rows)

    def acquire(self):
        return self.conn


@pytest.mark.asyncio
class TestEnsureActiveStamped:
    async def test_stamps_fully_unstamped_row(self, registry, monkeypatch):
        monkeypatch.setattr("services.atom_registry.discover", lambda: None)
        pool = _FakePool([{"slug": "canonical_blog", "graph_def": _unstamped_spec()}])

        count = await pa.ensure_active_graph_defs_stamped(pool)

        assert count == 1
        assert len(pool.conn.executed) == 1
        sql, args = pool.conn.executed[0]
        assert "UPDATE pipeline_templates" in sql
        written = json.loads(args[0])
        assert all(n["_contract_fp"] for n in written["nodes"])

    async def test_leaves_already_stamped_row_alone(self, registry, monkeypatch):
        monkeypatch.setattr("services.atom_registry.discover", lambda: None)
        stamped = pa.stamp_graph_def(_unstamped_spec())
        pool = _FakePool([{"slug": "canonical_blog", "graph_def": stamped}])

        count = await pa.ensure_active_graph_defs_stamped(pool)

        # No UPDATE issued — leaving stamped rows untouched is what keeps
        # assert_graph_def_current able to detect genuine drift.
        assert count == 0
        assert pool.conn.executed == []

    async def test_handles_graph_def_stored_as_json_string(self, registry, monkeypatch):
        monkeypatch.setattr("services.atom_registry.discover", lambda: None)
        pool = _FakePool(
            [{"slug": "canonical_blog", "graph_def": json.dumps(_unstamped_spec())}]
        )

        count = await pa.ensure_active_graph_defs_stamped(pool)

        assert count == 1
        assert len(pool.conn.executed) == 1

    async def test_skips_when_registry_unavailable(self, monkeypatch):
        def _boom():
            raise RuntimeError("no atom registry in this environment")

        monkeypatch.setattr("services.atom_registry.discover", _boom)
        pool = _FakePool([{"slug": "canonical_blog", "graph_def": _unstamped_spec()}])

        count = await pa.ensure_active_graph_defs_stamped(pool)

        assert count == 0
        assert pool.conn.executed == []

    async def test_mixed_rows_only_unstamped_updated(self, registry, monkeypatch):
        monkeypatch.setattr("services.atom_registry.discover", lambda: None)
        stamped = pa.stamp_graph_def(_unstamped_spec())
        pool = _FakePool(
            [
                {"slug": "already_stamped", "graph_def": stamped},
                {"slug": "needs_stamp", "graph_def": _unstamped_spec()},
            ]
        )

        count = await pa.ensure_active_graph_defs_stamped(pool)

        assert count == 1
        assert len(pool.conn.executed) == 1
        # Only the unstamped slug was written.
        _sql, args = pool.conn.executed[0]
        assert args[1] == "needs_stamp"
