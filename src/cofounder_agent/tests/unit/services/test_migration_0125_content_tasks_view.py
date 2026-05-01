"""Unit tests for migration 0125 (content_tasks unification → view shape).

Closes Glad-Labs/poindexter#329 — the table-vs-view drift between dev and
production. After this migration runs, ``content_tasks`` should be a VIEW
(``pg_class.relkind == 'v'``) in BOTH environments.

We don't spin up a real Postgres for unit tests. Instead we exercise:

  - The SQL strings in the migration are well-formed and contain the
    columns / functions the production view + redirects need.
  - ``up()`` correctly dispatches on ``pg_class.relkind`` — view shape is
    a no-op, table shape triggers the bootstrap path.
  - The bootstrap path executes the expected sequence of statements.
  - ``up()`` refuses to drop a table that has data (fail-loud guard).
  - Both ``bytes`` and ``str`` returns from asyncpg's ``fetchval`` are
    handled (the bug 0114 had to fix in the original implementation).
  - ``down()`` is symmetric and refuses to revert when data exists.

Schema-validation tests against a real Postgres live in the migrations
smoke job (.github/workflows/migrations-smoke.yml) which applies every
migration to an empty database, then in CI we observe the post-state via
the relkind probe in tests/integration/.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _import_migration():
    """Late-import so the migration discovery glob doesn't double-run it."""
    import importlib
    return importlib.import_module(
        "services.migrations.0125_unify_content_tasks_as_view"
    )


