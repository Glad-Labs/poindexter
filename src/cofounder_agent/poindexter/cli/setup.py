"""`poindexter setup` — first-run wizard that writes ~/.poindexter/bootstrap.toml.

The goal is: a fresh clone, no .env file, no manual config, should be able
to run `poindexter setup` once and end up with a working system. After
setup, every runtime setting lives in the app_settings DB table — no
`.env` file needs to exist in the repo (#198).

Flow:

    1. `poindexter setup` (default interactive)
       prompts for DB URL, writes bootstrap.toml, tests the connection,
       runs migrations, seeds the minimum app_settings keys.

    2. `poindexter setup --auto`  (the quick-start path)
       generates the stack's secrets, starts the Docker stack's OWN Postgres
       (the `postgres-local` service of the compose file
       scripts/start-stack.sh launches), writes bootstrap.toml pointing at
       it, runs migrations, seeds, provisions the CLI's OAuth client.
       Needs Docker and a Poindexter checkout (the compose file lives there).

       It used to start a separate `poindexter-postgres-auto` container on
       port 5434. The stack never read it — every container connects to
       `postgres-local` — so the CLI queued tasks into a database the
       pipeline could not see, and its OAuth client was unknown to the
       worker. One database now, by construction.

    3. `poindexter setup --db-url=<url>`
       non-interactive — takes a DB URL directly, verifies, writes,
       migrates. For CI, automation, and a Postgres you run yourself. The
       Docker stack keeps using its own `postgres-local`; this path is for
       running the worker outside that stack.

    4. `poindexter setup --check`
       verifies an existing bootstrap.toml still works. Good for ops.

Re-running with `--force` keeps every value the existing bootstrap.toml
already holds (see `_stack_secrets`): Postgres keeps the password its volume
was initialised with, and encrypted app_settings rows need the
poindexter_secret_key they were written with.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import click

from poindexter.cli._bootstrap import close_cli_pool, open_cli_pool


def _import_bootstrap():
    """Return the bootstrap module.

    ``brain`` is ``poindexter.brain`` since poindexter#1046 step 2 -- a sibling
    package of this CLI, importable wherever the CLI itself is. Kept as a
    function because callers (and tests) patch it.
    """
    from poindexter.brain import bootstrap

    return bootstrap


async def _test_db_connection(dsn: str) -> tuple[bool, str]:
    """Try to open a connection. Return (ok, reason)."""
    try:
        import asyncpg
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


class _PoolDatabaseService:
    """Adapter so we can hand a bare asyncpg pool to ``run_migrations``.

    ``services.migrations.run_migrations`` expects an object with a
    ``.pool`` attribute (the real ``DatabaseService`` provides it).
    Wrapping is cheaper than importing the full DatabaseService from a
    setup CLI process. Mirrors the same shim used by
    ``poindexter.cli.migrate`` and ``scripts/ci/migrations_smoke.py``.
    """

    def __init__(self, pool):
        self.pool = pool


async def _run_migrations(dsn: str) -> tuple[bool, str]:
    """Apply all pending migrations against the target DB.

    Idempotent: ``services.migrations.run_migrations`` records each
    applied file in ``schema_migrations`` and skips anything already
    applied, so re-running setup against an up-to-date DB is a fast
    no-op. The runner is the same code path the worker takes on boot —
    keeping setup on it means a fresh ``poindexter setup`` against an
    empty DB ends with every table the next steps need (notably
    ``oauth_clients`` for step 4 OAuth provisioning).
    """
    try:
        from poindexter.services.migrations import run_migrations
    except Exception as e:
        return False, f"could not import migration runner: {e}"

    try:
        # A missing asyncpg surfaces here too (open_cli_pool imports it),
        # as "ModuleNotFoundError: No module named 'asyncpg'".
        pool = await open_cli_pool(dsn, timeout=8)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    try:
        # Snapshot applied count before/after so the success message
        # tells the operator whether anything actually ran.
        async with pool.acquire() as conn:
            try:
                before = await conn.fetchval(
                    "SELECT COUNT(*) FROM schema_migrations"
                )
            except Exception:
                # Table doesn't exist yet — runner will create it.
                before = 0
        try:
            ok = await run_migrations(_PoolDatabaseService(pool))
        except Exception as e:
            return False, f"migration runner crashed: {type(e).__name__}: {e}"

        async with pool.acquire() as conn:
            after = await conn.fetchval(
                "SELECT COUNT(*) FROM schema_migrations"
            )

        applied = max(0, int(after or 0) - int(before or 0))

        if not ok:
            return (
                False,
                f"one or more migrations failed (applied {applied} before "
                "failure — see worker logs for the offending migration)",
            )

        # Seed code-side defaults that aren't covered by an explicit
        # migration (#379). Closes the fresh-DB app_settings gap so
        # the worker boots without lazy "default at query time"
        # surprises and `poindexter setup --check` doesn't false-flag
        # SKIP for keys that have a real default in code.
        seeded = 0
        try:
            from poindexter.services.settings_defaults import seed_all_defaults

            seeded = await seed_all_defaults(pool)
        except Exception as e:  # noqa: BLE001
            return (
                True,
                f"applied {applied} migration(s) "
                f"({int(after or 0)} total); settings seed FAILED: "
                f"{type(e).__name__}: {e}",
            )

        seed_suffix = f" + seeded {seeded} default(s)" if seeded else ""
        if applied == 0:
            return (
                True,
                f"already up to date ({int(after or 0)} migrations applied)"
                + seed_suffix,
            )
        return (
            True,
            f"applied {applied} migration(s) ({int(after or 0)} total)"
            + seed_suffix,
        )
    finally:
        await close_cli_pool(pool)


async def _check_migrations_status(dsn: str) -> tuple[bool, str]:
    """Read-only migrations status for ``poindexter setup --check``.

    Distinct from ``_run_migrations`` (which actually applies them) —
    ``--check`` is meant to be a passive system probe and must not
    mutate the DB. We compare on-disk migration files against the
    ``schema_migrations`` table and report drift.
    """
    try:
        import asyncpg
    except Exception as e:
        return False, f"asyncpg not installed: {e}"

    try:
        conn = await asyncpg.connect(dsn, timeout=8)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    try:
        try:
            rows = await conn.fetch("SELECT name FROM schema_migrations")
        except Exception:
            return (
                False,
                "schema_migrations table missing — run `poindexter setup` "
                "or `poindexter migrate up` to apply migrations",
            )

        applied_names = {row["name"] for row in rows}

        # Discover on-disk migration files via the same path the runner
        # uses, so the count matches.
        try:
            from poindexter.services import migrations as _migrations_pkg

            migrations_dir = Path(_migrations_pkg.__file__).resolve().parent
            on_disk = sorted(
                f.name
                for f in migrations_dir.glob("*.py")
                if f.name != "__init__.py"
            )
        except Exception:
            return (
                True,
                f"{len(applied_names)} migrations applied "
                "(unable to compare against on-disk files)",
            )

        pending = [n for n in on_disk if n not in applied_names]
        if pending:
            return (
                False,
                f"{len(pending)} pending migration(s) — run "
                "`poindexter migrate up` to apply",
            )
        return True, f"{len(applied_names)} migrations applied — up to date"
    finally:
        await conn.close()


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
        # silent-ok: `None` means "no rewrite applicable", which is also the
        # normal answer for any URL that isn't a docker-internal host. An
        # unparseable URL therefore degrades to leaving it untouched — the
        # safe result for a convenience rewrite.
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
        import httpx
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
    """Read one app_settings value, decrypting if marked secret. Returns
    ``''`` on any error.

    Routes through ``plugins.secrets.get_secret`` so encrypted secrets
    (``enc:v1:...`` ciphertext on ``is_secret=true`` rows like
    ``telegram_bot_token`` + ``discord_ops_webhook_url``) come back as
    plaintext. Caller relies on ``ensure_secret_key`` having loaded
    ``POINDEXTER_SECRET_KEY`` into env first; if it's missing the
    decryption silently fails and we fall back to ``''`` — same
    "configured but not actually usable" failure mode the operator
    sees today, so no behavior change for that path.
    """
    try:
        import asyncpg

        from poindexter.plugins.secrets import get_secret

        conn = await asyncpg.connect(dsn, timeout=5)
        try:
            val = await get_secret(conn, key)
            return val or ""
        finally:
            await conn.close()
    except Exception:
        # silent-ok: `""` reads as "not configured yet", which during setup is
        # the expected state — the caller prompts for the value instead. A
        # DB that isn't reachable yet is the normal case here, not a fault.
        return ""


async def _sync_compose_project_setting(dsn: str, project: str) -> bool:
    """Point ``app_settings.compose_project_name`` at bootstrap.toml's project.

    Two things name the stack's compose project: bootstrap.toml's
    ``compose_project_name`` (start-stack.sh exports it as
    COMPOSE_PROJECT_NAME for every launch) and the app_setting of the same
    name, which the brain's compose-drift probe uses when it re-creates a
    drifted container. Both default to ``poindexter``. When setup is run with
    another name, a stale app_setting would have the brain recreate services
    in a project that does not own them. Returns True when the row changed.
    """
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=8)
    try:
        status = await conn.execute(
            "UPDATE app_settings SET value = $1, updated_at = now() "
            "WHERE key = 'compose_project_name' AND value IS DISTINCT FROM $1",
            project,
        )
    finally:
        await conn.close()
    return status.endswith(" 1")


async def _configured_pull_command(dsn: str) -> str:
    """``ollama pull …`` for the models THIS database's pipeline is set to call.

    Read from the live app_settings (just seeded by step 2), so an operator
    who re-pointed a role before re-running setup sees their own models, not
    the README's defaults. The role list lives in services/required_models.py.
    """
    import asyncpg

    from poindexter.services.required_models import (
        PIPELINE_MODEL_KEYS,
        pull_command,
        required_models,
    )

    conn = await asyncpg.connect(dsn, timeout=8)
    try:
        rows = await conn.fetch(
            "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
            list(PIPELINE_MODEL_KEYS),
        )
    finally:
        await conn.close()
    return pull_command(required_models({r["key"]: r["value"] for r in rows}))


async def _check_brain_heartbeat(dsn: str) -> tuple[bool, str]:
    """Verify the brain daemon has touched its queue recently (last 10 min)."""
    try:
        import asyncpg

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
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            if resp.status_code == 200 and resp.json().get("ok"):
                name = resp.json().get("result", {}).get("username", "?")
                return True, f"bot @{name}"
            return False, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# --auto: provision the Docker stack's own Postgres
# ---------------------------------------------------------------------------

# Canonical local-dev Postgres host port. Mirrors the POSTGRES_HOST_PORT
# default published by docker-compose.local.yml and docker-compose.consumer.yml
# — keep them in sync (guarded by tests/unit/poindexter/cli/test_setup.py and
# scripts/ci/ports_lint.py). 15432 was retired 2026-06-21 after it landed
# inside a Windows Hyper-V reserved TCP range and became unbindable
# (WSAEACCES); see the compose-file comment.
_DEFAULT_LOCAL_DB_PORT = 5433
_DEFAULT_LOCAL_DB_URL = (
    f"postgresql://poindexter:poindexter-brain-local"
    f"@localhost:{_DEFAULT_LOCAL_DB_PORT}/poindexter_brain"
)

# The compose files scripts/start-stack.sh picks between, in its order: the
# operator's full stack when the checkout has it, else the public default
# stack. --auto must provision the database of the file start-stack.sh will
# launch — tests/unit/scripts/test_start_stack_compose_selection.py pins the
# two selections together.
_COMPOSE_FILES = ("docker-compose.local.yml", "docker-compose.consumer.yml")
# Every stack container connects to this service (postgres-local:5432 on the
# compose network), and the host reaches it on _DEFAULT_LOCAL_DB_PORT. User and
# database are the LOCAL_POSTGRES_USER / LOCAL_POSTGRES_DB compose defaults.
_STACK_DB_SERVICE = "postgres-local"
_STACK_DB_USER = "poindexter"
_STACK_DB_NAME = "poindexter_brain"
# Written to bootstrap.toml as compose_project_name; start-stack.sh exports it
# as COMPOSE_PROJECT_NAME so every launch lands in the same compose project
# (launching from a second directory would otherwise fork a parallel stack).
_DEFAULT_COMPOSE_PROJECT = "poindexter"

# --auto used to start this standalone container on port 5434. The stack never
# used it, so it is only looked up now to tell the operator it can go.
_LEGACY_AUTO_CONTAINER = "poindexter-postgres-auto"


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


def _legacy_auto_container_exists() -> bool:
    try:
        out = _run(
            ["docker", "ps", "-a", "--filter", f"name=^{_LEGACY_AUTO_CONTAINER}$", "--format", "{{.Names}}"],
            check=False,
        )
        return _LEGACY_AUTO_CONTAINER in (out.stdout or "").splitlines()
    except Exception:
        # silent-ok: this only decides whether to print an advisory about an
        # old container; "not there" costs nothing but the advisory, and a
        # real docker problem surfaces from the compose call right after.
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


def find_stack_root(start: Path | None = None) -> Path | None:
    """The Poindexter checkout whose Docker stack ``--auto`` provisions.

    A checkout is a directory holding ``scripts/start-stack.sh`` and one of
    ``_COMPOSE_FILES``. Searched from ``start`` (default: the working
    directory) upward, then from this package's own location upward — the
    quick start's ``pip install -e src/cofounder_agent`` is an editable
    install, so the package sits inside the checkout wherever the CLI is run
    from. ``None`` when neither is inside one (a PyPI install carries no
    compose file).
    """
    import poindexter

    bases = [Path(start) if start else Path.cwd(), Path(poindexter.__file__).parent]
    for base in bases:
        base = base.resolve()
        for candidate in (base, *base.parents):
            if (candidate / "scripts" / "start-stack.sh").is_file() and any(
                (candidate / name).is_file() for name in _COMPOSE_FILES
            ):
                return candidate
    return None


def compose_file_for(root: Path) -> Path:
    """The compose file scripts/start-stack.sh launches for this checkout."""
    for name in _COMPOSE_FILES:
        if (root / name).is_file():
            return root / name
    raise FileNotFoundError(
        f"no stack compose file ({' or '.join(_COMPOSE_FILES)}) in {root}"
    )


def _compose_env(values: dict[str, str]) -> dict[str, str]:
    """The environment scripts/start-stack.sh hands ``docker compose``.

    start-stack.sh exports every bootstrap.toml key uppercased; doing the same
    here means the compose file interpolates identically under setup and
    under every later launch — a compose file refuses to load while any
    ``${VAR:?...}`` sentinel is unset, even one in a service not being
    started.
    """
    env = dict(os.environ)
    for key, value in values.items():
        if value:
            env[key.upper()] = str(value)
    return env


def _stack_db_port(values: dict[str, str]) -> int:
    """Host port the stack publishes postgres-local on (POSTGRES_HOST_PORT)."""
    raw = str(values.get("postgres_host_port") or "").strip()
    if not raw:
        return _DEFAULT_LOCAL_DB_PORT
    try:
        return int(raw)
    except ValueError as e:
        raise click.ClickException(
            f"postgres_host_port / POSTGRES_HOST_PORT must be a port number, got {raw!r}"
        ) from e


def stack_database_url(values: dict[str, str]) -> str:
    """The host-side DSN of the stack's postgres-local for these secrets."""
    password = quote(values["local_postgres_password"], safe="")
    return (
        f"postgresql://{_STACK_DB_USER}:{password}"
        f"@localhost:{_stack_db_port(values)}/{_STACK_DB_NAME}"
    )


