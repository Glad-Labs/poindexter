"""`poindexter taps` — manage the external_taps table (GH-103).

Thin adapter over :mod:`services.declarative_config_service` (#1522, epic
#1340): list / show / enable / disable read & write config through the
service; create / delete go through ``services.tap_admin``, which validates
the type and niche first (poindexter#1128); `run` delegates to the tap_runner. No raw SQL or asyncpg connection
lives here — the shared ``_dataplane`` helper owns the pool lifecycle.
"""

from __future__ import annotations

import json
import sys

import click

from poindexter.cli._dataplane import dump_row, fmt_age, render_table, run_service
from poindexter.services import declarative_config_service as dcs

_SURFACE = "taps"

_COLUMNS = [
    ("name", "NAME", 18, None),
    ("handler_name", "HANDLER", 22, None),
    ("tap_type", "TAP TYPE", 12, None),
    ("schedule", "SCHEDULE", 17, None),
    ("enabled", "STATE", 9, lambda v: "enabled" if v else "disabled"),
    ("last_run_at", "LAST", 12, fmt_age),
    ("total_runs", "RUNS", 5, None),
    ("total_records", "RECORDS", 8, None),
]


def _state_filter(state: str) -> dict | None:
    if state == "enabled":
        return {"enabled": True}
    if state == "disabled":
        return {"enabled": False}
    return None


@click.group(
    name="taps",
    help="Manage external data taps (Singer + built-in scrapers).",
)
def taps_group() -> None:
    pass


@taps_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def taps_list(state: str) -> None:
    """List every tap with current status."""
    try:
        rows = run_service(lambda p: dcs.list_rows(p, _SURFACE, filters=_state_filter(state)))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    render_table(rows, _COLUMNS, empty="(no external taps)")


