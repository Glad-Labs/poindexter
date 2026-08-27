"""``poindexter game`` — claim the GPU for the operator for a bounded window.

Per ``feedback_cli_first`` the CLI is the primary operator surface; the MCP
tool (``set_game_mode``) is the phone-side mirror and shares this module's
service functions. Per the transport-adapter contract this file holds NO
policy: state, TTL and the parked-service list live in
``services/game_mode.py``.

The one thing that *is* adapter-local is the docker call. The worker container
has no docker socket, so container stopping cannot live in the service layer
and work everywhere. The CLI runs on the host, so it stops the parked
containers immediately for instant effect; the brain's ``compose_drift_probe``
independently keeps them down for the whole window. Triggering from MCP with
no docker access is therefore still correct — it pauses GPU admission at once
and the containers settle on the next brain cycle.

Subcommands:

- ``on [--hours N]``  — park GPU services, pause pipeline GPU admission.
- ``off``             — end early; compose_drift restores the services.
- ``status``          — active?, expiry, remaining, live container state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from typing import Any

import click

from poindexter.cli._bootstrap import close_cli_pool, open_cli_pool

logger = logging.getLogger(__name__)

_DOCKER_TIMEOUT_S = 30


async def _open_pool() -> Any:

    from ._bootstrap import resolve_dsn

    dsn = resolve_dsn()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    return await open_cli_pool(dsn, timeout=8)


async def _make_site_config(pool):
    from services.site_config import SiteConfig

    site_config = SiteConfig(pool=pool)
    await site_config.load(pool)
    return site_config


def _docker(*args: str) -> tuple[bool, str]:
    """Run a docker command. Returns (ok, message) — never raises."""
    if not shutil.which("docker"):
        return False, "docker not on PATH"
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=_DOCKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"docker {' '.join(args)} timed out"
    except OSError as exc:
        return False, f"docker {' '.join(args)} failed: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[:200]
    return True, (proc.stdout or "").strip()


def _running_containers(names: tuple[str, ...]) -> set[str]:
    ok, out = _docker("ps", "--format", "{{.Names}}")
    if not ok:
        return set()
    live = {ln.strip() for ln in out.splitlines() if ln.strip()}
    return {n for n in names if n in live}


def _stop_containers(names: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Stop the named containers. Returns (stopped, failed).

    Only stops what is actually running so a already-parked container does not
    surface as a spurious failure.
    """
    stopped: list[str] = []
    failed: list[str] = []
    running = _running_containers(names)
    for name in names:
        if name not in running:
            continue
        ok, msg = _docker("stop", name)
        if ok:
            stopped.append(name)
        else:
            failed.append(f"{name}: {msg}")
    return stopped, failed


# Ollama listens on the host; prod stores the CONTAINER-facing URL.
_CONTAINER_ONLY_HOSTS = ("host.docker.internal", "ollama", "poindexter-ollama")


def _host_reachable_url(url: str) -> str:
    """Rewrite a container-internal Ollama URL to its host equivalent.

    ``app_settings.ollama_base_url`` is ``http://host.docker.internal:11434``
    because the worker is containerized. That name does not resolve in a
    host-side process, so this CLI must translate it or every unload silently
    no-ops (observed on the first live ``game on``: two ConnectErrors, then a
    cheerful "nothing resident" while 21 GB stayed pinned). Ollama answers on
    localhost at the same port, so only the host part changes. See the
    host-CLI URL gotcha in the ops docs.
    """
    if not url:
        return url
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.hostname not in _CONTAINER_ONLY_HOSTS:
        return url
    netloc = "localhost" if parts.port is None else f"localhost:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def _ollama_reachable(base_url: str) -> bool:
    """Cheap liveness probe so 'nothing resident' can't mask 'never asked'."""
    import httpx

    url = (base_url or "http://localhost:11434").rstrip("/") + "/api/version"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            return (await client.get(url)).status_code == 200
    except Exception:  # noqa: BLE001
        # silent-ok: this probe's entire job is to answer "can we reach it?" —
        # a transport failure IS the answer (False), not an error to escalate.
        # The caller reports it loudly as "UNREACHABLE (VRAM NOT freed)".
        return False


