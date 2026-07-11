"""Windows event-loop policy shim for the whole ``poindexter`` CLI.

Every CLI subcommand runs its async body under ``asyncio.run(...)``. On Windows
the default ``ProactorEventLoop`` cannot run psycopg3's async mode — it raises
``InterfaceError``. The most visible casualty is ``pipeline resume``: it depends
on LangGraph's ``AsyncPostgresSaver`` (psycopg3) to load the durable checkpoint
the worker wrote when the graph paused at a gate. ``InterfaceError`` there is
caught by ``TemplateRunner._resolve_checkpointer`` and silently degraded to a
fresh ``MemorySaver``, which holds no checkpoint, so the graph re-runs from its
entry node with the CLI's thin initial state (no ``post_id``) and halts.

The switch below forces a ``SelectorEventLoop`` on Windows *before* any
``asyncio.run`` creates a loop. It is applied once at the CLI root
(``app.main``) so **every** subcommand inherits it — not just the handful that
remembered to call it — which future-proofs any command that later reaches for
psycopg3. ``asyncpg`` (what most commands use) runs on either loop, so the
switch is safe everywhere; off Windows it is a no-op and must not touch the
global policy (doing so could break a sibling command that relies on the default
loop). Mirrors ``scripts/smoke_371_postgres_checkpointer.py``.
"""
from __future__ import annotations

import asyncio
import sys


def ensure_selector_event_loop_on_windows() -> None:
    """Force the ``SelectorEventLoop`` policy on Windows; no-op elsewhere."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


__all__ = ["ensure_selector_event_loop_on_windows"]
