"""Manual smoke test for Glad-Labs/poindexter#371 — proves the
AsyncPostgresSaver wire-up actually persists state to Postgres.

Run on a host with a live Postgres + the worker venv:

    poetry run python scripts/smoke_371_postgres_checkpointer.py

Expected output (last line):

    SMOKE OK — durability verified across 2 TemplateRunner instances

The script:

1. Picks the SelectorEventLoop on Windows (psycopg async needs it).
2. Creates a disposable database.
3. Builds the same trivial 1-node graph the unit test uses.
4. Runs TemplateRunner with the flag on, against the disposable DB.
5. Opens a fresh AsyncPostgresSaver and asserts the checkpoint row
   landed in the LangGraph checkpoints table.
6. Drops the database.

Why this exists alongside the unit test: the unit-tier
TestPostgresCheckpointerSmoke is skipped on Windows because pytest
defaults to ProactorEventLoop and switching mid-test session is messy.
This script does the policy switch up-front, so Matt can run it once
manually to confirm the wire-up before flipping
``template_runner_use_postgres_checkpointer`` to true in production.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse


def _set_selector_loop_on_windows() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _ensure_paths() -> None:
    """Make services.* + brain.* importable without poetry shell."""
    here = Path(__file__).resolve().parent
    # scripts/ is in src/cofounder_agent/, repo root is two up.
    repo = here.parent.parent.parent
    for p in (
        repo,
        repo / "src" / "cofounder_agent",
    ):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)


async def _smoke() -> None:
    import asyncpg
    from langgraph.graph import END, StateGraph

    from services.site_config import SiteConfig
    site_config = SiteConfig()
    from services.template_runner import PipelineState, TemplateRunner
    import services.pipeline_templates as pt

    # ---- 1. Resolve a base DSN from bootstrap.toml or DATABASE_URL ----
    from brain.bootstrap import resolve_database_url
    base_dsn = resolve_database_url()
    if not base_dsn:
        raise SystemExit(
            "No DSN configured — set DATABASE_URL or write to bootstrap.toml"
        )

    # ---- 2. Make a disposable database ----
    parsed = urlparse(base_dsn)
    admin_dsn = urlunparse(parsed._replace(path="/postgres"))
    test_db = f"poindexter_lgcp_smoke_{secrets.token_hex(4)}"
    test_dsn = urlunparse(parsed._replace(path=f"/{test_db}"))

    print(f"[smoke] creating disposable DB {test_db}")
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {test_db}")
        await admin.execute(f"CREATE DATABASE {test_db}")
    finally:
        await admin.close()

    try:
        # ---- 3. Trivial 1-node graph + register it ----
        def _factory(*, pool, record_sink=None):
            g: StateGraph = StateGraph(PipelineState)

            async def _node(state):
                return {"content": f"topic was: {state.get('topic', '?')}"}

            g.add_node("record", _node)
            g.set_entry_point("record")
            g.add_edge("record", END)
            return g

        pt.TEMPLATES["smoke_trivial"] = _factory  # type: ignore[index]

        # ---- 4. Flip the flag on (in-memory; doesn't touch DB) ----
        site_config._config["template_runner_use_postgres_checkpointer"] = "true"

        thread_id = "smoke-thread"

        # ---- 5. First runner — writes a checkpoint ----
        print("[smoke] runner_a invoking trivial template")
        runner_a = TemplateRunner(pool=None, checkpointer_dsn=test_dsn)
        summary_a = await runner_a.run(
            "smoke_trivial",
            {"task_id": "smoke-1", "topic": "first run"},
            thread_id=thread_id,
        )
        assert summary_a.ok, f"first run failed: {summary_a}"
        print(f"[smoke] runner_a final_state.content = "
              f"{summary_a.final_state.get('content')!r}")

        # ---- 6. Inspect Postgres directly to confirm persistence ----
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        async with AsyncPostgresSaver.from_conn_string(test_dsn) as inspector:
            tup = await inspector.aget_tuple(
                {"configurable": {"thread_id": thread_id}}
            )
            if tup is None:
                raise AssertionError(
                    "no checkpoint row in Postgres — wire-up broken"
                )
            persisted = tup.checkpoint.get("channel_values", {})
            print(f"[smoke] persisted channels: {sorted(persisted.keys())}")
            print(f"[smoke] persisted content = {persisted.get('content')!r}")
            assert persisted.get("content") == "topic was: first run", (
                f"unexpected persisted content: {persisted!r}"
            )

        # ---- 7. Second runner — separate instance, same thread_id ----
        print("[smoke] runner_b (separate instance, same thread_id)")
        runner_b = TemplateRunner(pool=None, checkpointer_dsn=test_dsn)
        summary_b = await runner_b.run(
            "smoke_trivial",
            {"task_id": "smoke-2", "topic": "second run"},
            thread_id=thread_id,
        )
        assert summary_b.ok, f"second run failed: {summary_b}"
        print(f"[smoke] runner_b final_state.content = "
              f"{summary_b.final_state.get('content')!r}")

        # ---- 8. Confirm the second run also wrote a checkpoint ----
        async with AsyncPostgresSaver.from_conn_string(test_dsn) as inspector:
            history = []
            async for snap in inspector.alist(
                {"configurable": {"thread_id": thread_id}}
            ):
                history.append(snap)
            print(f"[smoke] checkpoint history length: {len(history)}")
            assert len(history) >= 2, (
                f"expected at least 2 checkpoint entries from 2 runs, "
                f"got {len(history)}"
            )
        print("\nSMOKE OK — durability verified across 2 TemplateRunner instances")
    finally:
        # ---- 9. Drop the disposable database ----
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                test_db,
            )
            await admin.execute(f"DROP DATABASE IF EXISTS {test_db}")
            print(f"[smoke] dropped disposable DB {test_db}")
        finally:
            await admin.close()


def main() -> None:
    _set_selector_loop_on_windows()
    _ensure_paths()
    asyncio.run(_smoke())


if __name__ == "__main__":
    main()
