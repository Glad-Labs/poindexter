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
import shutil
import subprocess
import sys
import time
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


_DOCKER_INTERNAL_HOSTS = {"worker", "host.docker.internal", "poindexter-worker"}


def _rewrite_to_host(url: str) -> str | None:
    """If url uses a Docker-internal alias, return the host-local equivalent.

    Returns None if the URL isn't Docker-internal.
    """
    from urllib.parse import urlparse, urlunparse

    try:
        parts = urlparse(url)
        host = (parts.hostname or "").lower()
        if host in _DOCKER_INTERNAL_HOSTS:
            netloc = "localhost"
            if parts.port:
                netloc = f"localhost:{parts.port}"
            return urlunparse(parts._replace(netloc=netloc))
    except Exception:
        pass
    return None


async def _check_http_endpoint(
    url: str, *, timeout: float = 5.0,
) -> tuple[bool, str]:
    """GET url, return (ok, detail). Never raises.

    Auto-retries with a localhost rewrite when the configured URL is a
    Docker-internal alias — so `poindexter setup --check` works both
    inside a container AND from the host.
    """
    try:
        import httpx  # noqa: PLC0415
    except Exception as e:
        return False, f"httpx not installed: {e}"

    async def _probe(u: str) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(u)
                if resp.status_code < 400:
                    return True, f"{resp.status_code} OK"
                return False, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    ok, reason = await _probe(url)
    if ok:
        return True, reason

    # Retry with a host-local rewrite when the configured URL looks like
    # a Docker-internal alias. Lets the operator run --check from the
    # host without editing app_settings.
    alt = _rewrite_to_host(url)
    if alt and alt != url:
        ok2, reason2 = await _probe(alt)
        if ok2:
            return True, f"{reason2} (via host rewrite {alt})"
        return False, f"{reason} / {alt}: {reason2}"
    return False, reason


async def _setting_value(dsn: str, key: str) -> str:
    """Read one app_settings value. Returns '' on any error."""
    try:
        import asyncpg  # noqa: PLC0415

        conn = await asyncpg.connect(dsn, timeout=5)
        try:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1", key
            )
            return str(row["value"]) if row and row["value"] is not None else ""
        finally:
            await conn.close()
    except Exception:
        return ""


async def _check_brain_heartbeat(dsn: str) -> tuple[bool, str]:
    """Verify the brain daemon has touched its queue recently (last 10 min)."""
    try:
        import asyncpg  # noqa: PLC0415

        conn = await asyncpg.connect(dsn, timeout=5)
        try:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'brain_decisions')"
            )
            if not exists:
                return False, "brain_decisions table missing (brain daemon has never run)"
            last = await conn.fetchval(
                "SELECT MAX(created_at) FROM brain_decisions"
            )
            if last is None:
                return False, "no decisions recorded yet"

            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            age_s = (now - last).total_seconds()
            if age_s < 600:  # 10 minutes
                return True, f"last decision {int(age_s)}s ago"
            if age_s < 3600:
                return False, f"stale — last decision {int(age_s / 60)}m ago"
            return False, f"stale — last decision {int(age_s / 3600)}h ago"
        finally:
            await conn.close()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _check_telegram(token: str, chat_id: str) -> tuple[bool, str]:
    """Call Telegram /getMe to verify the token is live. Doesn't send a message."""
    if not token or not chat_id:
        return False, "bot_token or chat_id missing"
    try:
        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            if resp.status_code == 200 and resp.json().get("ok"):
                name = resp.json().get("result", {}).get("username", "?")
                return True, f"bot @{name}"
            return False, f"HTTP {resp.status_code}"
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


# ---------------------------------------------------------------------------
# --auto: spin a local Docker Postgres (Phase 4)
# ---------------------------------------------------------------------------

_AUTO_CONTAINER = "poindexter-postgres-auto"
_AUTO_IMAGE = "pgvector/pgvector:pg16"
_AUTO_PORT = 15433  # one above the docker-compose.local.yml default (15432)
_AUTO_DB = "poindexter_brain"
_AUTO_USER = "poindexter"


