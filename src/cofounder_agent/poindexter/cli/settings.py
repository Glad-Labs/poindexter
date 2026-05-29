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


def _split_category_prefix(key: str) -> tuple[str, str | None]:
    """Strip an optional ``<category>/`` prefix from a settings key.

    ``settings list`` previously rendered rows as ``category/key`` and
    operators copy-pasted that form into ``settings set`` / ``settings
    get``. To support the copy-paste workflow without re-introducing
    the phantom-key trap (Glad-Labs/poindexter#253), the CLI accepts
    either a bare key or the ``category/key`` form and uses the bare
    key for the actual DB operation.

    Per spec we use ``rsplit("/", 1)`` so the canonical key is always
    the right-most token. The supplied prefix is returned alongside so
    callers can warn when it disagrees with the row's actual
    ``category`` column.

    Mirrors ``mcp-server/server.py::_strip_category_prefix`` behaviour
    (which uses ``partition("/")`` — first-slash split — because MCP
    server treats the slash as a single-level category separator;
    ``rsplit`` here is the spec'd CLI behaviour so canonical keys that
    themselves contain ``/`` still resolve to the right-most segment).
    """
    if "/" not in key:
        return key, None
    prefix, canonical = key.rsplit("/", 1)
    return canonical, prefix


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
        import asyncpg

        from poindexter.cli._bootstrap import resolve_dsn

        async def _list_db() -> list[dict[str, Any]]:
            dsn = resolve_dsn()
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
        # Leftmost token is the bare key so naive copy-paste into
        # ``settings set <key> <value>`` lands on the canonical row
        # rather than creating a phantom ``category/key`` upsert.
        for r in items:
            status_color = "white" if r.get("is_active") else "bright_black"
            active_flag = "" if r.get("is_active") else "  [DISABLED]"
            value = "(encrypted)" if r.get("is_secret") else (r.get("value") or "")
            category = r.get("category", "?")
            key_name = r.get("key", "?")
            click.secho(
                f"  {key_name:<40} [{category}] = {str(value)[:70]}{active_flag}",
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
    # Leftmost token is the bare key — see _split_category_prefix
    # for the rationale (Glad-Labs/poindexter#253 phantom-key trap).
    for s in items:
        key = s.get("key", "?")
        value = s.get("value_preview") or s.get("value") or ""
        cat = s.get("category", "?")
        if s.get("is_encrypted"):
            value = "******* (encrypted)"
        click.echo(f"  {key:<40} [{cat}] = {str(value)[:80]}")


@settings_group.command("get")
@click.argument("key")
@click.option("--json", "json_output", is_flag=True)
def settings_get(key: str, json_output: bool) -> None:
    """Get a specific setting by key.

    Accepts either a bare key or the ``category/key`` form rendered by
    older ``settings list`` output. The ``category/`` prefix is
    auto-stripped before the lookup — see :func:`_split_category_prefix`.
    """
    # Auto-strip the optional category/ prefix so copy-paste from the
    # display form still resolves. Mirrors the MCP server's behaviour.
    canonical_key, _supplied_prefix = _split_category_prefix(key)

    async def _get():
        async with WorkerClient() as c:
            resp = await c.get(f"/api/settings/{canonical_key}")
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
    click.secho(f"{s.get('key', canonical_key)} ({s.get('category', '?')})", fg="cyan")
    click.echo(f"  value       {value}")
    click.echo(f"  data_type   {s.get('data_type', '?')}")
    click.echo(f"  description {s.get('description', '')}")
    click.echo(f"  updated_at  {s.get('updated_at', '?')}")


@settings_group.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--category", default="general", show_default=True)
@click.option("--description", default="", help="Optional human-readable description.")
@click.option(
    "--allow-new",
    is_flag=True,
    help="Allow creating a brand-new setting key. Without this flag, an "
         "unknown bare key fails loud rather than silently upserting a "
         "row no consumer will read. Orthogonal to the slash-prefix "
         "handling — ``category/key`` always resolves to the bare "
         "canonical key, never creates a phantom row.",
)
def settings_set(
    key: str, value: str, category: str, description: str, allow_new: bool,
) -> None:
    """Upsert a setting by key — creates it (with ``--allow-new``) or updates if present.

    Uses a direct DB upsert rather than the HTTP ``PUT /api/settings/{key}``
    endpoint because the latter is update-only (404s on missing keys) and
    the POST-then-PUT dance isn't worth the round-trips. Writing a value
    also re-activates a previously disabled setting — the same behavior
    ``admin_db.set_setting`` provides.

    Accepts either a bare key or the ``category/key`` form rendered by
    older ``settings list`` output. The ``category/`` prefix is
    auto-stripped before the upsert — operators copy-pasting from list
    output get the canonical row updated, not a phantom new row.

    Phantom-key trap history (Glad-Labs/poindexter#253): the original
    ``settings list`` output rendered every row as
    ``{category}/{key} = {value}``. Operators copy that visible form and
    ran ``settings set pipeline/daily_post_limit 4``, which UPSERTed a
    NEW row with the literal key ``pipeline/daily_post_limit`` instead
    of updating the canonical key ``daily_post_limit``. Consumers only
    read canonical keys, so the "set" was silently dead. The 2026-05-27
    bandaid (reject any key containing ``/``) was replaced 2026-05-28
    with proper UX: auto-strip the prefix AND reshape list output so
    the leftmost token is always the bare key.
    """
    canonical_key, supplied_prefix = _split_category_prefix(key)

    async def _upsert() -> bool:
        import asyncpg

        from poindexter.cli._bootstrap import resolve_dsn

        dsn = resolve_dsn()
        conn = await asyncpg.connect(dsn)
        try:
            existing_row = await conn.fetchrow(
                "SELECT key, category FROM app_settings WHERE key = $1",
                canonical_key,
            )

            # Case A: a slash was supplied AND the canonical row exists.
            # Auto-strip the prefix; warn if it disagrees with the
            # actual category column (informational only — proceed
            # anyway, matching the MCP tool's behaviour).
            if supplied_prefix is not None and existing_row is not None:
                actual_category = existing_row["category"] or ""
                if supplied_prefix != actual_category:
                    click.secho(
                        f"warning: supplied prefix {supplied_prefix!r} does not "
                        f"match the row's actual category {actual_category!r}; "
                        f"ignoring the prefix and updating the canonical key "
                        f"{canonical_key!r} anyway",
                        fg="yellow",
                        err=True,
                    )

            # Case B: a slash was supplied AND the canonical row does
            # NOT exist. Fail loud — this is either a typo, a stale
            # paste from a no-longer-existing row, or a genuine new-key
            # request. Operator must opt in with --allow-new + a bare
            # key (no slash) to create a row.
            if supplied_prefix is not None and existing_row is None:
                click.echo(
                    f"Error: no setting named {canonical_key!r} found.\n"
                    f"\n"
                    f"You supplied {key!r} (with a category prefix). If you "
                    f"genuinely want to create a new key, re-run with "
                    f"`--allow-new` AND pass just the bare key (no '/'):\n"
                    f"\n"
                    f"    poindexter settings set {canonical_key} {value} "
                    f"--allow-new --category {supplied_prefix}\n",
                    err=True,
                )
                sys.exit(2)

            # Case C: bare key, no canonical row — only allowed with
            # --allow-new. Without the flag, fail loud so a typo
            # doesn't silently create a phantom row no consumer reads.
            if supplied_prefix is None and existing_row is None and not allow_new:
                click.echo(
                    f"Error: no setting named {canonical_key!r} found.\n"
                    f"\n"
                    f"If you genuinely want to create a new key, re-run "
                    f"with `--allow-new` (and `--category {category}` if "
                    f"you want a non-default category).",
                    err=True,
                )
                sys.exit(2)

            # Case D (regular case): bare key with existing row → update.
            # Or bare key + --allow-new + no existing row → create.
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
                canonical_key,
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

    click.secho(f"Updated: {canonical_key} = {value}", fg="green")


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

    Accepts the ``category/key`` form (auto-stripped).
    """
    canonical_key, _ = _split_category_prefix(key)
    try:
        updated = _run(_toggle_active(canonical_key, False))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not updated:
        click.echo(f"No setting found for key '{canonical_key}'", err=True)
        sys.exit(1)
    click.secho(
        f"Disabled: {canonical_key} (value preserved, is_active=false)",
        fg="yellow",
    )


@settings_group.command("enable")
@click.argument("key")
def settings_enable(key: str) -> None:
    """Re-activate a previously disabled setting.

    Accepts the ``category/key`` form (auto-stripped).
    """
    canonical_key, _ = _split_category_prefix(key)
    try:
        updated = _run(_toggle_active(canonical_key, True))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not updated:
        click.echo(f"No setting found for key '{canonical_key}'", err=True)
        sys.exit(1)
    click.secho(f"Enabled: {canonical_key} (is_active=true)", fg="green")
