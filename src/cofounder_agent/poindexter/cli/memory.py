"""`poindexter memory` subcommands — slice 6 of Gitea #192.

Thin CLI wrapper over `poindexter.memory.MemoryClient`. Uses the same
library every other tool in the stack imports, so the CLI and the worker
see identical data.

Subcommands:
    search   — semantic search, optionally filtered by writer/source_table
    status   — aggregate counts per source_table and per writer
    store    — write a memory note into pgvector (auto-embeds)
    hit      — show a single hit by source_id (debug)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import click

from poindexter.memory import MemoryClient, MemoryHit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine from a click command."""
    return asyncio.run(coro)


def _print_hits(hits: list[MemoryHit], *, json_output: bool) -> None:
    if json_output:
        click.echo(
            json.dumps(
                [
                    {
                        "source_table": h.source_table,
                        "source_id": h.source_id,
                        "similarity": round(h.similarity, 4),
                        "writer": h.writer,
                        "origin_path": h.origin_path,
                        "text_preview": h.text_preview,
                        "metadata": h.metadata,
                    }
                    for h in hits
                ],
                indent=2,
                default=str,
            )
        )
        return

    if not hits:
        click.echo("(no hits)")
        return

    for h in hits:
        writer_str = f" [{h.writer}]" if h.writer else ""
        click.secho(
            f"{h.similarity:.3f}{writer_str} {h.source_table}/{h.source_id}",
            fg="green",
        )
        preview = (h.text_preview or "").strip()
        if preview:
            click.echo(f"    {preview[:180]}")
        click.echo()


# ---------------------------------------------------------------------------
# `poindexter memory` group
# ---------------------------------------------------------------------------


@click.group(name="memory", help="Query and write to the shared pgvector memory store.")
def memory_group() -> None:
    pass


@memory_group.command("search")
@click.argument("query", nargs=-1, required=True)
@click.option("--writer", default="", help="Filter by writer column (e.g. claude-code, openclaw, worker).")
@click.option(
    "--source-table",
    "source_table",
    default="",
    help="Filter by source_table (memory, posts, issues, audit).",
)
@click.option("--min-similarity", type=float, default=0.3, show_default=True)
@click.option("--limit", type=int, default=10, show_default=True)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of human text.")
def memory_search(
    query: tuple[str, ...],
    writer: str,
    source_table: str,
    min_similarity: float,
    limit: int,
    json_output: bool,
) -> None:
    """Semantic search across the shared memory store.

    Examples:

        poindexter memory search "why did we pick gemma3"

        poindexter memory search --writer claude-code --limit 3 "decisions about pgvector"
    """
    q = " ".join(query).strip()
    if not q:
        click.echo("Error: query is empty.", err=True)
        sys.exit(2)

    async def _search():
        async with MemoryClient() as mem:
            return await mem.search(
                q,
                writer=writer or None,
                source_table=source_table or None,
                min_similarity=min_similarity,
                limit=limit,
            )

    hits = _run(_search())
    _print_hits(hits, json_output=json_output)


@memory_group.command("status")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of human text.")
def memory_status(json_output: bool) -> None:
    """Aggregate counts per source_table and per writer."""

    async def _stats() -> dict[str, Any]:
        async with MemoryClient() as mem:
            return await mem.stats()

    stats = _run(_stats())

    if json_output:
        click.echo(json.dumps(stats, indent=2, default=str))
        return

    click.secho("=== by source_table ===", fg="cyan", bold=True)
    for key, data in stats["by_source_table"].items():
        click.echo(
            f"  {key:15s} {data['count']:>6d}  newest={data['newest']}"
        )
    click.echo()

    click.secho("=== by writer ===", fg="cyan", bold=True)
    for key, data in stats["by_writer"].items():
        click.echo(
            f"  {key:15s} {data['count']:>6d}  newest={data['newest']}"
        )


@memory_group.command("store")
@click.option("--text", help="Text content to store. Mutually exclusive with --file.")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read text from a file on disk. Mutually exclusive with --text.",
)
@click.option(
    "--writer",
    required=True,
    help="Origin label (claude-code, openclaw, worker, user). Required.",
)
@click.option(
    "--source-id",
    "source_id",
    default=None,
    help="Stable id for dedup. Defaults to an auto-generated id if omitted.",
)
@click.option(
    "--source-table",
    "source_table",
    default="memory",
    show_default=True,
    help="Namespace. Usually 'memory'.",
)
@click.option("--tag", "tags", multiple=True, help="Repeatable --tag for metadata.")
def memory_store(
    text: str | None,
    file_path: Path | None,
    writer: str,
    source_id: str | None,
    source_table: str,
    tags: tuple[str, ...],
) -> None:
    """Store a memory note. Provide either --text or --file.

    Examples:

        poindexter memory store --writer claude-code --text "Decision: default writer is gemma3:27b"

        poindexter memory store --writer user --file ~/notes/2026-04-12.md --tag meeting --tag decisions
    """
    if text and file_path:
        click.echo("Error: pass --text OR --file, not both.", err=True)
        sys.exit(2)
    if not text and not file_path:
        click.echo("Error: --text or --file is required.", err=True)
        sys.exit(2)

    if file_path is not None:
        content = file_path.read_text(encoding="utf-8")
        if source_id is None:
            source_id = f"{writer}/{file_path.name}"
    else:
        content = text or ""

    async def _store() -> str:
        async with MemoryClient() as mem:
            return await mem.store(
                text=content,
                writer=writer,
                source_id=source_id,
                source_table=source_table,
                tags=list(tags) if tags else None,
            )

    stored_id = _run(_store())
    click.secho(f"Stored: {source_table}/{stored_id} ({len(content)} chars)", fg="green")


