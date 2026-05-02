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


@auth_group.command("migrate-mcp")
@click.option(
    "--name",
    default="poindexter-mcp",
    show_default=True,
    help="Client display name (shown in `poindexter auth list-clients`).",
)
@click.option(
    "--scopes",
    default="api:read api:write",
    show_default=True,
    help="Space-delimited subset of {api:read, api:write, mcp:read, mcp:write}.",
)
def migrate_mcp(name: str, scopes: str) -> None:
    """Register an OAuth client for the public MCP server and store creds.

    One-shot Phase 2 migration helper (#243). After this runs:

    * A new ``oauth_clients`` row exists with ``client_credentials`` grant.
    * ``app_settings.mcp_oauth_client_id`` + ``mcp_oauth_client_secret``
      hold the new credentials (encrypted via plugins.secrets).
    * The MCP server's ``_api()`` calls automatically prefer the new
      OAuth path on the next process restart; the static-Bearer
      fallback (``app_settings.api_token`` and ``POINDEXTER_API_TOKEN``
      env) stays available until Phase 3 retires it.

    Re-running creates a fresh client + secret pair; revoke the old one
    with ``poindexter auth revoke-client --client-id ...``.

    Restart the stdio MCP server (Claude Code / Claude Desktop reload)
    after running this so the new creds get picked up.
    """
    _bootstrap_path_for_secret_key()
    scope_list = [s.strip() for s in scopes.split() if s.strip()]
    if not scope_list:
        raise click.UsageError("--scopes must list at least one scope")

    # Setting keys live in mcp-server/oauth_client.py — duplicated here
    # so the CLI doesn't have to add mcp-server/ to its import path.
    # Keep these in sync if either side ever changes.
    MCP_CLIENT_ID_KEY = "mcp_oauth_client_id"
    MCP_CLIENT_SECRET_KEY = "mcp_oauth_client_secret"

    async def _impl():
        client_id, client_secret = await _provision_consumer_client(
            name=name,
            scopes=scope_list,
            client_id_setting_key=MCP_CLIENT_ID_KEY,
            client_secret_setting_key=MCP_CLIENT_SECRET_KEY,
        )
        click.echo("")
        click.echo(click.style(
            "MCP server OAuth client provisioned.", fg="green", bold=True,
        ))
        click.echo(f"  name:           {name}")
        click.echo(f"  scopes:         {' '.join(scope_list)}")
        click.echo(f"  client_id:      {client_id}")
        click.echo(f"  app_settings:   {MCP_CLIENT_ID_KEY} + {MCP_CLIENT_SECRET_KEY}")
        click.echo("")
        click.echo("Restart the stdio MCP server (reload Claude Code / Claude")
        click.echo("Desktop) so the new credentials are picked up.")

    _run(_impl())


@auth_group.command("migrate-mcp-gladlabs")
@click.option(
    "--name",
    default="gladlabs-mcp",
    show_default=True,
    help="Client display name (shown in `poindexter auth list-clients`).",
)
@click.option(
    "--scopes",
    default="mcp:read mcp:write api:read api:write",
    show_default=True,
    help=(
        "Space-delimited subset of {api:read, api:write, mcp:read, mcp:write}. "
        "The operator MCP defaults to the broader set because its tools tend "
        "to write — Discord posts, customer lookups, subscriber management."
    ),
)
def migrate_mcp_gladlabs(name: str, scopes: str) -> None:
    """Register an OAuth client for the gladlabs operator MCP and store creds.

    One-shot Phase 2 migration helper (#244). After this runs:

    * A new ``oauth_clients`` row exists with ``client_credentials`` grant.
    * ``app_settings.mcp_gladlabs_oauth_client_id`` +
      ``mcp_gladlabs_oauth_client_secret`` hold the new credentials
      (encrypted via plugins.secrets).
    * The gladlabs MCP server's worker-API calls (when added) will use
      OAuth automatically; the static-Bearer fallback (api_token /
      POINDEXTER_API_TOKEN) stays available until Phase 3.

    Distinct from ``migrate-mcp`` because the operator MCP is a separate
    consumer — different scopes, separate audit trail, independent
    revoke. Both can be active simultaneously.
    """
    _bootstrap_path_for_secret_key()
    scope_list = [s.strip() for s in scopes.split() if s.strip()]
    if not scope_list:
        raise click.UsageError("--scopes must list at least one scope")

    MCP_GLADLABS_CLIENT_ID_KEY = "mcp_gladlabs_oauth_client_id"
    MCP_GLADLABS_CLIENT_SECRET_KEY = "mcp_gladlabs_oauth_client_secret"

    async def _impl():
        client_id, client_secret = await _provision_consumer_client(
            name=name,
            scopes=scope_list,
            client_id_setting_key=MCP_GLADLABS_CLIENT_ID_KEY,
            client_secret_setting_key=MCP_GLADLABS_CLIENT_SECRET_KEY,
        )
        click.echo("")
        click.echo(click.style(
            "Gladlabs MCP OAuth client provisioned.", fg="green", bold=True,
        ))
        click.echo(f"  name:           {name}")
        click.echo(f"  scopes:         {' '.join(scope_list)}")
        click.echo(f"  client_id:      {client_id}")
        click.echo(
            f"  app_settings:   {MCP_GLADLABS_CLIENT_ID_KEY} + "
            f"{MCP_GLADLABS_CLIENT_SECRET_KEY}"
        )
        click.echo("")
        click.echo("Restart the stdio gladlabs MCP server so the new credentials")
        click.echo("are picked up.")

    _run(_impl())


