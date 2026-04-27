"""poindexter premium — license activation and premium prompt management.

Integrates with Lemon Squeezy's License API to validate subscriptions
and gate access to premium prompt templates.

Lemon Squeezy License API docs:
    https://docs.lemonsqueezy.com/api/license-api
"""

from __future__ import annotations

import asyncio
import logging
import platform
import socket
from datetime import datetime, timezone

import click
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lemon Squeezy License API
# ---------------------------------------------------------------------------

LS_ACTIVATE_URL = "https://api.lemonsqueezy.com/v1/licenses/activate"
LS_VALIDATE_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"
LS_DEACTIVATE_URL = "https://api.lemonsqueezy.com/v1/licenses/deactivate"


def _instance_name() -> str:
    """Generate a human-readable instance name for this machine."""
    return f"{socket.gethostname()}-{platform.system().lower()}"


async def _ls_request(url: str, license_key: str, instance_id: str = "") -> dict:
    """Make a request to the Lemon Squeezy License API."""
    payload = {"license_key": license_key, "instance_name": _instance_name()}
    if instance_id:
        payload["instance_id"] = instance_id
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        return resp.json()


# ---------------------------------------------------------------------------
# DB helpers (direct asyncpg, no worker API dependency)
# ---------------------------------------------------------------------------

async def _get_pool():
    """Get a connection to the local DB (#198: bootstrap-resolved DSN)."""
    import asyncpg
    try:
        import sys as _sys
        from pathlib import Path as _Path
        # Walk up to the repo root (where brain/ lives) rather than a
        # fixed parents[N] — works from both src/ and installed packages.
        _here = _Path(__file__).resolve()
        for _p in _here.parents:
            if (_p / "brain" / "bootstrap.py").is_file():
                if str(_p) not in _sys.path:
                    _sys.path.insert(0, str(_p))
                break
        from brain.bootstrap import resolve_database_url
    except Exception as e:  # pragma: no cover — shouldn't happen
        raise click.ClickException(f"bootstrap module import failed: {e}") from e

    dsn = resolve_database_url()
    if not dsn:
        raise click.ClickException(
            "No database URL configured. Run `poindexter setup` to create "
            "~/.poindexter/bootstrap.toml, or set DATABASE_URL in the "
            "environment."
        )
    return await asyncpg.connect(dsn)


async def _get_setting(conn, key: str, default: str = "") -> str:
    row = await conn.fetchrow("SELECT value FROM app_settings WHERE key = $1", key)
    return str(row["value"]) if row and row["value"] is not None else default


async def _set_setting(conn, key: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        key, value,
    )


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

async def _activate(license_key: str) -> None:
    """Activate a premium license key."""
    click.echo(f"Activating license on {_instance_name()}...")

    # 1. Call Lemon Squeezy activate API
    result = await _ls_request(LS_ACTIVATE_URL, license_key)

    if result.get("error"):
        click.echo(f"Activation failed: {result.get('error')}", err=True)
        return

    if not result.get("valid"):
        click.echo(f"License key is not valid: {result.get('error', 'unknown reason')}", err=True)
        return

    # 2. Extract info
    instance = result.get("instance", {})
    instance_id = instance.get("id", "")
    meta = result.get("meta", {})
    customer_email = meta.get("customer_email", "")
    customer_name = meta.get("customer_name", "")
    lk = result.get("license_key", {})
    activation_limit = lk.get("activation_limit", 0)
    activation_usage = lk.get("activation_usage", 0)

    # 3. Store in app_settings
    conn = await _get_pool()
    try:
        await _set_setting(conn, "premium_license_key", license_key)
        await _set_setting(conn, "premium_instance_id", str(instance_id))
        await _set_setting(conn, "premium_active", "true")
        await _set_setting(conn, "premium_email", customer_email)
        await _set_setting(conn, "premium_customer_name", customer_name)
        await _set_setting(conn, "premium_validated_at", datetime.now(timezone.utc).isoformat())
    finally:
        await conn.close()

    click.echo()
    click.echo("Premium activated!")
    click.echo(f"  Email:       {customer_email}")
    if customer_name:
        click.echo(f"  Name:        {customer_name}")
    click.echo(f"  Instance:    {_instance_name()} ({instance_id})")
    click.echo(f"  Activations: {activation_usage}/{activation_limit}")
    click.echo()
    click.echo("Premium prompts are now available to the pipeline.")
    click.echo("Run `poindexter premium status` to check your license anytime.")


