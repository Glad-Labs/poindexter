"""``poindexter migrate`` — schema migration runner CLI (gh#226).

Operator surface for the migration runner that already lives at
``services.migrations.run_migrations``. The runner has always been
invoked at worker boot, but operators who don't restart their container
can have unapplied migrations on disk indefinitely. This CLI exposes
``status`` / ``up`` / ``down`` so a migration can be applied (or rolled
back) without the worker restart dance.

Operates directly on the DB pool — no WorkerClient round-trip — because
the migration runner is local-only by design (it imports modules from
``services/migrations/`` on disk).

DB resolution mirrors ``services/voice_agent.py``: bootstrap.toml first
via ``brain.bootstrap.require_database_url``, then env var fallbacks.
No new env-var dependencies introduced here.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json as _json
import sys
from pathlib import Path
from typing import Any

import click


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _ensure_brain_on_path() -> None:
    """Add the repo root to ``sys.path`` so ``brain.bootstrap`` resolves.

    The CLI lives at ``src/cofounder_agent/poindexter/cli/migrate.py`` —
    the ``brain/`` package is at the repo root. Same trick
    ``setup.py`` uses (cf. ``_import_bootstrap``) so this CLI works
    regardless of which directory the operator launches it from.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "brain" / "bootstrap.py").is_file():
            p = str(parent)
            if p not in sys.path:
                sys.path.insert(0, p)
            return


async def _make_pool():
    """Build an asyncpg pool against the bootstrap-resolved DSN.

    Same pattern as ``services/voice_agent.py``. Bootstrap-only — env
    vars are accepted as the standard fallback inside
    ``resolve_database_url`` but no new ones are introduced.
    """
    import asyncpg

    _ensure_brain_on_path()
    from brain.bootstrap import require_database_url

    dsn = require_database_url(source="poindexter migrate")
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2)


def _migrations_dir() -> Path:
    """Path to the on-disk migrations directory."""
    from services import migrations as _migrations_pkg

    return Path(_migrations_pkg.__file__).parent


def _list_migration_files() -> list[Path]:
    """Sorted list of migration file paths (excludes ``__init__.py``)."""
    return sorted(
        f for f in _migrations_dir().glob("*.py") if f.name != "__init__.py"
    )


def _load_migration_module(path: Path):
    """Dynamically import a migration module from disk."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build import spec for {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _ensure_migrations_table(pool) -> None:
    """Idempotent — same DDL the runner uses on boot."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) UNIQUE NOT NULL,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )


