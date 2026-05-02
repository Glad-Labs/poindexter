"""`poindexter posts` — published post queries + status management.

Also hosts the `poindexter post` (singular) gate-engine commands added
for Glad-Labs/poindexter#24 — create / approve / reject / revise /
reopen / show. Both groups are exported from this module so the CLI
keeps a single entry point per file.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click

from ._api_client import WorkerClient


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared DB helpers for the gate-engine commands (mirrors approval.py)
# ---------------------------------------------------------------------------


from poindexter.cli._bootstrap import resolve_dsn as _gate_dsn  # noqa: E402


async def _make_gate_pool():
    """Open a tiny pool for one CLI invocation."""
    import asyncpg
    return await asyncpg.create_pool(_gate_dsn(), min_size=1, max_size=2)


def _gate_color(state: str) -> str:
    return {
        "pending": "yellow",
        "approved": "green",
        "rejected": "red",
        "revising": "magenta",
        "skipped": "bright_black",
    }.get(state, "white")


def _exit_error(msg: str, code: int = 1) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(code)


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _operator_identity() -> str:
    """Best-effort operator identity for audit attribution.

    Picks (in order): POINDEXTER_OPERATOR env var, USER env var, USERNAME
    env var (Windows), or 'cli'. Stored in the gate row's ``approver``
    column.
    """
    return (
        os.getenv("POINDEXTER_OPERATOR")
        or os.getenv("USER")
        or os.getenv("USERNAME")
        or "cli"
    )


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


# ===========================================================================
# `poindexter post ...` — gate-engine commands (Glad-Labs/poindexter#24)
# ===========================================================================
#
# This is a SEPARATE group from `posts` (singular vs plural). The plural
# group is published-post management; this one is the per-medium approval
# workflow. Kept in the same module so anyone grepping "post create" lands
# on the right file.


@click.group(
    name="post",
    help="Per-medium approval-gate workflow (create / approve / reject).",
)
def post_group() -> None:
    pass


# ---------------------------------------------------------------------------
# post create
# ---------------------------------------------------------------------------


@post_group.command("create")
@click.option("--topic", required=True, help="Topic / working title for the post.")
@click.option(
    "--media",
    default=None,
    help="Comma-separated list of media to generate "
    "(podcast,video,short). Empty/omitted = use default_media_to_generate "
    "from app_settings.",
)
@click.option(
    "--gates",
    default=None,
    help="Comma-separated list of gates to insert "
    "(topic,draft,podcast,video,short,final). Empty string ('') = fully "
    "autonomous. Omitted = use default_workflow_gates from app_settings.",
)
@click.option("--json", "json_output", is_flag=True)
def post_create(
    topic: str,
    media: str | None,
    gates: str | None,
    json_output: bool,
) -> None:
    """Create a new post + its gate row(s) and kick off the writer.

    The actual writer/research stages run in the worker; this CLI is a
    convenience entry point that creates the ``posts`` row and the
    matching ``post_approval_gates`` rows so the worker has everything
    it needs to advance.
    """
    from services.gates.post_approval_gates import (
        CANONICAL_GATE_NAMES,
        create_gates_for_post,
        notify_gate_pending,
    )
    from services.site_config import SiteConfig

    async def _impl():
        pool = await _make_gate_pool()
        try:
            site_cfg = SiteConfig(pool=pool)
            try:
                await site_cfg.load(pool)
            except Exception:
                pass

            # Resolve media + gates with three-stage fallback:
            # explicit flag → app_settings default → empty list.
            resolved_media = (
                _split_csv(media) if media is not None
                else _split_csv(site_cfg.get("default_media_to_generate", ""))
            )
            resolved_gates = (
                _split_csv(gates) if gates is not None
                else _split_csv(site_cfg.get("default_workflow_gates", "topic,draft,final"))
            )

            for g in resolved_gates:
                if g not in CANONICAL_GATE_NAMES:
                    raise RuntimeError(
                        f"Unknown gate {g!r}. Valid: {', '.join(CANONICAL_GATE_NAMES)}"
                    )

            # Insert a minimal posts row. Title = topic, slug = sanitized
            # topic + short random suffix (CLI-created posts don't yet
            # have a writer-generated slug, so they live under a
            # placeholder until the writer fills in the real one).
            import re
            import secrets

            slug_root = re.sub(r"[^\w\s-]", "", topic).lower().replace(" ", "-")[:48]
            slug = f"{slug_root}-{secrets.token_hex(3)}"

            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO posts
                        (title, slug, content, status, media_to_generate)
                    VALUES ($1, $2, '', 'draft', $3::text[])
                    RETURNING id::text AS id, slug, title, status
                    """,
                    topic, slug, resolved_media,
                )

            post_id = row["id"]
            inserted_gates = await create_gates_for_post(
                pool, post_id, resolved_gates,
            )
            # Notify the operator about the first pending gate (if any)
            # so the deep link + CLI hint hits Telegram/Discord
            # immediately. Best-effort — failures are logged in
            # notify_gate_pending and never raise.
            if inserted_gates:
                first = inserted_gates[0]
                await notify_gate_pending(
                    post_id=post_id,
                    gate_name=first["gate_name"],
                    site_config=site_cfg,
                )
            return {
                "post_id": post_id,
                "slug": row["slug"],
                "title": row["title"],
                "status": row["status"],
                "media_to_generate": resolved_media,
                "gates": [
                    {"gate_name": g["gate_name"], "ordinal": g["ordinal"], "state": g["state"]}
                    for g in inserted_gates
                ],
            }
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.secho(
        f"Created post {result['post_id'][:8]}  "
        f"({result['title'][:60]})",
        fg="green",
    )
    click.echo(f"  slug              {result['slug']}")
    click.echo(f"  media_to_generate {result['media_to_generate'] or '(none)'}")
    click.echo(f"  gates             {[g['gate_name'] for g in result['gates']] or '(autonomous)'}")


