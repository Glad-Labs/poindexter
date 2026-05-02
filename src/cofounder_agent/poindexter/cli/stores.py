"""`poindexter stores` — manage the object_stores table (GH-113).

Object stores are S3-compatible buckets that the pipeline uploads media
into (R2, AWS S3, B2, MinIO, Wasabi). Each row is one destination —
operators add a row to send podcast files to a different bucket from
featured images, or rotate to a new region without touching code.

v1 set: list / show / enable / disable / set-secret. Add / remove fall
out naturally from direct SQL (or the upcoming generic ``poindexter
data add-row`` command from the declarative-data-plane RFC).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click


from poindexter.cli._bootstrap import resolve_dsn as _dsn  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _connect():
    import asyncpg
    return await asyncpg.connect(_dsn())


def _format_age(ts: Any) -> str:
    if ts is None:
        return "—"
    import datetime as _dt
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


@click.group(
    name="stores",
    help="Manage S3-compatible object_stores rows (R2/S3/B2/MinIO/Wasabi).",
)
def stores_group() -> None:
    pass


@stores_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def stores_list(state: str) -> None:
    """List every configured object store with current status."""
    async def _impl():
        conn = await _connect()
        try:
            where = ""
            if state == "enabled":
                where = "WHERE enabled = TRUE"
            elif state == "disabled":
                where = "WHERE enabled = FALSE"
            rows = await conn.fetch(
                f"""
                SELECT name, provider, bucket, public_url, enabled,
                       cache_busting_strategy, last_upload_at,
                       last_upload_status, total_uploads, total_failures,
                       last_error
                  FROM object_stores
                  {where}
              ORDER BY name
                """
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    try:
        rows = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not rows:
        click.echo("(no object stores configured)")
        return

    click.echo(
        f"{'NAME':<18} {'PROVIDER':<14} {'BUCKET':<24} {'STATE':<9} "
        f"{'CACHE':<14} {'LAST':<12} {'UPLOADS':<8} {'FAILS':<6}"
    )
    for r in rows:
        state_txt = "enabled" if r["enabled"] else "disabled"
        color = "red" if r["last_error"] else ("green" if r["enabled"] else "yellow")
        line = (
            f"{r['name']:<18} {r['provider']:<14} {(r['bucket'] or ''):<24} "
            f"{state_txt:<9} {r['cache_busting_strategy']:<14} "
            f"{_format_age(r['last_upload_at']):<12} "
            f"{r['total_uploads']:<8} {r['total_failures']:<6}"
        )
        click.secho(line, fg=color)


@stores_group.command("show")
@click.argument("name")
def stores_show(name: str) -> None:
    """Show full details of one store row.

    The ``credentials_ref`` column is shown verbatim (it's a pointer to
    an app_settings key, not a secret itself). The actual secret value
    is masked — to update it use ``poindexter stores set-secret``.
    """
    async def _impl():
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM object_stores WHERE name = $1", name,
            )
            if not row:
                return None, None
            # Side-load the credentials_ref pointer so the user can see
            # whether it's set without revealing the value.
            ref = row["credentials_ref"]
            ref_set = False
            if ref:
                ref_row = await conn.fetchrow(
                    "SELECT value FROM app_settings WHERE key = $1 AND is_active = true",
                    ref,
                )
                ref_set = bool(ref_row and ref_row["value"])
            return dict(row), ref_set
        finally:
            await conn.close()

    try:
        row, ref_set = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not row:
        click.echo(f"(no store named {name!r})", err=True)
        sys.exit(1)

    for key in (
        "name", "provider", "endpoint_url", "bucket", "public_url",
        "credentials_ref", "cache_busting_strategy", "cache_busting_config",
        "enabled", "metadata",
        "last_upload_at", "last_upload_status", "last_error",
        "total_uploads", "total_failures", "total_bytes_uploaded",
        "created_at", "updated_at",
    ):
        click.echo(f"  {key:<24} {row[key]!r}")
    # Masked credentials view — just whether it's set, never the value.
    cred_status = "******* (set, encrypted)" if ref_set else "(not set)"
    click.echo(f"  credentials              {cred_status}")


@stores_group.command("enable")
@click.argument("name")
def stores_enable(name: str) -> None:
    _set_enabled(name, True)


@stores_group.command("disable")
@click.argument("name")
def stores_disable(name: str) -> None:
    _set_enabled(name, False)


def _set_enabled(name: str, enabled: bool) -> None:
    async def _impl():
        conn = await _connect()
        try:
            return await conn.execute(
                "UPDATE object_stores SET enabled = $2 WHERE name = $1",
                name, enabled,
            )
        finally:
            await conn.close()

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # asyncpg execute returns a status string like "UPDATE 1"
    if result.endswith(" 0"):
        click.echo(f"(no store named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@stores_group.command("set-secret")
@click.argument("name")
@click.option(
    "--access-key", default=None,
    help="S3 access key (prompted if omitted).",
)
@click.option(
    "--secret-key", default=None,
    help="S3 secret key (prompted if omitted; hidden input).",
)
@click.option(
    "--from-stdin", is_flag=True,
    help=(
        "Read JSON ``{access_key, secret_key}`` from stdin instead of "
        "prompting — handy for scripted rotation."
    ),
)
def stores_set_secret(
    name: str,
    access_key: str | None,
    secret_key: str | None,
    from_stdin: bool,
) -> None:
    """Store an encrypted ``{access_key, secret_key}`` JSON blob for the store.

    Looks up the row's ``credentials_ref`` column and writes the JSON
    blob to that app_settings key under pgcrypto encryption. If
    ``credentials_ref`` is NULL the command refuses — set it first via
    direct SQL (``UPDATE object_stores SET credentials_ref=...``).

    Two input modes:
      - **Interactive** (default): prompts for access_key, then secret_key
        with hidden input + confirmation. Most common operator path.
      - **Inline**: pass ``--access-key`` and ``--secret-key`` directly
        (visible in shell history — only do this for ephemeral test envs).
      - **Stdin JSON**: ``--from-stdin`` reads ``{"access_key": "...",
        "secret_key": "..."}`` from stdin. Useful for pipeline scripts.
    """
    if from_stdin:
        try:
            payload = json.loads(sys.stdin.read())
        except (ValueError, TypeError) as e:
            click.echo(f"Invalid JSON on stdin: {e}", err=True)
            sys.exit(1)
        access_key = payload.get("access_key", "")
        secret_key = payload.get("secret_key", "")
        if not access_key or not secret_key:
            click.echo(
                "JSON on stdin must include non-empty 'access_key' and 'secret_key'",
                err=True,
            )
            sys.exit(1)
    else:
        if access_key is None:
            access_key = click.prompt(f"Access key for {name}")
        if secret_key is None:
            secret_key = click.prompt(
                f"Secret key for {name}",
                hide_input=True,
                confirmation_prompt=True,
            )
    assert access_key is not None
    assert secret_key is not None

    blob = json.dumps({"access_key": access_key, "secret_key": secret_key})

    async def _run_set():
        conn = await _connect()
        try:
            ref = await conn.fetchval(
                "SELECT credentials_ref FROM object_stores WHERE name = $1",
                name,
            )
            if not ref:
                raise RuntimeError(
                    f"{name!r}: no credentials_ref on the row. "
                    "Set it first so the secret has somewhere to land."
                )
            from plugins.secrets import ensure_pgcrypto, set_secret
            await ensure_pgcrypto(conn)
            await set_secret(
                conn, ref, blob,
                description=f"S3 credentials JSON for object_store {name!r} (GH-113)",
            )
            return ref
        finally:
            await conn.close()

    try:
        ref = _run(_run_set())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(
        f"Credentials stored encrypted at app_settings.{ref} for store {name!r}",
        fg="green",
    )
