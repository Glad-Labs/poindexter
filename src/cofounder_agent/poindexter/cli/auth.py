"""``poindexter auth`` — manage OAuth 2.1 client credentials.

Phase 1 of #241. Each row in ``oauth_clients`` is one tool/agent
(MCP HTTP server, OpenClaw skill, brain daemon, Grafana webhook,
etc.). Operators register one client per tool; the plaintext secret
is shown ONCE on registration and never recoverable from the DB.

v1 surface:

- ``register-client``    create a row, print client_id + secret
- ``list-clients``       list registered clients (no secrets)
- ``revoke-client``      mark a client revoked (token endpoint refuses;
                         outstanding JWTs continue to verify until they
                         expire — 60min TTL bounds the window)
- ``mint-token``         dev/operator helper: exchange creds for a JWT
                         locally, useful for one-off curl tests
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import click


def _dsn() -> str:
    dsn = (
        os.getenv("POINDEXTER_MEMORY_DSN")
        or os.getenv("LOCAL_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    )
    if not dsn:
        raise RuntimeError(
            "No DSN — set POINDEXTER_MEMORY_DSN, LOCAL_DATABASE_URL, or DATABASE_URL.",
        )
    return dsn


def _run(coro):
    return asyncio.run(coro)


async def _pool():
    """Open a small asyncpg pool — services.auth.oauth_issuer takes a
    pool, not a single connection."""
    import asyncpg
    return await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)


def _bootstrap_path_for_secret_key() -> None:
    """Make sure ``POINDEXTER_SECRET_KEY`` is in env before we touch the
    issuer. Bootstrap normally exports it; CLI invocations skip the
    worker startup path so we read from ``bootstrap.toml`` directly."""
    if os.getenv("POINDEXTER_SECRET_KEY"):
        return
    try:
        # Local import — brain.bootstrap is heavy and only needed when
        # the env var isn't already populated.
        from brain.bootstrap import get_bootstrap_value  # type: ignore[import-not-found]
        key = get_bootstrap_value("poindexter_secret_key", "")
    except Exception:  # noqa: BLE001
        key = ""
    if key:
        os.environ["POINDEXTER_SECRET_KEY"] = key


@click.group(
    name="auth",
    help="Manage OAuth 2.1 client credentials (#241).",
)
def auth_group() -> None:
    pass


# ---------------------------------------------------------------------------
# register-client
# ---------------------------------------------------------------------------


@auth_group.command("register-client")
@click.option("--name", required=True, help="Human label for this client (e.g. 'mcp-http-prod').")
@click.option(
    "--scopes",
    required=True,
    help=(
        "Space-delimited subset of {mcp:read, mcp:write, api:read, api:write}. "
        'Quote the whole value, e.g. --scopes "mcp:read mcp:write".'
    ),
)
def register_client(name: str, scopes: str) -> None:
    """Create a new OAuth client. Prints client_id + plaintext secret ONCE.

    The secret is never stored in plaintext anywhere — capture it now,
    paste it into wherever the consuming tool reads its credentials, and
    discard the terminal scrollback.
    """
    _bootstrap_path_for_secret_key()

    requested = [s.strip() for s in scopes.split() if s.strip()]
    if not requested:
        raise click.UsageError("--scopes must list at least one scope")

    async def _impl():
        from services.auth.oauth_issuer import register_client as _register, InvalidScope

        pool = await _pool()
        try:
            try:
                client_id, secret = await _register(pool, name=name, scopes=requested)
            except InvalidScope as e:
                raise click.ClickException(str(e)) from e
        finally:
            await pool.close()

        click.echo("")
        click.echo(click.style("OAuth client registered.", fg="green", bold=True))
        click.echo(f"  name:          {name}")
        click.echo(f"  scopes:        {' '.join(sorted(set(requested)))}")
        click.echo(f"  client_id:     {client_id}")
        click.echo(f"  client_secret: {secret}")
        click.echo("")
        click.echo(click.style(
            "  Capture the client_secret NOW — it is not recoverable.",
            fg="yellow",
        ))
        click.echo("")
        click.echo("Test with curl:")
        click.echo(
            f'  curl -s -X POST http://localhost:8002/oauth/token \\\n'
            f'    -d grant_type=client_credentials \\\n'
            f'    -d client_id={client_id} \\\n'
            f'    -d client_secret={secret}'
        )

    _run(_impl())


# ---------------------------------------------------------------------------
# list-clients
# ---------------------------------------------------------------------------


def _format_age(ts: Any) -> str:
    if ts is None:
        return "—"
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    secs = int((now - ts).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


@auth_group.command("list-clients")
@click.option(
    "--include-revoked",
    is_flag=True,
    default=False,
    help="Include revoked clients in the listing.",
)
def list_clients(include_revoked: bool) -> None:
    """List registered OAuth clients (no secrets shown)."""
    async def _impl():
        pool = await _pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT client_id, name, scopes,
                           created_at, last_used_at, revoked_at
                      FROM oauth_clients
                     WHERE ($1::bool OR revoked_at IS NULL)
                  ORDER BY created_at DESC
                    """,
                    include_revoked,
                )
        finally:
            await pool.close()

        if not rows:
            click.echo("(no oauth_clients rows)")
            return

        header = f"{'CLIENT_ID':<24} {'NAME':<28} {'SCOPES':<32} {'CREATED':<12} {'LAST USE':<12} STATE"
        click.echo(header)
        click.echo("-" * len(header))
        for r in rows:
            scopes = " ".join(r["scopes"] or ())
            state = "revoked" if r["revoked_at"] else "active"
            click.echo(
                f"{r['client_id']:<24} {r['name'][:27]:<28} "
                f"{scopes[:31]:<32} {_format_age(r['created_at']):<12} "
                f"{_format_age(r['last_used_at']):<12} {state}"
            )

    _run(_impl())


