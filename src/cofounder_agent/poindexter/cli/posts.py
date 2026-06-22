"""`poindexter posts` — published post queries + status management.

Also hosts the `poindexter post` (singular) command group for creating
posts and queuing them for the content pipeline.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import click

from ._api_client import WorkerClient


def _run(coro):
    return asyncio.run(coro)


from poindexter.cli._aliases import deprecated_alias  # noqa: E402
from poindexter.cli._bootstrap import resolve_dsn as _gate_dsn  # noqa: E402


async def _make_gate_pool():
    """Open a tiny pool for one CLI invocation."""
    import asyncpg
    return await asyncpg.create_pool(_gate_dsn(), min_size=1, max_size=2)


def _exit_error(msg: str, code: int = 1) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(code)


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


# Canonical media-flavor names that may legitimately appear in
# ``posts.media_to_generate``. Distinct from the GATE namespace
# (``MEDIUM_GATE_NAMES`` uses the bare word ``short``) — the generation
# and distribution code keys off these exact strings:
# ``services.media_approval_service._VALID_MEDIA`` and
# ``services.publish_service`` (``_wants_short`` checks ``"video_short"``).
# (``video_long`` was collapsed into ``video`` in #1460.)
CANONICAL_MEDIA_NAMES: tuple[str, ...] = (
    "podcast", "video", "video_short",
)

# Operator-friendly aliases → canonical flavor. The ``--media`` help text
# and the gate namespace both call the short-form video ``short``; an
# operator who types ``--media short`` previously got it stored verbatim,
# and since no consumer matches ``short`` the short-video request silently
# no-op'd. Normalize it to the ``video_short`` the pipeline actually
# checks for (fail-loud on anything else, per ``feedback_no_silent_defaults``).
_MEDIA_ALIASES: dict[str, str] = {"short": "video_short"}


def _normalize_media(names: list[str]) -> list[str]:
    """Map operator media aliases to canonical names, de-duped, order-preserving.

    ``short`` → ``video_short``; canonical names pass through untouched.
    Collisions (``short`` + ``video_short``) collapse to one entry so the
    stored array — and the idempotency key derived from it — is stable
    regardless of which spelling the operator used.
    """
    out: list[str] = []
    for name in names:
        canonical = _MEDIA_ALIASES.get(name, name)
        if canonical not in out:
            out.append(canonical)
    return out


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
        if resp.status_code == 405 and c._http is not None:
            resp = await c._http.patch(f"/api/posts/{post_id}", json=updates)
        return await c.json_or_raise(resp)


async def _unpublish_post(post_id: str) -> dict:
    async with WorkerClient() as c:
        resp = await c.post(f"/api/posts/{post_id}/unpublish", json={})
        return await c.json_or_raise(resp)


@posts_group.command("publish")
@click.argument("post_id")
def posts_publish(post_id: str) -> None:
    """Mark a post as published (full id or 8-char prefix; sets status='published', published_at=now)."""
    try:
        p = _run(_patch_post(post_id, {"status": "published"}))
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(f"Published: {post_id}  status={p.get('status', '?')}", fg="green")


@posts_group.command("archive")
@click.argument("post_id")
def posts_archive(post_id: str) -> None:
    """Archive a post (full id or 8-char prefix; removes from live site, soft delete)."""
    try:
        p = _run(_patch_post(post_id, {"status": "archived"}))
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(f"Archived: {post_id}  status={p.get('status', '?')}", fg="yellow")


@posts_group.command("unpublish")
@click.argument("post_id")
def posts_unpublish(post_id: str) -> None:
    """Take a published post offline (full id or 8-char prefix).

    Immediate rollback for a wrong/bad publish: reverts published->draft AND
    retires the post's static JSON + busts its ISR cache, so the live site
    drops it now. Unlike `archive` (which only flips the status column), this
    pulls the post from storage so it stops being served immediately.
    """
    try:
        r = _run(_unpublish_post(post_id))
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if r.get("unpublished"):
        slug = r.get("slug", "?")
        click.secho(
            f"Unpublished: {post_id}  status=draft  (slug '{slug}' retired from live site)",
            fg="yellow",
        )
    else:
        click.secho(
            f"No change: {post_id}  ({r.get('reason', 'not currently published')})",
            fg="bright_black",
        )


@posts_group.command("retitle")
@click.argument("post_id")
@click.argument("title")
def posts_retitle(post_id: str, title: str) -> None:
    """Change a post's title (full id or 8-char prefix; e.g. to remove first-person pronouns)."""
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
    hidden=True,
    help=(
        "[DEPRECATED] merged into `poindexter posts` (#1652). Kept as a hidden "
        "alias for backcompat; use `poindexter posts create`."
    ),
)
def post_group() -> None:
    pass