@auth_group.command("migrate-openclaw")
@click.option(
    "--name",
    default="openclaw-skills",
    show_default=True,
    help="Client display name (shown in `poindexter auth list-clients`).",
)
@click.option(
    "--scopes",
    default="api:read api:write",
    show_default=True,
    help=(
        "Space-delimited subset of {api:read, api:write, mcp:read, mcp:write}. "
        "Default covers the existing skill surface (list/get reads + "
        "approve/publish/reject/create writes)."
    ),
)
def migrate_openclaw(name: str, scopes: str) -> None:
    """Register an OAuth client for the OpenClaw skill bash scripts.

    One-shot Phase 2 migration helper (#246). After this runs:

    * A new ``oauth_clients`` row exists with ``client_credentials`` grant.
    * ``app_settings.openclaw_oauth_client_id`` +
      ``openclaw_oauth_client_secret`` hold the new credentials
      (encrypted via plugins.secrets).
    * The CLI prints an env block — paste it into ``~/.openclaw/openclaw.json``
      under ``processes.<entry>.env`` so every skill subprocess inherits
      ``POINDEXTER_OAUTH_CLIENT_ID`` + ``POINDEXTER_OAUTH_CLIENT_SECRET``.
      The shared ``skills/openclaw/_lib/get_token.sh`` helper picks them
      up automatically and mints a fresh JWT per skill invocation
      (cached at ``~/.openclaw/.token-cache-<client_id>`` until 30s
      before exp).

    The legacy ``POINDEXTER_KEY`` static-Bearer fallback stays available
    until Phase 3 (#249) — the helper prefers OAuth when both are set.
    """
    _bootstrap_path_for_secret_key()
    scope_list = [s.strip() for s in scopes.split() if s.strip()]
    if not scope_list:
        raise click.UsageError("--scopes must list at least one scope")

    OPENCLAW_CLIENT_ID_KEY = "openclaw_oauth_client_id"
    OPENCLAW_CLIENT_SECRET_KEY = "openclaw_oauth_client_secret"

    async def _impl():
        client_id, client_secret = await _provision_consumer_client(
            name=name,
            scopes=scope_list,
            client_id_setting_key=OPENCLAW_CLIENT_ID_KEY,
            client_secret_setting_key=OPENCLAW_CLIENT_SECRET_KEY,
        )
        click.echo("")
        click.echo(click.style(
            "OpenClaw OAuth client provisioned.", fg="green", bold=True,
        ))
        click.echo(f"  name:           {name}")
        click.echo(f"  scopes:         {' '.join(scope_list)}")
        click.echo(f"  client_id:      {client_id}")
        click.echo(
            f"  app_settings:   {OPENCLAW_CLIENT_ID_KEY} + "
            f"{OPENCLAW_CLIENT_SECRET_KEY}"
        )
        click.echo("")
        click.echo("Paste this env block into ~/.openclaw/openclaw.json under")
        click.echo("each skill process's `env` map (or set globally so every")
        click.echo("subprocess inherits both vars):")
        click.echo("")
        click.echo("  POINDEXTER_OAUTH_CLIENT_ID=" + client_id)
        click.echo("  POINDEXTER_OAUTH_CLIENT_SECRET=" + client_secret)
        click.echo("  FASTAPI_URL=http://localhost:8002   # if not already set")
        click.echo("")
        click.echo(click.style(
            "  Capture the client_secret NOW — it is not recoverable.",
            fg="yellow",
        ))
        click.echo("")
        click.echo("Skill scripts source skills/openclaw/_lib/get_token.sh,")
        click.echo("which mints + caches a JWT per OAuth client. The legacy")
        click.echo("POINDEXTER_KEY fallback stays active until Phase 3 (#249).")

    _run(_impl())