def _provision_stack_db(root: Path, values: dict[str, str]) -> str:
    """Start the stack's postgres-local and return its host-side DSN.

    Uses the compose file and project scripts/start-stack.sh will use, so the
    container this creates is the one the full stack launch then adopts —
    the host CLI and every container share one database. Raises
    click.ClickException on failure.
    """
    click.echo()
    click.secho("Provisioning the stack's Postgres…", fg="cyan")
    click.echo()

    ok, detail = _docker_available()
    click.echo(f"  docker runtime: {detail}")
    if not ok:
        raise click.ClickException(
            "Docker is not available. Install Docker (Docker Desktop on "
            "Windows/macOS) or use `poindexter setup --db-url ...` against an "
            "existing Postgres."
        )

    compose_file = compose_file_for(root)
    project = values["compose_project_name"]
    click.echo(f"  stack: {compose_file} (compose project '{project}')")

    if _legacy_auto_container_exists():
        click.secho(
            f"  note: '{_LEGACY_AUTO_CONTAINER}' (port 5434) is from an older "
            "`setup --auto`. Nothing uses it now — the stack's own "
            f"{_STACK_DB_SERVICE} is the database. Once you have copied out "
            f"anything you need: docker rm -f {_LEGACY_AUTO_CONTAINER}",
            fg="yellow",
        )

    cmd = [
        "docker", "compose", "-p", project, "-f", str(compose_file),
        "up", "-d", _STACK_DB_SERVICE,
    ]
    click.echo(f"  $ {' '.join(cmd)}")
    try:
        # Not captured: the first run pulls the image, and the operator should
        # see that progress rather than a silent pause.
        proc = subprocess.run(
            cmd, cwd=root, env=_compose_env(values), text=True, timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise click.ClickException(
            f"`docker compose up -d {_STACK_DB_SERVICE}` did not finish in 15 minutes"
        ) from e
    if proc.returncode != 0:
        raise click.ClickException(
            f"`docker compose up -d {_STACK_DB_SERVICE}` failed (exit "
            f"{proc.returncode}) — see the compose output above."
        )

    dsn = stack_database_url(values)
    click.echo("  waiting for Postgres to accept connections…")
    # A fresh volume runs initdb and restarts once before it accepts TCP.
    ok, reason = _wait_for_postgres(dsn, timeout=120)
    if not ok:
        if "password authentication failed" in reason:
            raise click.ClickException(
                f"The stack's Postgres volume already exists with a different "
                f"password than bootstrap.toml's local_postgres_password: {reason}\n\n"
                "Restore the bootstrap.toml that created it (Postgres keeps "
                "the password it was first initialised with), or, to start "
                "over and DELETE that database, run "
                f"`docker compose -p {project} -f {compose_file.name} down -v` "
                "and re-run setup."
            )
        logs = subprocess.run(
            ["docker", "compose", "-p", project, "-f", str(compose_file),
             "logs", "--tail", "50", _STACK_DB_SERVICE],
            cwd=root, env=_compose_env(values), capture_output=True, text=True,
            check=False,
        )
        raise click.ClickException(
            f"Postgres did not become ready: {reason}\n\n"
            f"Last 50 log lines:\n{logs.stdout or logs.stderr}"
        )
    click.secho(f"  Postgres ready — {reason}", fg="green")
    return dsn


def _generate_secrets() -> dict[str, str]:
    """Generate the machine secrets that every stack needs.

    Everything a compose file interpolates through a ``${VAR:?...}`` sentinel
    must be generated here: compose refuses to load a file while ANY sentinel
    is unset — including one in an opt-in profile's service that is not being
    started (the Postiz pair below), so a missing key breaks every launch, not
    just that feature. tests/unit/poindexter/cli/test_setup_auto.py derives the
    sentinel list from docker-compose.consumer.yml and fails when a new one
    arrives without a generator here.

    ``poindexter_secret_key`` is the pgcrypto key every ``is_secret=true``
    app_settings row is encrypted with (plugins/secrets.py). The CLI needs it
    to store its own OAuth client (step 4/4) and every container reads it.

    Note: ``api_token`` is intentionally NOT generated here — Phase 3
    (Glad-Labs/poindexter#249) removed the static-Bearer auth path.
    Worker authentication uses OAuth 2.1 client credentials only; the
    setup wizard provisions an initial OAuth client via
    ``_provision_initial_oauth_client`` after migrations have run.

    Langfuse seeds (poindexter#413) — used by docker-compose.local.yml
    via env-var interpolation. SALT/ENCRYPTION_KEY persist across boots
    (rotating them renders existing encrypted rows unreadable). The
    INIT_* keys only matter on Langfuse's first boot — they seed the
    org/project/owner user/API key pair, after which Langfuse ignores
    them. The public/secret key pair is the one the worker uses to
    talk to Langfuse, so we generate them here in the documented
    ``pk-lf-`` / ``sk-lf-`` shape so an operator can copy/paste them
    into ``app_settings.langfuse_public_key`` / ``langfuse_secret_key``
    after first boot. ``langfuse_init_user_email`` defaults to
    ``admin@localhost`` — the operator can change it in bootstrap.toml
    before first boot if they want a real address.
    """
    import secrets

    return {
        "local_postgres_password": secrets.token_hex(32),
        "grafana_password": secrets.token_hex(32),
        # Encryption-at-rest key for app_settings secrets (plugins/secrets.py).
        "poindexter_secret_key": secrets.token_hex(32),
        # Postiz social hub (the opt-in `postiz` compose profile).
        "postiz_db_password": secrets.token_hex(32),
        "postiz_jwt_secret": secrets.token_hex(32),
        "pgadmin_password": secrets.token_hex(32),
        "woodpecker_secret": secrets.token_hex(24),
        # LGTM+ observability stack
        "glitchtip_db_password": secrets.token_hex(32),
        "glitchtip_secret_key": secrets.token_hex(32),
        # Langfuse stack (poindexter#413)
        "langfuse_salt": secrets.token_urlsafe(16),
        "langfuse_encryption_key": secrets.token_hex(32),
        "langfuse_nextauth_secret": secrets.token_urlsafe(32),
        "langfuse_init_project_public_key": f"pk-lf-{secrets.token_urlsafe(22)}",
        "langfuse_init_project_secret_key": f"sk-lf-{secrets.token_urlsafe(32)}",
        "langfuse_init_user_email": "admin@localhost",
        "langfuse_init_user_name": "Poindexter Admin",
        "langfuse_init_user_password": secrets.token_urlsafe(16),
    }


def _stack_secrets(existing: dict[str, str] | None = None) -> dict[str, str]:
    """The values to write: everything already on disk, plus what is missing.

    A re-run (``--force``) must NOT regenerate a secret bootstrap.toml already
    holds. Postgres keeps the password its volume was initialised with,
    Grafana keeps its admin password, and every encrypted app_settings row
    needs the poindexter_secret_key it was written with — a fresh value for
    any of them locks the stack out of its own data. So existing values win,
    and only keys the file lacks are generated. Non-secret keys the operator
    added (notification channels, OAuth clients, ``compose_project_name``)
    are carried over the same way.
    """
    values = dict(existing or {})
    for key, value in _generate_secrets().items():
        if not values.get(key):
            values[key] = value
    if not values.get("compose_project_name"):
        values["compose_project_name"] = (
            os.environ.get("COMPOSE_PROJECT_NAME") or _DEFAULT_COMPOSE_PROJECT
        )
    # A POSTGRES_HOST_PORT override (5433 reserved on the host) must outlive
    # this shell: start-stack.sh exports bootstrap.toml, so persisting it keeps
    # the port compose publishes and the port database_url names in step.
    if not values.get("postgres_host_port") and os.environ.get("POSTGRES_HOST_PORT"):
        values["postgres_host_port"] = os.environ["POSTGRES_HOST_PORT"]
    return values


def _prompt_defaults(existing: dict[str, str] | None = None) -> dict[str, str]:
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
        default=_DEFAULT_LOCAL_DB_URL,
        show_default=True,
    ).strip()

    secrets = _stack_secrets(existing)
    click.echo()
    click.secho("Generated secrets (stored in bootstrap.toml):", fg="cyan")
    click.echo(f"  Postgres:   {secrets['local_postgres_password'][:12]}...")
    click.echo(f"  Grafana:    {secrets['grafana_password'][:12]}...")
    click.echo(f"  pgAdmin:    {secrets['pgadmin_password'][:12]}...")
    click.echo(f"  GlitchTip:  {secrets['glitchtip_secret_key'][:12]}...")
    click.echo(f"  Langfuse:   {secrets['langfuse_init_project_public_key'][:18]}...")
    click.echo()
    click.echo(
        "Worker auth uses OAuth 2.1 — an initial client is provisioned\n"
        "automatically after migrations run (no manual register-client needed).\n"
        "Notification channels (Telegram, Discord) are set via the\n"
        "settings API after first boot — not in bootstrap.toml."
    )

    return {
        **secrets,
        "database_url": db_url,
    }