# ---------------------------------------------------------------------------
# post approve
# ---------------------------------------------------------------------------


@post_group.command("approve")
@click.argument("post_id")
@click.option("--gate", "gate_name", required=True, help="Gate name to approve.")
@click.option("--notes", default=None, help="Optional approval comment.")
@click.option("--json", "json_output", is_flag=True)
def post_approve(
    post_id: str, gate_name: str, notes: str | None, json_output: bool,
) -> None:
    """Approve a specific gate on a post."""
    from services.gates.post_approval_gates import (
        GateServiceError,
        advance_workflow,
        approve_gate,
    )

    async def _impl():
        pool = await _make_gate_pool()
        try:
            row = await approve_gate(
                pool, post_id, gate_name,
                approver=_operator_identity(), notes=notes,
            )
            advance = await advance_workflow(pool, post_id)
            return {"approved": row, "advance": advance.__dict__}
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except GateServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    approved = result["approved"]
    click.secho(
        f"Approved gate {approved['gate_name']!r} on post {post_id[:8]}",
        fg="green",
    )
    advance = result["advance"]
    if advance.get("ready_to_distribute"):
        click.secho("  → ready to distribute", fg="cyan")
    elif advance.get("next_gate"):
        ng = advance["next_gate"]
        click.secho(
            f"  → next gate: {ng['gate_name']} (ordinal {ng['ordinal']})",
            fg="yellow",
        )


# ---------------------------------------------------------------------------
# post reject
# ---------------------------------------------------------------------------


@post_group.command("reject")
@click.argument("post_id")
@click.option("--gate", "gate_name", required=True)
@click.option("--reason", required=True, help="Why the post is being rejected.")
@click.option("--json", "json_output", is_flag=True)
def post_reject(
    post_id: str, gate_name: str, reason: str, json_output: bool,
) -> None:
    """Reject a gate. Sets posts.status='rejected' and ends the workflow."""
    from services.gates.post_approval_gates import (
        GateServiceError,
        reject_gate,
    )

    async def _impl():
        pool = await _make_gate_pool()
        try:
            return await reject_gate(
                pool, post_id, gate_name,
                approver=_operator_identity(), reason=reason,
            )
        finally:
            await pool.close()

    try:
        row = _run(_impl())
    except GateServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(row, indent=2, default=str))
        return

    click.secho(
        f"Rejected gate {row['gate_name']!r} on post {post_id[:8]} "
        f"— post status set to 'rejected'",
        fg="red",
    )


# ---------------------------------------------------------------------------
# post revise
# ---------------------------------------------------------------------------