@auth_group.command("mint-grafana-token")
@click.option(
    "--ttl",
    "ttl_str",
    default="90d",
    show_default=True,
    help=(
        "Token lifetime — accepts <int>{s|m|h|d}. The MCP/CLI tokens use "
        "the 60-min default; Grafana's contact-point UI is operator-pasted "
        "and the rotation cadence is human-scale, so 90 days is a sensible "
        "default. Cap at 365d to keep blast-radius bounded."
    ),
)
@click.option(
    "--scopes",
    default="api:read api:write",
    show_default=True,
    help=(
        "Space-delimited subset of {api:read, api:write, mcp:read, mcp:write}. "
        "Default covers the alertmanager webhook (which writes to "
        "alert_events + dispatches to operator notify); narrow to "
        "'api:read' if you wire Grafana up to a read-only endpoint."
    ),
)
@click.option(
    "--name",
    default="grafana-alerts",
    show_default=True,
    help="Client display name (shown in `poindexter auth list-clients`).",
)
def mint_grafana_token(ttl_str: str, scopes: str, name: str) -> None:
    """Mint a long-TTL JWT for Grafana contact-point webhooks (#247).

    Pre-issued long-TTL JWT (option B from the #247 issue) — Grafana's
    own OAuth contact-point flow is fragile across versions, and the
    operator pastes the token into the contact-point UI exactly once
    per rotation. See ``docs/operations/oauth-grafana.md`` for the full
    paste-into-Grafana walkthrough.

    Idempotent in the sense that re-running mints a *new* JWT bound to
    the same ``grafana-alerts`` client. The previous JWT keeps verifying
    until its own ``exp`` elapses (or you ``revoke-client`` the underlying
    OAuth client to invalidate everything bound to it on the next mint
    cycle — outstanding JWTs continue verifying until they expire,
    which is exactly the trade-off documented for #241).

    First call provisions the OAuth client + persists encrypted creds to
    ``app_settings.grafana_oauth_client_id`` / ``_client_secret``. Subsequent
    calls reuse those creds.
    """
    _bootstrap_path_for_secret_key()

    ttl_seconds = _parse_ttl(ttl_str)
    if ttl_seconds < 60:
        raise click.UsageError(
            "--ttl must be at least 60 seconds (a token shorter than the "
            "default 60s skew on consumers would mint expired tokens)"
        )
    if ttl_seconds > 365 * 24 * 3600:
        raise click.UsageError(
            "--ttl capped at 365d. If you need a longer-lived credential, "
            "rotate at this cadence — anything longer is a code smell."
        )

    scope_list = [s.strip() for s in scopes.split() if s.strip()]
    if not scope_list:
        raise click.UsageError("--scopes must list at least one scope")

    GRAFANA_CLIENT_ID_KEY = "grafana_oauth_client_id"
    GRAFANA_CLIENT_SECRET_KEY = "grafana_oauth_client_secret"

    async def _impl():
        from plugins.secrets import get_secret
        from services.auth.oauth_issuer import (
            InvalidScope,
            issue_token,
        )

        pool = await _pool()
        try:
            async with pool.acquire() as conn:
                client_id = await get_secret(conn, GRAFANA_CLIENT_ID_KEY)

            if not client_id:
                # First mint — provision the client.
                client_id, _client_secret = await _provision_consumer_client(
                    name=name,
                    scopes=scope_list,
                    client_id_setting_key=GRAFANA_CLIENT_ID_KEY,
                    client_secret_setting_key=GRAFANA_CLIENT_SECRET_KEY,
                )
                provisioned_now = True
            else:
                provisioned_now = False

            # Mint the JWT. Note: we issue directly (skipping the HTTP
            # /token round trip) because the CLI already has DB access
            # and the issuer is synchronous. Same shape as ``mint-token``.
            try:
                token, claims = issue_token(
                    client_id, scope_list, ttl_seconds=ttl_seconds,
                )
            except InvalidScope as e:
                raise click.ClickException(str(e)) from e
        finally:
            await pool.close()

        click.echo("")
        if provisioned_now:
            click.echo(click.style(
                "Grafana OAuth client provisioned + token minted.",
                fg="green", bold=True,
            ))
        else:
            click.echo(click.style(
                "Token minted for existing Grafana OAuth client.",
                fg="green", bold=True,
            ))
        click.echo(f"  client_id:   {client_id}")
        click.echo(f"  scopes:      {' '.join(sorted(claims.scopes))}")
        click.echo(f"  ttl:         {ttl_str} ({ttl_seconds}s)")
        click.echo(
            f"  expires_at:  {claims.expires_at} "
            f"(epoch — {_format_epoch(claims.expires_at)})"
        )
        click.echo(f"  jti:         {claims.jti}")
        click.echo("")
        click.echo("Token (paste into Grafana contact-point Authorization Header):")
        click.echo("")
        click.echo(token)
        click.echo("")
        click.echo(click.style(
            "  Capture the token NOW — it is not recoverable from the DB.",
            fg="yellow",
        ))
        click.echo("")
        click.echo(
            "Walkthrough: see docs/operations/oauth-grafana.md for where to "
            "paste in Grafana's contact-point UI."
        )

    _run(_impl())


