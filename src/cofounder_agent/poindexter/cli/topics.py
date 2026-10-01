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

Plus a ``niche`` subgroup for niche configuration:

- ``poindexter topics niche list``   — every active niche
- ``poindexter topics niche show <slug>`` — full config as JSON
- ``poindexter topics niche set-scope <slug> --subject ... --exclude ...``
                                      — what the niche covers (poindexter#1127)
- ``poindexter topics niche check-scope <slug>`` — judge the current pool
                                      against the scope, changing nothing
- ``poindexter topics niche set-goals <slug> NICHE_DEPTH=35 AUTHORITY=25 ...``
- ``poindexter topics niche set-sources <slug> internal_rag=25 devto=15:off ...``
- ``poindexter topics niche set-writer-prompt <slug> --file prompt.md``

The CLI is the canonical operator surface; MCP tools and any future
REST endpoints call into the same service modules.
"""

from __future__ import annotations

import asyncio
import json
import re
from uuid import UUID

import click

from poindexter.cli._lifecycle import container_for_cli

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


from poindexter.cli._bootstrap import close_cli_pool, open_cli_pool  # noqa: E402

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

        from poindexter.services.niche_service import NicheService
        from poindexter.services.topic_batch_service import TopicBatchService

        pool = await open_cli_pool()
        try:
            # SiteConfig constructor-DI migration PR 2 canary (design doc:
            # ``docs/architecture/2026-05-28-site-config-di-migration.md``).
            # `container_for_cli` builds an AppContainer for the
            # duration of this command. Today the container holds zero
            # services — TopicBatchService still constructs inline below.
            # PR 3+ migrates TopicBatchService into the container and
            # the body of this command swaps `TopicBatchService(pool)`
            # for `container.topic_batch_service`.
            async with container_for_cli(pool) as container:
                # Best-effort audit_log entry so operators can verify
                # the wireup in production via a single SQL query:
                #     SELECT count(*) FROM audit_log
                #     WHERE event_type = 'cli_container_built'
                # When a service migrates we'll bump the count in
                # ``details.container_services`` automatically since
                # the field reads from the live container.
                try:
                    # Count is computed against the container's
                    # ``cached_property`` service entries — anything
                    # exposed publicly (no leading underscore) on the
                    # container class beyond its declared dataclass
                    # fields. Today: 0; future PRs: 1, 2, ... per
                    # migrated service.
                    from functools import cached_property as _cached_property

                    _container_cls = type(container)
                    _service_count = sum(
                        1
                        for _name, _attr in vars(_container_cls).items()
                        if isinstance(_attr, _cached_property)
                        and not _name.startswith("_")
                    )
                    async with pool.acquire() as conn:
                        await conn.execute(
                            """
                            INSERT INTO audit_log
                                (event_type, source, details, severity)
                            VALUES ($1, $2, $3::jsonb, $4)
                            """,
                            "cli_container_built",
                            "poindexter.cli.topics.sweep",
                            json.dumps(
                                {
                                    "command": "topics sweep",
                                    "container_services": _service_count,
                                    "niche": niche,
                                }
                            ),
                            "info",
                        )
                    click.echo(
                        f"[di-migration] AppContainer built "
                        f"({_service_count} services wired)"
                    )
                except Exception as audit_exc:  # noqa: BLE001
                    # Audit write is observability, never blocks the
                    # command. Log to stderr and continue — the sweep
                    # still runs.
                    click.echo(
                        f"[di-migration] audit_log write failed "
                        f"(non-blocking): {audit_exc}",
                        err=True,
                    )

                n = await NicheService(pool).get_by_slug(niche)
                if not n:
                    raise click.ClickException(f"unknown niche: {niche}")

                # #272 Phase-2d: TopicBatchService requires an explicit
                # site_config — pass the container's wired instance.
                svc = TopicBatchService(pool, site_config=container.site_config)
                snapshot = await svc.run_sweep(niche_id=n.id)

                click.echo(f"Niche: {n.slug} ({n.name})")
                if snapshot is None:
                    # run_sweep returns None on the two known
                    # short-circuits. Distinguish them so the operator
                    # knows which gate hit.
                    async with pool.acquire() as conn:
                        open_batch = await conn.fetchval(
                            "SELECT id FROM topic_batches "
                            "WHERE niche_id = $1 AND status = 'open'",
                            n.id,
                        )
                    if open_batch is not None:
                        click.echo(
                            f"No new batch — open batch already exists: "
                            f"{open_batch}"
                        )
                    else:
                        click.echo(
                            "No new batch — discovery cadence floor not "
                            f"elapsed (niche.discovery_cadence_minute_floor="
                            f"{n.discovery_cadence_minute_floor}m)"
                        )
                    return

                view = await svc.show_batch(batch_id=snapshot.id)
                top_title = (
                    view.candidates[0].title if view.candidates else "(none)"
                )
                click.echo(
                    f"Created batch {snapshot.id} — "
                    f"{snapshot.candidate_count} candidates, top: {top_title}"
                )
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# topics show-batch
# ---------------------------------------------------------------------------


@topics_group.command("show-batch")
@click.option("--niche", required=True, help="Niche slug.")
def show_batch(niche: str) -> None:
    """Show the current open batch for a niche."""
    async def _impl():

        from poindexter.services.niche_service import NicheService
        from poindexter.services.topic_batch_service import TopicBatchService

        pool = await open_cli_pool()
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
            # #272 Phase-2d: TopicBatchService requires an explicit
            # site_config — build one from the lifespan container.
            async with container_for_cli(pool) as container:
                view = await TopicBatchService(
                    pool, site_config=container.site_config,
                ).show_batch(batch_id=bid)
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
            await close_cli_pool(pool)

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

        from poindexter.services.topic_batch_service import TopicBatchService

        tokens = [s.strip() for s in order.split(",") if s.strip()]
        pool = await open_cli_pool()
        try:
            # #272 Phase-2d: TopicBatchService requires an explicit site_config.
            async with container_for_cli(pool) as container:
                svc = TopicBatchService(pool, site_config=container.site_config)
                view = await svc.show_batch(batch_id=batch_id)
                ids = _resolve_order_tokens(tokens, view.candidates)
                await svc.rank_batch(
                    batch_id=batch_id, ordered_candidate_ids=ids,
                )
                click.echo(f"Ranked {len(ids)} candidates in batch {batch_id}")
        finally:
            await close_cli_pool(pool)

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

        from poindexter.services.topic_batch_service import TopicBatchService

        pool = await open_cli_pool()
        try:
            # #272 Phase-2d: TopicBatchService requires an explicit site_config.
            async with container_for_cli(pool) as container:
                await TopicBatchService(
                    pool, site_config=container.site_config,
                ).edit_winner(
                    batch_id=batch_id, topic=topic, angle=angle,
                )
                click.echo("Edited winner.")
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


# ---------------------------------------------------------------------------
# topics resolve-batch
# ---------------------------------------------------------------------------


@topics_group.command("resolve-batch")
@click.argument("batch_id", type=click.UUID)
def resolve_batch(batch_id: UUID) -> None:
    """Resolve a batch — advance the rank-1 candidate to the pipeline."""
    async def _impl():

        from poindexter.services.topic_batch_service import TopicBatchService

        pool = await open_cli_pool()
        try:
            # #272 Phase-2d: TopicBatchService requires an explicit site_config.
            async with container_for_cli(pool) as container:
                await TopicBatchService(
                    pool, site_config=container.site_config,
                ).resolve_batch(batch_id=batch_id)
                click.echo(f"Resolved {batch_id}")
        finally:
            await close_cli_pool(pool)

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

        from poindexter.services.topic_batch_service import TopicBatchService

        pool = await open_cli_pool()
        try:
            # #272 Phase-2d: TopicBatchService requires an explicit site_config.
            async with container_for_cli(pool) as container:
                await TopicBatchService(
                    pool, site_config=container.site_config,
                ).reject_batch(
                    batch_id=batch_id, reason=reason,
                )
                click.echo(f"Rejected {batch_id}")
        finally:
            await close_cli_pool(pool)

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

        from poindexter.services.niche_service import NicheService

        pool = await open_cli_pool()
        try:
            for n in await NicheService(pool).list_active():
                click.echo(f"{n.slug:20s} {n.name:30s}")
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


@niche_group.command("show")
@click.argument("slug")
def niche_show(slug: str) -> None:
    """Print full niche config (slug, goals, sources) as JSON."""
    async def _impl():

        from poindexter.services.niche_service import NicheService

        pool = await open_cli_pool()
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
                        "topic_scope": {
                            "subject": n.topic_subject,
                            "exclusions": list(n.topic_exclusions),
                            "filter": n.topic_scope_filter,
                        },
                        "writer_prompt_override_chars": len(
                            n.writer_prompt_override or ""
                        ),
                    },
                    indent=2,
                )
            )
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


@niche_group.command("set-cadence")
@click.argument("niche")
@click.argument("target", type=float)
def niche_set_cadence(niche: str, target: float) -> None:
    """Set a niche's cadence target (posts/day).

    Read by ``probe_cadence_slo`` (poindexter/brain/health_probes.py) as a per-niche
    override of the site-wide ``cadence_slo_expected_posts_per_day``
    app_setting.
    """
    async def _impl():

        from poindexter.services.niche_service import NicheService

        pool = await open_cli_pool()
        try:
            svc = NicheService(pool)
            n = await svc.get_by_slug(niche)
            if not n:
                raise click.ClickException(f"unknown niche: {niche}")
            try:
                updated = await svc.set_cadence_target(n.id, target)
            except ValueError as e:
                raise click.ClickException(str(e)) from e
            click.echo(
                f"Set cadence target for {updated.slug}: "
                f"{updated.cadence_target_posts_per_day}/day"
            )
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


def _parse_weight_pairs(pairs: tuple[str, ...]) -> list[tuple[str, int, bool]]:
    """Parse ``NAME=WEIGHT`` or ``NAME=WEIGHT:off`` into
    ``(name, weight, enabled)``. Raises ``click.BadParameter`` on bad input."""
    parsed: list[tuple[str, int, bool]] = []
    seen: set[str] = set()
    for pair in pairs:
        name, sep, rest = pair.partition("=")
        name = name.strip()
        if not sep or not name:
            raise click.BadParameter(f"expected NAME=WEIGHT, got {pair!r}")
        weight_text, _, flag = rest.partition(":")
        try:
            weight = int(weight_text.strip())
        except ValueError as exc:
            raise click.BadParameter(f"weight must be an integer in {pair!r}") from exc
        if weight < 0:
            raise click.BadParameter(f"weight must be >= 0 in {pair!r}")
        flag = flag.strip().lower()
        if flag not in ("", "on", "off"):
            raise click.BadParameter(f"expected ':on' or ':off' in {pair!r}")
        if name in seen:
            raise click.BadParameter(f"{name!r} is listed twice")
        seen.add(name)
        parsed.append((name, weight, flag != "off"))
    if not parsed:
        raise click.BadParameter("give at least one NAME=WEIGHT")
    return parsed


def _merge_scope(
    *,
    current_subject: str | None,
    current_exclusions: tuple[str, ...],
    current_filter: bool,
    subject: str | None,
    exclude: tuple[str, ...],
    remove_exclude: tuple[str, ...],
    clear_exclusions: bool,
    scope_filter: bool | None,
    clear: bool,
) -> tuple[str | None, list[str], bool]:
    """Apply ``set-scope`` flags to the niche's current scope. Unchanged
    parts are kept, so each flag edits only what it names."""
    if clear:
        return None, [], current_filter if scope_filter is None else scope_filter
    new_subject = current_subject if subject is None else subject
    base = [] if clear_exclusions else list(current_exclusions)
    removed = {r.strip().lower() for r in remove_exclude}
    exclusions = [e for e in base if e.strip().lower() not in removed] + list(exclude)
    new_filter = current_filter if scope_filter is None else scope_filter
    return new_subject, exclusions, new_filter


@niche_group.command("set-scope")
@click.argument("slug")
@click.option("--subject", default=None,
              help="What the niche covers, in plain prose. Replaces the current subject.")
@click.option("--exclude", "exclude", multiple=True,
              help="An out-of-scope subject to add. Repeatable.")
@click.option("--remove-exclude", "remove_exclude", multiple=True,
              help="Remove an exclusion (exact text, case-insensitive). Repeatable.")
@click.option("--clear-exclusions", is_flag=True, help="Remove every exclusion first.")
@click.option("--filter/--no-filter", "scope_filter", default=None,
              help="Drop out-of-scope candidates before ranking (--filter), or only "
                   "use the subject to steer ranking (--no-filter).")
@click.option("--clear", is_flag=True, help="Remove the subject and exclusions.")
def niche_set_scope(
    slug: str, subject: str | None, exclude: tuple[str, ...],
    remove_exclude: tuple[str, ...], clear_exclusions: bool,
    scope_filter: bool | None, clear: bool,
) -> None:
    """Set what a niche's topics are about (poindexter#1127).

    With a subject set and the filter on, each sweep asks the structured
    model which candidates are in scope and drops the rest before ranking.
    The subject also steers the ranking prompt, the NICHE_DEPTH goal and
    internal story selection. Preview with ``check-scope`` first.
    """
    async def _impl():
        from poindexter.services.niche_service import NicheService

        pool = await open_cli_pool()
        try:
            svc = NicheService(pool)
            n = await svc.get_by_slug(slug)
            if not n:
                raise click.ClickException(f"unknown niche: {slug}")
            new_subject, exclusions, new_filter = _merge_scope(
                current_subject=n.topic_subject,
                current_exclusions=n.topic_exclusions,
                current_filter=n.topic_scope_filter,
                subject=subject, exclude=exclude, remove_exclude=remove_exclude,
                clear_exclusions=clear_exclusions, scope_filter=scope_filter,
                clear=clear,
            )
            try:
                updated = await svc.set_topic_scope(
                    n.id, subject=new_subject, exclusions=exclusions,
                    scope_filter=new_filter,
                )
            except ValueError as e:
                raise click.ClickException(str(e)) from e
            if not updated.has_topic_scope:
                click.echo(f"{updated.slug}: no topic scope (sweeps unfiltered)")
                return
            click.echo(f"{updated.slug} subject: {updated.topic_subject}")
            for exclusion in updated.topic_exclusions:
                click.echo(f"  exclude: {exclusion}")
            click.echo(
                "  filter: " + (
                    "on (out-of-scope candidates are dropped before ranking)"
                    if updated.topic_scope_filter
                    else "off (the subject only steers ranking)"
                )
            )
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


@niche_group.command("check-scope")
@click.argument("slug")
@click.option("--show", type=click.Choice(["all", "out", "in"]), default="all",
              help="Which verdicts to list.")
def niche_check_scope(slug: str, show: str) -> None:
    """Judge the niche's current topic pool against its scope.

    Read-only: nothing is dropped or written. Runs even when the filter is
    off, so you can see what turning it on would do.
    """
    async def _impl():
        from poindexter.services.niche_service import NicheService
        from poindexter.services.topic_batch_service import TopicBatchService

        pool = await open_cli_pool()
        try:
            async with container_for_cli(pool) as container:
                n = await NicheService(pool).get_by_slug(slug)
                if not n:
                    raise click.ClickException(f"unknown niche: {slug}")
                svc = TopicBatchService(pool, site_config=container.site_config)
                try:
                    rows, errors = await svc.preview_scope(niche_id=n.id)
                except ValueError as e:
                    raise click.ClickException(str(e)) from e
            counts = {"in": 0, "out": 0, "unjudged": 0}
            for row in rows:
                counts[row["verdict"]] += 1
                if show != "all" and row["verdict"] != show:
                    continue
                click.echo(f"{row['verdict']:8s} [{row['pool']}] {row['title']}")
            click.echo(
                f"{len(rows)} candidate(s): {counts['in']} in, {counts['out']} out, "
                f"{counts['unjudged']} unjudged"
                + ("" if n.topic_scope_filter else "  (filter is off: nothing is dropped)")
            )
            if errors:
                # Unjudged candidates are kept by the real sweep, so a check
                # that cannot reach its model filters nothing. Say why.
                click.echo(
                    f"The scope check failed {len(errors)} time(s), so "
                    f"{counts['unjudged']} candidate(s) are unjudged. First error: "
                    f"{errors[0]}",
                    err=True,
                )
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


@niche_group.command("set-goals")
@click.argument("slug")
@click.argument("pairs", nargs=-1, required=True)
def niche_set_goals(slug: str, pairs: tuple[str, ...]) -> None:
    """Replace a niche's ranking goals: ``GOAL=WEIGHT`` pairs summing to 100.

    Goals: TRAFFIC, EDUCATION, BRAND, AUTHORITY, REVENUE, COMMUNITY,
    NICHE_DEPTH. With a topic subject set, NICHE_DEPTH means "deep on the
    niche's subject".
    """
    async def _impl():
        from poindexter.services.niche_service import NicheGoal, NicheService

        goals = [
            NicheGoal(goal_type=name.upper(), weight_pct=weight)
            for name, weight, _ in _parse_weight_pairs(pairs)
        ]
        pool = await open_cli_pool()
        try:
            svc = NicheService(pool)
            n = await svc.get_by_slug(slug)
            if not n:
                raise click.ClickException(f"unknown niche: {slug}")
            try:
                await svc.set_goals(n.id, goals)
            except ValueError as e:
                raise click.ClickException(str(e)) from e
            for g in await svc.get_goals(n.id):
                click.echo(f"  {g.goal_type:12s} {g.weight_pct}")
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


@niche_group.command("set-sources")
@click.argument("slug")
@click.argument("pairs", nargs=-1, required=True)
def niche_set_sources(slug: str, pairs: tuple[str, ...]) -> None:
    """Replace a niche's source weights: ``NAME=WEIGHT`` or ``NAME=WEIGHT:off``.

    Sources not listed are removed from the niche.
    """
    async def _impl():
        from poindexter.services.niche_service import NicheService, NicheSource

        sources = [
            NicheSource(source_name=name, enabled=enabled, weight_pct=weight)
            for name, weight, enabled in _parse_weight_pairs(pairs)
        ]
        pool = await open_cli_pool()
        try:
            svc = NicheService(pool)
            n = await svc.get_by_slug(slug)
            if not n:
                raise click.ClickException(f"unknown niche: {slug}")
            await svc.set_sources(n.id, sources)
            for src in await svc.get_sources(n.id):
                state = "" if src.enabled else "  (off)"
                click.echo(f"  {src.source_name:16s} {src.weight_pct}{state}")
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


@niche_group.command("set-writer-prompt")
@click.argument("slug")
@click.option("--file", "path", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Read the prompt from this file.")
@click.option("--clear", is_flag=True, help="Remove the override.")
def niche_set_writer_prompt(slug: str, path: str | None, clear: bool) -> None:
    """Set or clear a niche's writer prompt override."""
    if bool(path) == clear:
        raise click.UsageError("give exactly one of --file or --clear")

    async def _impl():
        from pathlib import Path

        from poindexter.services.niche_service import NicheService

        # Exactly one of --file / --clear was given, so no path means --clear.
        prompt = None if path is None else Path(path).read_text(encoding="utf-8")
        pool = await open_cli_pool()
        try:
            svc = NicheService(pool)
            n = await svc.get_by_slug(slug)
            if not n:
                raise click.ClickException(f"unknown niche: {slug}")
            updated = await svc.set_writer_prompt(n.id, prompt)
            size = len(updated.writer_prompt_override or "")
            click.echo(
                f"{updated.slug}: writer prompt "
                + (f"set ({size} chars)" if size else "cleared")
            )
        finally:
            await close_cli_pool(pool)

    asyncio.run(_impl())


__all__ = ["topics_group"]
