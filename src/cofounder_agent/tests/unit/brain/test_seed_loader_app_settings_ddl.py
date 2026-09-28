"""The brain creates app_settings in the exact shape the baseline declares (poindexter#1097).

On a compose-first install the brain's boot seed creates ``app_settings`` before
the worker has run a single migration, and the baseline's own ``CREATE TABLE
IF NOT EXISTS`` then finds the table already there. The brain's table used to
have 8 of the 14 columns, and the baseline died on ``idx_app_settings_is_active``
on every fresh compose install, while the brain's own probes that read
``is_active`` and ``owner`` failed against it too.

Every expectation here is read out of ``0000_baseline.schema.sql``, never typed
in: the column entries, the constraints the dump declares inline and the ones it
adds afterwards with ``ALTER TABLE``, and which column the dump backs with a
sequence. A squash that changes app_settings fails this test until the brain's
DDL follows. The real-Postgres proof that both orders produce the same table is
migrations-smoke's ``--brain-first`` step.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from poindexter.brain import seed_loader
from tests.unit._nonempty import nonempty

_MIGRATIONS = Path(__file__).resolve().parents[3] / "poindexter" / "services" / "migrations"
_TABLE = "public.app_settings"
# What pg_dump's "<type> + owned sequence + nextval default" is spelled as inline.
_SERIAL_FOR = {"integer": "serial", "bigint": "bigserial", "smallint": "smallserial"}


def _load_baseline():
    """Load 0000_baseline.py as the migration runner does (never in sys.modules)."""
    spec = importlib.util.spec_from_file_location("0000_baseline", _MIGRATIONS / "0000_baseline.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _normalize(sql: str) -> str:
    return " ".join(sql.split())


@pytest.fixture(scope="module")
def baseline():
    return _load_baseline()


@pytest.fixture(scope="module")
def dump(baseline):
    """The app_settings pieces of the dump: its CREATE TABLE entries, every
    constraint it declares for the table, and its sequence-backed columns."""
    sql = (_MIGRATIONS / "0000_baseline.schema.sql").read_text(encoding="utf-8")
    statements = [
        _normalize(baseline._strip_comment_lines(s))
        for s in baseline._split_sql_statements(sql)
        if baseline._is_executable(s)
    ]
    create = next(s for s in statements if baseline._create_table_name(s) == _TABLE)
    _, elements = baseline.parse_create_table(create)
    constraints = {e.name: _normalize(e.sql) for e in elements if e.kind == "constraint"}
    serials: dict[str, str] = {}
    for stmt in statements:
        added = re.fullmatch(rf"ALTER TABLE ONLY {re.escape(_TABLE)} ADD CONSTRAINT (\S+) (.+)", stmt)
        if added:
            constraints[added.group(1)] = f"CONSTRAINT {added.group(1)} {added.group(2)}"
        default = re.fullmatch(
            rf"ALTER TABLE ONLY {re.escape(_TABLE)} ALTER COLUMN (\S+) SET DEFAULT "
            r"nextval\('public\.(\S+)'::regclass\)",
            stmt,
        )
        if default:
            sequence = re.search(
                rf"CREATE SEQUENCE IF NOT EXISTS public\.{re.escape(default.group(2))} AS (\w+)",
                "\n".join(statements),
            )
            assert sequence, f"no CREATE SEQUENCE for {default.group(2)}"
            serials[default.group(1)] = f"{default.group(2)}:{sequence.group(1)}"
    return {
        "columns": [e for e in elements if e.kind == "column"],
        "constraints": constraints,
        "serials": serials,
    }


@pytest.fixture(scope="module")
def brain(baseline):
    table, elements = baseline.parse_create_table(seed_loader.APP_SETTINGS_DDL)
    assert table == "app_settings"
    return elements


async def test_the_pinned_ddl_is_the_statement_the_brain_runs():
    """Pinning a constant proves nothing if the brain executes something else."""
    calls = []

    class _Conn:
        async def execute(self, sql):
            calls.append(sql)

    await seed_loader._ensure_app_settings_table(_Conn())
    assert calls == [seed_loader.APP_SETTINGS_DDL]


def test_brain_creates_every_baseline_column_in_the_baseline_order(dump, brain):
    """Order matters: on the brain-first boot this CREATE fixes the columns'
    positions, and migrations-smoke compares positions against the
    migrations-first database."""
    assert [e.name for e in brain if e.kind == "column"] == [e.name for e in dump["columns"]]


def test_each_column_is_declared_exactly_as_the_dump_declares_it(dump, brain):
    """Verbatim, except that a column the dump backs with a separate sequence,
    default and owner is spelled the inline way (``serial``). ``serial`` names its
    sequence ``<table>_<column>_seq``, which must be the dump's sequence name
    because the baseline's later statements address it by name."""
    declared = {e.name: e for e in brain if e.kind == "column"}
    assert dump["serials"], "the dump backs no app_settings column with a sequence"
    for column in nonempty(dump["columns"], "app_settings columns in the dump"):
        expected = _normalize(column.sql)
        if column.name in dump["serials"]:
            sequence, sql_type = dump["serials"][column.name].split(":")
            assert sequence == f"app_settings_{column.name}_seq"
            expected = re.sub(
                rf"^{re.escape(column.name)} {sql_type}\b",
                f"{column.name} {_SERIAL_FOR[sql_type]}",
                expected,
            )
        assert _normalize(declared[column.name].sql) == expected


def test_brain_declares_every_constraint_the_dump_gives_app_settings(dump, brain):
    """The dump's inline CHECK plus the primary and unique keys it adds with
    ALTER TABLE, under the same names. The names matter too: the brain's seed
    relies on the unique key for ``ON CONFLICT (key)``, and the baseline skips
    re-adding a constraint whose name is already taken."""
    assert {"app_settings_pkey", "app_settings_key_key"} <= set(dump["constraints"])
    declared = {e.name: _normalize(e.sql) for e in brain if e.kind == "constraint"}
    assert declared == dump["constraints"]


def test_seed_rows_satisfy_the_stricter_table():
    """The table now enforces ``value NOT NULL``, so a seed row without a string
    value would fail the brain's INSERT. None may."""
    for row in nonempty(seed_loader.load_seed_file(), "seed_app_settings.json rows"):
        assert isinstance(row["value"], str), row["key"]
