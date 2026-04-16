"""`poindexter sprint` — Gitea issues dashboard via the Gitea REST API.

Source of truth is the self-hosted Gitea at localhost:3001. GitHub mirrors
are deployment-only and don't carry issue state. See
`reference_gitea.md` and the `integration/gitea_*` app_settings.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import click
import httpx


def _run(coro):
    return asyncio.run(coro)


def _gitea_env() -> tuple[str, str, str]:
    # #198: no silent defaults — operator must set these explicitly
    url = os.getenv("GITEA_URL")
    if not url:
        raise click.ClickException(
            "GITEA_URL is not set. Configure it in the environment "
            "(e.g. http://localhost:3001 for local dev)."
        )
    repo = os.getenv("GITEA_REPO")
    if not repo:
        raise click.ClickException(
            "GITEA_REPO is not set. Configure it in the environment "
            "(e.g. gladlabs/glad-labs-codebase)."
        )
    token = os.getenv("GITEA_TOKEN") or ""
    return url.rstrip("/"), repo, token


async def _api(path: str, token: str, gitea_url: str) -> list | dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"
    async with httpx.AsyncClient(timeout=20) as c:
        resp = await c.get(f"{gitea_url}{path}", headers=headers)
        resp.raise_for_status()
        return resp.json()


@click.group(name="sprint", help="Gitea issues dashboard for the primary repo.")
def sprint_group() -> None:
    pass


@sprint_group.command("issues")
@click.option("--state", type=click.Choice(["open", "closed", "all"]), default="open", show_default=True)
@click.option("--limit", type=int, default=30, show_default=True)
@click.option("--label", default="", help="Filter by label name (exact match).")
@click.option("--json", "json_output", is_flag=True)
def sprint_issues(state: str, limit: int, label: str, json_output: bool) -> None:
    """List open issues from the primary Gitea repo."""
    url, repo, token = _gitea_env()
    if not token:
        click.secho(
            "(warning: GITEA_TOKEN not set — some reads may fail or be filtered)",
            fg="yellow", err=True,
        )

    path = f"/api/v1/repos/{repo}/issues?state={state}&type=issues&limit={limit}"
    if label:
        path += f"&labels={label}"

    try:
        rows = _run(_api(path, token, url))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not isinstance(rows, list):
        click.echo(f"Unexpected response: {rows}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("(no issues)")
        return

    click.secho(f"{repo} — {state} issues ({len(rows)} shown)", fg="cyan")
    click.echo()
    for i in rows:
        number = i.get("number", "?")
        title = i.get("title", "")
        ms = (i.get("milestone") or {}).get("title") or "no milestone"
        labels = ", ".join(l.get("name", "") for l in (i.get("labels") or [])) or "-"
        click.echo(f"  #{number}  {title[:76]}")
        click.secho(f"      [{ms}]  labels: {labels}", fg="bright_black")


@sprint_group.command("milestones")
@click.option("--state", type=click.Choice(["open", "closed", "all"]), default="open", show_default=True)
@click.option("--json", "json_output", is_flag=True)
def sprint_milestones(state: str, json_output: bool) -> None:
    """List milestones with open/closed counts."""
    url, repo, token = _gitea_env()
    path = f"/api/v1/repos/{repo}/milestones?state={state}&limit=50"
    try:
        rows = _run(_api(path, token, url))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not isinstance(rows, list):
        click.echo(f"Unexpected response: {rows}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("(no milestones)")
        return

    click.secho(f"{repo} — milestones ({state})", fg="cyan")
    click.echo()
    for m in rows:
        title = m.get("title", "?")
        open_count = m.get("open_issues", 0)
        closed_count = m.get("closed_issues", 0)
        due = m.get("due_on") or "no date"
        state_str = m.get("state", "?")
        click.echo(
            f"  [{state_str}] {title}  open={open_count}  closed={closed_count}  due={due}"
        )


@sprint_group.command("recent")
@click.option("--days", type=int, default=7, show_default=True)
def sprint_recent(days: int) -> None:
    """Recently closed issues from the last N days."""
    url, repo, token = _gitea_env()
    path = f"/api/v1/repos/{repo}/issues?state=closed&type=issues&limit=50&sort=newest"
    try:
        rows = _run(_api(path, token, url))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not isinstance(rows, list):
        click.echo(f"Unexpected response: {rows}", err=True)
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    shown = 0
    click.secho(f"{repo} — closed in the last {days} days", fg="cyan")
    click.echo()
    for i in rows:
        closed_at = i.get("closed_at")
        if not closed_at:
            continue
        try:
            dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt < cutoff:
            continue
        click.echo(f"  #{i.get('number', '?')}  {(i.get('title') or '')[:76]}")
        shown += 1
    if shown == 0:
        click.echo("  (none)")
