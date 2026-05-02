"""``poindexter validators`` -- manage the ``content_validator_rules`` table.

Validators CRUD V1 (migration 0135). Each row is one fine-grained content
rule -- ``first_person_claims``, ``unlinked_citation``, ``code_block_density``,
etc. -- and operators flip them on/off, tune severity, override the JSON
threshold, or scope them to specific niches at runtime.

Commands::

    poindexter validators list                                 # all rows
    poindexter validators list --state enabled                 # filter
    poindexter validators show NAME                            # full row dump
    poindexter validators enable NAME
    poindexter validators disable NAME
    poindexter validators set-severity NAME LEVEL              # info|warning|error
    poindexter validators set-threshold NAME KEY=VALUE [...]   # JSON-merged
    poindexter validators set-niches NAME a,b,c | --all        # scope or wide-open

Mutations are persisted directly to PostgreSQL. The runtime
(``services.validator_config``) caches rules for ``_CACHE_TTL_SECONDS``
(60s) so changes propagate within the next minute -- no app restart
required.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click


_VALID_SEVERITIES = ("info", "warning", "error")


from poindexter.cli._bootstrap import resolve_dsn as _dsn  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _connect():
    import asyncpg
    return await asyncpg.connect(_dsn())


@click.group(
    name="validators",
    help="Manage fine-grained content validator rules (content_validator_rules).",
)
def validators_group() -> None:
    """Root for ``poindexter validators ...`` commands."""


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@validators_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def validators_list(state: str) -> None:
    """List every validator rule with current state, severity, and niche scope."""
    async def _impl() -> list[dict[str, Any]]:
        conn = await _connect()
        try:
            where = ""
            args: list[Any] = []
            if state == "enabled":
                where = "WHERE enabled = TRUE"
            elif state == "disabled":
                where = "WHERE enabled = FALSE"
            rows = await conn.fetch(
                f"""
                SELECT name, enabled, severity, applies_to_niches, threshold,
                       description
                  FROM content_validator_rules
                  {where}
              ORDER BY name ASC
                """,
                *args,
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
        click.echo("(no content_validator_rules rows -- run migration 0135)")
        return

    click.echo(
        f"{'NAME':<26} {'STATE':<9} {'SEVERITY':<9} {'NICHES':<24}"
    )
    for r in rows:
        state_txt = "enabled" if r["enabled"] else "disabled"
        niches_raw = r["applies_to_niches"]
        if niches_raw is None:
            niches_txt = "(all)"
        elif not niches_raw:
            niches_txt = "(none)"
        else:
            niches_txt = ",".join(niches_raw)
        if len(niches_txt) > 23:
            niches_txt = niches_txt[:20] + "..."
        color = "green" if r["enabled"] else "yellow"
        click.secho(
            f"{r['name']:<26} {state_txt:<9} {r['severity']:<9} {niches_txt:<24}",
            fg=color,
        )


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@validators_group.command("show")
@click.argument("name")
def validators_show(name: str) -> None:
    """Show full details of a single validator rule."""
    async def _impl():
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT name, enabled, severity, threshold, applies_to_niches,
                       description, created_at, updated_at
                  FROM content_validator_rules
                 WHERE name = $1
                """,
                name,
            )
            return dict(row) if row else None
        finally:
            await conn.close()

    try:
        row = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not row:
        click.echo(f"(no content_validator_rules row named {name!r})", err=True)
        sys.exit(1)

    for key in (
        "name", "enabled", "severity", "threshold", "applies_to_niches",
        "description", "created_at", "updated_at",
    ):
        val = row.get(key)
        if isinstance(val, (dict, list)):
            val = json.dumps(val, default=str)
        click.echo(f"  {key:<20} {val!r}")


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


@validators_group.command("enable")
@click.argument("name")
def validators_enable(name: str) -> None:
    """Mark a validator enabled -- runtime picks it up within ~60s (cache TTL)."""
    _set_enabled(name, True)


@validators_group.command("disable")
@click.argument("name")
def validators_disable(name: str) -> None:
    """Mark a validator disabled -- runtime stops firing it within ~60s."""
    _set_enabled(name, False)


