"""Windows event-loop policy shim for psycopg3-dependent ``poindexter`` commands.

On Windows the default ``ProactorEventLoop`` cannot run psycopg3's async mode —
it raises ``InterfaceError``. The most visible casualty is ``pipeline resume``:
it depends on LangGraph's ``AsyncPostgresSaver`` (psycopg3) to load the durable
checkpoint the worker wrote when the graph paused at a gate. ``InterfaceError``
there is caught by ``TemplateRunner._resolve_checkpointer`` and silently
degraded to a fresh ``MemorySaver``, which holds no checkpoint, so the graph
re-runs from its entry node with the CLI's thin initial state (no ``post_id``)
and halts.

The switch below forces a ``SelectorEventLoop`` on Windows *before* any
``asyncio.run`` creates a loop. It is applied ONLY by the commands that need
it (``pipeline.py``'s ``_run``, shared by every ``pipeline`` subcommand) —
**not** at the CLI root. It used to be applied CLI-wide on the theory that
``asyncpg`` (what most commands use) runs on either loop, so the switch was
"safe everywhere" — that theory missed that Windows' ``SelectorEventLoop``
cannot spawn subprocesses at all (``asyncio.create_subprocess_exec`` raises
``NotImplementedError``), which silently broke ``taps run`` for every
Singer-tap handler (poindexter#857 follow-up). Off Windows this is a no-op and
must not touch the global policy (doing so could break a sibling command that
relies on the default loop). Mirrors ``scripts/smoke_371_postgres_checkpointer.py``.
"""
from __future__ import annotations

import asyncio
import sys


def ensure_selector_event_loop_on_windows() -> None:
    """Force the ``SelectorEventLoop`` policy on Windows; no-op elsewhere."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


__all__ = ["ensure_selector_event_loop_on_windows"]