async def _evict_ollama(site_config) -> str:
    """Evict resident Ollama models across EVERY configured host.

    There are two instances on this box — the all-GPU primary and the
    vision-pinned second (see the dual-Ollama note in the ops docs) — and the
    vision one is exactly the process that parks ~21 GB on the second card.
    Unloading only the primary would leave the bigger allocation resident and
    make game mode look like it did nothing.

    ``confirm=True`` re-polls ``/api/ps`` until the model is actually gone
    rather than firing ``keep_alive:0`` and hoping; the operator is about to
    launch a game against this VRAM, so an unconfirmed unload is worthless.
    """
    from services import game_mode

    if not site_config.get_bool(game_mode.EVICT_OLLAMA_KEY, True):
        return "skipped (game_mode_evict_ollama=false)"

    from services.llm_providers.ollama_unload import (
        ollama_base_urls,
        unload_loaded_ollama_models,
    )

    try:
        hosts = [_host_reachable_url(u) for u in (ollama_base_urls(site_config) or [""])]
    except Exception:  # noqa: BLE001 - fall back to the primary-only path
        hosts = [""]

    freed: list[str] = []
    unreachable: list[str] = []
    errors: list[str] = []
    for host in hosts:
        # Probe FIRST. ``unload_loaded_ollama_models`` swallows a transport
        # failure (logs a warning, returns []), which is indistinguishable from
        # "no models were loaded" — and reporting "nothing resident" when we
        # could not even ask is a lie the operator acts on. Ask explicitly.
        if not await _ollama_reachable(host):
            unreachable.append(host or "primary")
            continue
        try:
            freed.extend(
                await unload_loaded_ollama_models(
                    site_config=site_config,
                    base_url_override=host,
                    confirm=True,
                )
            )
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            errors.append(f"{host or 'primary'}: {type(exc).__name__}")

    parts: list[str] = []
    if freed:
        parts.append(f"evicted {len(freed)} model(s)")
    elif not unreachable and not errors:
        parts.append("nothing resident")
    if unreachable:
        # Distinct from "nothing resident" on purpose — VRAM is still held.
        parts.append(f"UNREACHABLE: {', '.join(unreachable)} (VRAM NOT freed)")
    if errors:
        parts.append(f"FAILED on {'; '.join(errors)}")
    return " — ".join(parts)


@click.group("game", help="Claim the GPU for gaming for a bounded window.")
def game_group() -> None:
    pass


@game_group.command("on")
@click.option("--hours", type=float, default=None, help="Window length (default from app_settings).")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def game_on(hours: float | None, as_json: bool) -> None:
    """Park GPU services and pause pipeline GPU admission."""

    async def _run() -> dict[str, Any]:
        from services import game_mode

        pool = await _open_pool()
        try:
            site_config = await _make_site_config(pool)
            status = await game_mode.enable(pool, site_config, hours=hours)
            names = game_mode.container_names(site_config)
            stopped, failed = _stop_containers(names)
            evicted = await _evict_ollama(site_config)
            payload = status.as_dict()
            payload.update(
                {"stopped": stopped, "failed": failed, "ollama": evicted}
            )
            return payload
        finally:
            await close_cli_pool(pool)

    try:
        result = asyncio.run(_run())
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    click.echo(f"game mode ON until {result['until']}")
    click.echo(f"  parked   {', '.join(result['stopped']) or '(none were running)'}")
    click.echo(f"  ollama   {result['ollama']}")
    click.echo("  paused   pipeline GPU admission + new task claims")
    if result["failed"]:
        click.echo(f"  FAILED   {'; '.join(result['failed'])}")
        click.echo("  (brain will still hold these down; stop them by hand if needed)")


@game_group.command("off")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def game_off(as_json: bool) -> None:
    """End game mode early. Services come back on the next brain cycle."""

    async def _run() -> dict[str, Any]:
        from services import game_mode

        pool = await _open_pool()
        try:
            site_config = await _make_site_config(pool)
            status = await game_mode.disable(pool, site_config)
            return status.as_dict()
        finally:
            await close_cli_pool(pool)

    result = asyncio.run(_run())
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    click.echo("game mode OFF")
    click.echo("  parked services restart on the next compose-drift cycle (<=5 min)")


@game_group.command("status")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def game_status(as_json: bool) -> None:
    """Show whether game mode is active, and the live container state."""

    async def _run() -> dict[str, Any]:
        from services import game_mode

        pool = await _open_pool()
        try:
            site_config = await _make_site_config(pool)
            status = await game_mode.status(pool, site_config)
            names = game_mode.container_names(site_config)
            payload = status.as_dict()
            # Intent vs reality: a service listed as parked but still running
            # means the stop did not land (no docker socket, or a manual
            # restart). Showing both stops "game mode is on" from implying
            # "the GPU is actually free".
            payload["running_now"] = sorted(_running_containers(names))
            payload["configured_containers"] = list(names)
            return payload
        finally:
            await close_cli_pool(pool)

    result = asyncio.run(_run())
    if as_json:
        click.echo(json.dumps(result, indent=2))
        return
    if not result["active"]:
        click.echo("game mode OFF")
        return
    mins = result["seconds_remaining"] // 60
    click.echo(f"game mode ON until {result['until']} ({mins}m left)")
    still_up = result["running_now"]
    click.echo(f"  parked   {', '.join(result['parked_services'])}")
    if still_up:
        click.echo(f"  STILL UP {', '.join(still_up)} — GPU is not fully free")
    else:
        click.echo("  all parked containers are down")
