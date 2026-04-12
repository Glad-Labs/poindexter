"""`poindexter posts` — published post queries + status management."""

from __future__ import annotations

import asyncio
import json
import sys

import click

from ._api_client import WorkerClient


def _run(coro):
    return asyncio.run(coro)


@click.group(name="posts", help="Query and manage published/draft blog posts.")
def posts_group() -> None:
    pass


def _print_post_summary(p: dict) -> None:
    status = p.get("status", "?")
    pid = (p.get("id") or "?")[:8]
    slug = p.get("slug") or "-"
    title = p.get("title") or "(no title)"
    color = {
        "published": "green",
        "draft": "white",
        "archived": "bright_black",
        "scheduled": "cyan",
    }.get(status, "white")
    click.secho(f"  {pid}  {status:<10}  {title[:65]}", fg=color)
    click.secho(f"    /posts/{slug}", fg="bright_black")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@posts_group.command("list")
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.option(
    "--include-drafts",
    is_flag=True,
    help="Include non-published posts (requires API token).",
)
@click.option("--json", "json_output", is_flag=True)
def posts_list(limit: int, offset: int, include_drafts: bool, json_output: bool) -> None:
    """List posts with pagination."""
    params = {
        "limit": limit,
        "offset": offset,
        "published_only": "false" if include_drafts else "true",
    }

    async def _list():
        async with WorkerClient() as c:
            resp = await c.get("/api/posts", params=params)
            return await c.json_or_raise(resp)

    try:
        data = _run(_list())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    posts = data.get("posts") or []
    if json_output:
        click.echo(json.dumps(posts, indent=2, default=str))
        return

    if not posts:
        click.echo("(no posts)")
        return

    click.secho(
        f"Posts: {len(posts)} shown / {data.get('total', '?')} total "
        f"(offset={offset}, limit={limit})",
        fg="cyan",
    )
    click.echo()
    for p in posts:
        _print_post_summary(p)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@posts_group.command("search")
@click.argument("query", nargs=-1, required=True)
@click.option("--limit", type=int, default=20, show_default=True)
@click.option(
    "--semantic",
    is_flag=True,
    help="Use pgvector semantic search instead of title/content substring match.",
)
@click.option("--json", "json_output", is_flag=True)
def posts_search(
    query: tuple[str, ...], limit: int, semantic: bool, json_output: bool
) -> None:
    """Search posts. Default is title/content substring; --semantic uses pgvector.

    Examples:

        poindexter posts search "fastapi"              # substring match
        poindexter posts search --semantic "local LLM performance"
    """
    q = " ".join(query).strip()
    if not q:
        click.echo("Error: query is empty.", err=True)
        sys.exit(2)

    if semantic:
        # Semantic path goes straight through MemoryClient — no HTTP hop.
        from poindexter.memory import MemoryClient

        async def _semantic():
            async with MemoryClient() as mem:
                return await mem.find_similar_posts(
                    q, limit=limit, min_similarity=0.3
                )

        hits = _run(_semantic())
        if json_output:
            click.echo(
                json.dumps(
                    [
                        {
                            "source_id": h.source_id,
                            "similarity": round(h.similarity, 4),
                            "metadata": h.metadata,
                            "text_preview": h.text_preview,
                        }
                        for h in hits
                    ],
                    indent=2,
                    default=str,
                )
            )
            return

        if not hits:
            click.echo("(no matches)")
            return

        click.secho(f"Semantic search: {len(hits)} hits for '{q}'", fg="cyan")
        click.echo()
        for h in hits:
            title = (h.metadata or {}).get("title", "(no title)")
            click.secho(f"  {h.similarity:.3f}  {title[:70]}", fg="green")
            click.secho(f"    {h.source_id}", fg="bright_black")
        return

    # Substring path — hits /api/posts/search
    async def _search():
        async with WorkerClient() as c:
            resp = await c.get("/api/posts/search", params={"q": q, "limit": limit})
            return await c.json_or_raise(resp)

    try:
        data = _run(_search())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    posts = data.get("posts") or []
    if json_output:
        click.echo(json.dumps(posts, indent=2, default=str))
        return

    if not posts:
        click.echo("(no matches)")
        return

    click.secho(f"Substring search: {len(posts)} hits for '{q}'", fg="cyan")
    click.echo()
    for p in posts:
        _print_post_summary(p)


# ---------------------------------------------------------------------------
# get — by slug
# ---------------------------------------------------------------------------


@posts_group.command("get")
@click.argument("slug")
@click.option("--content", is_flag=True, help="Include full content.")
@click.option("--json", "json_output", is_flag=True)
def posts_get(slug: str, content: bool, json_output: bool) -> None:
    """Show a single post by slug."""

    async def _get():
        async with WorkerClient() as c:
            resp = await c.get(f"/api/posts/{slug}")
            return await c.json_or_raise(resp)

    try:
        p = _run(_get())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if json_output:
        if not content and isinstance(p, dict):
            p = {k: v for k, v in p.items() if k != "content"}
        click.echo(json.dumps(p, indent=2, default=str))
        return

    click.secho(p.get("title", "?"), fg="cyan", bold=True)
    click.echo(f"  id           {p.get('id', '?')}")
    click.echo(f"  status       {p.get('status', '?')}")
    click.echo(f"  slug         {p.get('slug', '?')}")
    click.echo(f"  published_at {p.get('published_at', '?')}")
    click.echo(f"  updated_at   {p.get('updated_at', '?')}")
    excerpt = p.get("excerpt") or ""
    if excerpt:
        click.echo()
        click.secho("  excerpt:", fg="bright_black")
        click.echo(f"    {excerpt[:300]}")
    if content and p.get("content"):
        click.echo()
        click.secho("--- content ---", fg="cyan")
        click.echo(p["content"])


# ---------------------------------------------------------------------------
# status change (publish, archive, unpublish)
# ---------------------------------------------------------------------------


async def _patch_post(post_id: str, updates: dict) -> dict:
    async with WorkerClient() as c:
        resp = await c.put(f"/api/posts/{post_id}", json=updates)
        # cms_routes uses PATCH but httpx doesn't expose it on our wrapper.
        # Try PATCH via the raw _client if we got method-not-allowed.
        if resp.status_code == 405 and c._client is not None:
            resp = await c._client.patch(f"/api/posts/{post_id}", json=updates)
        return await c.json_or_raise(resp)


@posts_group.command("publish")
@click.argument("post_id")
def posts_publish(post_id: str) -> None:
    """Mark a post as published (sets status='published' and published_at=now)."""
    try:
        p = _run(_patch_post(post_id, {"status": "published"}))
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(f"Published: {post_id}  status={p.get('status', '?')}", fg="green")


@posts_group.command("archive")
@click.argument("post_id")
def posts_archive(post_id: str) -> None:
    """Archive a post (removes from live site, soft delete)."""
    try:
        p = _run(_patch_post(post_id, {"status": "archived"}))
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(f"Archived: {post_id}  status={p.get('status', '?')}", fg="yellow")


@posts_group.command("retitle")
@click.argument("post_id")
@click.argument("title")
def posts_retitle(post_id: str, title: str) -> None:
    """Change a post's title (e.g. to remove first-person pronouns)."""
    try:
        p = _run(_patch_post(post_id, {"title": title}))
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(
        f"Retitled: {post_id}  new title: {p.get('title', title)[:70]}",
        fg="green",
    )
