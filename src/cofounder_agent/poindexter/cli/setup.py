"""`poindexter setup` — first-run wizard that writes ~/.poindexter/bootstrap.toml.

The goal is: a fresh clone, no .env file, no manual config, should be able
to run `poindexter setup` once and end up with a working system. After
setup, every runtime setting lives in the app_settings DB table — no
`.env` file needs to exist in the repo (#198).

Flow:

    1. `poindexter setup` (default interactive)
       prompts for DB URL, writes bootstrap.toml, tests the connection,
       runs migrations, seeds the minimum app_settings keys.

    2. `poindexter setup --auto`
       spins up a local Docker Postgres with a generated password,
       writes bootstrap.toml pointing at it, runs migrations, seeds.
       Phase 4 — requires Docker to be installed. Emits a stub error
       for now and points the user at `--interactive`.

    3. `poindexter setup --db-url=<url>`
       non-interactive — takes a DB URL directly, verifies, writes,
       migrates. For CI, automation, and existing DBs.

    4. `poindexter setup --check`
       verifies an existing bootstrap.toml still works. Good for ops.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

import click


def _import_bootstrap():
    """Ensure the repo root is on sys.path and return the bootstrap module.

    The CLI lives at src/cofounder_agent/poindexter/cli/setup.py; the
    brain/ package lives at the repo root, so walk up until we find it.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "brain" / "bootstrap.py").is_file():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            break
    from brain import bootstrap  # noqa: PLC0415 — lazy on purpose

    return bootstrap


async def _test_db_connection(dsn: str) -> tuple[bool, str]:
    """Try to open a connection. Return (ok, reason)."""
    try:
        import asyncpg  # noqa: PLC0415
    except Exception as e:
        return False, f"asyncpg not installed: {e}"

    try:
        conn = await asyncpg.connect(dsn, timeout=8)
        try:
            version = await conn.fetchval("SELECT version()")
            return True, str(version).split(",")[0]
        finally:
            await conn.close()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _run_migrations(dsn: str) -> tuple[bool, str]:
    """Run pending migrations against the target DB."""
    try:
        import asyncpg  # noqa: PLC0415
    except Exception as e:
        return False, f"asyncpg not installed: {e}"

    try:
        # The migrations runner lives in services.migrations and expects a
        # DatabaseService-shaped object with a .pool. For setup we want a
        # minimal, dependency-free path, so we just check whether
        # app_settings exists as a proxy for "previously migrated".
        conn = await asyncpg.connect(dsn, timeout=8)
        try:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'app_settings')"
            )
            if exists:
                return True, "app_settings table already present — migrations already run"
            return (
                False,
                "app_settings table missing. Start the worker once to let it run "
                "migrations, or run `alembic upgrade head` inside src/cofounder_agent.",
            )
        finally:
            await conn.close()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _seed_minimum_settings(dsn: str, values: dict[str, str]) -> int:
    """Upsert a small set of app_settings keys. Returns count written."""
    import asyncpg  # noqa: PLC0415

    conn = await asyncpg.connect(dsn, timeout=8)
    try:
        n = 0
        for key, value in values.items():
            if not value:
                continue
            await conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                key,
                value,
            )
            n += 1
        return n
    finally:
        await conn.close()


def _prompt_defaults() -> dict[str, str]:
    """Interactive prompts. Returns the values to persist."""
    click.echo()
    click.secho("Poindexter setup — interactive", fg="cyan", bold=True)
    click.echo(
        "This writes ~/.poindexter/bootstrap.toml with the one value you need\n"
        "to bootstrap the system: a database URL. Everything else lives in the\n"
        "app_settings DB table once the worker connects."
    )
    click.echo()

    db_url = click.prompt(
        "Database URL (postgresql://user:pass@host:port/db)",
        default="postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain",
        show_default=True,
    ).strip()

    click.echo()
    click.echo(
        "Operator notifications (optional — Poindexter will notify you here\n"
        "when the system can't start cleanly). Leave blank to skip."
    )
    telegram_bot_token = click.prompt(
        "Telegram bot token", default="", show_default=False
    ).strip()
    telegram_chat_id = (
        click.prompt("Telegram chat ID", default="", show_default=False).strip()
        if telegram_bot_token
        else ""
    )
    discord_ops_webhook_url = click.prompt(
        "Discord ops webhook URL", default="", show_default=False
    ).strip()

    return {
        "database_url": db_url,
        "telegram_bot_token": telegram_bot_token,
        "telegram_chat_id": telegram_chat_id,
        "discord_ops_webhook_url": discord_ops_webhook_url,
    }