async def _fetch_applied(pool) -> dict[str, Any]:
    """Map of ``name -> applied_at`` for everything in schema_migrations."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name, applied_at FROM schema_migrations ORDER BY name",
        )
        return {r["name"]: r["applied_at"] for r in rows}


def _name_matches_target(name: str, target: str) -> bool:
    """``name`` sorts ``<= target`` using the same comparison the
    runner uses to order files.

    Accepts either a full filename (``0103_xxx.py``) or just a numeric
    prefix (``0103``). The comparison is string-based on the prefix so
    ``0103`` matches every ``0103_*.py`` migration regardless of
    the slug suffix.
    """
    target = target.strip()
    if not target:
        return True
    # Strip trailing .py for comparison ergonomics.
    if target.endswith(".py"):
        target = target[:-3]
    name_stripped = name[:-3] if name.endswith(".py") else name
    # Allow "0103" to match "0103_xxx" — compare by the leading digits
    # block when the target is purely a numeric prefix.
    if target.isdigit():
        prefix = name_stripped.split("_", 1)[0]
        return prefix <= target
    return name_stripped <= target


# ---------------------------------------------------------------------------
# Group root
# ---------------------------------------------------------------------------


@click.group(
    name="migrate",
    help=(
        "Schema migration operator commands.\n\n"
        "Wraps services/migrations/run_migrations so an operator can apply "
        "or roll back DB schema changes without restarting the worker. "
        "Operates directly on the DB pool — no worker round-trip needed."
    ),
)
def migrate_group() -> None:
    pass


# ---------------------------------------------------------------------------
# migrate status
# ---------------------------------------------------------------------------


@migrate_group.command("status")
@click.option("--json", "json_output", is_flag=True, help="Machine-readable output.")
def migrate_status(json_output: bool) -> None:
    """List every migration on disk alongside its applied state.

    Format::

        [✓] 0104_seed_voice_agent_defaults.py  applied 2026-04-29
        [ ] 0105_xxx.py                        pending

    Pending count + applied count printed at the bottom.
    """

    async def _impl():
        pool = await _make_pool()
        try:
            await _ensure_migrations_table(pool)
            applied = await _fetch_applied(pool)
        finally:
            await pool.close()
        return applied

    try:
        applied = _run(_impl())
    except SystemExit:
        raise
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    files = _list_migration_files()
    rows: list[dict[str, Any]] = []
    for f in files:
        ts = applied.get(f.name)
        rows.append(
            {
                "name": f.name,
                "applied": ts is not None,
                "applied_at": ts.isoformat() if ts is not None else None,
            },
        )

    if json_output:
        click.echo(_json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("(no migrations on disk)")
        return

    width = max(len(r["name"]) for r in rows)
    applied_count = sum(1 for r in rows if r["applied"])
    pending_count = len(rows) - applied_count

    # Pick a check-mark glyph the active stream can encode. Windows
    # ``cp1252`` consoles barf on ``✓`` so we fall back to ``x``.
    check_glyph = "✓"
    try:
        encoding = (sys.stdout.encoding or "").lower()
        check_glyph.encode(encoding or "utf-8")
    except (UnicodeEncodeError, LookupError):
        check_glyph = "x"

    for r in rows:
        if r["applied"]:
            ts = r["applied_at"] or ""
            # Trim to date for the human-readable line.
            ts_short = ts[:10] if ts else ""
            click.secho(
                f"[{check_glyph}] {r['name']:<{width}}  applied {ts_short}",
                fg="green",
            )
        else:
            click.secho(
                f"[ ] {r['name']:<{width}}  pending", fg="yellow",
            )

    click.echo()
    click.secho(
        f"Total: {len(rows)} migrations — {applied_count} applied, "
        f"{pending_count} pending",
        fg="cyan",
    )


# ---------------------------------------------------------------------------
# migrate up
# ---------------------------------------------------------------------------


class _PoolDatabaseService:
    """Adapter so we can hand a bare asyncpg pool to ``run_migrations``.

    ``run_migrations`` expects an object with a ``.pool`` attribute (the
    real ``DatabaseService`` provides it). Wrapping is cheaper than
    importing the full DatabaseService from a CLI process.
    """

    def __init__(self, pool):
        self.pool = pool


@migrate_group.command("up")
@click.option(
    "--to", "to_target", default=None,
    help=(
        "Stop applying after migration ``<name>`` (e.g. ``0103`` or "
        "``0103_xxx``). Migrations whose name sorts strictly greater "
        "than the target are left pending."
    ),
)
@click.option("--json", "json_output", is_flag=True, help="Machine-readable output.")
def migrate_up(to_target: str | None, json_output: bool) -> None:
    """Apply all pending migrations (idempotent).

    With no flags, this is the same code path the worker takes on
    boot: every pending migration runs in alphabetical order, each one
    recorded in ``schema_migrations`` once it completes.

    With ``--to <name>``, only migrations whose name sorts ``<= name``
    run. Useful for staged rollouts ("apply through 0103, hold 0104
    until I review the seed data").
    """

    async def _impl() -> dict[str, Any]:
        from services.migrations import run_migrations

        pool = await _make_pool()
        try:
            await _ensure_migrations_table(pool)

            if to_target is None:
                # Plain run_migrations call — applies everything pending.
                # We compute summary counts ourselves for the print path
                # because the runner only logs them.
                applied_before = await _fetch_applied(pool)
                ok = await run_migrations(_PoolDatabaseService(pool))
                applied_after = await _fetch_applied(pool)
                newly_applied = sorted(
                    set(applied_after.keys()) - set(applied_before.keys()),
                )
                files = _list_migration_files()
                pending_after = [
                    f.name for f in files if f.name not in applied_after
                ]
                return {
                    "ok": ok,
                    "applied": newly_applied,
                    "skipped_count": len(applied_before),
                    "pending_after": pending_after,
                }

            # --to path: don't call run_migrations (it walks every file).
            # Apply each pending migration up to the target inline so we
            # honor the cap.
            applied = await _fetch_applied(pool)
            files = _list_migration_files()
            newly_applied: list[str] = []
            failed: list[str] = []

            for f in files:
                if f.name in applied:
                    continue
                if not _name_matches_target(f.name, to_target):
                    # Past the target — stop.
                    break

                module = _load_migration_module(f)
                has_up = hasattr(module, "up")
                has_run_migration = hasattr(module, "run_migration")
                if not has_up and not has_run_migration:
                    continue

                try:
                    if has_up:
                        await module.up(pool)
                    else:
                        async with pool.acquire() as mig_conn:
                            await module.run_migration(mig_conn)
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "INSERT INTO schema_migrations (name) VALUES ($1) "
                            "ON CONFLICT (name) DO NOTHING",
                            f.name,
                        )
                    newly_applied.append(f.name)
                except Exception as exc:  # noqa: BLE001 — log + continue
                    failed.append(f"{f.name}: {type(exc).__name__}: {exc}")

            applied_after = await _fetch_applied(pool)
            pending_after = [
                f.name for f in files if f.name not in applied_after
            ]
            return {
                "ok": len(failed) == 0,
                "applied": newly_applied,
                "skipped_count": len(applied),
                "failed": failed,
                "pending_after": pending_after,
            }
        finally:
            await pool.close()

    try:
        summary = _run(_impl())
    except SystemExit:
        raise
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(_json.dumps(summary, indent=2, default=str))
        sys.exit(0 if summary.get("ok") else 1)

    applied_list = summary.get("applied") or []
    failed_list = summary.get("failed") or []
    pending_after = summary.get("pending_after") or []

    if applied_list:
        click.secho(f"Applied {len(applied_list)} migration(s):", fg="green")
        for n in applied_list:
            click.echo(f"  + {n}")
    else:
        click.secho("No new migrations to apply.", fg="cyan")

    if failed_list:
        click.secho(f"\nFailed {len(failed_list)} migration(s):", fg="red")
        for line in failed_list:
            click.echo(f"  ! {line}")

    click.echo()
    click.secho(
        f"Summary: applied {len(applied_list)}, "
        f"skipped {summary.get('skipped_count', 0)}, "
        f"failed {len(failed_list)}, pending {len(pending_after)}",
        fg="cyan",
    )
    if not summary.get("ok"):
        sys.exit(1)


# ---------------------------------------------------------------------------
# migrate down
# ---------------------------------------------------------------------------


def _has_down_callable(module) -> str | None:
    """Return the rollback fn name on ``module`` or ``None``.

    Two conventions match the up path:

    - ``async def down(pool)`` (pool-based)
    - ``async def rollback_migration(conn)`` (connection-based)
    """
    if hasattr(module, "down"):
        return "down"
    if hasattr(module, "rollback_migration"):
        return "rollback_migration"
    return None


async def _rollback_one(pool, name: str) -> tuple[bool, str | None]:
    """Roll back the named migration. Returns ``(ok, error_or_skip_reason)``.

    Skip reason is non-None when the module has no down() — the row is
    NOT removed in that case so the operator can re-run with a manual
    fix.
    """
    files = _list_migration_files()
    matching = [f for f in files if f.name == name]
    if not matching:
        return False, f"on-disk file missing: {name}"
    path = matching[0]
    try:
        module = _load_migration_module(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"import failed: {type(exc).__name__}: {exc}"

    fn_name = _has_down_callable(module)
    if fn_name is None:
        return False, "no down()/rollback_migration() defined — skipped"

    try:
        if fn_name == "down":
            await module.down(pool)
        else:
            async with pool.acquire() as conn:
                await module.rollback_migration(conn)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM schema_migrations WHERE name = $1", name,
        )
    return True, None


@migrate_group.command("down")
@click.option(
    "--to", "to_target", default=None,
    help=(
        "Roll back every migration whose name sorts strictly greater "
        "than ``<name>``. Without --to / --all, only the most recent "
        "applied migration is rolled back."
    ),
)
@click.option(
    "--all", "all_flag", is_flag=True,
    help="Roll back every applied migration. Asks for confirmation unless --yes.",
)
@click.option(
    "--yes", "skip_confirm", is_flag=True,
    help="Skip the confirmation prompt for --all.",
)
@click.option("--json", "json_output", is_flag=True, help="Machine-readable output.")
def migrate_down(
    to_target: str | None,
    all_flag: bool,
    skip_confirm: bool,
    json_output: bool,
) -> None:
    """Roll back applied migrations.

    Default — roll back only the most recent applied migration. Use
    ``--to <name>`` to roll back to a specific migration (everything
    strictly newer rolls back). Use ``--all`` to roll back everything.
    """
    if all_flag and to_target is not None:
        click.echo("Error: --all and --to are mutually exclusive.", err=True)
        sys.exit(2)

    if all_flag and not skip_confirm and not json_output:
        confirmed = click.confirm(
            "Roll back EVERY applied migration? This drops all tracked schema "
            "changes that have a down() defined.",
            default=False,
        )
        if not confirmed:
            click.echo("Aborted.")
            sys.exit(1)

    async def _impl() -> dict[str, Any]:
        pool = await _make_pool()
        try:
            await _ensure_migrations_table(pool)
            applied = await _fetch_applied(pool)

            # ``schema_migrations`` is an unordered set; sort by the
            # filename convention (numeric prefix) since that's how the
            # up runner orders them.
            applied_sorted = sorted(applied.keys())

            # Decide which names to roll back, in reverse order.
            if all_flag:
                targets = list(reversed(applied_sorted))
            elif to_target is not None:
                targets = [
                    n for n in reversed(applied_sorted)
                    if not _name_matches_target(n, to_target)
                ]
            else:
                if not applied_sorted:
                    return {"ok": True, "rolled_back": [], "errors": []}
                targets = [applied_sorted[-1]]

            rolled_back: list[str] = []
            errors: list[str] = []
            for name in targets:
                ok, msg = await _rollback_one(pool, name)
                if ok:
                    rolled_back.append(name)
                else:
                    errors.append(f"{name}: {msg}")

            return {
                "ok": len(errors) == 0,
                "rolled_back": rolled_back,
                "errors": errors,
            }
        finally:
            await pool.close()

    try:
        summary = _run(_impl())
    except SystemExit:
        raise
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(_json.dumps(summary, indent=2, default=str))
        sys.exit(0 if summary.get("ok") else 1)

    rolled_back = summary.get("rolled_back") or []
    errors = summary.get("errors") or []

    if rolled_back:
        click.secho(f"Rolled back {len(rolled_back)} migration(s):", fg="green")
        for n in rolled_back:
            click.echo(f"  - {n}")
    else:
        click.secho("Nothing rolled back.", fg="cyan")

    if errors:
        click.secho(f"\nIssues with {len(errors)} migration(s):", fg="yellow")
        for line in errors:
            click.echo(f"  ! {line}")

    if not summary.get("ok"):
        sys.exit(1)
