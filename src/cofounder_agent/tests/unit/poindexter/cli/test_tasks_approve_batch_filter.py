"""Contract test for ``poindexter tasks approve-batch --filter`` SQL.

Pins the 2026-05-26 fix: the CLI's filter feature documented
``quality_score>=N`` as the canonical use case but the underlying
SELECT only saw ``pipeline_tasks`` columns. ``quality_score`` lives
on ``pipeline_versions``, so asyncpg blew up with
``UndefinedColumnError: column "quality_score" does not exist`` the
moment an operator tried to use the documented example.

The fix LEFT JOIN LATERALs the latest ``pipeline_versions`` row so
``quality_score`` resolves to ``v.quality_score`` without changing the
operator-facing filter syntax. This test pins both the join shape
(so a future "simplification" can't drop it) AND the operator UX
(``quality_score>=N`` is reachable from a filter string).
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest


def _load_tasks_module() -> Any:
    """Load ``poindexter.cli.tasks`` without going through the click entry.

    pyproject's ``tool.poetry.packages`` remaps ``src/cofounder_agent/poindexter``
    to the importable ``poindexter`` name, which the test conftest already
    has on sys.path.
    """
    import poindexter.cli.tasks as mod  # type: ignore[import-not-found]
    return mod


class _FakePool:
    """Captures the SQL that ``_resolve_filter_ids`` builds."""

    def __init__(self) -> None:
        self.captured_sql: str | None = None

    async def fetch(self, sql: str) -> list[dict[str, str]]:
        self.captured_sql = sql
        # Return one fake row so the caller's list comprehension works.
        return [{"task_id": "fake-task-uuid"}]

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_pool() -> _FakePool:
    return _FakePool()


@pytest.mark.asyncio
async def test_filter_query_joins_pipeline_versions(fake_pool: _FakePool) -> None:
    """The query must include the LATERAL pipeline_versions join so
    ``quality_score`` in the operator's filter resolves. Without this
    the SQL fails with UndefinedColumnError on ``quality_score``."""
    tasks = _load_tasks_module()

    with patch("asyncpg.create_pool", new=AsyncMock(return_value=fake_pool)), \
         patch("poindexter.cli._bootstrap.resolve_dsn", return_value="postgres://x"):
        ids = await tasks._resolve_filter_ids(
            "status='awaiting_approval' AND quality_score>=85",
        )

    assert ids == ["fake-task-uuid"]
    sql = fake_pool.captured_sql or ""
    # Pin the seam: a LATERAL subquery on pipeline_versions that exposes
    # quality_score. Whitespace-tolerant so a formatter pass doesn't
    # break the test.
    assert re.search(r"LEFT\s+JOIN\s+LATERAL", sql, re.IGNORECASE), (
        f"Expected LEFT JOIN LATERAL in query, got: {sql!r}"
    )
    assert "pipeline_versions" in sql.lower()
    assert "quality_score" in sql.lower()
    # The operator's filter must be the WHERE clause, not silently
    # rewritten or dropped.
    assert "status='awaiting_approval' AND quality_score>=85" in sql


@pytest.mark.asyncio
async def test_filter_query_preserves_pipeline_tasks_columns(fake_pool: _FakePool) -> None:
    """Filters that reference only pipeline_tasks columns (the common
    case — ``status``, ``stage``, ``topic``) must still work after the
    join is added. Operator UX: bare ``status='x'`` works, and so does
    bare ``quality_score>=85``."""
    tasks = _load_tasks_module()

    with patch("asyncpg.create_pool", new=AsyncMock(return_value=fake_pool)), \
         patch("poindexter.cli._bootstrap.resolve_dsn", return_value="postgres://x"):
        await tasks._resolve_filter_ids("status='awaiting_approval'")

    sql = (fake_pool.captured_sql or "").lower()
    # The FROM clause must alias pipeline_tasks as ``t`` so the join
    # condition can reference ``t.task_id``. SELECT must be on ``t.task_id``
    # (not ambiguous) so a future refactor that drops the alias breaks
    # this test instead of silently breaking production.
    assert "from pipeline_tasks t" in sql
    assert "select t.task_id" in sql


@pytest.mark.asyncio
async def test_filter_query_does_not_expose_versions_task_id(fake_pool: _FakePool) -> None:
    """``task_id`` exists on both tables. The LATERAL subquery must NOT
    expose its own ``task_id`` to the outer scope, otherwise the
    operator's ``task_id='xxx'`` filter becomes ambiguous and postgres
    raises. The fix subquery only SELECTs ``quality_score`` for that
    reason — preserve it."""
    tasks = _load_tasks_module()

    with patch("asyncpg.create_pool", new=AsyncMock(return_value=fake_pool)), \
         patch("poindexter.cli._bootstrap.resolve_dsn", return_value="postgres://x"):
        await tasks._resolve_filter_ids("task_id='xxx'")

    sql = fake_pool.captured_sql or ""
    # Find the LATERAL subquery and confirm it doesn't SELECT task_id.
    # Use a non-greedy match so we capture just the subquery body, not
    # the rest of the SQL.
    lateral = re.search(r"LEFT\s+JOIN\s+LATERAL\s*\((.+?)\)\s*v\s+ON", sql, re.IGNORECASE | re.DOTALL)
    assert lateral is not None, f"No LATERAL subquery in: {sql!r}"
    subquery = lateral.group(1)
    # The subquery references task_id in its WHERE clause (correlated
    # join condition) — that's fine. What it must NOT do is SELECT
    # task_id into the outer scope.
    select_match = re.search(r"SELECT\s+(.+?)\s+FROM", subquery, re.IGNORECASE | re.DOTALL)
    assert select_match is not None, f"No SELECT in subquery: {subquery!r}"
    selected_cols = select_match.group(1).lower()
    assert "task_id" not in selected_cols, (
        f"Subquery SELECTs task_id which makes outer filter ambiguous: {selected_cols!r}"
    )
