"""``poindexter schedule`` — operator interface for the scheduled
publisher queue (Glad-Labs/poindexter#147).

Subcommands
-----------

* ``poindexter publish-at <post_id> <when>`` — single-post slot
* ``poindexter schedule batch --count N --interval DUR --start TIME``
* ``poindexter schedule list [--json]``
* ``poindexter schedule show <post_id> [--json]``
* ``poindexter schedule shift <post_id|--all> --by DUR``
* ``poindexter schedule clear <post_id|--all>``

All commands talk directly to the local Postgres pool (no HTTP hop) so
they keep working when the FastAPI worker is down.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click


# ---------------------------------------------------------------------------
# DSN + asyncpg helpers (mirror the qa-gates / taps CLI patterns)
# ---------------------------------------------------------------------------


from poindexter.cli._bootstrap import resolve_dsn as _dsn  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _SinglePool:
    """Tiny asyncpg-pool-shaped wrapper around a single connection.

    The scheduling service expects a ``pool.acquire()`` async context
    manager. For one-shot CLI commands we don't need a real pool —
    a single connection is plenty. This wrapper presents the right
    surface area without pulling in asyncpg.create_pool."""

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):  # type: ignore[no-untyped-def]
        conn = self._conn

        class _Ctx:
            async def __aenter__(self_inner):  # noqa: N805
                return conn

            async def __aexit__(self_inner, *exc):  # noqa: N805
                return False

        return _Ctx()


async def _with_pool(fn):
    """Open one asyncpg connection, hand a pool-shaped wrapper to ``fn``,
    close on exit."""
    import asyncpg

    conn = await asyncpg.connect(_dsn())
    try:
        pool = _SinglePool(conn)
        return await fn(pool)
    finally:
        await conn.close()


def _make_site_config():
    """Build a SiteConfig from the same DSN the CLI just opened.

    Loads ``app_settings`` so ``publish_quiet_hours`` etc. are
    available without extra plumbing. Falls back to an empty config
    if the DB is unreachable so the CLI still parses ``--quiet-hours``
    overrides.
    """
    from services.site_config import SiteConfig
    return SiteConfig(initial_config={})


async def _load_site_config(pool):
    from services.site_config import SiteConfig

    cfg = SiteConfig(initial_config={})
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT key, value FROM app_settings WHERE is_secret = false"
            )
        for r in rows:
            if r["value"]:
                cfg._config[r["key"]] = r["value"]  # noqa: SLF001
        cfg._loaded = True  # noqa: SLF001
    except Exception:
        # If app_settings hasn't been bootstrapped yet, an empty
        # SiteConfig is still useful — operator overrides via CLI
        # flags still flow through.
        pass
    return cfg


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _emit_json(obj: Any) -> None:
    click.echo(json.dumps(obj, indent=2, default=str))


def _format_row_line(r: dict) -> str:
    pid = (r.get("post_id") or r.get("id") or "?")
    if isinstance(pid, str):
        pid = pid[:8]
    title = r.get("title") or "(no title)"
    pa = r.get("published_at")
    pa_s = pa.isoformat() if hasattr(pa, "isoformat") else str(pa) if pa else "—"
    status = r.get("status") or "?"
    return f"  {pid}  {status:<10}  {pa_s:<25}  {title[:55]}"


def _result_to_dict(result) -> dict:
    """Serialise a ScheduleResult dataclass for --json output."""
    return {
        "ok": result.ok,
        "detail": result.detail,
        "count": result.count,
        "rows": result.rows,
    }


def _print_result(result, *, json_output: bool, header: str = "") -> int:
    if json_output:
        _emit_json(_result_to_dict(result))
        return 0 if result.ok else 1

    if header:
        click.secho(header, fg="cyan")
    click.echo(result.detail)
    if result.rows:
        click.echo()
        for r in result.rows:
            click.echo(_format_row_line(r))
    return 0 if result.ok else 1


# ---------------------------------------------------------------------------
# `poindexter schedule` group
# ---------------------------------------------------------------------------


@click.group(
    name="schedule",
    help=(
        "Manage the scheduled-publish queue (Glad-Labs/poindexter#147).\n\n"
        "The system already has a background loop that publishes posts when "
        "their schedule time arrives. These commands populate, inspect, "
        "shift, and clear that queue."
    ),
)
def schedule_group() -> None:
    pass


# ---------------------------------------------------------------------------
# schedule batch
# ---------------------------------------------------------------------------


@schedule_group.command("batch")
@click.option(
    "--count", type=int, required=True,
    help="How many approved posts to schedule.",
)
@click.option(
    "--interval", type=str, required=True,
    help="Spacing between slots — e.g. 30m, 1h, 1h30m, 2h, 1d.",
)
@click.option(
    "--start", type=str, required=True,
    help="First slot — ISO 8601, 'now', 'tomorrow 9am', 'next monday 14:00'.",
)
@click.option(
    "--quiet-hours", "quiet_hours", type=str, default=None,
    help="Skip slots inside HH:MM-HH:MM (e.g. 22:00-07:00). "
         "Falls back to publish_quiet_hours app_setting when omitted.",
)
@click.option(
    "--ordered-by", type=str, default="approved_at", show_default=True,
    help="Source-queue ordering — approved_at | created_at | id | title.",
)
@click.option(
    "--force", is_flag=True,
    help="Re-schedule posts even if they already have a slot.",
)
@click.option("--json", "json_output", is_flag=True)
def schedule_batch(
    count: int,
    interval: str,
    start: str,
    quiet_hours: str | None,
    ordered_by: str,
    force: bool,
    json_output: bool,
) -> None:
    """Bulk-assign publish slots to the approved queue.

    Reads up to N approved posts (in approval order by default) and
    walks the slot calendar: first at --start, second at --start +
    --interval, etc. Slots inside the quiet-hours window are skipped
    forward to the next allowed time.
    """
    from services.scheduling_service import assign_batch

    async def _impl():
        async def _run_with_pool(pool):
            cfg = await _load_site_config(pool)
            return await assign_batch(
                count=count,
                interval=interval,
                start=start,
                quiet_hours=quiet_hours,
                ordered_by=ordered_by,
                pool=pool,
                site_config=cfg,
                force=force,
            )

        return await _with_pool(_run_with_pool)

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    sys.exit(_print_result(result, json_output=json_output))


# ---------------------------------------------------------------------------
# schedule list
# ---------------------------------------------------------------------------


@schedule_group.command("list")
@click.option(
    "--all", "show_all", is_flag=True,
    help="Include past schedules (default: upcoming only).",
)
@click.option("--json", "json_output", is_flag=True)
def schedule_list(show_all: bool, json_output: bool) -> None:
    """List scheduled posts in publish-time order."""
    from services.scheduling_service import list_scheduled

    async def _impl():
        async def _run_with_pool(pool):
            return await list_scheduled(pool=pool, upcoming_only=not show_all)

        return await _with_pool(_run_with_pool)

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    if not result.rows and not json_output:
        click.echo("(no scheduled posts)")
        return

    sys.exit(_print_result(
        result,
        json_output=json_output,
        header=f"Scheduled posts: {len(result.rows)}",
    ))


# ---------------------------------------------------------------------------
# schedule show
# ---------------------------------------------------------------------------


@schedule_group.command("show")
@click.argument("post_id")
@click.option("--json", "json_output", is_flag=True)
def schedule_show(post_id: str, json_output: bool) -> None:
    """Show schedule detail for a single post."""
    from services.scheduling_service import show_scheduled

    async def _impl():
        async def _run_with_pool(pool):
            return await show_scheduled(post_id, pool=pool)

        return await _with_pool(_run_with_pool)

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    sys.exit(_print_result(result, json_output=json_output))


# ---------------------------------------------------------------------------
# schedule shift
# ---------------------------------------------------------------------------


@schedule_group.command("shift")
@click.argument("post_id", required=False)
@click.option("--all", "shift_all", is_flag=True,
              help="Shift every still-future scheduled post.")
@click.option("--by", "by_delta", type=str, required=True,
              help="Duration to shift by (e.g. 1h, 30m, 2h, 1d).")
@click.option("--json", "json_output", is_flag=True)
def schedule_shift(
    post_id: str | None,
    shift_all: bool,
    by_delta: str,
    json_output: bool,
) -> None:
    """Push a single schedule (or every future one with --all) by the
    given duration."""
    if not post_id and not shift_all:
        click.echo(
            "Error: provide a POST_ID or --all (got neither).", err=True
        )
        sys.exit(2)
    if post_id and shift_all:
        click.echo(
            "Error: provide POST_ID or --all, not both.", err=True
        )
        sys.exit(2)

    from services.scheduling_service import shift as shift_fn

    async def _impl():
        async def _run_with_pool(pool):
            cfg = await _load_site_config(pool)
            ids = None if shift_all else [post_id]
            return await shift_fn(
                by_delta=by_delta,
                post_ids=ids,
                pool=pool,
                site_config=cfg,
            )

        return await _with_pool(_run_with_pool)

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    sys.exit(_print_result(result, json_output=json_output))


# ---------------------------------------------------------------------------
# schedule clear
# ---------------------------------------------------------------------------


@schedule_group.command("clear")
@click.argument("post_id", required=False)
@click.option("--all", "clear_all", is_flag=True,
              help="Clear every still-future scheduled post.")
@click.option("--json", "json_output", is_flag=True)
def schedule_clear(
    post_id: str | None,
    clear_all: bool,
    json_output: bool,
) -> None:
    """Drop the schedule (resets to status='approved')."""
    if not post_id and not clear_all:
        click.echo(
            "Error: provide a POST_ID or --all (got neither).", err=True
        )
        sys.exit(2)
    if post_id and clear_all:
        click.echo(
            "Error: provide POST_ID or --all, not both.", err=True
        )
        sys.exit(2)

    from services.scheduling_service import clear as clear_fn

    async def _impl():
        async def _run_with_pool(pool):
            cfg = await _load_site_config(pool)
            ids = None if clear_all else [post_id]
            return await clear_fn(
                post_ids=ids,
                pool=pool,
                site_config=cfg,
            )

        return await _with_pool(_run_with_pool)

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    sys.exit(_print_result(result, json_output=json_output))


# ---------------------------------------------------------------------------
# `poindexter publish-at` — single-post convenience command
# ---------------------------------------------------------------------------


@click.command(
    name="publish-at",
    help=(
        "Schedule a single post for a specific time.\n\n"
        "TIME accepts ISO 8601 ('2026-04-28 09:00'), 'now', "
        "'tomorrow 9am', or 'next monday 14:00'. "
        "Pass --in DUR instead of TIME for relative scheduling "
        "(e.g. --in 2h)."
    ),
)
@click.argument("post_id")
@click.argument("time_spec", required=False)
@click.option("--in", "in_delta", type=str, default=None,
              help="Schedule N from now — e.g. --in 2h, --in 7d.")
@click.option("--force", is_flag=True,
              help="Overwrite any existing schedule.")
@click.option("--json", "json_output", is_flag=True)
def publish_at_command(
    post_id: str,
    time_spec: str | None,
    in_delta: str | None,
    force: bool,
    json_output: bool,
) -> None:
    """Single-post scheduling shortcut."""
    from datetime import datetime, timezone

    from services.scheduling_service import (
        assign_slot,
        parse_duration,
        parse_when,
    )

    if (time_spec is None) == (in_delta is None):
        click.echo(
            "Error: provide either TIME_SPEC or --in DUR (got "
            f"{'both' if time_spec else 'neither'}).",
            err=True,
        )
        sys.exit(2)

    try:
        if in_delta is not None:
            target = datetime.now(timezone.utc) + parse_duration(in_delta)
        else:
            target = parse_when(time_spec)  # type: ignore[arg-type]
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    async def _impl():
        async def _run_with_pool(pool):
            cfg = await _load_site_config(pool)
            return await assign_slot(
                post_id, target, pool=pool, site_config=cfg, force=force,
            )

        return await _with_pool(_run_with_pool)

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    sys.exit(_print_result(result, json_output=json_output))