@click.command(name="setup")
@click.option("--db-url", default=None, help="Non-interactive: use this DB URL.")
@click.option(
    "--auto",
    is_flag=True,
    help=(
        "Quick-start path: start the Docker stack's own Postgres (the "
        "postgres-local service scripts/start-stack.sh launches) and point "
        "bootstrap.toml at it. Run from a Poindexter checkout."
    ),
)
@click.option(
    "--check",
    is_flag=True,
    help="Verify an existing bootstrap.toml without changing anything.",
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Rewrite an existing bootstrap.toml without confirmation. Secrets it "
        "already holds are kept — regenerating them would lock the stack out "
        "of its own database and encrypted settings."
    ),
)
def setup_command(db_url: str | None, auto: bool, check: bool, force: bool) -> None:
    """First-run wizard — writes ~/.poindexter/bootstrap.toml."""
    bootstrap = _import_bootstrap()

    if check:
        _run_check(bootstrap)
        return

    existing: dict[str, str] = {}
    if bootstrap.bootstrap_file_exists():
        if not force:
            click.secho(
                f"{bootstrap.BOOTSTRAP_FILE} already exists.", fg="yellow",
            )
            click.echo("Re-run with --force to overwrite, or --check to verify it.")
            sys.exit(1)
        existing = bootstrap.get_all_bootstrap_values()

    if auto:
        # Provision the Postgres the stack itself uses, so the CLI and every
        # container read and write one database. No prompts.
        root = find_stack_root()
        if root is None:
            raise click.ClickException(
                "--auto starts the Postgres of the Docker stack that lives in a "
                "Poindexter checkout, and none was found from the current "
                "directory or from where the poindexter package is installed. "
                "Run it from your clone (git clone "
                "https://github.com/Glad-Labs/poindexter.git && cd poindexter), "
                "or pass --db-url to use a Postgres you run yourself."
            )
        values = _stack_secrets(existing)
        values["database_url"] = _provision_stack_db(root, values)
    elif db_url:
        values = {**_stack_secrets(existing), "database_url": db_url}
    else:
        values = _prompt_defaults(existing)

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
    click.secho("2/4 — applying migrations…", fg="cyan")
    ok, reason = asyncio.run(_run_migrations(values["database_url"]))
    migrations_ok = ok
    if not ok:
        click.secho(f"{reason}", fg="yellow")
        click.echo(
            "Continuing — re-run `poindexter migrate up` once the underlying "
            "issue is resolved (step 4 OAuth provisioning will be skipped)."
        )
    else:
        click.secho(f"OK — {reason}", fg="green")
        try:
            if asyncio.run(_sync_compose_project_setting(
                values["database_url"], values["compose_project_name"],
            )):
                click.echo(
                    "  app_settings.compose_project_name -> "
                    f"{values['compose_project_name']} (matches bootstrap.toml)"
                )
        except Exception as e:  # noqa: BLE001
            click.secho(
                "  could not align app_settings.compose_project_name with "
                f"bootstrap.toml ({type(e).__name__}: {e}); set it with "
                "`poindexter settings set compose_project_name "
                f"{values['compose_project_name']}`",
                fg="yellow",
            )

    click.echo()
    click.secho(f"3/4 — writing {bootstrap.BOOTSTRAP_FILE}…", fg="cyan")
    # database_url first: it is the one line a human opens this file to read.
    values = {"database_url": values["database_url"], **values}
    try:
        path = bootstrap.write_bootstrap_toml(values)
    except Exception as e:
        click.secho(f"Failed to write bootstrap.toml: {e}", fg="red")
        sys.exit(2)
    click.secho(f"OK — wrote {path}", fg="green")

    # The OAuth client below is stored encrypted, with the key this file now
    # holds. Load it the way every later CLI invocation will (bootstrap.toml
    # wins over the shell, as it does in start-stack.sh), so the client this
    # writes is one the stack can decrypt.
    os.environ["POINDEXTER_SECRET_KEY"] = values["poindexter_secret_key"]

    click.echo()
    click.secho("4/4 — provisioning initial OAuth client…", fg="cyan")
    if migrations_ok:
        try:
            client_id, client_secret = asyncio.run(
                _provision_initial_oauth_client(values["database_url"])
            )
            click.secho("OK — initial OAuth client provisioned", fg="green")
            click.echo(f"  client_id:      {client_id}")
            click.echo(f"  client_secret:  {client_secret}")
            click.echo(
                "  app_settings:   cli_oauth_client_id + cli_oauth_client_secret"
            )
            click.echo()
            click.secho(
                "  Capture the client_secret NOW — it is not recoverable.",
                fg="yellow",
            )
        except Exception as e:  # noqa: BLE001
            click.secho(f"Could not provision OAuth client: {e}", fg="yellow")
            click.echo(
                "bootstrap.toml is saved; provision an OAuth client later via "
                "`poindexter auth migrate-cli`."
            )
    else:
        click.echo(
            "Skipped — migrations haven't run yet. Run `poindexter auth "
            "migrate-cli` after the worker boots once."
        )

    click.echo()
    click.secho("Setup complete.", fg="green", bold=True)
    if auto:
        pulls = ""
        if migrations_ok:
            try:
                pulls = asyncio.run(_configured_pull_command(values["database_url"]))
            except Exception as e:  # noqa: BLE001
                click.secho(
                    f"  (could not read the configured models: {type(e).__name__}: {e})",
                    fg="yellow",
                )
        click.echo("Next:")
        if pulls:
            click.echo(f"  1. Pull the models the pipeline is configured to call:\n       {pulls}")
        else:
            click.echo("  1. Pull the models listed in the README's quick start.")
        click.echo("  2. Start the stack:\n       bash scripts/start-stack.sh up -d")
    else:
        click.echo(
            "Start the worker and brain daemon — they'll read from bootstrap.toml."
        )


