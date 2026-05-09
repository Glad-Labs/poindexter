"""``poindexter publishers`` — operate on the declarative publishing_adapters table.

Sibling of ``poindexter webhooks`` / ``poindexter taps`` /
``poindexter retention``. v1 ships list / show / enable / disable /
set-secret / fire — the moves an operator actually makes when wiring a
new social platform. Full CRUD (add / remove) lands once the seed flow
has been exercised enough to know the ergonomics.

All commands hit the DB directly via the same DSN-resolution pattern as
the other CLI groups. No HTTP layer — the table is small, operator-only,
and doesn't need a worker round-trip.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import click

from poindexter.cli._bootstrap import resolve_dsn as _dsn  # noqa: E402


async def _connect():
    import asyncpg
    return await asyncpg.connect(_dsn())


def _run(coro):
    return asyncio.run(coro)


def _format_age(ts: Any) -> str:
    if ts is None:
        return "—"
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    delta = now - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return "future"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


@click.group(
    name="publishers",
    help="Manage declarative publishing_adapters rows (poindexter#112).",
)
def publishers_group() -> None:
    pass


@publishers_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def publishers_list(state: str) -> None:
    """List all publisher rows with current status."""
    async def _run_list():
        conn = await _connect()
        try:
            where = ""
            if state == "enabled":
                where = "WHERE enabled = TRUE"
            elif state == "disabled":
                where = "WHERE enabled = FALSE"
            rows = await conn.fetch(
                f"""
                SELECT name, platform, handler_name, enabled,
                       last_run_at, last_run_status,
                       total_runs, total_failures, last_error
                  FROM publishing_adapters
                  {where}
              ORDER BY platform, name
                """,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    try:
        rows = _run(_run_list())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not rows:
        click.echo("(no publishing adapters)")
        return

    click.echo(
        f"{'NAME':<22} {'PLATFORM':<12} {'HANDLER':<14} {'STATE':<9} "
        f"{'LAST RUN':<12} {'RUNS':<6} {'FAIL':<5}"
    )
    for r in rows:
        state_txt = "enabled" if r["enabled"] else "disabled"
        color = "green" if r["enabled"] else "yellow"
        line = (
            f"{r['name']:<22} {r['platform']:<12} {r['handler_name']:<14} "
            f"{state_txt:<9} {_format_age(r['last_run_at']):<12} "
            f"{r['total_runs']:<6} {r['total_failures']:<5}"
        )
        click.secho(line, fg=color)


@publishers_group.command("show")
@click.argument("name")
def publishers_show(name: str) -> None:
    """Show full details of one publisher row including config + metadata."""
    async def _run_show():
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM publishing_adapters WHERE name = $1", name
            )
            return dict(row) if row else None
        finally:
            await conn.close()

    try:
        row = _run(_run_show())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not row:
        click.echo(f"(no publishing adapter named {name!r})", err=True)
        sys.exit(1)

    for key in (
        "name", "platform", "handler_name", "credentials_ref", "enabled",
        "default_tags", "rate_limit_per_day",
        "last_run_at", "last_run_status", "last_run_duration_ms",
        "total_runs", "total_failures", "last_error",
        "config", "metadata",
        "created_at", "updated_at",
    ):
        click.echo(f"  {key:<22} {row[key]!r}")


@publishers_group.command("enable")
@click.argument("name")
def publishers_enable(name: str) -> None:
    """Flip enabled=TRUE for one publisher."""
    _set_enabled(name, True)


@publishers_group.command("disable")
@click.argument("name")
def publishers_disable(name: str) -> None:
    """Flip enabled=FALSE for one publisher."""
    _set_enabled(name, False)


def _set_enabled(name: str, enabled: bool) -> None:
    async def _run_update():
        conn = await _connect()
        try:
            return await conn.execute(
                "UPDATE publishing_adapters SET enabled = $2 WHERE name = $1",
                name, enabled,
            )
        finally:
            await conn.close()

    try:
        result = _run(_run_update())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if result.endswith(" 0"):
        click.echo(f"(no row named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@publishers_group.command("set-secret")
@click.argument("name")
@click.argument("key")
@click.option(
    "--value", default=None,
    help="Provide the secret inline. Omit to be prompted (recommended).",
)
def publishers_set_secret(name: str, key: str, value: str | None) -> None:
    """Store an encrypted credential under app_settings for the publisher.

    Secrets live in ``app_settings`` (with ``is_secret=true`` flag),
    NOT in ``publishing_adapters.config``. The row's ``credentials_ref``
    column is the prefix for the keys this publisher uses — e.g. for
    ``bluesky_main`` (credentials_ref=``bluesky_``), valid keys are
    ``bluesky_identifier`` and ``bluesky_app_password``.

    The command verifies ``key`` starts with ``credentials_ref`` so an
    operator can't accidentally write a key to the wrong publisher's
    namespace.
    """
    if value is None:
        value = click.prompt(
            f"Secret value for {key}", hide_input=True, confirmation_prompt=True,
        )
    assert value is not None

    async def _run_set():
        conn = await _connect()
        try:
            ref = await conn.fetchval(
                "SELECT credentials_ref FROM publishing_adapters WHERE name = $1",
                name,
            )
            if ref is None:
                raise RuntimeError(f"no publisher named {name!r}")
            if ref and not key.startswith(ref):
                raise RuntimeError(
                    f"key {key!r} does not match publisher's credentials_ref "
                    f"prefix {ref!r}; refusing to cross-write namespaces"
                )
            from plugins.secrets import ensure_pgcrypto, set_secret
            await ensure_pgcrypto(conn)
            await set_secret(
                conn, key, value,
                description=f"Credential for publisher {name!r} (poindexter#112)",
            )
            return key
        finally:
            await conn.close()

    try:
        stored = _run(_run_set())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(
        f"Secret stored encrypted at app_settings.{stored} for publisher {name!r}",
        fg="green",
    )


@publishers_group.command("fire")
@click.argument("name")
@click.option("--text", default="poindexter test post — please ignore",
              help="Body text for the test post.")
@click.option("--url", default="https://gladlabs.io",
              help="URL to attach to the post.")
def publishers_fire(name: str, text: str, url: str) -> None:
    """Trigger one publisher manually — the 'did I configure this right?' smoke test.

    Loads the row, ensures handlers are registered, dispatches through
    the registry with the lifespan SiteConfig, prints the adapter's
    return dict.
    """
    async def _run_fire():
        from services import social_poster
        from services.integrations import registry
        from services.integrations.handlers import load_all
        from services.publishing_adapters_db import PublishingAdapterRow

        load_all()  # idempotent — registry refuses duplicate registrations
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT id, name, platform, handler_name, credentials_ref, "
                "       enabled, config, metadata "
                "  FROM publishing_adapters "
                " WHERE name = $1",
                name,
            )
            if row is None:
                raise RuntimeError(f"no publisher named {name!r}")
            pub = PublishingAdapterRow(
                id=row["id"], name=row["name"], platform=row["platform"],
                handler_name=row["handler_name"],
                credentials_ref=row["credentials_ref"],
                enabled=bool(row["enabled"]),
                config=dict(row["config"] or {}),
                metadata=dict(row["metadata"] or {}),
            )
        finally:
            await conn.close()
        return await registry.dispatch(
            "publishing", pub.handler_name,
            {"text": text, "url": url},
            site_config=social_poster.site_config,
            row=pub.as_dict(),
            pool=None,
        )

    try:
        result = _run(_run_fire())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    fg = "green" if result.get("success") else "yellow"
    click.secho(f"{name}: {result!r}", fg=fg)
