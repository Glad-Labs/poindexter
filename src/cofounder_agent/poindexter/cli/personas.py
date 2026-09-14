"""``poindexter personas`` — presenter personas, a face bound to a voice.

A persona is the key family ``persona.<slug>.<field>`` in ``app_settings``
(``services/persona_service.py``). This group is a thin Click wrapper: it
opens a pool, loads a ``SiteConfig``, and calls the service — validation,
resolution and the portrait render live there, not here.

Examples
--------
    poindexter personas list
    poindexter personas show presenter
    poindexter personas create host --display-name Host --voice-id bm_george --style stylized
    poindexter personas set presenter voice_id bf_emma
    poindexter personas portrait presenter --seed 21      # render + upload → portrait_url
    poindexter personas set-portrait presenter face.png   # upload an existing still
"""

from __future__ import annotations

import json
from typing import Any

import click

from poindexter.cli._dataplane import run_service
from poindexter.services import persona_service as ps


async def _site_config(pool: Any) -> Any:
    from poindexter.services.site_config import SiteConfig

    sc = SiteConfig()
    await sc.load(pool)
    return sc


def _settings_service(pool: Any) -> Any:
    from poindexter.services.settings_service import SettingsService

    return SettingsService(pool)


def _fail(message: str) -> None:
    raise click.ClickException(message)


@click.group(
    "personas",
    help="Presenter personas — a face bound to a voice (persona.<slug>.* settings).",
)
def personas_group() -> None:
    """Presenter persona management."""


@personas_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def personas_list(as_json: bool) -> None:
    """List every persona that has at least one key."""

    async def _run(pool: Any) -> list[dict[str, Any]]:
        sc = await _site_config(pool)
        default = str(sc.get(ps.DEFAULT_PERSONA_SETTING, "") or "")
        return [{**p.to_dict(), "default": p.slug == default} for p in ps.list_personas(sc)]

    rows = run_service(_run)
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("(no personas)")
        return
    for r in rows:
        flags = " ".join(
            f for f in (
                "default" if r["default"] else "",
                "" if r["enabled"] else "disabled",
                "portrait" if r["portrait_url"] else "no-portrait",
            ) if f
        )
        voice = r["voice_id"] or "(inherits podcast_tts_voice)"
        click.echo(f"{r['slug']:<20} {r['display_name']:<20} {r['voice_provider']}/{voice:<28} {r['style_policy']:<9} {flags}")


@personas_group.command("show")
@click.argument("slug")
def personas_show(slug: str) -> None:
    """Show one persona as JSON."""

    async def _run(pool: Any) -> dict[str, Any] | None:
        sc = await _site_config(pool)
        p = ps.get_persona(sc, slug)
        return p.to_dict() if p else None

    row = run_service(_run)
    if row is None:
        _fail(f"no persona {slug!r} (no persona.{slug}.* keys)")
    click.echo(json.dumps(row, indent=2))


@personas_group.command("create")
@click.argument("slug")
@click.option("--display-name", required=True)
@click.option("--description", default="", help="Who the presenter is; seeds the portrait prompt.")
@click.option("--voice-provider", default="kokoro", show_default=True)
@click.option("--voice-id", default="", help="Voice id; empty inherits podcast_tts_voice.")
@click.option("--style", "style_policy", default="photoreal", show_default=True, help="photoreal | stylized")
@click.option("--prompt", "portrait_prompt", default="", help="Portrait prompt; empty derives one from the description.")
def personas_create(slug: str, **fields: str) -> None:
    """Create (or overwrite) a persona's fields."""
    payload = {
        "display_name": fields["display_name"],
        "description": fields["description"],
        "voice_provider": fields["voice_provider"],
        "voice_id": fields["voice_id"],
        "style_policy": fields["style_policy"],
        "portrait_prompt": fields["portrait_prompt"],
        "enabled": "true",
    }
    try:
        stored = run_service(lambda pool: ps.upsert_persona(_settings_service(pool), slug, payload))
    except ps.PersonaError as e:
        _fail(str(e))
    click.echo(f"persona {slug}: {len(stored)} fields written")


@personas_group.command("set")
@click.argument("slug")
@click.argument("field")
@click.argument("value")
def personas_set(slug: str, field: str, value: str) -> None:
    """Set one field (validated): voice_id, style_policy, enabled, ..."""
    try:
        stored = run_service(lambda pool: ps.upsert_persona(_settings_service(pool), slug, {field: value}))
    except ps.PersonaError as e:
        _fail(str(e))
    click.echo(f"{ps.persona_key(slug, field)} = {stored[field]!r}")


@personas_group.command("enable")
@click.argument("slug")
def personas_enable(slug: str) -> None:
    """Enable a persona."""
    run_service(lambda pool: ps.upsert_persona(_settings_service(pool), slug, {"enabled": "true"}))
    click.echo(f"persona {slug}: enabled")


@personas_group.command("disable")
@click.argument("slug")
def personas_disable(slug: str) -> None:
    """Disable a persona (resolution falls back to no persona, never another)."""
    run_service(lambda pool: ps.upsert_persona(_settings_service(pool), slug, {"enabled": "false"}))
    click.echo(f"persona {slug}: disabled")


@personas_group.command("portrait")
@click.argument("slug")
@click.option("--seed", type=int, default=None, help="Seed recorded on the persona for regeneration.")
@click.option("--prompt", default=None, help="Override the portrait prompt for this render.")
@click.option("--out-dir", default=None, help="Keep the local render here (default: a temp dir).")
def personas_portrait(slug: str, seed: int | None, prompt: str | None, out_dir: str | None) -> None:
    """Render a reference portrait through the image service, upload it, and
    store portrait_url / portrait_prompt / portrait_seed on the persona."""

    async def _run(pool: Any) -> tuple[str, str, str]:
        sc = await _site_config(pool)
        persona = ps.get_persona(sc, slug)
        if persona is None:
            raise ps.PersonaError(f"no persona {slug!r} — create it first")
        url, used_prompt, local = await ps.render_portrait(
            persona, site_config=sc, seed=seed, prompt=prompt, output_dir=out_dir,
        )
        fields: dict[str, Any] = {"portrait_url": url, "portrait_prompt": used_prompt}
        if seed is not None:
            fields["portrait_seed"] = str(seed)
        await ps.upsert_persona(_settings_service(pool), slug, fields)
        return url, used_prompt, local

    try:
        url, used_prompt, local = run_service(_run)
    except ps.PersonaError as e:
        _fail(str(e))
    click.echo(f"portrait_url = {url}\nprompt       = {used_prompt}\nlocal        = {local}")


@personas_group.command("set-portrait")
@click.argument("slug")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
def personas_set_portrait(slug: str, file: str) -> None:
    """Upload an existing still as the persona's portrait."""

    async def _run(pool: Any) -> str:
        sc = await _site_config(pool)
        persona = ps.get_persona(sc, slug)
        if persona is None:
            raise ps.PersonaError(f"no persona {slug!r} — create it first")
        url = await ps.upload_portrait(persona, file, site_config=sc)
        await ps.upsert_persona(_settings_service(pool), slug, {"portrait_url": url})
        return url

    try:
        url = run_service(_run)
    except ps.PersonaError as e:
        _fail(str(e))
    click.echo(f"portrait_url = {url}")
