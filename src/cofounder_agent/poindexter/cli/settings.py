"""`poindexter settings` subcommands — thin wrappers over /api/settings."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click

from ._api_client import WorkerClient


def _run(coro):
    return asyncio.run(coro)


@click.group(name="settings", help="Read and write app_settings (DB-first config).")
def settings_group() -> None:
    pass


@settings_group.command("list")
@click.option("--category", default="", help="Optional category filter (e.g. pipeline, models, quality).")
@click.option("--search", default="", help="Optional substring search on key.")
@click.option("--limit", type=int, default=100, show_default=True)
@click.option(
    "--include-inactive",
    is_flag=True,
    help="Include soft-deleted settings (is_active=false).",
)
@click.option("--json", "json_output", is_flag=True)
def settings_list(
    category: str,
    search: str,
    limit: int,
    include_inactive: bool,
    json_output: bool,
) -> None:
    """List app_settings, optionally filtered by category or key substring.

    By default only active settings (`is_active=true`) are shown. Pass
    `--include-inactive` to also see soft-deleted rows (useful for
    fallback testing and debugging disabled keys).
    """

    # The HTTP endpoint doesn't expose an include_inactive filter yet, so
    # we fall back to direct DB for that case. Active-only can still go
    # through the cached HTTP path.
    if include_inactive:
        import os

        import asyncpg

        async def _list_db() -> list[dict[str, Any]]:
            dsn = (
                os.getenv("POINDEXTER_MEMORY_DSN")
                or os.getenv("LOCAL_DATABASE_URL")
                or os.getenv("DATABASE_URL")
                or ""
            )
            if not dsn:
                raise RuntimeError("No DSN — set POINDEXTER_MEMORY_DSN / LOCAL_DATABASE_URL / DATABASE_URL.")
            conn = await asyncpg.connect(dsn)
            try:
                where = ["1=1"]
                params: list[Any] = []
                if category:
                    where.append(f"category = ${len(params) + 1}")
                    params.append(category)
                if search:
                    where.append(f"key ILIKE ${len(params) + 1}")
                    params.append(f"%{search}%")
                params.append(limit)
                sql = (
                    "SELECT id, key, value, category, description, is_secret, is_active, "
                    "created_at, updated_at FROM app_settings "
                    f"WHERE {' AND '.join(where)} ORDER BY key LIMIT ${len(params)}"
                )
                rows = await conn.fetch(sql, *params)
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        try:
            items = _run(_list_db())
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        if json_output:
            click.echo(json.dumps(items, indent=2, default=str))
            return

        if not items:
            click.echo("(no settings)")
            return

        active_count = sum(1 for r in items if r.get("is_active"))
        click.secho(
            f"Settings: {len(items)} rows ({active_count} active, {len(items) - active_count} inactive)",
            fg="cyan",
        )
        click.echo()
        for r in items:
            status_color = "white" if r.get("is_active") else "bright_black"
            active_flag = "" if r.get("is_active") else "  [DISABLED]"
            value = "(encrypted)" if r.get("is_secret") else (r.get("value") or "")
            click.secho(
                f"  {r.get('category', '?')}/{r.get('key', '?'):<40} = {str(value)[:70]}{active_flag}",
                fg=status_color,
            )
        return

    # Active-only path: use HTTP endpoint (which already filters is_active)
    async def _list():
        params: dict[str, str | int] = {"per_page": limit}
        if category:
            params["category"] = category
        if search:
            params["search"] = search
        async with WorkerClient() as c:
            resp = await c.get("/api/settings", params=params)
            return await c.json_or_raise(resp)

    try:
        data = _run(_list())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    items = data.get("items") or []
    if json_output:
        click.echo(json.dumps(items, indent=2, default=str))
        return

    if not items:
        click.echo("(no settings)")
        return

    click.secho(f"Settings: {len(items)} of {data.get('total', '?')}", fg="cyan")
    click.echo()
    for s in items:
        key = s.get("key", "?")
        value = s.get("value_preview") or s.get("value") or ""
        cat = s.get("category", "?")
        if s.get("is_encrypted"):
            value = "******* (encrypted)"
        click.echo(f"  {cat}/{key:<40} = {str(value)[:80]}")


@settings_group.command("get")
@click.argument("key")
@click.option("--json", "json_output", is_flag=True)
def settings_get(key: str, json_output: bool) -> None:
    """Get a specific setting by key."""

    async def _get():
        async with WorkerClient() as c:
            resp = await c.get(f"/api/settings/{key}")
            return await c.json_or_raise(resp)

    try:
        s = _run(_get())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(s, indent=2, default=str))
        return

    value = s.get("value", "")
    if s.get("is_encrypted"):
        value = "******* (encrypted)"
    click.secho(f"{s.get('key', key)} ({s.get('category', '?')})", fg="cyan")
    click.echo(f"  value       {value}")
    click.echo(f"  data_type   {s.get('data_type', '?')}")
    click.echo(f"  description {s.get('description', '')}")
    click.echo(f"  updated_at  {s.get('updated_at', '?')}")


@settings_group.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--category", default="general", show_default=True)
@click.option("--description", default="", help="Optional human-readable description.")
def settings_set(key: str, value: str, category: str, description: str) -> None:
    """Upsert a setting by key — creates it if missing, updates if present.

    Uses a direct DB upsert rather than the HTTP `PUT /api/settings/{key}`
    endpoint because the latter is update-only (404s on missing keys) and
    the POST-then-PUT dance isn't worth the round-trips. Writing a value
    also re-activates a previously disabled setting — the same behavior
    `admin_db.set_setting` provides.
    """

    async def _upsert() -> bool:
        import os

        import asyncpg

        dsn = (
            os.getenv("POINDEXTER_MEMORY_DSN")
            or os.getenv("LOCAL_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or ""
        )
        if not dsn:
            raise RuntimeError(
                "No DSN found — set POINDEXTER_MEMORY_DSN, LOCAL_DATABASE_URL, or DATABASE_URL."
            )
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_active)
                VALUES ($1, $2, $3, $4, true)
                ON CONFLICT (key) DO UPDATE SET
                    value       = EXCLUDED.value,
                    category    = COALESCE(EXCLUDED.category, app_settings.category),
                    description = COALESCE(EXCLUDED.description, app_settings.description),
                    is_active   = true,
                    updated_at  = NOW()
                """,
                key,
                value,
                category,
                description,
            )
            return True
        finally:
            await conn.close()

    try:
        _run(_upsert())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.secho(f"Updated: {key} = {value}", fg="green")


