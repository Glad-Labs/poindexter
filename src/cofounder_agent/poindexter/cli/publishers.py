"""``poindexter publishers`` — operate on the declarative publishing_adapters table.

Sibling of ``poindexter webhooks`` / ``taps`` / ``retention``. v1 ships
list / show / enable / disable / set-secret / fire.

Thin adapter over :mod:`services.declarative_config_service` (#1522, epic
#1340): config reads/writes go through the service. ``set-secret`` hands a
connection to :mod:`plugins.secrets` (encrypted write); ``fire`` loads the row
via the service then dispatches through the integrations registry. No raw
config SQL or asyncpg connection lives here.
"""

from __future__ import annotations

import sys

import click

from poindexter.cli._dataplane import dump_row, fmt_age, render_table, run_service
from services import declarative_config_service as dcs

_SURFACE = "publishers"

_COLUMNS = [
    ("name", "NAME", 22, None),
    ("platform", "PLATFORM", 12, None),
    ("handler_name", "HANDLER", 14, None),
    ("enabled", "STATE", 9, lambda v: "enabled" if v else "disabled"),
    ("last_run_at", "LAST RUN", 12, fmt_age),
    ("total_runs", "RUNS", 6, None),
    ("total_failures", "FAIL", 5, None),
]


def _state_filter(state: str) -> dict | None:
    if state == "enabled":
        return {"enabled": True}
    if state == "disabled":
        return {"enabled": False}
    return None


@click.group(
    name="publishers",
    help="Manage declarative publishing_adapters rows (poindexter#112).",
)
def publishers_group() -> None:
    pass


@publishers_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def publishers_list(state: str) -> None:
    """List all publisher rows with current status."""
    try:
        rows = run_service(lambda p: dcs.list_rows(p, _SURFACE, filters=_state_filter(state)))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    rows.sort(key=lambda r: (r.get("platform", ""), r.get("name", "")))
    render_table(rows, _COLUMNS, empty="(no publishing adapters)")


@publishers_group.command("show")
@click.argument("name")
def publishers_show(name: str) -> None:
    """Show full details of one publisher row including config + metadata."""
    try:
        row = run_service(lambda p: dcs.get_row(p, _SURFACE, name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not dump_row(row, missing=f"(no publishing adapter named {name!r})"):
        sys.exit(1)


@publishers_group.command("enable")
@click.argument("name")
def publishers_enable(name: str) -> None:
    """Flip enabled=TRUE for one publisher."""
    _set_enabled(name, True)


@publishers_group.command("disable")
@click.argument("name")
def publishers_disable(name: str) -> None:
    """Flip enabled=FALSE for one publisher."""
    _set_enabled(name, False)


def _set_enabled(name: str, enabled: bool) -> None:
    async def _impl(pool):
        row = await dcs.get_row(pool, _SURFACE, name)
        if row is None:
            return False
        await dcs.upsert_row(pool, _SURFACE, {**row, "enabled": enabled})
        return True

    try:
        ok = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not ok:
        click.echo(f"(no row named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@publishers_group.command("set-secret")
@click.argument("name")
@click.argument("key")
@click.option(
    "--value", default=None,
    help="Provide the secret inline. Omit to be prompted (recommended).",
)
def publishers_set_secret(name: str, key: str, value: str | None) -> None:
    """Store an encrypted credential under app_settings for the publisher.

    Secrets live in ``app_settings`` (``is_secret=true``), NOT in
    ``publishing_adapters.config``. The row's ``credentials_ref`` column is
    the prefix for the keys this publisher uses; ``key`` must start with it so
    an operator can't cross-write another publisher's namespace.
    """
    if value is None:
        value = click.prompt(
            f"Secret value for {key}", hide_input=True, confirmation_prompt=True,
        )
    assert value is not None

    async def _impl(pool):
        row = await dcs.get_row(pool, _SURFACE, name)
        if row is None:
            raise RuntimeError(f"no publisher named {name!r}")
        ref = row.get("credentials_ref")
        if ref and not key.startswith(ref):
            raise RuntimeError(
                f"key {key!r} does not match publisher's credentials_ref "
                f"prefix {ref!r}; refusing to cross-write namespaces"
            )
        # plugins.secrets owns the encrypted write; we just hand it a conn.
        from plugins.secrets import ensure_pgcrypto, set_secret

        async with pool.acquire() as conn:
            await ensure_pgcrypto(conn)
            await set_secret(
                conn, key, value,
                description=f"Credential for publisher {name!r} (poindexter#112)",
            )
        return key

    try:
        stored = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(
        f"Secret stored encrypted at app_settings.{stored} for publisher {name!r}",
        fg="green",
    )


@publishers_group.command("fire")
@click.argument("name")
@click.option("--text", default="poindexter test post — please ignore",
              help="Body text for the test post.")
@click.option("--url", default="https://gladlabs.io",
              help="URL to attach to the post.")
def publishers_fire(name: str, text: str, url: str) -> None:
    """Trigger one publisher manually — the 'did I configure this right?' smoke test.

    Loads the row via the service, ensures handlers are registered, dispatches
    through the registry with a run-bound SiteConfig, prints the return dict.
    """
    async def _impl(pool):
        from services.integrations import registry
        from services.integrations.handlers import load_all
        from services.publishing_adapters_db import PublishingAdapterRow
        from services.site_config import SiteConfig

        load_all()  # idempotent — registry refuses duplicate registrations
        # SiteConfig DI (#272): build a run-bound instance from the pool so the
        # publishing dispatcher gets a real, DB-loaded config (adapters
        # short-circuit when site_config is missing).
        site_cfg = SiteConfig(pool=pool)
        try:
            await site_cfg.load(pool)
        except Exception:  # noqa: silent-ok — keep the smoke test usable on partial bootstrap
            pass

        row = await dcs.get_row(pool, _SURFACE, name)
        if row is None:
            raise RuntimeError(f"no publisher named {name!r}")
        pub = PublishingAdapterRow(
            id=row["id"], name=row["name"], platform=row["platform"],
            handler_name=row["handler_name"], credentials_ref=row.get("credentials_ref"),
            enabled=bool(row["enabled"]), config=dict(row.get("config") or {}),
            metadata=dict(row.get("metadata") or {}),
        )
        return await registry.dispatch(
            "publishing", pub.handler_name,
            {"text": text, "url": url},
            site_config=site_cfg,
            row=pub.as_dict(),
            pool=None,
        )

    try:
        result = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    fg = "green" if result.get("success") else "yellow"
    click.secho(f"{name}: {result!r}", fg=fg)