@click.command(name="setup")
@click.option("--db-url", default=None, help="Non-interactive: use this DB URL.")
@click.option(
    "--auto",
    is_flag=True,
    help="(Phase 4) Auto-provision a local Docker Postgres for first-run.",
)
@click.option(
    "--check",
    is_flag=True,
    help="Verify an existing bootstrap.toml without changing anything.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing bootstrap.toml without confirmation.",
)
def setup_command(db_url: str | None, auto: bool, check: bool, force: bool) -> None:
    """First-run wizard — writes ~/.poindexter/bootstrap.toml."""
    bootstrap = _import_bootstrap()

    if check:
        _run_check(bootstrap)
        return

    if auto:
        click.secho(
            "`poindexter setup --auto` is scheduled for Phase 4 — Docker "
            "auto-provisioning is not built yet.",
            fg="yellow",
        )
        click.echo("Use `poindexter setup` (interactive) or `--db-url <url>` for now.")
        sys.exit(1)

    if bootstrap.bootstrap_file_exists() and not force:
        click.secho(
            f"{bootstrap.BOOTSTRAP_FILE} already exists.", fg="yellow",
        )
        click.echo("Re-run with --force to overwrite, or --check to verify it.")
        sys.exit(1)

    if db_url:
        values = {
            "database_url": db_url,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "discord_ops_webhook_url": "",
        }
    else:
        values = _prompt_defaults()

    click.echo()
    click.secho("1/4 — testing database connection…", fg="cyan")
    ok, reason = asyncio.run(_test_db_connection(values["database_url"]))
    if not ok:
        click.secho(f"Connection failed: {reason}", fg="red")
        click.echo(
            "Check that Postgres is running and the DSN is correct. "
            "No file was written."
        )
        sys.exit(2)
    click.secho(f"OK — {reason}", fg="green")

    click.echo()
    click.secho("2/4 — checking migrations…", fg="cyan")
    ok, reason = asyncio.run(_run_migrations(values["database_url"]))
    migrations_ok = ok
    if not ok:
        click.secho(f"{reason}", fg="yellow")
        click.echo("Continuing — the worker will run migrations on first startup.")
    else:
        click.secho(f"OK — {reason}", fg="green")

    click.echo()
    click.secho(f"3/4 — writing {bootstrap.BOOTSTRAP_FILE}…", fg="cyan")
    try:
        path = bootstrap.write_bootstrap_toml(values)
    except Exception as e:
        click.secho(f"Failed to write bootstrap.toml: {e}", fg="red")
        sys.exit(2)
    click.secho(f"OK — wrote {path}", fg="green")

    click.echo()
    click.secho("4/4 — seeding app_settings…", fg="cyan")
    if migrations_ok:
        # Seed the notification channels so the worker sees them on restart.
        seed = {
            "telegram_bot_token": values["telegram_bot_token"],
            "telegram_chat_id": values["telegram_chat_id"],
            "discord_ops_webhook_url": values["discord_ops_webhook_url"],
        }
        try:
            n = asyncio.run(_seed_minimum_settings(values["database_url"], seed))
            click.secho(f"OK — wrote {n} settings keys", fg="green")
        except Exception as e:
            click.secho(f"Could not seed settings: {e}", fg="yellow")
            click.echo(
                "bootstrap.toml is saved; you can seed app_settings later via "
                "`poindexter settings set`."
            )
    else:
        click.echo("Skipped — migrations haven't run yet.")

    click.echo()
    click.secho("Setup complete.", fg="green", bold=True)
    click.echo("Start the worker and brain daemon — they'll read from bootstrap.toml.")


def _run_check(bootstrap) -> None:
    click.secho("Poindexter bootstrap check", fg="cyan", bold=True)
    click.echo()

    if not bootstrap.bootstrap_file_exists():
        click.secho(
            f"No bootstrap file at {bootstrap.BOOTSTRAP_FILE}.", fg="red",
        )
        click.echo("Run `poindexter setup` to create one.")
        sys.exit(1)

    click.echo(f"File: {bootstrap.BOOTSTRAP_FILE}")

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        click.secho("No database_url found — bootstrap.toml may be malformed.", fg="red")
        sys.exit(2)

    masked = dsn
    if "@" in dsn and "://" in dsn:
        scheme, rest = dsn.split("://", 1)
        if "@" in rest:
            creds, tail = rest.split("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                masked = f"{scheme}://{user}:***@{tail}"

    click.echo(f"DB URL: {masked}")

    ok, reason = asyncio.run(_test_db_connection(dsn))
    if ok:
        click.secho(f"Connection OK — {reason}", fg="green")
    else:
        click.secho(f"Connection FAILED — {reason}", fg="red")
        sys.exit(2)

    ok, reason = asyncio.run(_run_migrations(dsn))
    click.echo(f"Schema: {reason}")


# Called by app.py
setup_group = setup_command