# ---------------------------------------------------------------------------
# enable / disable — soft activation toggle
# ---------------------------------------------------------------------------
#
# Goes through the HTTP endpoint (POST /api/settings/{key}/activate) so the
# worker's 60s admin_db settings cache is invalidated as part of the toggle.
# A direct DB UPDATE would work but leave stale values in the in-process
# cache, which matters for fallback-test workflows where you disable a
# setting and immediately want the fallback path to kick in.


async def _toggle_active(key: str, active: bool) -> bool:
    async with WorkerClient() as c:
        resp = await c.post(
            f"/api/settings/{key}/activate",
            json={"active": active},
        )
        if resp.status_code == 404:
            return False
        await c.json_or_raise(resp)
        return True


@settings_group.command("disable")
@click.argument("key")
def settings_disable(key: str) -> None:
    """Soft-delete a setting — `is_active=false`, value preserved.

    Use this to test fallback behavior without losing the current value.
    Re-enable with `poindexter settings enable <key>`.
    """
    try:
        updated = _run(_toggle_active(key, False))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not updated:
        click.echo(f"No setting found for key '{key}'", err=True)
        sys.exit(1)
    click.secho(f"Disabled: {key} (value preserved, is_active=false)", fg="yellow")


@settings_group.command("enable")
@click.argument("key")
def settings_enable(key: str) -> None:
    """Re-activate a previously disabled setting."""
    try:
        updated = _run(_toggle_active(key, True))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not updated:
        click.echo(f"No setting found for key '{key}'", err=True)
        sys.exit(1)
    click.secho(f"Enabled: {key} (is_active=true)", fg="green")