def _set_enabled(name: str, enabled: bool) -> None:
    async def _impl():
        conn = await _connect()
        try:
            return await conn.execute(
                "UPDATE content_validator_rules SET enabled = $2, "
                "updated_at = NOW() WHERE name = $1",
                name, enabled,
            )
        finally:
            await conn.close()

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if result.endswith(" 0"):
        click.echo(f"(no content_validator_rules row named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


# ---------------------------------------------------------------------------
# set-severity
# ---------------------------------------------------------------------------


@validators_group.command("set-severity")
@click.argument("name")
@click.argument("level", type=click.Choice(_VALID_SEVERITIES))
def validators_set_severity(name: str, level: str) -> None:
    """Change a rule's severity tier ('info', 'warning', 'error')."""
    async def _impl():
        conn = await _connect()
        try:
            return await conn.execute(
                "UPDATE content_validator_rules SET severity = $2, "
                "updated_at = NOW() WHERE name = $1",
                name, level,
            )
        finally:
            await conn.close()

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if result.endswith(" 0"):
        click.echo(f"(no content_validator_rules row named {name!r})", err=True)
        sys.exit(1)
    click.secho(f"{name}: severity = {level}", fg="cyan")


# ---------------------------------------------------------------------------
# set-threshold
# ---------------------------------------------------------------------------


@validators_group.command("set-threshold")
@click.argument("name")
@click.argument("entries", nargs=-1, required=True)
def validators_set_threshold(name: str, entries: tuple[str, ...]) -> None:
    """Update one or more keys in the rule's JSON threshold dict.

    Each ENTRY is ``KEY=VALUE``. VALUE is parsed as JSON so you can
    pass numbers, booleans, strings, or arrays without quoting weirdness:

        poindexter validators set-threshold code_block_density min_blocks_per_700w=2
        poindexter validators set-threshold first_person_claims max_penalty=4.5

    Existing keys not mentioned are preserved -- this is a JSON-merge,
    not a wholesale overwrite. To clear a key, set it to ``null``.
    """
    updates: dict[str, Any] = {}
    for entry in entries:
        if "=" not in entry:
            click.echo(f"Error: expected KEY=VALUE, got {entry!r}", err=True)
            sys.exit(1)
        key, _, raw = entry.partition("=")
        key = key.strip()
        if not key:
            click.echo(f"Error: empty key in {entry!r}", err=True)
            sys.exit(1)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            # Bare strings round-trip as strings; the user's "abc" without
            # quotes is the most common case.
            value = raw
        updates[key] = value

    async def _impl():
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT threshold FROM content_validator_rules WHERE name = $1",
                name,
            )
            if not row:
                return None
            current = row["threshold"]
            if isinstance(current, str):
                try:
                    current = json.loads(current)
                except json.JSONDecodeError:
                    current = {}
            current = dict(current or {})
            for k, v in updates.items():
                if v is None:
                    current.pop(k, None)
                else:
                    current[k] = v
            await conn.execute(
                "UPDATE content_validator_rules SET threshold = $2::jsonb, "
                "updated_at = NOW() WHERE name = $1",
                name, json.dumps(current),
            )
            return current
        finally:
            await conn.close()

    try:
        merged = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if merged is None:
        click.echo(f"(no content_validator_rules row named {name!r})", err=True)
        sys.exit(1)
    click.secho(f"{name}: threshold = {json.dumps(merged)}", fg="cyan")


# ---------------------------------------------------------------------------
# set-niches
# ---------------------------------------------------------------------------


@validators_group.command("set-niches")
@click.argument("name")
@click.option(
    "--all", "wide_open",
    is_flag=True,
    help="Drop the niche scope (rule applies to every niche).",
)
@click.argument("niches", required=False, default="")
def validators_set_niches(name: str, wide_open: bool, niches: str) -> None:
    """Pin or clear a rule's niche scope.

    Pass a comma-separated list of niche slugs to scope the rule to
    those niches::

        poindexter validators set-niches first_person_claims dev_diary,gaming

    Or use ``--all`` to wipe the scope (rule applies to every niche)::

        poindexter validators set-niches first_person_claims --all
    """
    if wide_open and niches:
        click.echo(
            "Error: pass either --all or a niche list, not both.", err=True
        )
        sys.exit(1)
    if not wide_open and not niches:
        click.echo(
            "Error: pass either --all or a comma-separated list of niche slugs.",
            err=True,
        )
        sys.exit(1)

    if wide_open:
        new_niches: list[str] | None = None
    else:
        new_niches = [
            n.strip().lower() for n in niches.split(",") if n.strip()
        ]
        if not new_niches:
            click.echo("Error: niche list parsed to empty -- bad input?", err=True)
            sys.exit(1)

    async def _impl():
        conn = await _connect()
        try:
            return await conn.execute(
                "UPDATE content_validator_rules "
                "SET applies_to_niches = $2, updated_at = NOW() "
                "WHERE name = $1",
                name, new_niches,
            )
        finally:
            await conn.close()

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if result.endswith(" 0"):
        click.echo(f"(no content_validator_rules row named {name!r})", err=True)
        sys.exit(1)
    if new_niches is None:
        click.secho(f"{name}: applies_to_niches = (all niches)", fg="cyan")
    else:
        click.secho(
            f"{name}: applies_to_niches = {','.join(new_niches)}",
            fg="cyan",
        )


__all__ = ["validators_group"]