# ---------------------------------------------------------------------------
# revoke-client
# ---------------------------------------------------------------------------


@auth_group.command("revoke-client")
@click.option("--client-id", required=True, help="The pdx_... id to revoke.")
def revoke_client(client_id: str) -> None:
    """Mark a client revoked. Token endpoint refuses immediately.

    Outstanding JWTs continue to verify until they expire — 60-minute
    TTL caps the window. This is by design (statelessness > liveness
    revocation for our scale).
    """
    async def _impl():
        pool = await _pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE oauth_clients
                       SET revoked_at = NOW()
                     WHERE client_id = $1
                       AND revoked_at IS NULL
                    """,
                    client_id,
                )
        finally:
            await pool.close()

        # asyncpg returns a string like "UPDATE 1"
        if result.endswith("0"):
            raise click.ClickException(
                f"client_id={client_id!r} not found, or already revoked"
            )
        click.echo(click.style(f"Revoked {client_id}.", fg="green"))


    _run(_impl())


# ---------------------------------------------------------------------------
# mint-token (operator helper)
# ---------------------------------------------------------------------------


@auth_group.command("mint-token")
@click.option("--client-id", required=True)
@click.option("--client-secret", required=True)
@click.option("--scopes", default="", help="Optional subset of the client's scopes.")
def mint_token(client_id: str, client_secret: str, scopes: str) -> None:
    """Exchange creds for a JWT locally (skips the HTTP /oauth/token round-trip).

    Useful for one-off ``curl -H "Authorization: Bearer …"`` tests or
    poking the MCP HTTP server before any HTTP issuer is reachable.
    """
    _bootstrap_path_for_secret_key()

    requested = [s.strip() for s in scopes.split() if s.strip()] or None

    async def _impl():
        from services.auth.oauth_issuer import (
            mint_token_from_credentials, InvalidClient, InvalidScope,
        )
        pool = await _pool()
        try:
            try:
                token, claims = await mint_token_from_credentials(
                    pool,
                    client_id=client_id,
                    client_secret=client_secret,
                    requested_scopes=requested,
                )
            except (InvalidClient, InvalidScope) as e:
                raise click.ClickException(str(e)) from e
        finally:
            await pool.close()

        ttl = claims.expires_at - claims.issued_at
        click.echo(token)
        click.echo("", err=True)
        click.echo(
            f"# scopes={' '.join(sorted(claims.scopes))} expires_in={ttl}s",
            err=True,
        )

    _run(_impl())