@post_group.command("revise")
@click.argument("post_id")
@click.option("--gate", "gate_name", required=True)
@click.option(
    "--notes", required=True,
    help="Feedback for the regenerator. Stored in metadata.feedback.",
)
@click.option("--json", "json_output", is_flag=True)
def post_revise(
    post_id: str, gate_name: str, notes: str, json_output: bool,
) -> None:
    """Bounce a gate back for regeneration with operator feedback."""
    from services.gates.post_approval_gates import (
        GateServiceError,
        revise_gate,
    )

    async def _impl():
        pool = await _make_gate_pool()
        try:
            return await revise_gate(
                pool, post_id, gate_name,
                approver=_operator_identity(), feedback=notes,
            )
        finally:
            await pool.close()

    try:
        row = _run(_impl())
    except GateServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(row, indent=2, default=str))
        return

    click.secho(
        f"Revising gate {row['gate_name']!r} on post {post_id[:8]} — "
        "regen stage will pick up feedback",
        fg="magenta",
    )


# ---------------------------------------------------------------------------
# post reopen
# ---------------------------------------------------------------------------


@post_group.command("reopen")
@click.argument("post_id")
@click.option("--gate", "gate_name", required=True)
@click.option(
    "--cascade", is_flag=True,
    help="Also invalidate every downstream decided gate. Without this "
    "flag, reopen refuses if downstream gates are already approved.",
)
@click.option("--json", "json_output", is_flag=True)
def post_reopen(
    post_id: str, gate_name: str, cascade: bool, json_output: bool,
) -> None:
    """Re-open a previously decided gate (rolls it back to pending)."""
    from services.gates.post_approval_gates import (
        GateCascadeRequiredError,
        GateServiceError,
        reopen_gate,
    )

    async def _impl():
        pool = await _make_gate_pool()
        try:
            return await reopen_gate(
                pool, post_id, gate_name, cascade=cascade,
            )
        finally:
            await pool.close()

    try:
        row = _run(_impl())
    except GateCascadeRequiredError as e:
        _exit_error(f"{e}\nRetry with --cascade to confirm.", code=2)
        return
    except GateServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(row, indent=2, default=str))
        return

    click.secho(
        f"Re-opened gate {row['gate_name']!r} on post {post_id[:8]} "
        f"(cascade={cascade})",
        fg="cyan",
    )


# ---------------------------------------------------------------------------
# post show
# ---------------------------------------------------------------------------


@post_group.command("show")
@click.argument("post_id")
@click.option("--json", "json_output", is_flag=True)
def post_show(post_id: str, json_output: bool) -> None:
    """Pretty-print a post's current status + every gate row."""
    from services.gates.post_approval_gates import (
        advance_workflow,
        get_gates_for_post,
    )

    async def _impl() -> dict[str, Any]:
        pool = await _make_gate_pool()
        try:
            async with pool.acquire() as conn:
                post_row = await conn.fetchrow(
                    """
                    SELECT id::text AS id, title, slug, status, published_at,
                           media_to_generate
                      FROM posts WHERE id::text = $1
                    """,
                    post_id,
                )
            if post_row is None:
                raise RuntimeError(f"Post {post_id} not found")
            gates = await get_gates_for_post(pool, post_id)
            advance = await advance_workflow(pool, post_id)
            return {
                "post": dict(post_row),
                "gates": gates,
                "advance": advance.__dict__,
            }
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    p = result["post"]
    click.secho(p["title"] or "(no title)", fg="cyan", bold=True)
    click.echo(f"  id           {p['id']}")
    click.echo(f"  status       {p['status']}")
    click.echo(f"  slug         {p['slug']}")
    click.echo(f"  media        {p['media_to_generate'] or '(none)'}")
    click.echo(f"  published_at {p['published_at'] or '-'}")
    click.echo()

    gates = result["gates"]
    if not gates:
        click.echo("  (no gates configured — autonomous workflow)")
    else:
        click.echo(f"  {'#':<3} {'GATE':<28} {'STATE':<10} {'APPROVER':<14} NOTES")
        for g in gates:
            color = _gate_color(g["state"])
            notes_short = (g.get("notes") or "")[:50]
            click.secho(
                f"  {g['ordinal']:<3} {g['gate_name']:<28} "
                f"{g['state']:<10} {(g.get('approver') or '-'):<14} {notes_short}",
                fg=color,
            )

    click.echo()
    advance = result["advance"]
    if advance.get("ready_to_distribute"):
        click.secho("  → ready to distribute (all gates decided)", fg="cyan")
    elif advance.get("next_gate"):
        ng = advance["next_gate"]
        click.secho(
            f"  → next pending: {ng['gate_name']} (ordinal {ng['ordinal']})",
            fg="yellow",
        )
    elif advance.get("finished"):
        click.secho(f"  → finished ({advance.get('reason')})", fg="bright_black")
