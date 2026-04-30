"""``poindexter topics`` — operator commands for the niche topic-discovery batch.

Wraps ``services.topic_batch_service.TopicBatchService`` and
``services.niche_service.NicheService`` so the operator can:

- ``poindexter topics show-batch --niche <slug>``  — peek at the open batch
- ``poindexter topics rank-batch <id> --order ...`` — set operator ranking
- ``poindexter topics edit-winner <id> [--topic ...] [--angle ...]``
                                                    — override the rank-1 row
- ``poindexter topics resolve-batch <id>``         — advance rank-1 to pipeline
- ``poindexter topics reject-batch <id> [--reason ...]`` — discard the batch

Plus a ``niche`` subgroup for read-only inspection of niche configuration:

- ``poindexter topics niche list``   — every active niche
- ``poindexter topics niche show <slug>`` — full config as JSON

The CLI is the canonical operator surface; MCP tools and any future
REST endpoints call into the same service modules.
"""

from __future__ import annotations

import asyncio
import json
import os

import click


# ---------------------------------------------------------------------------
# Shared helpers — same env-var ladder used by the rest of the poindexter CLI.
# ---------------------------------------------------------------------------


def _dsn() -> str:
    """Resolve the PostgreSQL DSN from the standard env-var ladder."""
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
    return dsn


# ---------------------------------------------------------------------------
# Group root
# ---------------------------------------------------------------------------


@click.group(
    name="topics",
    help=(
        "Operator commands for the niche topic-discovery batch.\n\n"
        "Drives the discover -> rank -> batch -> gate flow exposed by "
        "``services.topic_batch_service.TopicBatchService``."
    ),
)
def topics_group() -> None:
    pass


# ---------------------------------------------------------------------------
# topics show-batch
# ---------------------------------------------------------------------------


@topics_group.command("show-batch")
@click.option("--niche", required=True, help="Niche slug.")
def show_batch(niche: str) -> None:
    """Show the current open batch for a niche."""
    async def _impl():
        import asyncpg
        from services.niche_service import NicheService
        from services.topic_batch_service import TopicBatchService

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            n = await NicheService(pool).get_by_slug(niche)
            if not n:
                raise click.ClickException(f"unknown niche: {niche}")
            async with pool.acquire() as conn:
                bid = await conn.fetchval(
                    "SELECT id FROM topic_batches "
                    "WHERE niche_id = $1 AND status = 'open'",
                    n.id,
                )
            if bid is None:
                click.echo(f"No open batch for niche {niche}.")
                return
            view = await TopicBatchService(pool).show_batch(batch_id=bid)
            click.echo(f"Batch {view.id} (status={view.status})")
            for c in view.candidates:
                marker = (
                    f"#{c.operator_rank}"
                    if c.operator_rank
                    else f"sys#{c.rank_in_batch}"
                )
                click.echo(
                    f"  {marker:6s} [{c.kind:8s}] "
                    f"eff={c.effective_score:5.1f} — {c.title}"
                )
        finally:
            await pool.close()

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# topics rank-batch
# ---------------------------------------------------------------------------


@topics_group.command("rank-batch")
@click.argument("batch_id")
@click.option(
    "--order",
    required=True,
    help="Comma-separated candidate ids in your preferred order, best-first.",
)
def rank_batch(batch_id: str, order: str) -> None:
    """Set operator ranking for a batch's candidates."""
    async def _impl():
        import asyncpg
        from services.topic_batch_service import TopicBatchService

        ids = [s.strip() for s in order.split(",") if s.strip()]
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            await TopicBatchService(pool).rank_batch(
                batch_id=batch_id, ordered_candidate_ids=ids,
            )
            click.echo(f"Ranked {len(ids)} candidates in batch {batch_id}")
        finally:
            await pool.close()

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# topics edit-winner
# ---------------------------------------------------------------------------


@topics_group.command("edit-winner")
@click.argument("batch_id")
@click.option("--topic", help="Override the winner's title.")
@click.option("--angle", help="Override the winner's angle/summary.")
def edit_winner(batch_id: str, topic: str | None, angle: str | None) -> None:
    """Edit the title/angle of the rank-1 candidate before resolution."""
    if not topic and not angle:
        raise click.UsageError("Provide --topic and/or --angle.")

    async def _impl():
        import asyncpg
        from services.topic_batch_service import TopicBatchService

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            await TopicBatchService(pool).edit_winner(
                batch_id=batch_id, topic=topic, angle=angle,
            )
            click.echo("Edited winner.")
        finally:
            await pool.close()

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# topics resolve-batch
# ---------------------------------------------------------------------------


@topics_group.command("resolve-batch")
@click.argument("batch_id")
def resolve_batch(batch_id: str) -> None:
    """Resolve a batch — advance the rank-1 candidate to the pipeline."""
    async def _impl():
        import asyncpg
        from services.topic_batch_service import TopicBatchService

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            await TopicBatchService(pool).resolve_batch(batch_id=batch_id)
            click.echo(f"Resolved {batch_id}")
        finally:
            await pool.close()

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# topics reject-batch
# ---------------------------------------------------------------------------


@topics_group.command("reject-batch")
@click.argument("batch_id")
@click.option("--reason", default="", help="Optional reason text.")
def reject_batch(batch_id: str, reason: str) -> None:
    """Reject a batch — discard candidates, allow a fresh sweep."""
    async def _impl():
        import asyncpg
        from services.topic_batch_service import TopicBatchService

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            await TopicBatchService(pool).reject_batch(
                batch_id=batch_id, reason=reason,
            )
            click.echo(f"Rejected {batch_id}")
        finally:
            await pool.close()

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# topics niche subgroup
# ---------------------------------------------------------------------------


@topics_group.group("niche")
def niche_group() -> None:
    """Manage niche configurations."""
    pass


@niche_group.command("list")
def niche_list() -> None:
    """List every active niche."""
    async def _impl():
        import asyncpg
        from services.niche_service import NicheService

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            for n in await NicheService(pool).list_active():
                click.echo(
                    f"{n.slug:20s} {n.name:30s} mode={n.writer_rag_mode}"
                )
        finally:
            await pool.close()

    asyncio.run(_impl())


@niche_group.command("show")
@click.argument("slug")
def niche_show(slug: str) -> None:
    """Print full niche config (slug, goals, sources) as JSON."""
    async def _impl():
        import asyncpg
        from services.niche_service import NicheService

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            svc = NicheService(pool)
            n = await svc.get_by_slug(slug)
            if not n:
                raise click.ClickException(f"unknown niche: {slug}")
            click.echo(
                json.dumps(
                    {
                        "slug": n.slug,
                        "name": n.name,
                        "active": n.active,
                        "writer_rag_mode": n.writer_rag_mode,
                        "batch_size": n.batch_size,
                        "discovery_cadence_minute_floor":
                            n.discovery_cadence_minute_floor,
                        "audience_tags": n.target_audience_tags,
                        "goals": [
                            {"type": g.goal_type, "weight": g.weight_pct}
                            for g in await svc.get_goals(n.id)
                        ],
                        "sources": [
                            {
                                "name": s.source_name,
                                "enabled": s.enabled,
                                "weight": s.weight_pct,
                            }
                            for s in await svc.get_sources(n.id)
                        ],
                    },
                    indent=2,
                )
            )
        finally:
            await pool.close()

    asyncio.run(_impl())


__all__ = ["topics_group"]