def _parse_ttl(s: str) -> int:
    """Parse a TTL string like '90d', '60m', '3600' into seconds.

    Bare integers count as seconds (matches the ``--ttl 3600`` legacy
    pattern). Suffixes ``s|m|h|d`` are case-insensitive.
    """
    s = s.strip().lower()
    if not s:
        raise click.UsageError("--ttl is required")
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s[-1] in multipliers:
        try:
            n = int(s[:-1])
        except ValueError as e:
            raise click.UsageError(
                f"--ttl: cannot parse '{s}' as <int>{{s|m|h|d}}"
            ) from e
        return n * multipliers[s[-1]]
    try:
        return int(s)
    except ValueError as e:
        raise click.UsageError(
            f"--ttl: cannot parse '{s}' as integer-of-seconds or "
            "<int>{s|m|h|d}"
        ) from e


def _format_epoch(epoch_seconds: int) -> str:
    """Format a unix epoch as ``YYYY-MM-DD HH:MM UTC``. Best-effort —
    if the value is wildly out of range we fall back to repr."""
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(
            epoch_seconds, tz=_dt.timezone.utc,
        ).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return repr(epoch_seconds)


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


# ---------------------------------------------------------------------------
# migrate-scripts — one-shot scripts/ provisioning (Round 2B / #248)
# ---------------------------------------------------------------------------


def _write_bootstrap_oauth_creds(client_id: str, client_secret: str) -> bool:
    """Append/update OAuth scripts creds in ``~/.poindexter/bootstrap.toml``.

    The brain daemon's bootstrap helper owns full TOML serialisation,
    but we only need to set/update two keys, so we do a minimal
    in-place edit that preserves the rest of the file. Returns True
    when the write succeeded.

    Why bootstrap.toml in addition to app_settings: some scripts under
    ``scripts/`` run on the operator's host without DB access (e.g.
    ``scripts/daemon.py`` reading from ``~/.poindexter`` before any
    pool is opened). Storing the creds in both places lets those
    scripts use OAuth without an extra DB roundtrip. The values are
    plaintext on disk in bootstrap.toml — same risk profile as the
    legacy ``api_token`` line that already lives there.
    """
    import re
    from pathlib import Path

    path = Path.home() / ".poindexter" / "bootstrap.toml"
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""

    def _upsert(text: str, key: str, value: str) -> str:
        line = f'{key} = "{value}"'
        pattern = re.compile(rf'^{re.escape(key)}\s*=\s*".*?"$', re.MULTILINE)
        if pattern.search(text):
            return pattern.sub(line, text, count=1)
        # Append — keep a trailing newline for clean concatenation.
        sep = "" if text.endswith("\n") or text == "" else "\n"
        return f"{text}{sep}{line}\n"

    text = _upsert(text, "scripts_oauth_client_id", client_id)
    text = _upsert(text, "scripts_oauth_client_secret", client_secret)

    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        click.echo(click.style(
            f"  WARN: could not write {path}: {exc}", fg="yellow",
        ))
        return False

    # Restrict permissions where the platform supports it (no-op on Windows).
    try:
        import stat
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:  # noqa: BLE001
        pass
    return True


