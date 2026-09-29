"""Regression guard for the ``sync_metrics`` table, dropped 2026-09-28.

Follow-up to ``20260928_231739_drop_the_sync_metrics_table_orphaned_by_the_sync_service_removal``
(Glad-Labs/poindexter#1114).

``SyncService`` created the table lazily, wrote one newsletter snapshot into it
per pull and read the latest row back. Glad-Labs/poindexter#1112 deleted the
class, which left the table with no writer and no reader. The baseline schema
still creates it (it is a frozen snapshot), and this migration drops it.

This is the first post-baseline migration to drop a table, so these tests pin
the choices its docstring records: the statement names only ``sync_metrics``,
never cascades, ``down()`` restores the baseline's columns, and nothing in the
backend or the dashboards refers to the table again.
"""

from __future__ import annotations

import ast
import logging
import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_BACKEND_PKG = _REPO / "src" / "cofounder_agent" / "poindexter"
_MIGRATIONS_DIR = _BACKEND_PKG / "services" / "migrations"
_BASELINE_SCHEMA = _MIGRATIONS_DIR / "0000_baseline.schema.sql"
_GRAFANA = _REPO / "infrastructure" / "grafana"
_MIGRATION = (
    _MIGRATIONS_DIR
    / "20260928_231739_drop_the_sync_metrics_table_orphaned_by_the_sync_service_removal.py"
)

# The backend package holds ~800 non-migration modules and Grafana ships a
# dozen dashboards. A scan that visits far fewer has lost its root, so its
# "nothing refers to the table" verdict means nothing.
_MIN_PY_SCANNED = 500
_MIN_DASHBOARDS_SCANNED = 5


def _load(path: Path, name: str):
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    # The filename starts with a digit, so it is not importable by name.
    return _load(_MIGRATION, "drop_sync_metrics_under_test")


def _sql_literals(function_name: str) -> list[str]:
    """String literals in ``function_name``'s body, docstring excluded."""
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            return [
                n.value
                for stmt in body
                for n in ast.walk(stmt)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
    raise AssertionError(f"{function_name}() not found in {_MIGRATION.name}")


def test_up_drops_only_sync_metrics_and_never_cascades() -> None:
    drops = [
        stmt
        for literal in _sql_literals("up")
        for stmt in re.findall(r"DROP\s+\w+[^;]*", literal, re.IGNORECASE)
    ]
    assert [" ".join(d.split()) for d in drops] == ["DROP TABLE IF EXISTS sync_metrics"], (
        "up() must drop exactly one table, by name, with IF EXISTS and no CASCADE: "
        f"a CASCADE would silently take an operator's own view over the table with it. Found {drops!r}"
    )


# ---------------------------------------------------------------------------
# up() / down() behaviour, against a recording fake pool
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, *, present: bool, rows: int = 0) -> None:
        self._present = present
        self._rows = rows
        self.fetchval_calls: list[str] = []
        self.execute_calls: list[str] = []

    async def fetchval(self, sql: str, *args):
        self.fetchval_calls.append(sql)
        if "to_regclass" in sql:
            return self._present
        if "count(*)" in sql:
            return self._rows
        raise AssertionError(f"unexpected query: {sql!r}")

    async def execute(self, sql: str, *args) -> None:
        self.execute_calls.append(sql)


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_up_drops_the_table_and_logs_the_rows_it_removed(migration, caplog) -> None:
    conn = _FakeConn(present=True, rows=138)
    with caplog.at_level(logging.INFO):
        await migration.up(_FakePool(conn))

    assert [" ".join(s.split()) for s in conn.execute_calls] == [
        "DROP TABLE IF EXISTS sync_metrics"
    ]
    assert any("138 row(s)" in r.getMessage() for r in caplog.records), (
        "the migration log must record how many rows it dropped"
    )


@pytest.mark.asyncio
async def test_up_is_a_noop_where_the_table_is_already_gone(migration, caplog) -> None:
    conn = _FakeConn(present=False)
    with caplog.at_level(logging.INFO):
        await migration.up(_FakePool(conn))  # must not raise

    # No row count is taken for a table that is not there, and the statement
    # stays IF EXISTS so a re-run is harmless.
    assert not any("count(*)" in q for q in conn.fetchval_calls)
    assert [" ".join(s.split()) for s in conn.execute_calls] == [
        "DROP TABLE IF EXISTS sync_metrics"
    ]
    assert any("not present" in r.getMessage() for r in caplog.records)


def _normalise_columns(ddl_body: str) -> list[tuple[str, str]]:
    """``[(name, type)]`` from a CREATE TABLE column list, spelling-insensitive."""
    columns: list[tuple[str, str]] = []
    for raw in ddl_body.strip().splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.upper().startswith(("PRIMARY", "CONSTRAINT")):
            continue
        name, _, rest = line.partition(" ")
        col_type = rest.lower()
        col_type = re.sub(r"\s+(not null|primary key|default .*)$", "", col_type)
        col_type = re.sub(r"\s+(not null|primary key)$", "", col_type)
        col_type = col_type.replace("character varying", "varchar")
        col_type = "integer" if col_type == "serial" else col_type
        columns.append((name, col_type))
    return columns


@pytest.mark.asyncio
async def test_down_recreates_the_baseline_columns(migration) -> None:
    conn = _FakeConn(present=False)
    await migration.down(_FakePool(conn))

    assert len(conn.execute_calls) == 1
    ddl = conn.execute_calls[0]
    assert "CREATE TABLE IF NOT EXISTS sync_metrics" in ddl

    restored = _normalise_columns(ddl[ddl.index("(") + 1 : ddl.rindex(")")])

    baseline = _BASELINE_SCHEMA.read_text(encoding="utf-8")
    block = re.search(
        r"CREATE TABLE IF NOT EXISTS public\.sync_metrics \((?P<body>.*?)\n\);", baseline, re.S
    )
    assert block, "sync_metrics is no longer in the baseline schema; re-derive this test"
    assert restored == _normalise_columns(block.group("body")), (
        "down() must recreate the table with the columns the baseline defines"
    )


# ---------------------------------------------------------------------------
# nothing refers to the table again
# ---------------------------------------------------------------------------


def test_nothing_in_the_backend_or_the_dashboards_refers_to_the_table() -> None:
    """Why the table is dead, pinned so a new reader or writer can't slip in.

    If code starts using ``sync_metrics`` again, prod has lost the table to this
    migration and a fresh install drops it during migration, so the new code
    would fail at first use. Add the table back through a normal migration and
    retire this guard deliberately instead.
    """
    python_files = [
        p
        for p in sorted(_BACKEND_PKG.rglob("*.py"))
        if _MIGRATIONS_DIR not in p.parents
    ]
    dashboards = sorted(_GRAFANA.rglob("*.json"))
    assert len(python_files) >= _MIN_PY_SCANNED, (
        f"scanned only {len(python_files)} backend modules under {_BACKEND_PKG} — the "
        "scan root moved, so this guard is blind."
    )
    assert len(dashboards) >= _MIN_DASHBOARDS_SCANNED, (
        f"scanned only {len(dashboards)} Grafana JSON files under {_GRAFANA} — the "
        "scan root moved, so this guard is blind."
    )
    pattern = re.compile(r"\bsync_metrics\b")
    hits = [
        str(p.relative_to(_REPO))
        for p in (*python_files, *dashboards)
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not hits, (
        f"sync_metrics is referenced again by {hits} — it was dropped as an orphan "
        "(migration 20260928_231739, poindexter#1114)."
    )