def _run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, capturing stdout/stderr as text. Raises on error if check=True."""
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        timeout=60,
    )


def _docker_available() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "docker binary not on PATH"
    try:
        out = _run(["docker", "version", "--format", "{{.Server.Version}}"])
        if out.returncode != 0:
            return False, f"docker version failed: {out.stderr.strip()}"
        return True, out.stdout.strip() or "unknown version"
    except subprocess.TimeoutExpired:
        return False, "docker daemon not responding (is Docker Desktop running?)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _container_exists() -> bool:
    try:
        out = _run(
            ["docker", "ps", "-a", "--filter", f"name=^{_AUTO_CONTAINER}$", "--format", "{{.Names}}"],
            check=False,
        )
        return _AUTO_CONTAINER in (out.stdout or "").splitlines()
    except Exception:
        return False


def _container_running() -> bool:
    try:
        out = _run(
            ["docker", "ps", "--filter", f"name=^{_AUTO_CONTAINER}$", "--format", "{{.Names}}"],
            check=False,
        )
        return _AUTO_CONTAINER in (out.stdout or "").splitlines()
    except Exception:
        return False


def _wait_for_postgres(dsn: str, *, timeout: float = 30.0) -> tuple[bool, str]:
    """Poll the DB until it accepts connections or we give up."""
    import asyncio as _asyncio

    deadline = time.monotonic() + timeout
    last_err = "never attempted"
    while time.monotonic() < deadline:
        ok, reason = _asyncio.run(_test_db_connection(dsn))
        if ok:
            return True, reason
        last_err = reason
        time.sleep(1.0)
    return False, f"timed out after {timeout:.0f}s — last error: {last_err}"


def _auto_provision() -> str:
    """Spin up (or reuse) a local Docker pgvector container. Returns the DSN.

    Prints each step with click.echo so the operator can see what's happening.
    Raises click.ClickException on failure.
    """
    click.echo()
    click.secho("Auto-provisioning a local Docker Postgres…", fg="cyan")
    click.echo()

    ok, detail = _docker_available()
    click.echo(f"  docker runtime: {detail}")
    if not ok:
        raise click.ClickException(
            "Docker is not available. Install Docker Desktop or use "
            "`poindexter setup --db-url ...` against an existing Postgres."
        )

    # If the container already exists from a prior setup, reuse it — but we
    # need to read back the password somehow. We stored it in bootstrap.toml
    # last time; if that's missing or pointing elsewhere, we can't recover.
    # Safest: refuse and tell the operator to remove the container manually.
    if _container_exists():
        if _container_running():
            click.echo(f"  reusing running container '{_AUTO_CONTAINER}'")
        else:
            click.echo(f"  starting stopped container '{_AUTO_CONTAINER}'…")
            _run(["docker", "start", _AUTO_CONTAINER])
        # Best-effort: pull the DSN from the running bootstrap.toml if it
        # matches this container.
        try:
            from brain import bootstrap

            existing_dsn = bootstrap.resolve_database_url()
            if existing_dsn and f":{_AUTO_PORT}/" in existing_dsn:
                click.secho(
                    "  reusing DSN from existing bootstrap.toml "
                    "(the password stays what it was)", fg="green",
                )
                return existing_dsn
        except Exception:
            pass
        raise click.ClickException(
            f"Container '{_AUTO_CONTAINER}' already exists but the password "
            "isn't recoverable from bootstrap.toml. Either remove it "
            f"(`docker rm -f {_AUTO_CONTAINER}`) and re-run --auto, or use "
            "`poindexter setup --db-url ...` to point at a different DB."
        )

    password = secrets.token_urlsafe(24)
    click.echo(
        f"  creating '{_AUTO_CONTAINER}' on port {_AUTO_PORT} "
        f"(image: {_AUTO_IMAGE})…"
    )
    try:
        _run(
            [
                "docker", "run", "-d",
                "--name", _AUTO_CONTAINER,
                "--restart", "unless-stopped",
                "-p", f"{_AUTO_PORT}:5432",
                "-e", f"POSTGRES_DB={_AUTO_DB}",
                "-e", f"POSTGRES_USER={_AUTO_USER}",
                "-e", f"POSTGRES_PASSWORD={password}",
                "-v", f"{_AUTO_CONTAINER}-data:/var/lib/postgresql/data",
                _AUTO_IMAGE,
            ]
        )
    except subprocess.CalledProcessError as e:
        raise click.ClickException(
            f"docker run failed: {e.stderr.strip() if e.stderr else e}"
        ) from e

    dsn = f"postgresql://{_AUTO_USER}:{password}@localhost:{_AUTO_PORT}/{_AUTO_DB}"

    click.echo("  waiting for Postgres to accept connections…")
    ok, reason = _wait_for_postgres(dsn, timeout=45)
    if not ok:
        # Surface container logs to help the operator debug.
        logs = subprocess.run(
            ["docker", "logs", "--tail", "50", _AUTO_CONTAINER],
            capture_output=True, text=True, check=False,
        )
        raise click.ClickException(
            f"Postgres did not become ready: {reason}\n\n"
            f"Last 50 log lines from container:\n{logs.stdout or logs.stderr}"
        )
    click.secho(f"  Postgres ready — {reason}", fg="green")
    return dsn


def _generate_secrets() -> dict[str, str]:
    """Generate the machine secrets that every stack needs."""
    import secrets

    return {
        "api_token": f"poindexter-{secrets.token_hex(24)}",
        "local_postgres_password": secrets.token_hex(32),
        "grafana_password": secrets.token_hex(32),
        "pgadmin_password": secrets.token_hex(32),
    }


def _prompt_defaults() -> dict[str, str]:
    """Interactive prompts. Returns the values to persist."""
    click.echo()
    click.secho("Poindexter setup — interactive", fg="cyan", bold=True)
    click.echo(
        "This writes ~/.poindexter/bootstrap.toml with everything needed\n"
        "to bootstrap the system: database URL + generated secrets.\n"
        "All other settings live in the app_settings DB table."
    )
    click.echo()

    db_url = click.prompt(
        "Database URL (postgresql://user:pass@host:port/db)",
        default="postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain",
        show_default=True,
    ).strip()

    secrets = _generate_secrets()
    click.echo()
    click.secho("Generated secrets (stored in bootstrap.toml):", fg="cyan")
    click.echo(f"  API token:  {secrets['api_token'][:20]}...")
    click.echo(f"  Postgres:   {secrets['local_postgres_password'][:12]}...")
    click.echo(f"  Grafana:    {secrets['grafana_password'][:12]}...")
    click.echo(f"  pgAdmin:    {secrets['pgadmin_password'][:12]}...")
    click.echo()
    click.echo(
        "Notification channels (Telegram, Discord) are set via the\n"
        "settings API after first boot — not in bootstrap.toml."
    )

    return {
        "database_url": db_url,
        **secrets,
    }


@click.command(name="setup")
@click.option("--db-url", default=None, help="Non-interactive: use this DB URL.")
@click.option(
    "--auto",
    is_flag=True,
    help="Auto-provision a local Docker Postgres (pgvector/pgvector:pg16).",
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

    if bootstrap.bootstrap_file_exists() and not force:
        click.secho(
            f"{bootstrap.BOOTSTRAP_FILE} already exists.", fg="yellow",
        )
        click.echo("Re-run with --force to overwrite, or --check to verify it.")
        sys.exit(1)

    if auto:
        # Spin a local Docker Postgres and use its DSN for the rest of the
        # wizard. No prompts (other than what _auto_provision itself prints).
        provisioned_dsn = _auto_provision()
        secrets = _generate_secrets()
        values = {
            "database_url": provisioned_dsn,
            **secrets,
        }
    elif db_url:
        secrets = _generate_secrets()
        values = {
            "database_url": db_url,
            **secrets,
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
        seed = {
            "api_token": values.get("api_token", ""),
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


def _mask_dsn(dsn: str) -> str:
    """Hide the password in a libpq connection string."""
    if "@" in dsn and "://" in dsn:
        scheme, rest = dsn.split("://", 1)
        if "@" in rest:
            creds, tail = rest.split("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{scheme}://{user}:***@{tail}"
    return dsn


def _status_line(label: str, ok: bool | None, detail: str) -> None:
    """Pretty-print one check line. ok=None means 'skipped / info'."""
    if ok is True:
        marker = click.style("  OK  ", fg="green", bold=True)
    elif ok is False:
        marker = click.style(" FAIL ", fg="red", bold=True)
    else:
        marker = click.style(" SKIP ", fg="yellow")
    click.echo(f"{marker} {label:<22s} {detail}")


def _run_check(bootstrap) -> None:
    """Run the full system check: DB + migrations + services + notifications."""
    click.secho("Poindexter system check", fg="cyan", bold=True)
    click.echo()

    if not bootstrap.bootstrap_file_exists():
        click.secho(
            f"No bootstrap file at {bootstrap.BOOTSTRAP_FILE}.", fg="red",
        )
        click.echo("Run `poindexter setup` to create one.")
        sys.exit(1)

    _status_line(
        "bootstrap.toml",
        True,
        str(bootstrap.BOOTSTRAP_FILE),
    )

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        _status_line("database URL", False, "no database_url found in bootstrap.toml")
        sys.exit(2)

    _status_line("database URL", True, _mask_dsn(dsn))

    # --- DB connection ----------------------------------------------------
    failed = 0

    ok, reason = asyncio.run(_test_db_connection(dsn))
    _status_line("postgres connection", ok, reason)
    if not ok:
        # Without DB, we can't check anything else. Bail early.
        click.echo()
        click.secho(
            "Fix the database connection first — the remaining checks "
            "need it.",
            fg="yellow",
        )
        sys.exit(2)

    # --- migrations -------------------------------------------------------
    ok, reason = asyncio.run(_run_migrations(dsn))
    _status_line("migrations", ok, reason)
    if not ok:
        failed += 1

    # --- worker API + Ollama URLs come from app_settings (#198) -----------
    api_url = asyncio.run(_setting_value(dsn, "api_base_url"))
    if api_url:
        ok, reason = asyncio.run(
            _check_http_endpoint(f"{api_url.rstrip('/')}/health")
        )
        _status_line("worker API", ok, f"{api_url} — {reason}")
        if not ok:
            failed += 1
    else:
        _status_line("worker API", None, "api_base_url unset in app_settings")

    ollama_url = (
        asyncio.run(_setting_value(dsn, "ollama_url"))
        or asyncio.run(_setting_value(dsn, "ollama_base_url"))
    )
    if ollama_url:
        ok, reason = asyncio.run(
            _check_http_endpoint(f"{ollama_url.rstrip('/')}/api/tags")
        )
        _status_line("ollama", ok, f"{ollama_url} — {reason}")
        if not ok:
            failed += 1
    else:
        _status_line(
            "ollama",
            None,
            "ollama_url unset (worker will use hardcoded fallback if set)",
        )

    # --- brain daemon heartbeat ------------------------------------------
    ok, reason = asyncio.run(_check_brain_heartbeat(dsn))
    _status_line("brain daemon", ok, reason)
    if not ok:
        failed += 1

    # --- notification channels -------------------------------------------
    tg_token = asyncio.run(_setting_value(dsn, "telegram_bot_token")) or \
        bootstrap.get_bootstrap_value("telegram_bot_token")
    tg_chat = asyncio.run(_setting_value(dsn, "telegram_chat_id")) or \
        bootstrap.get_bootstrap_value("telegram_chat_id")

    if tg_token or tg_chat:
        ok, reason = asyncio.run(_check_telegram(tg_token, tg_chat))
        _status_line("telegram", ok, reason)
        if not ok:
            failed += 1
    else:
        _status_line("telegram", None, "unset — operator won't be paged")

    discord_url = (
        asyncio.run(_setting_value(dsn, "discord_ops_webhook_url"))
        or bootstrap.get_bootstrap_value("discord_ops_webhook_url")
    )
    if discord_url:
        _status_line(
            "discord webhook",
            True,
            f"configured ({discord_url[:40]}…) — not probing to avoid noise",
        )
    else:
        _status_line("discord webhook", None, "unset")

    click.echo()
    if failed:
        click.secho(
            f"{failed} check(s) failed — system is partially degraded.",
            fg="red",
            bold=True,
        )
        sys.exit(2)
    click.secho("All checks passed.", fg="green", bold=True)


# Called by app.py
setup_group = setup_command
