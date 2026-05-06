"""``poindexter topics`` — operator commands for the niche topic-discovery batch.

Wraps ``services.topic_batch_service.TopicBatchService`` and
``services.niche_service.NicheService`` so the operator can:

- ``poindexter topics sweep --niche <slug>``       — fire a discovery sweep
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
import re
from uuid import UUID

import click


_MARKER_RE = re.compile(r"^(sys)?#(\d+)$")


def _resolve_order_tokens(tokens, candidates):
    """Translate a list of `--order` tokens into candidate UUIDs.

    Each token is one of:
      - ``sys#N``  → candidate with ``rank_in_batch == N``
      - ``#N``     → candidate with ``operator_rank == N``
      - anything else is assumed to already be a UUID and passed through

    Raises ``click.ClickException`` if a marker doesn't resolve.
    """
    by_sys = {c.rank_in_batch: c.id for c in candidates}
    by_op = {c.operator_rank: c.id for c in candidates if c.operator_rank}
    resolved = []
    for tok in tokens:
        m = _MARKER_RE.match(tok)
        if not m:
            resolved.append(tok)
            continue
        kind, num = m.group(1), int(m.group(2))
        lookup = by_sys if kind == "sys" else by_op
        cid = lookup.get(num)
        if cid is None:
            label = f"sys#{num}" if kind == "sys" else f"#{num}"
            raise click.ClickException(
                f"no candidate matches {label} in this batch"
            )
        resolved.append(cid)
    return resolved


# ---------------------------------------------------------------------------
# Shared helpers — same env-var ladder used by the rest of the poindexter CLI.
# ---------------------------------------------------------------------------


from poindexter.cli._bootstrap import resolve_dsn as _dsn  # noqa: E402


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
# topics sweep
# ---------------------------------------------------------------------------


@topics_group.command("sweep")
@click.option("--niche", required=True, help="Niche slug.")
def sweep(niche: str) -> None:
    """Fire a topic-discovery sweep on demand for a niche.

    Calls the same ``TopicBatchService.run_sweep`` the scheduler's
    ``run_niche_topic_sweep`` job uses, so behaviour matches the
    background sweep exactly: cadence-floor + open-batch checks still
    apply, and the run is recorded in ``discovery_runs``.

    Prints either the new batch id + candidate count + top-ranked title,
    or the reason a sweep was skipped (cadence floor / open batch).
    """
    async def _impl():
        import asyncpg
        from services.niche_service import NicheService
        from services.topic_batch_service import TopicBatchService

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            n = await NicheService(pool).get_by_slug(niche)
            if not n:
                raise click.ClickException(f"unknown niche: {niche}")

            svc = TopicBatchService(pool)
            snapshot = await svc.run_sweep(niche_id=n.id)

            click.echo(f"Niche: {n.slug} ({n.name})")
            if snapshot is None:
                # run_sweep returns None on the two known short-circuits.
                # Distinguish them so the operator knows which gate hit.
                async with pool.acquire() as conn:
                    open_batch = await conn.fetchval(
                        "SELECT id FROM topic_batches "
                        "WHERE niche_id = $1 AND status = 'open'",
                        n.id,
                    )
                if open_batch is not None:
                    click.echo(
                        f"No new batch — open batch already exists: {open_batch}"
                    )
                else:
                    click.echo(
                        "No new batch — discovery cadence floor not elapsed "
                        f"(niche.discovery_cadence_minute_floor="
                        f"{n.discovery_cadence_minute_floor}m)"
                    )
                return

            view = await svc.show_batch(batch_id=snapshot.id)
            top_title = view.candidates[0].title if view.candidates else "(none)"
            click.echo(
                f"Created batch {snapshot.id} — "
                f"{snapshot.candidate_count} candidates, top: {top_title}"
            )
        finally:
            await pool.close()

    asyncio.run(_impl())


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
                    f"eff={c.effective_score:5.1f}  {c.id}  — {c.title}"
                )
        finally:
            await pool.close()

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# topics rank-batch
# ---------------------------------------------------------------------------


@topics_group.command("rank-batch")
@click.argument("batch_id", type=click.UUID)
@click.option(
    "--order",
    required=True,
    help=(
        "Comma-separated candidate identifiers in preferred order, best-first. "
        "Accepts UUIDs, ``sys#N`` (rank_in_batch) or ``#N`` (operator_rank) "
        "markers — same labels printed by ``topics show-batch``."
    ),
)
def rank_batch(batch_id: UUID, order: str) -> None:
    """Set operator ranking for a batch's candidates."""
    async def _impl():
        import asyncpg
        from services.topic_batch_service import TopicBatchService

        tokens = [s.strip() for s in order.split(",") if s.strip()]
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            svc = TopicBatchService(pool)
            view = await svc.show_batch(batch_id=batch_id)
            ids = _resolve_order_tokens(tokens, view.candidates)
            await svc.rank_batch(
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
@click.argument("batch_id", type=click.UUID)
@click.option("--topic", help="Override the winner's title.")
@click.option("--angle", help="Override the winner's angle/summary.")
def edit_winner(batch_id: UUID, topic: str | None, angle: str | None) -> None:
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
@click.argument("batch_id", type=click.UUID)
def resolve_batch(batch_id: UUID) -> None:
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
@click.argument("batch_id", type=click.UUID)
@click.option("--reason", default="", help="Optional reason text.")
def reject_batch(batch_id: UUID, reason: str) -> None:
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
