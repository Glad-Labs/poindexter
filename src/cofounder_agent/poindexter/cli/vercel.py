"""`poindexter vercel` — Vercel deployment status via the REST API.

Mirrors the vercel-status openclaw skill but in CLI form. Never shells out
to the `vercel` CLI — reads projectId/orgId from .vercel/project.json at
repo root and uses VERCEL_TOKEN from the environment.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

import click
import httpx

VERCEL_API = "https://api.vercel.com"


def _run(coro):
    return asyncio.run(coro)


def _resolve_project() -> tuple[str, str]:
    """Find .vercel/project.json and return (project_id, team_id)."""
    candidates = [
        Path.cwd() / ".vercel" / "project.json",
        Path(os.getenv("POINDEXTER_REPO_ROOT", "")) / ".vercel" / "project.json",
        Path.home() / "glad-labs-website" / ".vercel" / "project.json",
        Path("/app") / ".vercel" / "project.json",
    ]
    for path in candidates:
        if path and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("projectId", ""), data.get("orgId", "")
    raise FileNotFoundError(
        "Could not find .vercel/project.json. Run `vercel link` in the repo "
        "root once, or set POINDEXTER_REPO_ROOT to the directory that "
        "contains the .vercel folder."
    )


async def _vercel_api(path: str) -> dict:
    token = os.getenv("VERCEL_TOKEN", "")
    if not token:
        raise RuntimeError(
            "VERCEL_TOKEN env var not set. Generate one at "
            "https://vercel.com/account/settings/tokens and export it."
        )
    async with httpx.AsyncClient(timeout=20) as c:
        resp = await c.get(
            f"{VERCEL_API}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Vercel API HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()


@click.group(name="vercel", help="Vercel deployment status via the REST API (no CLI needed).")
def vercel_group() -> None:
    pass


def _format_deployment(d: dict) -> str:
    url = d.get("url") or "(no url)"
    state = d.get("state") or d.get("readyState") or "?"
    target = d.get("target") or "preview"
    created = d.get("created") or d.get("createdAt")
    if created:
        ts = dt.datetime.fromtimestamp(created / 1000, tz=dt.timezone.utc).isoformat()
    else:
        ts = "?"
    commit = (d.get("meta") or {}).get("githubCommitSha", "")[:7]
    return f"  {state:<10} [{target:<10}] https://{url}  {ts}  {commit}"


@vercel_group.command("deployments")
@click.option("--limit", type=int, default=5, show_default=True)
@click.option("--target", type=click.Choice(["production", "preview", "all"]), default="all", show_default=True)
@click.option("--json", "json_output", is_flag=True)
def vercel_deployments(limit: int, target: str, json_output: bool) -> None:
    """List recent Vercel deployments for the linked project."""
    try:
        project_id, team_id = _resolve_project()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    query = f"projectId={project_id}&teamId={team_id}&limit={limit}"
    if target != "all":
        query += f"&target={target}"

    try:
        data = _run(_vercel_api(f"/v6/deployments?{query}"))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    deployments = data.get("deployments", [])
    if json_output:
        click.echo(json.dumps(deployments, indent=2, default=str))
        return

    if not deployments:
        click.echo("(no deployments)")
        return

    click.secho(f"Recent deployments ({len(deployments)})", fg="cyan")
    click.echo()
    for d in deployments:
        click.echo(_format_deployment(d))


@vercel_group.command("production")
@click.option("--json", "json_output", is_flag=True)
def vercel_production(json_output: bool) -> None:
    """Show the latest production deployment."""
    try:
        project_id, team_id = _resolve_project()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    query = f"projectId={project_id}&teamId={team_id}&limit=1&target=production"
    try:
        data = _run(_vercel_api(f"/v6/deployments?{query}"))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    deployments = data.get("deployments", [])
    if json_output:
        click.echo(json.dumps(deployments, indent=2, default=str))
        return

    if not deployments:
        click.echo("(no production deployments)")
        return

    click.secho("Latest Production Deployment", fg="cyan")
    click.echo()
    click.echo(_format_deployment(deployments[0]))


@vercel_group.command("domains")
@click.option("--json", "json_output", is_flag=True)
def vercel_domains(json_output: bool) -> None:
    """List domains attached to the linked project."""
    try:
        project_id, team_id = _resolve_project()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    try:
        data = _run(_vercel_api(f"/v9/projects/{project_id}/domains?teamId={team_id}"))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    domains = data.get("domains", [])
    if json_output:
        click.echo(json.dumps(domains, indent=2, default=str))
        return

    if not domains:
        click.echo("(no domains)")
        return

    click.secho(f"Domains ({len(domains)})", fg="cyan")
    click.echo()
    for d in domains:
        verified = "verified" if d.get("verified") else "UNVERIFIED"
        branch = d.get("gitBranch") or ""
        redirect = d.get("redirect") or ""
        extras = []
        if branch:
            extras.append(f"branch={branch}")
        if redirect:
            extras.append(f"→{redirect}")
        extra_str = ("  " + " ".join(extras)) if extras else ""
        click.echo(f"  {d.get('name', '?')}  [{verified}]{extra_str}")