def _make_pool(relkind_value, count_value=0, pt_exists=True):
    """Build a minimal asyncpg pool mock.

    ``relkind_value`` is what ``fetchval(_RELKIND_QUERY)`` returns —
    pass ``b'r'`` (bytes) or ``'v'`` (str) to model the asyncpg-version
    drift the relkind dispatch must handle, or ``None`` for "no such
    relation."

    ``count_value`` is what ``SELECT COUNT(*) FROM content_tasks``
    returns — used by the data-guard.

    ``pt_exists`` controls whether the pipeline_tasks-existence probe
    succeeds (only consulted when relkind is None).
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)

    call_count = {"fetchval": 0}

    async def _fetchval(query, *args, **kwargs):
        call_count["fetchval"] += 1
        q = query.strip()
        if "relkind" in q.lower():
            return relkind_value
        if "count(*)" in q.lower():
            return count_value
        if "pipeline_tasks" in q.lower():
            return 1 if pt_exists else None
        return None

    conn.fetchval = _fetchval

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Static SQL checks — make sure the trigger function bodies and the view
# DDL contain what production needs.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLBodiesAreWellFormed:
    def test_update_redirect_targets_pipeline_tables(self):
        m = _import_migration()
        # The UPDATE trigger writes to BOTH base tables.
        assert "UPDATE pipeline_tasks SET" in m._UPDATE_REDIRECT_FN
        assert "UPDATE pipeline_versions SET" in m._UPDATE_REDIRECT_FN
        # COALESCE guards against accidental NULLs (canonical from 0078).
        assert "COALESCE(NEW.stage" in m._UPDATE_REDIRECT_FN
        assert "COALESCE(NEW.qa_feedback" in m._UPDATE_REDIRECT_FN
        # Auto-stamps completed_at on terminal statuses.
        assert "WHEN NEW.status IN ('published','failed','cancelled','rejected','rejected_final')" in m._UPDATE_REDIRECT_FN
        # Does NOT write to the niche columns directly — that's a
        # base-table concern, callers go through pipeline_db for niche
        # writes. (If this assumption changes, update both this test
        # and the trigger body.)
        assert "niche_slug" not in m._UPDATE_REDIRECT_FN

    def test_insert_redirect_creates_both_rows(self):
        m = _import_migration()
        assert "INSERT INTO pipeline_tasks" in m._INSERT_REDIRECT_FN
        assert "INSERT INTO pipeline_versions" in m._INSERT_REDIRECT_FN
        # Idempotent — re-INSERT for an existing task_id is a no-op
        # rather than a constraint violation.
        assert "ON CONFLICT (task_id) DO NOTHING" in m._INSERT_REDIRECT_FN
        assert "ON CONFLICT (task_id, version) DO NOTHING" in m._INSERT_REDIRECT_FN
        # Always starts at version 1 — version history is append-only
        # via pipeline_db.upsert_version.
        assert "1," in m._INSERT_REDIRECT_FN  # version = 1
        # Defaults match what 0066 declared on pipeline_tasks.
        assert "'blog_post'" in m._INSERT_REDIRECT_FN
        assert "'pending'" in m._INSERT_REDIRECT_FN

    def test_delete_redirect_only_touches_pipeline_tasks(self):
        m = _import_migration()
        assert "DELETE FROM pipeline_tasks" in m._DELETE_REDIRECT_FN
        # pipeline_versions / pipeline_reviews / pipeline_distributions
        # all have ON DELETE CASCADE, so deleting from pipeline_tasks
        # alone cleans up the rest. If we DELETEd from pipeline_versions
        # first the FK would block — be explicit about that ordering.
        assert "DELETE FROM pipeline_versions" not in m._DELETE_REDIRECT_FN
        assert "DELETE FROM pipeline_reviews" not in m._DELETE_REDIRECT_FN

    def test_view_definition_includes_niche_columns(self):
        m = _import_migration()
        # The view must surface every column 0114 added to pipeline_tasks
        # or the post-conversion shape would silently lose data exposure.
        for col in ("niche_slug", "writer_rag_mode", "topic_batch_id"):
            assert f"pt.{col}" in m._CONTENT_TASKS_VIEW_DDL, (
                f"view DDL must SELECT pt.{col}"
            )

    def test_view_definition_includes_hitl_columns(self):
        m = _import_migration()
        # The view must surface 0098's HITL approval-gate columns.
        for col in ("awaiting_gate", "gate_artifact", "gate_paused_at"):
            assert f"pt.{col}" in m._CONTENT_TASKS_VIEW_DDL, (
                f"view DDL must SELECT pt.{col}"
            )

    def test_view_aliases_task_type_as_content_type(self):
        m = _import_migration()
        # Backward-compat — the legacy column name code still uses.
        assert "pt.task_type AS content_type" in m._CONTENT_TASKS_VIEW_DDL

    def test_three_triggers_declared(self):
        m = _import_migration()
        names = [t[0] for t in m._TRIGGERS]
        assert names == [
            "content_tasks_update_trigger",
            "content_tasks_insert_trigger",
            "content_tasks_delete_trigger",
        ]
        # All three are INSTEAD OF (not BEFORE / AFTER) — required for
        # writing to a view.
        for _name, when_clause, _func in m._TRIGGERS:
            assert when_clause.startswith("INSTEAD OF "), when_clause


# ---------------------------------------------------------------------------
# Behavioral tests — relkind branching.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRelkindDispatch:
    """Verify ``up()`` picks the right branch based on pg_class.relkind."""

    def test_view_shape_is_noop(self):
        """Production reality — relkind='v' — should do nothing."""
        m = _import_migration()
        pool, conn = _make_pool(relkind_value=b'v')
        _run(m.up(pool))
        # Only the relkind probe should have been executed — no DDL.
        assert conn.execute.await_count == 0, (
            "view-shape branch must be a pure no-op"
        )

    def test_view_shape_handles_str_relkind(self):
        """Some asyncpg versions return relkind as ``str`` instead of
        ``bytes`` — both must be handled the same way."""
        m = _import_migration()
        pool, conn = _make_pool(relkind_value='v')
        _run(m.up(pool))
        assert conn.execute.await_count == 0

    def test_table_shape_triggers_bootstrap(self):
        """Dev reality — relkind='r' — should run the full bootstrap."""
        m = _import_migration()
        pool, conn = _make_pool(relkind_value=b'r', count_value=0)
        _run(m.up(pool))
        # Bootstrap path runs many statements — sanity check we hit them.
        executed = [c.args[0] for c in conn.execute.await_args_list]
        joined = "\n".join(executed)
        assert "DROP TABLE content_tasks CASCADE" in joined
        assert "ALTER TABLE pipeline_tasks" in joined
        assert "content_tasks_update_redirect" in joined
        assert "content_tasks_insert_redirect" in joined
        assert "content_tasks_delete_redirect" in joined
        assert "CREATE VIEW content_tasks AS" in joined
        # All three INSTEAD OF triggers attached.
        assert "content_tasks_update_trigger" in joined
        assert "content_tasks_insert_trigger" in joined
        assert "content_tasks_delete_trigger" in joined

    def test_table_with_data_refuses_to_drop(self):
        """If content_tasks has rows, fail loud — never silently destroy data."""
        m = _import_migration()
        pool, conn = _make_pool(relkind_value=b'r', count_value=42)
        with pytest.raises(RuntimeError, match="42 rows"):
            _run(m.up(pool))

    def test_missing_relation_with_pipeline_tasks_present_creates_view(self):
        """If content_tasks doesn't exist but pipeline_tasks does, build
        the view directly (no table to drop first)."""
        m = _import_migration()
        pool, conn = _make_pool(relkind_value=None, pt_exists=True)
        _run(m.up(pool))
        executed = [c.args[0] for c in conn.execute.await_args_list]
        joined = "\n".join(executed)
        # No DROP TABLE — the table didn't exist.
        assert "DROP TABLE content_tasks" not in joined
        # But the view was still created.
        assert "CREATE VIEW content_tasks AS" in joined

    def test_missing_relation_without_pipeline_tasks_fails_loud(self):
        """If neither relation exists, the schema is broken — fail loud
        rather than silently masking the problem."""
        m = _import_migration()
        pool, conn = _make_pool(relkind_value=None, pt_exists=False)
        with pytest.raises(RuntimeError, match="neither content_tasks nor pipeline_tasks"):
            _run(m.up(pool))

    def test_unexpected_relkind_fails_loud(self):
        """Anything other than r/v/None means a schema we don't model
        (materialized view 'm', sequence 'S', composite 'c', etc.).
        Refuse rather than guess."""
        m = _import_migration()
        # 'm' = materialized view — wrong kind; refuse.
        pool, conn = _make_pool(relkind_value=b'm')
        with pytest.raises(RuntimeError, match="unexpected relkind"):
            _run(m.up(pool))


# ---------------------------------------------------------------------------
# down() symmetry.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDownSymmetry:
    def test_down_on_table_is_noop(self):
        """If content_tasks is a TABLE, this migration didn't set up the
        view — nothing to revert."""
        m = _import_migration()
        pool, conn = _make_pool(relkind_value=b'r')
        _run(m.down(pool))
        # Only the relkind probe ran.
        assert conn.execute.await_count == 0

    def test_down_on_view_with_data_refuses(self):
        """Don't drop a populated view — there's no roundtrip back to a
        flat content_tasks table that preserves the version history."""
        m = _import_migration()
        pool, conn = _make_pool(relkind_value=b'v', count_value=17)
        with pytest.raises(RuntimeError, match="17 rows"):
            _run(m.down(pool))

    def test_down_on_empty_view_drops_triggers_and_view(self):
        m = _import_migration()
        pool, conn = _make_pool(relkind_value=b'v', count_value=0)
        _run(m.down(pool))
        executed = [c.args[0] for c in conn.execute.await_args_list]
        joined = "\n".join(executed)
        assert "DROP TRIGGER IF EXISTS content_tasks_update_trigger" in joined
        assert "DROP TRIGGER IF EXISTS content_tasks_insert_trigger" in joined
        assert "DROP TRIGGER IF EXISTS content_tasks_delete_trigger" in joined
        assert "DROP VIEW IF EXISTS content_tasks" in joined


# ---------------------------------------------------------------------------
# Drift probe — the spec asked us to assert content_tasks resolves to the
# same relkind in dev as in prod. Without a live DB in unit tests we
# verify the contract via the same _RELKIND_QUERY the migration uses,
# and confirm the probe is consistent for both bytes and str returns.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRelkindProbeContract:
    def test_relkind_query_targets_public_schema(self):
        """The relkind probe must scope to schema 'public' so a same-named
        relation in another schema can't satisfy the check."""
        m = _import_migration()
        assert "n.nspname = 'public'" in m._RELKIND_QUERY
        assert "c.relname = 'content_tasks'" in m._RELKIND_QUERY

    def test_post_migration_relkind_is_view_in_both_environments(self):
        """The contract: after this migration runs, content_tasks must be
        a VIEW. Dev (was 'r') gets converted to 'v'; prod (already 'v')
        stays 'v'. Both end states match.

        We exercise this by simulating both starting environments and
        checking the dispatch in ``up()`` reaches the view-shape branch
        (or the bootstrap-then-view branch). The actual relkind after
        DDL execution is a Postgres concern verified by the smoke job —
        this test pins the contract at the migration-logic level.
        """
        m = _import_migration()

        # Production starting state — view, no-op, ends as view.
        prod_pool, prod_conn = _make_pool(relkind_value=b'v')
        _run(m.up(prod_pool))
        # Confirm: no DDL was issued, so prod's existing view is intact.
        assert prod_conn.execute.await_count == 0

        # Dev starting state — table, bootstrap, ends as view.
        dev_pool, dev_conn = _make_pool(relkind_value=b'r', count_value=0)
        _run(m.up(dev_pool))
        executed = [c.args[0] for c in dev_conn.execute.await_args_list]
        # The final CREATE VIEW is what flips relkind from 'r' to 'v'.
        assert any("CREATE VIEW content_tasks" in s for s in executed)
        # And the prior DROP TABLE removed the 'r' relation that was
        # blocking the view from being created with the same name.
        assert any("DROP TABLE content_tasks" in s for s in executed)
