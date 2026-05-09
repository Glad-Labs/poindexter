"""Tests for the Phase 1.5 Postgres-checkpointer wire-up in
``services/template_runner.py`` (Glad-Labs/poindexter#371).

Three test groups:

- **TestPostgresCheckpointerSmoke** — uses the live ``db_pool`` fixture
  (skipped when no DSN resolves) to round-trip a 1-node graph with the
  flag on. Asserts state persists across two ``TemplateRunner.run()``
  invocations on the same ``thread_id`` — i.e. durability actually
  works end-to-end.

- **TestCheckpointerFallback** — mocks the construction +
  ``setup()`` paths to verify graceful fallback to ``MemorySaver`` on
  connect-time failures, and the loud raise when Postgres is REACHABLE
  but ``setup()`` blows up (per ``feedback_no_silent_defaults``).

- **TestBackwardCompat** — flag-off path runs the existing dev_diary
  template unchanged (regression guard for the existing OSS users).

The tests do NOT exercise the real ``services.pipeline_templates``
factories — those depend on a heavy plugin/registry/stage stack that's
out of scope for a checkpointer-wiring test. Instead they monkeypatch
``services.pipeline_templates.TEMPLATES`` with a tiny synthetic graph
that exercises the only thing #371 changes: how ``compile()`` is
called and the checkpointer life-cycle around it.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from services.template_runner import (
    PipelineState,
    TemplateRunner,
    _CheckpointerSetupError,
)


def _trivial_graph_factory(*, pool: Any, record_sink: list | None = None) -> StateGraph:
    """A 1-node StateGraph that writes its input ``topic`` into
    ``content`` so we can verify checkpoint round-tripping.

    No plugin lookups, no SiteConfig, no DB IO inside the node — keeps
    the test focused on the checkpointer wiring layer.
    """
    g: StateGraph = StateGraph(PipelineState)

    async def _record_topic(state: PipelineState) -> dict[str, Any]:
        return {"content": f"topic was: {state.get('topic', '?')}"}

    g.add_node("record_topic", _record_topic)
    g.set_entry_point("record_topic")
    g.add_edge("record_topic", END)
    return g


@pytest.fixture
def trivial_templates(monkeypatch):
    """Replace TEMPLATES with our synthetic 1-node factory + return slug."""
    fake_registry = {"trivial": _trivial_graph_factory}
    # Defer import so the test module loads even if the templates module
    # has its own registry side effects.
    import services.pipeline_templates as pt
    monkeypatch.setattr(pt, "TEMPLATES", fake_registry)
    return "trivial"


@pytest.fixture
def flag_on(monkeypatch):
    """Force template_runner_use_postgres_checkpointer = true."""
    import services.template_runner as _scm
    site_config = _scm.site_config
    monkeypatch.setitem(
        site_config._config,
        "template_runner_use_postgres_checkpointer",
        "true",
    )


@pytest.fixture
def flag_off(monkeypatch):
    """Force template_runner_use_postgres_checkpointer = false (default)."""
    import services.template_runner as _scm
    site_config = _scm.site_config
    monkeypatch.setitem(
        site_config._config,
        "template_runner_use_postgres_checkpointer",
        "false",
    )


# ---------------------------------------------------------------------------
# Group 1: live-Postgres smoke test (durability across runner instances)
# ---------------------------------------------------------------------------


def _resolve_smoke_dsn() -> str | None:
    """Helper for the smoke test — resolve a DSN without dragging in the
    heavy unit-tier db_pool fixture (which replays 140+ migrations and
    has its own flakiness profile unrelated to checkpointer logic).

    The smoke test only needs an empty namespace where AsyncPostgresSaver
    can create its three tables — a fresh disposable database does the
    job with zero migration cost.
    """
    try:
        from brain.bootstrap import resolve_database_url
        dsn = resolve_database_url()
    except Exception:
        return None
    if not dsn or dsn == "postgresql://test:test@localhost/test":
        return None
    return dsn


@pytest.mark.unit
@pytest.mark.integration_db
class TestPostgresCheckpointerSmoke:
    """End-to-end: state survives across two TemplateRunner instances.

    Uses a disposable database (created/dropped per test) instead of the
    full unit-tier db_pool fixture — the checkpointer doesn't need any
    of the project schema, just an empty namespace where it can run its
    own ``setup()`` + write its three tables.
    """

    @pytest.mark.asyncio
    async def test_state_persists_across_runs_on_same_thread(
        self, trivial_templates, flag_on,
    ):
        """Run the trivial graph twice with the same thread_id; a fresh
        AsyncPostgresSaver instance built afterwards should see the
        first run's checkpoint persisted to Postgres.

        This is the smoke test that proves AsyncPostgresSaver is actually
        wired through compile() — without the checkpointer, no rows
        would land in the checkpoints table.
        """
        import asyncio
        import sys
        import asyncpg
        import secrets
        from urllib.parse import urlparse, urlunparse

        # psycopg async cannot run on Windows' default ProactorEventLoop —
        # it needs SelectorEventLoop. The production worker is started
        # with the right policy (see brain/cli/start_worker.py); the test
        # has to handle this itself because pytest-asyncio uses whatever
        # the current loop is.
        if sys.platform == "win32" and isinstance(
            asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy,
        ):
            pytest.skip(
                "psycopg async requires SelectorEventLoop on Windows; "
                "set asyncio.WindowsSelectorEventLoopPolicy() before pytest "
                "(handled in CI by the conftest selector-policy fixture or "
                "by using a Linux runner). Skipping the live-DB smoke test "
                "to keep the suite green on Matt's Windows host."
            )

        base_dsn = _resolve_smoke_dsn()
        if not base_dsn:
            pytest.skip(
                "No live Postgres DSN configured — smoke test requires a "
                "reachable DB (set DATABASE_URL or write database_url to "
                "bootstrap.toml)"
            )

        # Create a disposable database for this test so the checkpointer
        # has a clean namespace + the project's own tables don't get in
        # the way. No migrations needed — AsyncPostgresSaver.setup()
        # creates the three tables it needs.
        parsed = urlparse(base_dsn)
        admin_dsn = urlunparse(parsed._replace(path="/postgres"))
        test_db = f"poindexter_lgcp_{secrets.token_hex(6)}"
        test_dsn = urlunparse(parsed._replace(path=f"/{test_db}"))

        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(f"DROP DATABASE IF EXISTS {test_db}")
            await admin.execute(f"CREATE DATABASE {test_db}")
        finally:
            await admin.close()

        try:
            thread_id = "test-thread-371-smoke"
            slug = trivial_templates

            # First runner: writes a checkpoint via AsyncPostgresSaver.
            runner_a = TemplateRunner(pool=None, checkpointer_dsn=test_dsn)
            summary_a = await runner_a.run(
                slug,
                {"task_id": "smoke-1", "topic": "first run"},
                thread_id=thread_id,
            )
            assert summary_a.ok, f"first run failed: {summary_a}"
            assert summary_a.final_state.get("content") == "topic was: first run"

            # Build a fresh checkpointer + inspect history to confirm the
            # first run was actually persisted to Postgres.
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            async with AsyncPostgresSaver.from_conn_string(test_dsn) as inspector:
                # aget_tuple returns the most recent checkpoint for the thread.
                tup = await inspector.aget_tuple(
                    {"configurable": {"thread_id": thread_id}}
                )
                assert tup is not None, (
                    "no checkpoint found in Postgres after first run — "
                    "AsyncPostgresSaver was not actually wired into compile()"
                )
                persisted_values = tup.checkpoint.get("channel_values", {})
                # The "content" channel must be in the persisted snapshot
                # — that's what the trivial graph node writes.
                assert "content" in persisted_values, (
                    f"checkpoint exists but state was empty: "
                    f"{persisted_values!r}"
                )
                assert persisted_values["content"] == "topic was: first run"

            # Second runner: separate instance, same thread_id. Without
            # state-channel fan-in this just re-runs the node, but the
            # important durability check (above) has already passed —
            # the second run is a smoke check that re-using a thread_id
            # against a populated checkpointer doesn't blow up.
            runner_b = TemplateRunner(pool=None, checkpointer_dsn=test_dsn)
            summary_b = await runner_b.run(
                slug,
                {"task_id": "smoke-2", "topic": "second run"},
                thread_id=thread_id,
            )
            assert summary_b.ok, f"second run failed: {summary_b}"
        finally:
            # Drop the disposable database.
            admin = await asyncpg.connect(admin_dsn)
            try:
                await admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = $1 AND pid <> pg_backend_pid()",
                    test_db,
                )
                await admin.execute(f"DROP DATABASE IF EXISTS {test_db}")
            finally:
                await admin.close()


# ---------------------------------------------------------------------------
# Group 2: fallback / failure-mode tests (no live DB required)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckpointerFallback:
    """Verify the resolver picks the right checkpointer in each branch
    of the truth table without needing a live Postgres."""

    @pytest.mark.asyncio
    async def test_no_dsn_falls_back_to_memorysaver(
        self, trivial_templates, flag_on, caplog,
    ):
        """Flag is on but no DSN resolvable → log warning, use MemorySaver."""
        runner = TemplateRunner(pool=None, checkpointer_dsn=None)

        with patch(
            "brain.bootstrap.resolve_database_url", return_value=None,
        ), caplog.at_level(logging.WARNING):
            summary = await runner.run(
                trivial_templates,
                {"task_id": "no-dsn", "topic": "hi"},
                thread_id="no-dsn-thread",
            )

        assert summary.ok
        assert summary.final_state.get("content") == "topic was: hi"
        assert any(
            "no DSN resolved" in rec.message
            for rec in caplog.records
        ), f"expected DSN-missing warning, got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_import_failure_falls_back_to_memorysaver(
        self, trivial_templates, flag_on, caplog, monkeypatch,
    ):
        """If langgraph-checkpoint-postgres can't be imported, fall back
        cleanly. Simulates an environment without the dep installed."""
        runner = TemplateRunner(pool=None, checkpointer_dsn="postgresql://x/y")

        # Force the lazy import in _resolve_checkpointer to ImportError.
        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "langgraph.checkpoint.postgres.aio":
                raise ImportError("simulated missing dep")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        with caplog.at_level(logging.WARNING):
            summary = await runner.run(
                trivial_templates,
                {"task_id": "no-dep", "topic": "hi"},
                thread_id="no-dep-thread",
            )

        assert summary.ok
        assert any(
            "not installed" in rec.message
            for rec in caplog.records
        )

    @pytest.mark.asyncio
    async def test_setup_failure_raises_loudly(
        self, trivial_templates, flag_on, caplog,
    ):
        """Postgres reachable but setup() raises → re-raise as
        _CheckpointerSetupError. Per feedback_no_silent_defaults: we
        MUST NOT silently use MemorySaver when Postgres is reachable
        but the schema migration broke."""
        runner = TemplateRunner(pool=None, checkpointer_dsn="postgresql://x/y")

        # Build a fake AsyncPostgresSaver context-manager whose checkpointer
        # has a setup() that raises.
        class _FakeCheckpointer:
            async def setup(self):
                raise RuntimeError("simulated permission denied creating tables")

        class _FakeCM:
            async def __aenter__(self):
                return _FakeCheckpointer()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeSaver:
            @classmethod
            def from_conn_string(cls, dsn):
                return _FakeCM()

        # Inject the fake module into sys.modules so the lazy import in
        # _resolve_checkpointer picks it up.
        import sys
        import types
        fake_mod = types.ModuleType("langgraph.checkpoint.postgres.aio")
        fake_mod.AsyncPostgresSaver = _FakeSaver
        with patch.dict(sys.modules, {
            "langgraph.checkpoint.postgres.aio": fake_mod,
        }):
            with caplog.at_level(logging.ERROR):
                with pytest.raises(_CheckpointerSetupError):
                    await runner.run(
                        trivial_templates,
                        {"task_id": "setup-fail", "topic": "hi"},
                        thread_id="setup-fail-thread",
                    )

        assert any(
            "refusing to silently fall back" in rec.message
            for rec in caplog.records
        ), f"expected loud-fail message, got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_construction_failure_falls_back_to_memorysaver(
        self, trivial_templates, flag_on, caplog,
    ):
        """from_conn_string itself raises (e.g. malformed DSN) → fall back
        gracefully to MemorySaver. Distinct from setup() failure because
        Postgres was NEVER reached — this is a "Postgres unreachable"
        condition, not a "Postgres reachable but broken" condition."""
        runner = TemplateRunner(
            pool=None, checkpointer_dsn="postgresql://x/y",
        )

        class _BoomSaver:
            @classmethod
            def from_conn_string(cls, dsn):
                raise OSError("simulated DNS lookup failed")

        import sys
        import types
        fake_mod = types.ModuleType("langgraph.checkpoint.postgres.aio")
        fake_mod.AsyncPostgresSaver = _BoomSaver
        with patch.dict(sys.modules, {
            "langgraph.checkpoint.postgres.aio": fake_mod,
        }):
            with caplog.at_level(logging.WARNING):
                summary = await runner.run(
                    trivial_templates,
                    {"task_id": "ctor-fail", "topic": "hi"},
                    thread_id="ctor-fail-thread",
                )

        assert summary.ok
        assert any(
            "from_conn_string" in rec.message and "falling back to MemorySaver" in rec.message
            for rec in caplog.records
        )


# ---------------------------------------------------------------------------
# Group 3: backward-compat — flag-off runs unchanged
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackwardCompat:
    """When the flag is off (the PR default), behavior should be
    identical to pre-#371 — no Postgres lookups, MemorySaver only."""

    @pytest.mark.asyncio
    async def test_flag_off_uses_memorysaver_no_postgres_lookup(
        self, trivial_templates, flag_off,
    ):
        """With the flag off, _resolve_checkpointer should never even
        attempt to import the AsyncPostgresSaver module — verified by
        spying on the langgraph postgres import path."""
        runner = TemplateRunner(pool=None, checkpointer_dsn=None)

        # Spy: track every attempted import of the postgres saver module.
        import builtins
        postgres_imports: list[str] = []
        real_import = builtins.__import__

        def _spy_import(name, *args, **kwargs):
            if "checkpoint.postgres" in name:
                postgres_imports.append(name)
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_spy_import):
            summary = await runner.run(
                trivial_templates,
                {"task_id": "flag-off", "topic": "no postgres"},
                thread_id="flag-off-thread",
            )

        assert summary.ok
        assert summary.final_state.get("content") == "topic was: no postgres"
        assert not postgres_imports, (
            f"flag-off path tried to import postgres saver: {postgres_imports}"
        )

    @pytest.mark.asyncio
    async def test_flag_off_returns_memorysaver_directly(
        self, flag_off,
    ):
        """Direct unit test of _resolve_checkpointer — flag off yields
        a MemorySaver instance. Belt-and-suspenders for the more
        integrated test above."""
        runner = TemplateRunner(pool=None, checkpointer_dsn=None)
        async with runner._resolve_checkpointer() as cp:
            assert isinstance(cp, MemorySaver)
