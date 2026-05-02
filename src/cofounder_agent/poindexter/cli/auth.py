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
@click.option(
    "--redirect-uri",
    "redirect_uris",
    multiple=True,
    help=(
        "Allowed callback URL for Auth Code Grant clients (Custom Connectors, "
        "browser apps). May be repeated. Omit for headless clients that only "
        "use client_credentials."
    ),
)
@click.option(
    "--grant-type",
    "grant_types",
    multiple=True,
    type=click.Choice(["authorization_code", "refresh_token", "client_credentials"]),
    help=(
        "Allowed grant types. May be repeated. Default: all three (covers both "
        "browser and headless clients)."
    ),
)
def register_client(
    name: str,
    scopes: str,
    redirect_uris: tuple[str, ...],
    grant_types: tuple[str, ...],
) -> None:
    """Create a new OAuth client. Prints client_id + plaintext secret ONCE.

    The secret is encrypted at rest with the bootstrap secret key — what's
    in the DB is opaque without the key. The CLI prints the plaintext
    here once for the operator to capture; capture it now and discard the
    terminal scrollback.
    """
    _bootstrap_path_for_secret_key()

    requested_scopes = [s.strip() for s in scopes.split() if s.strip()]
    if not requested_scopes:
        raise click.UsageError("--scopes must list at least one scope")

    grants = list(grant_types) if grant_types else [
        "authorization_code", "refresh_token", "client_credentials",
    ]

    # The Custom Connector / browser flow needs at least one redirect_uri,
    # but a CLI-only client doesn't. Warn the operator if they're asking
    # for authorization_code without one.
    if "authorization_code" in grants and not redirect_uris:
        click.echo(click.style(
            "Note: registering with no --redirect-uri — this client can use "
            "client_credentials only. Add a --redirect-uri to enable browser "
            "Auth Code Grant clients (e.g. Anthropic Custom Connector).",
            fg="yellow",
        ))

    async def _impl():
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyUrl
        from services.auth.oauth_issuer import generate_client_id, generate_client_secret
        from services.auth.oauth_provider import PoindexterOAuthProvider

        client_id = generate_client_id()
        client_secret = generate_client_secret()

        # OAuthClientMetadata's redirect_uris field requires min_length=1
        # even for clients that only use client_credentials. Stash a
        # localhost placeholder when none was supplied — it'll never be
        # exercised because such a client doesn't hit /authorize anyway.
        uri_list = (
            [AnyUrl(u) for u in redirect_uris]
            if redirect_uris
            else [AnyUrl("http://localhost/")]
        )

        client_info = OAuthClientInformationFull(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=uri_list,
            token_endpoint_auth_method="client_secret_post",
            grant_types=grants,
            response_types=["code"],
            scope=" ".join(requested_scopes),
            client_name=name,
        )

        pool = await _pool()
        try:
            provider = PoindexterOAuthProvider(pool)
            await provider.register_client(client_info)
        finally:
            await pool.close()

        click.echo("")
        click.echo(click.style("OAuth client registered.", fg="green", bold=True))
        click.echo(f"  name:           {name}")
        click.echo(f"  scopes:         {' '.join(sorted(set(requested_scopes)))}")
        click.echo(f"  grant_types:    {' '.join(grants)}")
        if redirect_uris:
            click.echo(f"  redirect_uris:  {' '.join(redirect_uris)}")
        click.echo(f"  client_id:      {client_id}")
        click.echo(f"  client_secret:  {client_secret}")
        click.echo("")
        click.echo(click.style(
            "  Capture the client_secret NOW — it is not recoverable.",
            fg="yellow",
        ))
        click.echo("")
        click.echo("Test with curl (client_credentials):")
        click.echo(
            f'  curl -s -X POST http://localhost:8002/token \\\n'
            f'    -d grant_type=client_credentials \\\n'
            f'    -d client_id={client_id} \\\n'
            f'    -d client_secret={client_secret}'
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
    """Exchange creds for a JWT locally (skips the HTTP /token round-trip).

    Useful for one-off ``curl -H "Authorization: Bearer …"`` tests or
    poking services before any HTTP issuer is reachable.
    """
    _bootstrap_path_for_secret_key()

    requested = [s.strip() for s in scopes.split() if s.strip()] or None

    async def _impl():
        import hmac
        from services.auth.oauth_issuer import issue_token, InvalidScope
        from services.auth.oauth_provider import PoindexterOAuthProvider

        pool = await _pool()
        try:
            provider = PoindexterOAuthProvider(pool)
            client = await provider.get_client(client_id)
            if client is None:
                raise click.ClickException("invalid client credentials")
            if not client.client_secret or not hmac.compare_digest(
                client.client_secret.encode(), client_secret.encode(),
            ):
                raise click.ClickException("invalid client credentials")
            granted = set((client.scope or "").split())
            if requested:
                extras = set(requested) - granted
                if extras:
                    raise click.ClickException(
                        f"scope(s) not granted to client: {sorted(extras)}",
                    )
                issued = requested
            else:
                issued = sorted(granted)
            try:
                token, claims = issue_token(client_id, issued)
            except InvalidScope as e:
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


# ---------------------------------------------------------------------------
# migrate-cli / migrate-brain — one-shot Phase 2 client provisioning
# ---------------------------------------------------------------------------


async def _provision_consumer_client(
    name: str,
    scopes: list[str],
    client_id_setting_key: str,
    client_secret_setting_key: str,
) -> tuple[str, str]:
    """Register a new OAuth client and persist creds to app_settings.

    Returns (client_id, client_secret). Used by ``migrate-cli`` and
    ``migrate-brain`` so the helper has identical semantics in both
    spots — only the name + setting keys differ.

    The client is registered with ``client_credentials`` grant only (no
    browser callback), since both CLI and brain run headless. The
    secret is stored encrypted via ``plugins.secrets.set_secret`` with
    ``is_secret=true``.
    """
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl
    from services.auth.oauth_issuer import generate_client_id, generate_client_secret
    from services.auth.oauth_provider import PoindexterOAuthProvider
    from plugins.secrets import set_secret

    client_id = generate_client_id()
    client_secret = generate_client_secret()

    client_info = OAuthClientInformationFull(
        client_id=client_id,
        client_secret=client_secret,
        # The SDK requires at least one redirect_uri even for headless
        # clients; localhost placeholder is harmless because the
        # client_credentials path doesn't hit /authorize.
        redirect_uris=[AnyUrl("http://localhost/")],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["client_credentials"],
        response_types=["code"],
        scope=" ".join(scopes),
        client_name=name,
    )

    pool = await _pool()
    try:
        provider = PoindexterOAuthProvider(pool)
        await provider.register_client(client_info)
        async with pool.acquire() as conn:
            await set_secret(
                conn, client_id_setting_key, client_id,
                description=f"OAuth client_id for {name} (Phase 2 #241)",
            )
            await set_secret(
                conn, client_secret_setting_key, client_secret,
                description=f"OAuth client_secret for {name} (Phase 2 #241)",
            )
    finally:
        await pool.close()

    return client_id, client_secret


@auth_group.command("migrate-cli")
@click.option(
    "--name",
    default="poindexter-cli",
    show_default=True,
    help="Client display name (shown in `poindexter auth list-clients`).",
)
@click.option(
    "--scopes",
    default="api:read api:write",
    show_default=True,
    help="Space-delimited subset of {api:read, api:write, mcp:read, mcp:write}.",
)
def migrate_cli(name: str, scopes: str) -> None:
    """Register an OAuth client for the Poindexter CLI and store creds.

    One-shot Phase 2 migration helper (#242). After this runs:

    * A new ``oauth_clients`` row exists with ``client_credentials`` grant.
    * ``app_settings.cli_oauth_client_id`` + ``cli_oauth_client_secret``
      hold the new credentials (encrypted via plugins.secrets).
    * The CLI's ``WorkerClient`` automatically prefers the new OAuth
      path; the static-Bearer fallback stays available until Phase 3
      (#249) removes it.

    Idempotent in the sense that re-running creates a *new* client +
    secret pair (the previous client stays registered until you
    ``revoke-client`` it). Run once per environment.
    """
    _bootstrap_path_for_secret_key()
    scope_list = [s.strip() for s in scopes.split() if s.strip()]
    if not scope_list:
        raise click.UsageError("--scopes must list at least one scope")

    from poindexter.cli._api_client import (
        CLI_CLIENT_ID_KEY,
        CLI_CLIENT_SECRET_KEY,
    )

    async def _impl():
        client_id, client_secret = await _provision_consumer_client(
            name=name,
            scopes=scope_list,
            client_id_setting_key=CLI_CLIENT_ID_KEY,
            client_secret_setting_key=CLI_CLIENT_SECRET_KEY,
        )
        click.echo("")
        click.echo(click.style("CLI OAuth client provisioned.", fg="green", bold=True))
        click.echo(f"  name:           {name}")
        click.echo(f"  scopes:         {' '.join(scope_list)}")
        click.echo(f"  client_id:      {client_id}")
        click.echo(f"  app_settings:   {CLI_CLIENT_ID_KEY} + {CLI_CLIENT_SECRET_KEY}")
        click.echo("")
        click.echo("The CLI will use OAuth on the next invocation. The legacy")
        click.echo("static-Bearer fallback stays active until Phase 3 (#249).")

    _run(_impl())


@auth_group.command("migrate-brain")
@click.option(
    "--name",
    default="brain-daemon",
    show_default=True,
    help="Client display name (shown in `poindexter auth list-clients`).",
)
@click.option(
    "--scopes",
    default="api:read api:write",
    show_default=True,
    help="Space-delimited subset of {api:read, api:write, mcp:read, mcp:write}.",
)
def migrate_brain(name: str, scopes: str) -> None:
    """Register an OAuth client for the brain daemon and store creds.

    One-shot Phase 2 migration helper (#245). After this runs:

    * A new ``oauth_clients`` row exists with ``client_credentials`` grant.
    * ``app_settings.brain_oauth_client_id`` + ``brain_oauth_client_secret``
      hold the new credentials (encrypted via plugins.secrets).
    * The brain daemon's HTTP probes will use OAuth on the next cycle;
      the static-Bearer fallback stays available until Phase 3.

    Re-running creates a fresh client + secret pair; revoke the old one
    afterwards with ``poindexter auth revoke-client --client-id ...``.
    """
    _bootstrap_path_for_secret_key()
    scope_list = [s.strip() for s in scopes.split() if s.strip()]
    if not scope_list:
        raise click.UsageError("--scopes must list at least one scope")

    # Brain helper module owns the canonical setting keys.
    BRAIN_CLIENT_ID_KEY = "brain_oauth_client_id"
    BRAIN_CLIENT_SECRET_KEY = "brain_oauth_client_secret"

    async def _impl():
        client_id, client_secret = await _provision_consumer_client(
            name=name,
            scopes=scope_list,
            client_id_setting_key=BRAIN_CLIENT_ID_KEY,
            client_secret_setting_key=BRAIN_CLIENT_SECRET_KEY,
        )
        click.echo("")
        click.echo(click.style("Brain OAuth client provisioned.", fg="green", bold=True))
        click.echo(f"  name:           {name}")
        click.echo(f"  scopes:         {' '.join(scope_list)}")
        click.echo(f"  client_id:      {client_id}")
        click.echo(f"  app_settings:   {BRAIN_CLIENT_ID_KEY} + {BRAIN_CLIENT_SECRET_KEY}")
        click.echo("")
        click.echo("The brain will pick up the new credentials on its next")
        click.echo("daemon restart (or call brain.oauth_client.oauth_client_from_pool")
        click.echo("from a probe to use them inline).")

    _run(_impl())
