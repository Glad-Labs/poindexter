"""Tests for scripts/ci/adapter_purity_lint.py — the transport-adapter guard.

Pins the detection contract for epic #1340 (the service layer is the contract;
adapters delegate, they don't hold business SQL): a `conn.fetch*/execute*` (or
`pool.*`) call whose first argument is a string literal that *looks like SQL*
is an inline-SQL violation, while opening a pool/connection
(`asyncpg.create_pool`/`connect`), delegating to a service, or passing a
non-literal query is not. A `# noqa: adapter-ok` marker exempts an intentional
case. This is the ratchet that stops inline SQL from re-rotting back into the
route / CLI / MCP adapters.
"""

import importlib.util
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "adapter_purity_lint.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/adapter_purity_lint.py")


def _load_lint_module():
    path = _find_repo_root(Path(__file__)) / "scripts" / "ci" / "adapter_purity_lint.py"
    spec = importlib.util.spec_from_file_location("adapter_purity_lint_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _scan_src(tmp_path: Path, src: str) -> int:
    f = tmp_path / "sample.py"
    f.write_text(src, encoding="utf-8")
    return LINT.scan_file(f)


class TestInlineSqlDetection:
    def test_conn_fetch_select_is_flagged(self, tmp_path):
        src = 'async def f(conn):\n    return await conn.fetch("SELECT * FROM posts")\n'
        assert _scan_src(tmp_path, src) == 1

    def test_pool_execute_insert_is_flagged(self, tmp_path):
        src = 'async def f(pool):\n    await pool.execute("INSERT INTO t (a) VALUES ($1)", 1)\n'
        assert _scan_src(tmp_path, src) == 1

    def test_fetchrow_update_is_flagged(self, tmp_path):
        src = 'async def f(c):\n    await c.fetchrow("UPDATE t SET a=1 WHERE id=$1", 2)\n'
        assert _scan_src(tmp_path, src) == 1

    def test_fetchval_delete_is_flagged(self, tmp_path):
        src = 'async def f(c):\n    await c.fetchval("DELETE FROM t WHERE id=$1", 2)\n'
        assert _scan_src(tmp_path, src) == 1

    def test_executemany_is_flagged(self, tmp_path):
        src = 'async def f(c, rows):\n    await c.executemany("INSERT INTO t VALUES ($1)", rows)\n'
        assert _scan_src(tmp_path, src) == 1

    def test_cte_with_is_flagged(self, tmp_path):
        src = 'async def f(c):\n    await c.fetch("WITH x AS (SELECT 1) SELECT * FROM x")\n'
        assert _scan_src(tmp_path, src) == 1

    def test_cursor_execute_is_flagged(self, tmp_path):
        src = 'def f(cur):\n    cur.execute("SELECT 1")\n'
        assert _scan_src(tmp_path, src) == 1

    def test_lowercase_sql_is_flagged(self, tmp_path):
        src = 'async def f(c):\n    await c.execute("select 1")\n'
        assert _scan_src(tmp_path, src) == 1

    def test_multiline_sql_is_flagged(self, tmp_path):
        src = (
            'async def f(c):\n'
            '    await c.fetch(\n'
            '        """\n'
            '        SELECT a, b\n'
            '        FROM t\n'
            '        """\n'
            '    )\n'
        )
        assert _scan_src(tmp_path, src) == 1

    def test_fstring_sql_is_flagged(self, tmp_path):
        src = 'async def f(c, col):\n    await c.fetch(f"SELECT {col} FROM t")\n'
        assert _scan_src(tmp_path, src) == 1

    def test_two_inline_sql_calls_counted(self, tmp_path):
        src = (
            'async def f(c):\n'
            '    await c.execute("INSERT INTO t VALUES (1)")\n'
            '    return await c.fetch("SELECT * FROM t")\n'
        )
        assert _scan_src(tmp_path, src) == 2


class TestNonViolations:
    def test_create_pool_not_flagged(self, tmp_path):
        # Opening a pool to hand to a service is the correct adapter pattern.
        src = 'import asyncpg\nasync def f(dsn):\n    return await asyncpg.create_pool(dsn)\n'
        assert _scan_src(tmp_path, src) == 0

    def test_connect_not_flagged(self, tmp_path):
        src = 'import asyncpg\nasync def f(dsn):\n    return await asyncpg.connect(dsn)\n'
        assert _scan_src(tmp_path, src) == 0

    def test_service_delegation_not_flagged(self, tmp_path):
        src = (
            'from services import declarative_config_service as dcs\n'
            'async def f(pool):\n'
            '    return await dcs.list_rows(pool, "taps")\n'
        )
        assert _scan_src(tmp_path, src) == 0

    def test_variable_query_arg_not_flagged(self, tmp_path):
        # First arg is a name, not a SQL literal — the guard flags inline
        # literals, not every DB call (a query built/imported elsewhere is
        # out of scope by design, avoiding false positives).
        src = 'async def f(c, query):\n    return await c.fetch(query)\n'
        assert _scan_src(tmp_path, src) == 0

    def test_non_sql_string_arg_not_flagged(self, tmp_path):
        src = 'async def f(c):\n    return await c.fetch("not a query at all")\n'
        assert _scan_src(tmp_path, src) == 0

    def test_non_db_method_with_sql_string_not_flagged(self, tmp_path):
        # `.info` is not a DB-exec method — a log line mentioning SQL is fine.
        src = 'def f(logger):\n    logger.info("SELECT is a keyword")\n'
        assert _scan_src(tmp_path, src) == 0

    def test_plain_function_call_not_flagged(self, tmp_path):
        src = 'def f():\n    print("SELECT * FROM t")\n'
        assert _scan_src(tmp_path, src) == 0


class TestOverride:
    def test_adapter_ok_override_exempts(self, tmp_path):
        src = (
            'async def f(c):\n'
            '    return await c.fetch("SELECT 1")  # noqa: adapter-ok bootstrap probe\n'
        )
        assert _scan_src(tmp_path, src) == 0


class TestBaselineRatchet:
    def test_real_tree_matches_baseline(self):
        """The committed baseline must satisfy the live tree (no drift).

        If this fails, either inline SQL was added to an adapter without
        baselining, or a violation was removed and the baseline wasn't lowered.
        """
        counts = LINT.compute_counts()
        baseline = LINT.load_baseline()
        offenders = {
            rel: (n, baseline.get(rel, 0))
            for rel, n in counts.items()
            if n > baseline.get(rel, 0)
        }
        assert offenders == {}, f"adapter-purity baseline drift: {offenders}"
