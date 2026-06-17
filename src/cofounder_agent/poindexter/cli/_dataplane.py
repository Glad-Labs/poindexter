"""Shared helper for the declarative data-plane CLI groups (#1522).

The 5 data-plane groups (taps / retention / webhooks / qa-gates / publishers)
used to each hand-roll ``asyncpg.connect`` + raw SQL + a near-identical table
renderer. Per epic #1340 (no SQL in adapters), they now delegate to
``services.declarative_config_service`` — this module owns the bits they share:
the pool lifecycle (``run_service``) and the table/detail rendering.

``run_service`` opening a pool to hand to the service is the *correct* adapter
pattern — it executes no SQL itself; the service owns the SQL.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from collections.abc import Awaitable, Callable
from typing import Any

import click

from poindexter.cli._bootstrap import resolve_dsn as _dsn


def run_service(factory: Callable[[Any], Awaitable[Any]]) -> Any:
    """Open a short-lived asyncpg pool, ``await factory(pool)``, then close it.

    Propagates exceptions (the pool is still closed via ``finally``) so the
    calling command keeps its own try/except for the user-facing error +
    exit code. Opening the pool here executes no SQL — the service the pool
    is handed to owns every query.
    """

    async def _impl() -> Any:
        import asyncpg

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            return await factory(pool)
        finally:
            await pool.close()

    return asyncio.run(_impl())


def fmt_age(ts: Any) -> str:
    """Human-friendly relative age for a timestamp (or ``—`` when None)."""
    if ts is None:
        return "—"
    now = _dt.datetime.now(_dt.timezone.utc)
    secs = int((now - ts).total_seconds())
    if secs < 0:
        return "future"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


# A column spec: (row_key, HEADER, width, optional value->str formatter).
Column = tuple[str, str, int, Callable[[Any], str] | None]


def render_table(rows: list[dict[str, Any]], columns: list[Column], *, empty: str) -> None:
    """Print ``rows`` as an aligned, colored table using ``columns``.

    Color per row: red if it carries a ``last_error``, green if ``enabled``,
    else yellow — the convention every data-plane group already used.
    """
    if not rows:
        click.echo(empty)
        return
    header = " ".join(f"{head:<{width}}" for _, head, width, _ in columns)
    click.echo(header)
    for row in rows:
        cells = []
        for key, _, width, fmt in columns:
            value = row.get(key)
            text = fmt(value) if fmt else ("" if value is None else str(value))
            cells.append(f"{text:<{width}}")
        color = "red" if row.get("last_error") else ("green" if row.get("enabled") else "yellow")
        click.secho(" ".join(cells), fg=color)


def dump_row(row: dict[str, Any] | None, *, missing: str) -> bool:
    """Print every field of a single row, one per line. Returns False (after
    printing ``missing`` to stderr) when ``row`` is None, so the caller can
    ``sys.exit(1)``."""
    if row is None:
        click.echo(missing, err=True)
        return False
    for key, value in row.items():
        click.echo(f"  {key:<22} {value!r}")
    return True