async def _provision_initial_oauth_client(dsn: str) -> tuple[str, str]:
    """Provision a fresh OAuth client for the CLI on first setup.

    Mirrors the ``_provision_consumer_client`` helper in
    ``cli/auth.py`` but inlined here so the setup wizard doesn't
    pull the larger auth module's import graph during a fresh
    install. Encrypts secrets via ``plugins.secrets.set_secret``.

    The wizard creates a CLI OAuth client by default — every other
    consumer (brain, mcp, scripts, openclaw, grafana) gets its own
    client via the matching ``poindexter auth migrate-*`` command,
    which the operator runs once per consumer.
    """
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    from poindexter.plugins.secrets import set_secret
    from poindexter.services.auth.oauth_issuer import (
        generate_client_id,
        generate_client_secret,
    )
    from poindexter.services.auth.oauth_provider import PoindexterOAuthProvider

    client_id = generate_client_id()
    client_secret = generate_client_secret()

    client_info = OAuthClientInformationFull(
        client_id=client_id,
        client_secret=client_secret,
        # Headless client; localhost placeholder satisfies the SDK's
        # min_length=1 requirement on redirect_uris but is never used.
        redirect_uris=[AnyUrl("http://localhost/")],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["client_credentials"],
        response_types=["code"],
        scope="api:read api:write",
        client_name="poindexter-cli (initial)",
    )

    pool = await open_cli_pool(dsn)
    try:
        provider = PoindexterOAuthProvider(pool)
        await provider.register_client(client_info)
        async with pool.acquire() as conn:
            await set_secret(
                conn, "cli_oauth_client_id", client_id,
                description="OAuth client_id for poindexter CLI (initial setup #249)",
            )
            await set_secret(
                conn, "cli_oauth_client_secret", client_secret,
                description="OAuth client_secret for poindexter CLI (initial setup #249)",
            )
    finally:
        await close_cli_pool(pool)

    return client_id, client_secret


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

    # Load POINDEXTER_SECRET_KEY into env so _setting_value's get_secret
    # path can decrypt is_secret=true rows (telegram_bot_token,
    # discord_ops_webhook_url, etc.). Without this every secret check
    # silently appears as "unset" and the check report misleads.
    from ._bootstrap import ensure_secret_key
    ensure_secret_key()

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

    # --- migrations (read-only — does NOT apply them; that's `setup` /
    # `migrate up`'s job)
    ok, reason = asyncio.run(_check_migrations_status(dsn))
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