@auth_group.command("migrate-scripts")
@click.option(
    "--name",
    default="scripts-shared",
    show_default=True,
    help="Client display name (shown in `poindexter auth list-clients`).",
)
@click.option(
    "--scopes",
    default="api:read api:write",
    show_default=True,
    help="Space-delimited subset of {api:read, api:write, mcp:read, mcp:write}.",
)
@click.option(
    "--no-bootstrap",
    is_flag=True,
    default=False,
    help=(
        "Skip writing the credentials into ~/.poindexter/bootstrap.toml. "
        "Useful when the scripts run only inside containers that read from "
        "app_settings via asyncpg."
    ),
)
def migrate_scripts(name: str, scopes: str, no_bootstrap: bool) -> None:
    """Register a shared OAuth client for ``scripts/`` and store creds.

    One-shot Phase 2 Round 2B migration helper (#248). After this runs:

    * A new ``oauth_clients`` row exists with ``client_credentials`` grant.
    * ``app_settings.scripts_oauth_client_id`` +
      ``scripts_oauth_client_secret`` hold the new credentials
      (encrypted via plugins.secrets).
    * Unless ``--no-bootstrap`` is passed, ``~/.poindexter/bootstrap.toml``
      gets the same two keys in plaintext so host-side scripts that
      don't open a DB pool can still mint JWTs (matches the existing
      ``api_token`` plaintext line already in bootstrap.toml).
    * Every script in ``scripts/`` that has been migrated to
      ``ScriptsOAuthClient`` will pick up OAuth on its next start;
      legacy static-Bearer fallback stays active until Phase 3 (#249).

    A single shared client per operator covers all migrated scripts —
    per-script clients would be overkill at our scale and create
    rotation churn.

    Re-running creates a fresh client + secret pair; revoke the old one
    afterwards with ``poindexter auth revoke-client --client-id ...``.
    """
    _bootstrap_path_for_secret_key()
    scope_list = [s.strip() for s in scopes.split() if s.strip()]
    if not scope_list:
        raise click.UsageError("--scopes must list at least one scope")

    SCRIPTS_CLIENT_ID_KEY = "scripts_oauth_client_id"
    SCRIPTS_CLIENT_SECRET_KEY = "scripts_oauth_client_secret"

    async def _impl():
        client_id, client_secret = await _provision_consumer_client(
            name=name,
            scopes=scope_list,
            client_id_setting_key=SCRIPTS_CLIENT_ID_KEY,
            client_secret_setting_key=SCRIPTS_CLIENT_SECRET_KEY,
        )

        bootstrap_written = False
        if not no_bootstrap:
            bootstrap_written = _write_bootstrap_oauth_creds(
                client_id, client_secret,
            )

        click.echo("")
        click.echo(click.style("Scripts OAuth client provisioned.", fg="green", bold=True))
        click.echo(f"  name:           {name}")
        click.echo(f"  scopes:         {' '.join(scope_list)}")
        click.echo(f"  client_id:      {client_id}")
        click.echo(f"  app_settings:   {SCRIPTS_CLIENT_ID_KEY} + {SCRIPTS_CLIENT_SECRET_KEY}")
        if no_bootstrap:
            click.echo("  bootstrap.toml: skipped (--no-bootstrap)")
        elif bootstrap_written:
            click.echo("  bootstrap.toml: scripts_oauth_client_id + scripts_oauth_client_secret")
        else:
            click.echo("  bootstrap.toml: write FAILED — see warning above")
        click.echo("")
        click.echo("Migrated scripts (telegram-bot.py, discord-voice-bot.py,")
        click.echo("daemon.py, ...) will use OAuth on next start. The legacy")
        click.echo("static-Bearer fallback stays active until Phase 3 (#249).")

    _run(_impl())
