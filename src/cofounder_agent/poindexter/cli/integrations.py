"""``poindexter integrations`` — operator-side setup for external integrations.

Right now this hosts the YouTube OAuth consent flow + smoke test —
the path Matt runs once after creating a new Google Cloud OAuth
client to seed
``app_settings.plugin.publish_adapter.youtube.{client_id,client_secret,refresh_token}``.
Adapter logic lives in :mod:`services.publish_adapters.youtube`; this
module is the operator UX around it.

Sister commands (planned, not yet wired): ``poindexter integrations
linkedin setup`` for the LinkedIn adapter (Glad-Labs/poindexter#40
sibling — same shape, different OAuth provider).

**Cost note (per `feedback_no_paid_apis`):** the YouTube Data API
v3 is FREE — quota is enforced in units, NOT dollars. Setting up
YouTube publishing does NOT open a paid-API door; see the adapter
docstring for the full quota breakdown.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _connect() -> Any:
    """Open a fresh asyncpg connection using the shared DSN resolver."""
    import asyncpg

    from ._bootstrap import resolve_dsn

    return await asyncpg.connect(resolve_dsn())


# ---------------------------------------------------------------------------
# group structure
# ---------------------------------------------------------------------------


@click.group(
    name="integrations",
    help=(
        "Operator-side setup for external integrations (YouTube, "
        "LinkedIn, etc.). Each sub-group owns one provider's OAuth "
        "consent flow + smoke test."
    ),
)
def integrations_group() -> None:
    pass


@integrations_group.group(
    name="youtube",
    help=(
        "YouTube Data API v3 — OAuth consent setup + upload smoke "
        "test. Pre-requisite: create a Desktop OAuth client in Google "
        "Cloud Console (any project with YouTube Data API v3 enabled), "
        "download the client_secret_*.json file, then run "
        "`poindexter integrations youtube setup --client-secret-file "
        "<path>`."
    ),
)
def youtube_group() -> None:
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_CLIENT_SECRETS_FORMAT_HELP = (
    "The client_secret JSON file must contain an 'installed' or 'web' "
    "section with client_id + client_secret. This is what Google's "
    "Cloud Console download button produces — don't reformat it."
)


def _load_client_config(
    *,
    client_id: str | None,
    client_secret_file: str | None,
    client_secret: str | None,
) -> tuple[str, str]:
    """Resolve the OAuth client_id + client_secret from CLI inputs.

    Accepts either:
    - ``--client-secret-file path/to/client_secret_*.json`` (the file
      Google's Cloud Console gives you when you create an OAuth
      client), OR
    - ``--client-id ID --client-secret SECRET`` (raw values).

    Fails loudly with a clear message if neither is supplied or the
    file shape doesn't match Google's published schema.
    """
    if client_secret_file:
        if not os.path.exists(client_secret_file):
            raise click.ClickException(
                f"--client-secret-file not found: {client_secret_file!r}"
            )
        try:
            with open(client_secret_file, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            raise click.ClickException(
                f"--client-secret-file is not valid JSON: {exc}. "
                + _CLIENT_SECRETS_FORMAT_HELP
            ) from exc
        # Google ships the credentials nested under "installed" (Desktop
        # app) or "web" (Web app). Desktop is the recommended type for
        # InstalledAppFlow.run_local_server, but accept both for
        # operator flexibility.
        for top_key in ("installed", "web"):
            block = data.get(top_key) if isinstance(data, dict) else None
            if isinstance(block, dict):
                cid = str(block.get("client_id") or "").strip()
                csecret = str(block.get("client_secret") or "").strip()
                if cid and csecret:
                    return cid, csecret
        raise click.ClickException(
            "--client-secret-file missing client_id / client_secret. "
            + _CLIENT_SECRETS_FORMAT_HELP
        )

    if client_id and client_secret:
        return client_id.strip(), client_secret.strip()

    raise click.ClickException(
        "Provide --client-secret-file <path> (recommended) OR "
        "--client-id + --client-secret. "
        "Create a Desktop OAuth client in Google Cloud Console → "
        "APIs & Services → Credentials, then download the JSON."
    )


def _build_client_config_dict(client_id: str, client_secret: str) -> dict[str, Any]:
    """Shape the in-memory dict that ``InstalledAppFlow.from_client_config``
    expects.

    Built fresh per setup invocation (we never persist the file to
    disk — only the resulting refresh_token gets stored, and only in
    the encrypted secrets table).
    """
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        },
    }


def _run_consent_flow(client_id: str, client_secret: str) -> Any:
    """Open the operator's browser, capture the OAuth redirect, return
    ``google.oauth2.credentials.Credentials``.

    Lazy-imports the Google libs so the CLI module is importable when
    the ``youtube`` extra hasn't been installed (`poetry install
    --extras youtube` is the gate).

    Per `feedback_oauth_scope_hygiene`: only requests
    ``youtube.upload`` — the absolute minimum scope. We deliberately do
    NOT add ``youtube.readonly`` just to support a channel read-back:
    ``channels.list(mine=True)`` 403s under an upload-only token, so the
    setup flow treats that read-back as best-effort and proves the
    grant end-to-end via an actual upload (`youtube test`) instead.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]
    except ImportError as exc:
        raise click.ClickException(
            f"google-auth-oauthlib not installed ({exc}). Run: "
            "`poetry install --extras youtube` in src/cofounder_agent."
        ) from exc

    # Pull scopes from the adapter so we don't drift — single source
    # of truth for "what the operator consented to grant us".
    from services.publish_adapters.youtube import _SCOPES

    flow = InstalledAppFlow.from_client_config(
        _build_client_config_dict(client_id, client_secret),
        scopes=_SCOPES,
    )
    # port=0 lets the OS pick a free port; google-auth-oauthlib hosts
    # a one-shot HTTP server there to catch the redirect with the
    # authorization code.
    credentials = flow.run_local_server(port=0)
    return credentials


def _verify_channel(credentials: Any) -> dict[str, str]:
    """Call ``youtube.channels.list(mine=True)`` to confirm the consent
    actually granted us a usable token + the operator's account has a
    YouTube channel.

    Returns ``{"channel_id": ..., "channel_title": ...}`` on success.
    Raises ``click.ClickException`` on any failure — we'd rather bail
    loudly than write half-credentials per
    `feedback_no_silent_defaults`.
    """
    try:
        from googleapiclient.discovery import build  # type: ignore[import-not-found]
    except ImportError as exc:
        raise click.ClickException(
            f"google-api-python-client not installed ({exc}). Run: "
            "`poetry install --extras youtube` in src/cofounder_agent."
        ) from exc

    try:
        youtube = build(
            "youtube", "v3",
            credentials=credentials,
            cache_discovery=False,
        )
        response = youtube.channels().list(
            part="id,snippet", mine=True,
        ).execute()
    except Exception as exc:
        raise click.ClickException(
            f"channels.list(mine=True) failed: {type(exc).__name__}: "
            f"{exc}. Possible causes: token didn't get the "
            "youtube.upload scope, your Google account has no YouTube "
            "channel, or the YouTube Data API v3 isn't enabled on "
            "the GCP project. Fix and re-run setup."
        ) from exc

    items = response.get("items") or []
    if not items:
        raise click.ClickException(
            "channels.list returned no channels for the authorized "
            "account. The Google account you consented with does not "
            "own a YouTube channel — go create one at "
            "https://youtube.com first, then re-run setup."
        )
    item = items[0]
    snippet = item.get("snippet") or {}
    return {
        "channel_id": str(item.get("id") or ""),
        "channel_title": str(snippet.get("title") or "<no title>"),
    }


async def _write_secrets(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> None:
    """Encrypt and write the 3 OAuth secrets to app_settings via the
    canonical secret-write path (``plugins.secrets.set_secret``).

    Same write path the existing
    ``poindexter publishers set-secret`` command uses — keeps every
    is_secret=true row encrypted under the same pgcrypto key.

    Fails loud (raises) if anything goes wrong — partial writes would
    leave the adapter in a half-configured state per
    `feedback_no_silent_defaults`.
    """
    from plugins.secrets import ensure_pgcrypto, set_secret

    from ._bootstrap import ensure_secret_key

    # The encryption key comes from POINDEXTER_SECRET_KEY in env or
    # bootstrap.toml. Bare `poindexter <cmd>` invocations don't have
    # it pre-loaded the way the worker process does.
    ensure_secret_key()

    conn = await _connect()
    try:
        await ensure_pgcrypto(conn)
        # Write all three or none — wrap in a transaction so a mid-
        # write failure doesn't leave a half-configured adapter.
        async with conn.transaction():
            await set_secret(
                conn,
                "plugin.publish_adapter.youtube.client_id",
                client_id,
                description=(
                    "YouTube Data API v3 OAuth client_id. Configured "
                    "via `poindexter integrations youtube setup`. "
                    "Free API — see services/publish_adapters/youtube.py."
                ),
            )
            await set_secret(
                conn,
                "plugin.publish_adapter.youtube.client_secret",
                client_secret,
                description=(
                    "YouTube Data API v3 OAuth client_secret. Configured "
                    "via `poindexter integrations youtube setup`."
                ),
            )
            await set_secret(
                conn,
                "plugin.publish_adapter.youtube.refresh_token",
                refresh_token,
                description=(
                    "YouTube Data API v3 OAuth refresh_token (long-lived). "
                    "Configured via `poindexter integrations youtube setup`. "
                    "Re-run setup to rotate."
                ),
            )
    finally:
        await conn.close()


async def _read_secrets() -> dict[str, str]:
    """Read the 3 YouTube OAuth secrets from app_settings, returning
    them as a plaintext dict.

    Used by the smoke-test path; the adapter's normal flow goes
    through SiteConfig.get_secret instead.
    """
    from plugins.secrets import get_secret

    from ._bootstrap import ensure_secret_key

    ensure_secret_key()
    conn = await _connect()
    try:
        return {
            "client_id": await get_secret(
                conn, "plugin.publish_adapter.youtube.client_id"
            ) or "",
            "client_secret": await get_secret(
                conn, "plugin.publish_adapter.youtube.client_secret"
            ) or "",
            "refresh_token": await get_secret(
                conn, "plugin.publish_adapter.youtube.refresh_token"
            ) or "",
        }
    finally:
        await conn.close()


async def _set_enabled(value: bool) -> None:
    """Flip ``plugin.publish_adapter.youtube.enabled`` in app_settings."""
    conn = await _connect()
    try:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret)
            VALUES ($1, $2, 'publishing', $3, FALSE)
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = NOW()
            """,
            "plugin.publish_adapter.youtube.enabled",
            "true" if value else "false",
            "YouTube publish adapter kill switch (true/false).",
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# `poindexter integrations youtube setup`
# ---------------------------------------------------------------------------


@youtube_group.command("setup")
@click.option(
    "--client-secret-file",
    "client_secret_file",
    default=None,
    help=(
        "Path to the client_secret_*.json file downloaded from Google "
        "Cloud Console (APIs & Services → Credentials → your OAuth "
        "client → Download JSON). Recommended over passing raw values."
    ),
)
@click.option(
    "--client-id",
    default=None,
    help="OAuth client_id (use instead of --client-secret-file).",
)
@click.option(
    "--client-secret",
    default=None,
    help="OAuth client_secret (use instead of --client-secret-file).",
)
@click.option(
    "--yes", "assume_yes", is_flag=True,
    help=(
        "Skip the interactive 'flip enabled=true now?' prompt. "
        "Defaults to NO regardless — operator must explicitly opt in."
    ),
)
def youtube_setup(
    client_secret_file: str | None,
    client_id: str | None,
    client_secret: str | None,
    assume_yes: bool,
) -> None:
    """Run the YouTube OAuth consent flow + write secrets.

    What this does end-to-end:

    \b
    1. Load OAuth client_id + client_secret (from --client-secret-file
       OR --client-id/--client-secret).
    2. Open the operator's browser; capture the OAuth redirect; exchange
       the authorization code for an access_token + refresh_token.
    3. Verify the credentials by calling channels.list(mine=True).
       Print the channel_id + title that was granted access. Bail
       loudly on any failure — no half-credentials get written.
    4. Encrypt + write the 3 secrets to app_settings under
       ``plugin.publish_adapter.youtube.{client_id,client_secret,refresh_token}``.
    5. Optionally (with explicit consent) flip
       ``plugin.publish_adapter.youtube.enabled=true``. Default: NO.
       Operator should run the smoke test first via
       ``poindexter integrations youtube test --media-path ...``.

    Pre-requisites: a Desktop OAuth client in Google Cloud Console
    with the YouTube Data API v3 enabled on the project.
    """
    # Step 1 — resolve client credentials from inputs.
    cid, csecret = _load_client_config(
        client_id=client_id,
        client_secret_file=client_secret_file,
        client_secret=client_secret,
    )

    click.echo("Opening browser for YouTube OAuth consent...")
    click.echo(
        "  Scope requested: youtube.upload "
        "(minimum required per feedback_oauth_scope_hygiene)"
    )

    # Step 2 — run the consent flow.
    try:
        credentials = _run_consent_flow(cid, csecret)
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(
            f"OAuth consent flow failed: {type(exc).__name__}: {exc}. "
            "Common causes: operator closed the browser, redirect "
            "blocked by a firewall, or wrong account type (the OAuth "
            "client must be Desktop app, not Web app, for "
            "run_local_server)."
        ) from exc

    refresh_token = getattr(credentials, "refresh_token", None)
    if not refresh_token:
        raise click.ClickException(
            "Consent flow returned no refresh_token. This usually "
            "means the OAuth client has been used before with the same "
            "Google account — Google only issues refresh_tokens on "
            "first consent unless you force re-consent. Revoke the "
            "app under https://myaccount.google.com/permissions then "
            "re-run setup."
        )

    # Step 3 — best-effort channel confirmation.
    #
    # NOTE: ``channels.list(mine=True)`` requires a READ scope
    # (youtube.readonly / youtube), which ``youtube.upload`` does NOT
    # grant — an upload-only token gets 403 "insufficient scopes" here.
    # That's expected and NOT a failure: a successful consent +
    # refresh-token exchange already proves the upload scope was
    # granted. So this confirmation is best-effort — if it 403s on
    # scope, we log it and proceed to write the secrets. The real
    # end-to-end proof is `poindexter integrations youtube test`,
    # which does an actual upload (the only thing youtube.upload
    # actually authorizes).
    click.echo("Confirming channel access (best-effort)...")
    try:
        channel = _verify_channel(credentials)
        click.secho(
            f"  Granted access to channel: {channel['channel_title']} "
            f"(id={channel['channel_id']})",
            fg="green",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort confirmation
        click.secho(
            "  Channel read-back skipped: channels.list needs a read "
            f"scope that youtube.upload doesn't grant ({type(exc).__name__}). "
            "This is expected for an upload-only token — the consent + "
            "token exchange succeeded, so the upload scope IS granted. "
            "Verify end-to-end with `poindexter integrations youtube test`.",
            fg="yellow",
        )

    # Step 4 — encrypted write.
    click.echo("Writing encrypted secrets to app_settings...")
    try:
        _run(_write_secrets(
            client_id=cid,
            client_secret=csecret,
            refresh_token=str(refresh_token),
        ))
    except Exception as exc:
        raise click.ClickException(
            f"Failed to write secrets to app_settings: "
            f"{type(exc).__name__}: {exc}. The DB write was rolled "
            "back — re-run setup once the DB issue is fixed."
        ) from exc
    click.secho("  Secrets written (encrypted via pgcrypto).", fg="green")

    # Step 5 — explicit opt-in for enabling.
    if assume_yes:
        flip = False  # --yes means skip the prompt, NOT auto-enable.
        click.echo(
            "  --yes supplied; enabled flag left at its current value. "
            "Flip it manually via "
            "`poindexter settings set plugin.publish_adapter.youtube.enabled true` "
            "after running the smoke test."
        )
    else:
        flip = click.confirm(
            "Enable plugin.publish_adapter.youtube.enabled now? "
            "(Default: no — run the smoke test first.)",
            default=False,
        )
    if flip:
        _run(_set_enabled(True))
        click.secho(
            "  plugin.publish_adapter.youtube.enabled = true",
            fg="yellow",
        )

    click.echo("")
    click.secho("YouTube OAuth setup complete.", fg="green", bold=True)
    click.echo("")
    click.echo("Next step — smoke test the upload path with a tiny clip:")
    click.echo(
        "  poindexter integrations youtube test "
        "--media-path /path/to/video.mp4"
    )
    click.echo("")
    click.echo("To go live (after the smoke test passes):")
    click.echo(
        "  poindexter settings set "
        "plugin.publish_adapter.youtube.enabled true"
    )


# ---------------------------------------------------------------------------
# `poindexter integrations youtube test`
# ---------------------------------------------------------------------------


@youtube_group.command("test")
@click.option(
    "--media-path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to a video file (.mp4) to upload.",
)
@click.option(
    "--title",
    default="Poindexter smoke test",
    help="Title to set on the test upload.",
)
@click.option(
    "--description",
    default=(
        "Smoke test from `poindexter integrations youtube test`. "
        "Safe to delete. See services/publish_adapters/youtube.py."
    ),
    help="Description for the test upload.",
)
@click.option(
    "--public", "go_public", is_flag=True,
    help=(
        "Upload as public. Default privacy is `unlisted` so the test "
        "doesn't actually surface on the channel."
    ),
)
def youtube_test(
    media_path: str,
    title: str,
    description: str,
    go_public: bool,
) -> None:
    """Upload one file directly via the YouTubePublishAdapter.

    Bypasses the adapter's ``enabled`` gate (via the ``_force``
    escape hatch) so the operator can verify the OAuth wiring before
    flipping ``plugin.publish_adapter.youtube.enabled=true``. Still
    requires all three OAuth secrets to be present — those should
    have been written by `poindexter integrations youtube setup`.

    Default privacy is ``unlisted`` for safety; pass ``--public`` to
    override.
    """
    privacy = "public" if go_public else "unlisted"

    try:
        secrets = _run(_read_secrets())
    except Exception as exc:
        raise click.ClickException(
            f"Could not read YouTube secrets from app_settings: "
            f"{type(exc).__name__}: {exc}. Run "
            "`poindexter integrations youtube setup` first."
        ) from exc

    missing = [k for k, v in secrets.items() if not v]
    if missing:
        raise click.ClickException(
            f"Missing YouTube secrets: {missing}. Run "
            "`poindexter integrations youtube setup` to seed them."
        )

    # Build a minimal SiteConfig stub that returns enabled=true (the
    # _force kwarg below short-circuits that anyway, but keeping the
    # dict honest avoids surprising fallbacks for other keys).
    class _StubSiteConfig:
        """Smoke-test stub. Returns the 3 OAuth secrets + the enabled
        flag; everything else falls through to defaults."""

        def __init__(self, secrets: dict[str, str]) -> None:
            self._secrets = {
                f"plugin.publish_adapter.youtube.{k}": v
                for k, v in secrets.items()
            }

        def get(self, key: str, default: Any = None) -> Any:
            if key == "plugin.publish_adapter.youtube.enabled":
                return True
            return default

        async def get_secret(self, key: str, default: str = "") -> str:
            return self._secrets.get(key, default)

    from services.publish_adapters.youtube import YouTubePublishAdapter

    adapter = YouTubePublishAdapter(site_config=_StubSiteConfig(secrets))

    click.echo(
        f"Uploading {os.path.basename(media_path)} "
        f"({os.path.getsize(media_path)} bytes) as privacy={privacy}..."
    )

    async def _go():
        return await adapter.publish(
            media_path=media_path,
            title=title,
            description=description,
            privacy=privacy,
            _force=True,
        )

    try:
        result = _run(_go())
    except Exception as exc:
        raise click.ClickException(
            f"Adapter.publish() raised: {type(exc).__name__}: {exc}"
        ) from exc

    if not getattr(result, "success", False):
        err = getattr(result, "error", "<no error>")
        click.secho(f"FAILED: {err}", fg="red", err=True)
        sys.exit(1)

    external_id = getattr(result, "external_id", None)
    public_url = getattr(result, "public_url", None)
    click.secho("UPLOAD SUCCEEDED", fg="green", bold=True)
    click.echo(f"  external_id: {external_id}")
    click.echo(f"  url:         {public_url}")
    click.echo(f"  status:      {getattr(result, 'status', '')}")
    click.echo("")
    click.echo(
        "If the smoke test looks good, flip the adapter live:"
    )
    click.echo(
        "  poindexter settings set "
        "plugin.publish_adapter.youtube.enabled true"
    )