async def _deactivate() -> None:
    """Deactivate the current premium license."""
    conn = await _get_pool()
    try:
        license_key = await _get_setting(conn, "premium_license_key")
        instance_id = await _get_setting(conn, "premium_instance_id")

        if not license_key:
            click.echo("No premium license is currently active.", err=True)
            return

        click.echo("Deactivating license...")

        result = await _ls_request(LS_DEACTIVATE_URL, license_key, instance_id)

        if result.get("error") and "not found" not in str(result.get("error", "")).lower():
            click.echo(f"Deactivation warning: {result.get('error')}", err=True)

        # Clear local state regardless
        await _set_setting(conn, "premium_active", "false")
        await _set_setting(conn, "premium_instance_id", "")

        click.echo("Premium deactivated. Activation slot freed.")
        click.echo("Pipeline will use free default prompts.")
    finally:
        await conn.close()


async def _status() -> None:
    """Show premium license status."""
    conn = await _get_pool()
    try:
        license_key = await _get_setting(conn, "premium_license_key")
        email = await _get_setting(conn, "premium_email")
        instance_id = await _get_setting(conn, "premium_instance_id")
        validated_at = await _get_setting(conn, "premium_validated_at")

        if not license_key:
            click.echo("No premium license configured.")
            click.echo()
            click.echo("To activate: poindexter premium activate YOUR-LICENSE-KEY")
            click.echo("Get a key at: https://gladlabs.lemonsqueezy.com")
            return

        # Revalidate against Lemon Squeezy
        click.echo("Checking license status...")
        result = await _ls_request(LS_VALIDATE_URL, license_key)

        is_valid = result.get("valid", False)
        lk = result.get("license_key", {})
        ls_status = lk.get("status", "unknown")
        activation_limit = lk.get("activation_limit", 0)
        activation_usage = lk.get("activation_usage", 0)

        # Update local state
        if is_valid:
            await _set_setting(conn, "premium_active", "true")
            await _set_setting(conn, "premium_validated_at", datetime.now(timezone.utc).isoformat())
        else:
            await _set_setting(conn, "premium_active", "false")

        # Display
        status_icon = "ACTIVE" if is_valid else "INACTIVE"
        click.echo()
        click.echo(f"  Status:      {status_icon} ({ls_status})")
        click.echo(f"  Email:       {email}")
        masked_key = license_key[:8] + "..." + license_key[-4:] if len(license_key) > 12 else license_key
        click.echo(f"  License:     {masked_key}")
        click.echo(f"  Instance:    {_instance_name()} ({instance_id})")
        click.echo(f"  Activations: {activation_usage}/{activation_limit}")
        click.echo(f"  Last check:  {validated_at or 'never'}")
        click.echo()
        if is_valid:
            click.echo("Premium prompts are active.")
        else:
            click.echo("License is not valid. Pipeline using free defaults.")
            click.echo("Renew at: https://gladlabs.lemonsqueezy.com")
    finally:
        await conn.close()


async def _validate_silent() -> bool:
    """Silent revalidation for use by the idle worker / brain daemon.

    Returns True if premium is active, False otherwise.
    Updates app_settings accordingly.
    """
    conn = await _get_pool()
    try:
        license_key = await _get_setting(conn, "premium_license_key")
        if not license_key:
            return False

        result = await _ls_request(LS_VALIDATE_URL, license_key)
        is_valid = result.get("valid", False)

        await _set_setting(conn, "premium_active", "true" if is_valid else "false")
        await _set_setting(conn, "premium_validated_at", datetime.now(timezone.utc).isoformat())

        return is_valid
    except Exception as e:
        # Network blip / Lemon Squeezy API outage / DB write failure —
        # log so a real outage doesn't silently downgrade the operator
        # to "free" mode while their license is actually paid.
        logger.warning(
            "[premium] silent license revalidation failed "
            "(returning False, premium gates will close): %s", e,
        )
        return False
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Click command group
# ---------------------------------------------------------------------------

@click.group("premium", help="Manage your Poindexter Premium subscription.")
def premium_group() -> None:
    pass


@premium_group.command("activate")
@click.argument("license_key")
def activate(license_key: str) -> None:
    """Activate a premium license key from Lemon Squeezy."""
    asyncio.run(_activate(license_key))


@premium_group.command("deactivate")
def deactivate() -> None:
    """Deactivate premium and free your activation slot."""
    asyncio.run(_deactivate())


@premium_group.command("status")
def status() -> None:
    """Show current premium license status."""
    asyncio.run(_status())