@taps_group.command("show")
@click.argument("name")
def taps_show(name: str) -> None:
    """Show full details of one tap row."""
    try:
        row = run_service(lambda p: dcs.get_row(p, _SURFACE, name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not dump_row(row, missing=f"(no tap named {name!r})"):
        sys.exit(1)


@taps_group.command("enable")
@click.argument("name")
def taps_enable(name: str) -> None:
    _set_enabled(name, True)


@taps_group.command("disable")
@click.argument("name")
def taps_disable(name: str) -> None:
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
        click.echo(f"(no tap named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@taps_group.command("set-config")
@click.argument("name")
@click.argument("config_json")
@click.option(
    "--replace", is_flag=True, default=False,
    help="Replace the whole config object instead of merging keys into it.",
)
def taps_set_config(name: str, config_json: str, replace: bool) -> None:
    """Set CONFIG_JSON (a JSON object) on tap NAME.

    Merges into the existing config by default, so unrelated keys
    (weight_pct, secret_fields, …) survive; --replace overwrites wholesale.

    \b
    Give web_search taps something to search for:
      poindexter taps set-config glad-labs_web_search \\
        '{"categories": ["technology", "engineering", "insights"]}'
    """
    try:
        parsed = json.loads(config_json)
    except json.JSONDecodeError as e:
        click.echo(f"Error: CONFIG_JSON is not valid JSON ({e})", err=True)
        sys.exit(1)
    if not isinstance(parsed, dict):
        click.echo(
            f"Error: CONFIG_JSON must be a JSON object, got {type(parsed).__name__}",
            err=True,
        )
        sys.exit(1)

    async def _impl(pool):
        row = await dcs.get_row(pool, _SURFACE, name)
        if row is None:
            return None
        current = row.get("config") or {}
        if isinstance(current, str):
            # declarative_config_service hands jsonb back as a dict, but a row
            # written by an older path can still carry a JSON string.
            current = json.loads(current)
        merged = parsed if replace else {**current, **parsed}
        await dcs.upsert_row(pool, _SURFACE, {**row, "config": merged})
        return merged

    try:
        merged = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if merged is None:
        click.echo(f"(no tap named {name!r})", err=True)
        sys.exit(1)
    click.secho(f"{name}: config updated", fg="green")
    click.echo(json.dumps(merged, indent=2, default=str))


@taps_group.command("create")
@click.argument("name")
@click.option("--type", "tap_type", required=True,
              help="Topic source to run (rss, hackernews, web_search, …), or the handler's tap type.")
@click.option("--niche", "niche_slug", default=None, help="Niche slug the topics land in (required for topic sources).")
@click.option("--config", "config_json", default="{}", show_default=True,
              help='Tap config as a JSON object, e.g. \'{"feed_url": "https://example.com/feed"}\'.')
@click.option("--schedule", default=None, help='Cadence, e.g. "every 6 hours".')
@click.option("--handler", default="builtin_topic_source", show_default=True,
              help="Tap handler. The default runs one topic source per row.")
@click.option("--disabled", is_flag=True, default=False, help="Create it disabled.")
def taps_create(
    name: str, tap_type: str, niche_slug: str | None, config_json: str,
    schedule: str | None, handler: str, disabled: bool,
) -> None:
    """Create tap NAME: add a topic source for a niche without SQL.

    The type must be a registered topic source and the niche a known one,
    checked before anything is written. An existing NAME is refused; change
    one with set-config / enable / disable.

    \b
    Add an RSS feed to a niche:
      poindexter taps create glad-labs_rss_example --type rss --niche glad-labs \\
        --config '{"feed_url": "https://example.com/feed"}' --schedule "every 6 hours"
    """
    from poindexter.services.tap_admin import TapAdminError, create_tap

    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as e:
        click.echo(f"Error: --config is not valid JSON ({e})", err=True)
        sys.exit(1)

    try:
        row = run_service(lambda p: create_tap(
            p, name=name, tap_type=tap_type, niche_slug=niche_slug, handler=handler,
            config=config, schedule=schedule, enabled=not disabled,
        ))
    except TapAdminError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    state = "enabled" if row.get("enabled") else "disabled"
    click.secho(f"{name}: created ({state})", fg="green")
    dump_row(row, missing="")


@taps_group.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, default=False, help="Confirm the delete.")
def taps_delete(name: str, yes: bool) -> None:
    """Delete tap NAME. Its run history goes with it; `disable` keeps it."""
    from poindexter.services.tap_admin import delete_tap

    if not yes:
        click.echo(
            f"Refusing to delete {name!r} without --yes. `poindexter taps disable {name}` "
            "stops it and keeps its history.", err=True,
        )
        sys.exit(1)
    try:
        deleted = run_service(lambda p: delete_tap(p, name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not deleted:
        click.echo(f"(no tap named {name!r})", err=True)
        sys.exit(1)
    click.secho(f"{name}: deleted", fg="yellow")


@taps_group.command("run")
@click.argument("name", required=False)
def taps_run(name: str | None) -> None:
    """Invoke the tap runner immediately.

    Without NAME: runs every enabled tap.
    With NAME: runs just that tap (requires enabled=TRUE).
    """
    async def _impl(pool):
        from poindexter.cli._bootstrap import cli_site_config
        from poindexter.services.integrations import tap_runner
        from poindexter.services.integrations.handlers import load_all

        load_all()  # idempotent — registry refuses duplicate registrations
        # SiteConfig DI (#272): build a run-bound instance so tap.singer_subprocess
        # can resolve config.secret_fields via site_config.get_secret() (#857/#2502).
        # Without this, any tap with secret_fields set (gsc_main, ga4_main) fails
        # with "secret_fields is set but no site_config was provided to resolve it".
        site_cfg = cli_site_config(pool)
        try:
            await site_cfg.load(pool)
        except Exception:  # silent-ok: taps without secret_fields don't need this to succeed
            pass
        return await tap_runner.run_all(
            pool, site_config=site_cfg, only_names=[name] if name else None,
        )

    try:
        summary = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(json.dumps(summary.to_dict(), indent=2, default=str))
