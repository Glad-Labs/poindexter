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
@click.option(
    "--status",
    type=str,
    default=None,
    help=(
        "Filter to a single pipeline_tasks.status value: e.g. "
        "``dry_run``, ``awaiting_approval``, ``rejected_final``, "
        "``published``. When set, queries pipeline_tasks directly "
        "(via DSN) instead of the public /api/posts endpoint — surfaces "
        "rows that are not yet posts (dry_run, rejected, etc)."
    ),
)
@click.option("--json", "json_output", is_flag=True)
def posts_list(
    limit: int,
    offset: int,
    include_drafts: bool,
    status: str | None,
    json_output: bool,
) -> None:
    """List posts with pagination.

    Default: queries the worker's public ``/api/posts`` endpoint
    (status='published', or all statuses with --include-drafts).

    With ``--status`` the command bypasses the API and queries
    ``pipeline_tasks`` directly so the operator can inspect dry-run
    output, rejected drafts, or any other intermediate state.
    """
    if status:
        _list_by_status(status, limit=limit, offset=offset, json_output=json_output)
        return
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


def _list_by_status(
    status: str, *, limit: int, offset: int, json_output: bool,
) -> None:
    """Query pipeline_tasks directly for a given status filter.

    Used when ``--status`` is provided. Surfaces dry_run / rejected /
    cancelled rows the public API hides.
    """
    async def _impl():
        import asyncpg
        import os

        dsn = (
            os.getenv("POINDEXTER_MEMORY_DSN")
            or os.getenv("LOCAL_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or ""
        )
        if not dsn:
            raise RuntimeError(
                "No DSN — set POINDEXTER_MEMORY_DSN, LOCAL_DATABASE_URL, "
                "or DATABASE_URL.",
            )
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT
                    task_id, topic, status, stage,
                    quality_score, model_used,
                    started_at, completed_at, updated_at
                FROM content_tasks
                WHERE status = $1
                ORDER BY updated_at DESC NULLS LAST
                LIMIT $2 OFFSET $3
                """,
                status, limit, offset,
            )
            total_row = await conn.fetchrow(
                "SELECT COUNT(*) AS c FROM content_tasks WHERE status = $1",
                status,
            )
            return [dict(r) for r in rows], int(total_row["c"]) if total_row else 0
        finally:
            await conn.close()

    try:
        rows, total = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo(f"(no tasks with status={status!r})")
        return

    click.secho(
        f"Tasks status={status!r}: {len(rows)} shown / {total} total "
        f"(offset={offset}, limit={limit})",
        fg="cyan",
    )
    click.echo()
    for r in rows:
        tid = (r.get("task_id") or "?")[:8]
        topic = (r.get("topic") or "(no topic)")[:65]
        score = r.get("quality_score")
        score_s = f"q={score}" if score is not None else "q=?"
        click.secho(f"  {tid}  {score_s:>6}  {topic}", fg="white")
        click.secho(
            f"    stage={r.get('stage','?')}  model={r.get('model_used','?')}  "
            f"updated={(r.get('updated_at') or '?')!s:.19}",
            fg="bright_black",
        )


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


@posts_group.command("refresh")
@click.argument("slug")
@click.option("--json", "json_output", is_flag=True)
def posts_refresh(slug: str, json_output: bool) -> None:
    """Refresh a post's caches — fires Vercel ISR revalidation + R2 static export.

    Use after a direct DB edit or any out-of-band change that didn't go
    through PATCH /api/posts/{id}. The PATCH endpoint already triggers
    this automatically (gh#193).

    Example:

        poindexter posts refresh the-ai-first-freelancer-building-...

    Returns the per-step result so you can see which path succeeded.
    """
    import asyncio
    import os

    async def _impl():
        import asyncpg
        from services.site_config import SiteConfig

        dsn = (
            os.getenv("POINDEXTER_MEMORY_DSN")
            or os.getenv("LOCAL_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or ""
        )
        if not dsn:
            raise RuntimeError(
                "No DSN — set POINDEXTER_MEMORY_DSN, LOCAL_DATABASE_URL, or DATABASE_URL."
            )
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        try:
            cfg = SiteConfig(pool=pool)
            try:
                await cfg.load(pool)
            except Exception:
                pass
            from routes.cms_routes import _refresh_post_caches
            return await _refresh_post_caches(pool, slug, cfg)
        finally:
            await pool.close()

    try:
        result = asyncio.run(_impl())
    except Exception as e:
        click.echo(f"Error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    rev = result.get("revalidation")
    exp = result.get("static_export")
    rev_ok = "✓" if rev else "✗"
    exp_ok = "✓" if exp else "✗"
    click.secho(f"Refreshed: {slug}", fg="cyan", bold=True)
    click.echo(f"  ISR revalidation:    {rev_ok} {rev}")
    click.echo(f"  Static export (R2):  {exp_ok} {exp}")
    if "revalidation_error" in result:
        click.secho(f"  rev error: {result['revalidation_error']}", fg="yellow")
    if "static_export_error" in result:
        click.secho(f"  export error: {result['static_export_error']}", fg="yellow")