@memory_group.command("embed")
@click.argument("text", nargs=-1, required=True)
@click.option("--json", "json_output", is_flag=True)
def memory_embed(text: tuple[str, ...], json_output: bool) -> None:
    """Print the raw 768-dim embedding for a given text. Debug tool."""

    content = " ".join(text)

    async def _embed():
        async with MemoryClient() as mem:
            return await mem.embed(content)

    vec = _run(_embed())
    if json_output:
        click.echo(json.dumps(vec))
    else:
        click.echo(f"dim={len(vec)}")
        click.echo(f"first_5={vec[:5]}")


@memory_group.command("backfill-posts")
@click.option(
    "--since",
    default="7d",
    show_default=True,
    help="Backfill posts published within this window (e.g. 7d, 30d, 1y, all).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List what would be embedded without writing to pgvector.",
)
def memory_backfill_posts(since: str, dry_run: bool) -> None:
    """Re-embed published posts into pgvector (source_table='posts').

    The publish hook's embedder had a namespace bug that wrote to
    source_table='post' (singular) while every reader queries 'posts'
    (plural). After fixing the hook, run this once to backfill posts
    that published while the hook was broken.
    """
    # Resolve DB URL through the bootstrap helper so this works on a
    # fresh clone without a .env file.
    import os as _os
    import sys as _sys
    _here = Path(__file__).resolve()
    for _p in _here.parents:
        if (_p / "brain" / "bootstrap.py").is_file():
            if str(_p) not in _sys.path:
                _sys.path.insert(0, str(_p))
            break
    from brain.bootstrap import resolve_database_url

    dsn = resolve_database_url()
    if not dsn:
        raise click.ClickException(
            "No database URL. Run `poindexter setup` or set DATABASE_URL."
        )

    # Parse --since → SQL interval
    if since == "all":
        where_clause = ""
    else:
        # accept 7d, 30d, 1y, 12h, etc.
        import re as _re
        m = _re.fullmatch(r"(\d+)([dhmy])", since.strip())
        if not m:
            raise click.ClickException(
                "--since must be 'all' or NUMBER+unit (d/h/m/y), e.g. 7d, 30d, 1y."
            )
        n, unit = m.group(1), m.group(2)
        interval = {"d": "day", "h": "hour", "m": "month", "y": "year"}[unit]
        where_clause = f"WHERE published_at > NOW() - INTERVAL '{n} {interval}s'"

    async def _backfill() -> None:
        import asyncpg

        from services.embedding_service import EmbeddingService
        from services.embeddings_db import EmbeddingsDatabase
        from services.ollama_client import OllamaClient

        # We need a DB pool, Ollama, and the embeddings_db + service.
        # We're standalone (outside the worker process) so we build them
        # here and close them at the end.
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        try:
            rows = await pool.fetch(
                f"""
                SELECT id::text, title, excerpt, content, published_at
                FROM posts
                {where_clause}
                ORDER BY published_at DESC NULLS LAST
                """
            )
            if not rows:
                click.echo("No posts match — nothing to backfill.")
                return

            click.secho(
                f"Found {len(rows)} published posts matching --since={since}.",
                fg="cyan",
            )

            if dry_run:
                for r in rows:
                    click.echo(f"  {r['published_at']}  {r['title'][:70]}  [{r['id']}]")
                click.echo()
                click.echo("(--dry-run — no embeddings written)")
                return

            embeddings_db = EmbeddingsDatabase(pool)

            # OllamaClient reads OLLAMA_URL or site_config — for a CLI run
            # from the host, OLLAMA_URL env var is the most reliable path.
            # If site_config hasn't been loaded, fall back to localhost.
            ollama_url = _os.getenv("OLLAMA_URL") or "http://localhost:11434"
            ollama = OllamaClient(base_url=ollama_url)
            svc = EmbeddingService(ollama_client=ollama, embeddings_db=embeddings_db)

            embedded, skipped, failed = 0, 0, 0
            with click.progressbar(rows, label="Embedding posts") as bar:
                for row in bar:
                    try:
                        eid = await svc.embed_post({
                            "id": row["id"],
                            "title": row["title"],
                            "excerpt": row["excerpt"] or "",
                            "content": row["content"] or "",
                        })
                        if eid:
                            embedded += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        failed += 1
                        click.echo(f"\n  FAILED for {row['id']}: {e}", err=True)

            await ollama.close()
            click.secho(
                f"Done. embedded={embedded}  skipped (unchanged)={skipped}  "
                f"failed={failed}",
                fg="green" if failed == 0 else "yellow",
            )
        finally:
            await pool.close()

    _run(_backfill())