# ---------------------------------------------------------------------------
# post create
# ---------------------------------------------------------------------------


def _compute_idempotency_key(
    *,
    content: str,
    operator: str,
) -> str:
    """Derive a stable 16-hex-char key from a manual upload's identity.

    For a manual write/upload the **body** is the identity (not the topic or
    the requested media): re-uploading the same file is a no-op, while an
    edited body is legitimately a new post. So the key hashes ``content +
    operator`` — media is deliberately excluded so the same body with a
    different ``--media`` request still collapses to one post.
    """
    import hashlib

    payload = "|".join([content.strip(), operator.strip()])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@click.command("create")
@click.option(
    "--from-file",
    "from_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    default=None,
    help="Markdown file to upload as the post body. Omit to read the body "
    "from stdin (pipe markdown in).",
)
@click.option(
    "--title",
    default=None,
    help="Post title. Omit to derive it from the first markdown H1 in the body.",
)
@click.option(
    "--topic",
    default=None,
    hidden=True,
    help="[DEPRECATED] alias for --title.",
)
@click.option(
    "--slug",
    default=None,
    help="URL slug. Omit to derive from the title plus a short random suffix.",
)
@click.option(
    "--niche",
    default=None,
    help="Niche slug stored in metadata.niche_slug (e.g. ai_ml, gaming, "
    "pc_hardware). Omitting it emits a warning — niche-gated publishing will "
    "skip a post with no niche.",
)
@click.option(
    "--excerpt",
    default=None,
    help="Optional excerpt / summary for listings and the SEO description.",
)
@click.option(
    "--status",
    type=click.Choice(["draft", "awaiting_approval"]),
    default="draft",
    show_default=True,
    help="Initial status. 'awaiting_approval' routes it into the approval "
    "queue; 'draft' keeps it private.",
)
@click.option(
    "--media",
    default=None,
    help="Comma-separated list of media to generate "
    "(podcast, video, video_short; 'short' is accepted as an "
    "alias for video_short). Empty/omitted = use default_media_to_generate "
    "from app_settings. Media is generated at publish time, not now.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Bypass the semantic dedup guard AND idempotency — create the post "
    "even if its title near-duplicates a published post or the same body was "
    "uploaded within the window (#338).",
)
@click.option("--json", "json_output", is_flag=True)
def post_create(
    from_file: str | None,
    title: str | None,
    topic: str | None,
    slug: str | None,
    niche: str | None,
    excerpt: str | None,
    status: str,
    media: str | None,
    force: bool,
    json_output: bool,
) -> None:
    """Upload a finished markdown post you wrote by hand.

    This is the MANUAL counterpart to the AI pipeline (the ``create_post`` MCP
    tool / ``POST /api/tasks``, which *generates* a post from a topic). Here you
    supply the body yourself — from ``--from-file`` or piped via stdin — and a
    complete ``posts`` row is inserted directly. No writer LLM runs.

      poindexter posts create --from-file my-post.md --niche ai_ml
      cat my-post.md | poindexter posts create --niche gaming

    Title is ``--title`` (else the first markdown ``# H1`` in the body); slug is
    ``--slug`` (else the slugified title plus a short suffix). ``--status``
    defaults to ``draft``; pass ``awaiting_approval`` to route it into the
    approval queue. ``--niche`` lands in ``metadata.niche_slug``. Any
    ``--media`` is generated at publish time.

    Semantic dedup: the resolved title is checked against already-published
    posts and refused if too similar (``create_post_dedup_threshold``, default
    0.75); ``--force`` overrides. Shared with the AI path via
    ``services.topic_dedup_guard`` (glad-labs-stack#1823).

    Idempotency (#338): a stable key is computed from ``body + operator``. If
    the same body was uploaded inside the window
    (``cli_post_create_idempotency_window_minutes``, default 30), the existing
    post id is returned instead of inserting a duplicate. ``--force`` bypasses
    this, or set ``cli_post_create_idempotency_enabled=false`` to disable it.
    """
    import re
    import secrets

    from services.site_config import SiteConfig
    from services.title_generation import extract_h1_title

    # --- Resolve body (no DB needed — fail fast before opening a pool) ----
    # --from-file wins; otherwise read piped stdin. An interactive TTY with
    # nothing piped is treated as empty so we fail loud instead of hanging.
    if from_file:
        with open(from_file, encoding="utf-8") as fh:
            raw_body = fh.read()
    elif sys.stdin.isatty():
        raw_body = ""
    else:
        raw_body = sys.stdin.read()
    content = raw_body.strip()
    if not content:
        _exit_error(
            "No post body. Provide --from-file PATH or pipe markdown via "
            "stdin (e.g. `cat post.md | poindexter posts create`)."
        )
        return

    # --- Resolve title: explicit flag → --topic alias → first H1 ---------
    resolved_title = (title or topic or "").strip() or extract_h1_title(content)
    if not resolved_title:
        _exit_error(
            "No title. Pass --title (or --topic), or start the body with a "
            "markdown H1 (`# My Title`)."
        )
        return
    resolved_title = resolved_title.strip()

    # --- Resolve slug: explicit → slugified title + short random suffix --
    if slug:
        resolved_slug = slug.strip()
    else:
        slug_root = re.sub(
            r"[^\w\s-]", "", resolved_title
        ).lower().strip().replace(" ", "-")[:48].strip("-")
        resolved_slug = f"{slug_root}-{secrets.token_hex(3)}"

    async def _impl():
        pool = await _make_gate_pool()
        try:
            site_cfg = SiteConfig(pool=pool)
            try:
                await site_cfg.load(pool)
            except Exception:
                pass

            # Resolve media with two-stage fallback:
            # explicit flag → app_settings default. (Needs the DB for the
            # default, so it's the one resolution that can't run pre-pool.)
            resolved_media = (
                _split_csv(media) if media is not None
                else _split_csv(site_cfg.get("default_media_to_generate", ""))
            )

            # Normalize media aliases (``short`` → ``video_short``) and validate.
            resolved_media = _normalize_media(resolved_media)
            for m in resolved_media:
                if m not in CANONICAL_MEDIA_NAMES:
                    raise RuntimeError(
                        f"Unknown media flavor {m!r}. Valid: "
                        f"{', '.join(CANONICAL_MEDIA_NAMES)} "
                        f"('short' is accepted as an alias for video_short)."
                    )

            # --- Idempotency check (#338), re-keyed on body+operator ----
            #
            # The body is the upload's identity (not the topic/media): the
            # same file uploaded twice is a no-op, an edited body is a new
            # post. The lookup runs against the partial index
            # ``idx_posts_cli_idempotency_key`` (0000_baseline.schema.sql).
            operator = _operator_identity()
            idempotency_enabled = (
                site_cfg.get("cli_post_create_idempotency_enabled", "true")
                .strip().lower() == "true"
            )
            try:
                window_minutes = int(
                    site_cfg.get(
                        "cli_post_create_idempotency_window_minutes", "30"
                    )
                )
            except (ValueError, TypeError):
                window_minutes = 30

            idempotency_key = _compute_idempotency_key(
                content=content,
                operator=operator,
            )

            if idempotency_enabled and not force:
                async with pool.acquire() as conn:
                    existing = await conn.fetchrow(
                        """
                        SELECT id::text AS id, slug, title, status,
                               media_to_generate
                          FROM posts
                         WHERE cli_idempotency_key = $1
                           AND created_at > NOW() - ($2::int || ' minutes')::interval
                         ORDER BY created_at DESC
                         LIMIT 1
                        """,
                        idempotency_key, window_minutes,
                    )
                if existing is not None:
                    click.echo(
                        f"[CLI] post create idempotent hit — returning "
                        f"existing post {existing['id']}",
                        err=True,
                    )
                    return {
                        "post_id": existing["id"],
                        "slug": existing["slug"],
                        "title": existing["title"],
                        "status": existing["status"],
                        "media_to_generate": list(
                            existing["media_to_generate"] or []
                        ),
                        "idempotent_hit": True,
                        "idempotency_key": idempotency_key,
                    }

            # Pre-insert semantic dedup guard — shared with the create_post
            # MCP tool / POST /api/tasks path (glad-labs-stack#1823). A
            # manually-uploaded post whose TITLE near-duplicates an
            # already-published post is refused; --force overrides (the same
            # flag that bypasses idempotency above).
            if not force:
                from services.topic_dedup_guard import (
                    DuplicateTopicError,
                    assert_topic_not_duplicate,
                )

                try:
                    await assert_topic_not_duplicate(
                        resolved_title, site_config=site_cfg
                    )
                except DuplicateTopicError as dup:
                    raise RuntimeError(
                        f"{dup.topic!r} is too similar to published post "
                        f"{dup.match_title!r} ({dup.match_post_id}) — cosine "
                        f"{dup.similarity:.2f} ≥ {dup.threshold:.2f}. Refusing to "
                        "create a near-duplicate; re-run with --force to override."
                    ) from dup

            # --- Niche → metadata.niche_slug (warn when omitted) --------
            if niche:
                metadata_json = json.dumps({"niche_slug": niche.strip()})
            else:
                metadata_json = json.dumps({})
                click.echo(
                    "warning: no --niche given; the post has no niche_slug in "
                    "metadata, so niche-gated publishing will skip it. Pass "
                    "--niche <slug> to set one.",
                    err=True,
                )

            # Persist the idempotency key on the row when the feature is
            # enabled (and not forced — forced inserts skip dedup so don't
            # poison the lookup with a key an earlier post already owns).
            stored_key = (
                idempotency_key if (idempotency_enabled and not force) else None
            )

            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO posts
                        (title, slug, content, excerpt, status,
                         media_to_generate, metadata, cli_idempotency_key)
                    VALUES ($1, $2, $3, $4, $5, $6::text[], $7::jsonb, $8)
                    RETURNING id::text AS id, slug, title, status
                    """,
                    resolved_title, resolved_slug, content, excerpt, status,
                    resolved_media, metadata_json, stored_key,
                )

            return {
                "post_id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "status": row["status"],
                "media_to_generate": resolved_media,
                "idempotent_hit": False,
                "idempotency_key": stored_key,
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

    if result.get("idempotent_hit"):
        click.secho(
            f"Reused post {result['post_id'][:8]}  "
            f"({result['title'][:60]})  [idempotent]",
            fg="cyan",
        )
    else:
        click.secho(
            f"Created post {result['post_id'][:8]}  "
            f"({result['title'][:60]})  [{result['status']}]",
            fg="green",
        )
    click.echo(f"  slug              {result['slug']}")
    click.echo(f"  media_to_generate {result['media_to_generate'] or '(none)'}")


# ---------------------------------------------------------------------------
# Consolidation (#1652, sibling of epic #1340): merge the singular `post` group
# into `posts`. `post create` becomes `posts create` (canonical); the `post`
# group stays hidden with a deprecated `create` alias for backcompat.
# ---------------------------------------------------------------------------

posts_group.add_command(post_create, name="create")
post_group.add_command(
    deprecated_alias(post_create, name="create", new_path="posts create"),
)


__all__ = ["posts_group", "post_group", "post_create"]
