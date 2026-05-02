"""`poindexter experiments` — operate on the experiments + experiment_assignments tables.

A/B experiment harness CLI. Wraps :class:`services.experiment_service.ExperimentService`
so operators can declare experiments, flip status, inspect assignments, and conclude
experiments without touching SQL.

The harness itself (assign / record_outcome on the hot path) is wired into the
content pipeline via ``content_router_service.process_content_generation_task``;
this CLI is the operator-facing half.

All commands go directly to the DB via the same DSN resolution pattern as
``poindexter webhooks``. No HTTP layer — the table is small, operator-only,
and doesn't need the worker roundtrip.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click


from poindexter.cli._bootstrap import resolve_dsn as _dsn  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _open_pool():
    """Open a small asyncpg pool — the ExperimentService takes a pool, not
    a connection. Keeps min/max=1 so the CLI exits cleanly without hanging
    on idle connections.
    """
    import asyncpg
    return await asyncpg.create_pool(_dsn(), min_size=1, max_size=1)


def _make_service(pool):
    """Construct an ExperimentService with a stub site_config.

    The service doesn't currently consult site_config for any of its
    methods — kept on the constructor for forward-compat with the broader
    DI sweep. Pass an empty dict-backed shim so we don't pull in the
    real site_config module from the CLI process.
    """
    from services.experiment_service import ExperimentService

    class _StubSiteConfig:
        def get(self, key: str, default: Any = None) -> Any:
            return default

    return ExperimentService(site_config=_StubSiteConfig(), pool=pool)


@click.group(
    name="experiments",
    help="Manage A/B experiments (variants, assignments, outcomes).",
)
def experiments_group() -> None:
    pass


@experiments_group.command("list")
@click.option(
    "--status", default="",
    type=click.Choice(["", "draft", "running", "paused", "complete"]),
    help="Filter by status.",
)
@click.option("--json", "json_output", is_flag=True)
def experiments_list(status: str, json_output: bool) -> None:
    """List experiments with current status + assignment counts."""

    async def _run_list():
        import asyncpg
        conn = await asyncpg.connect(_dsn())
        try:
            where = ""
            args: list[Any] = []
            if status:
                where = "WHERE e.status = $1"
                args.append(status)
            rows = await conn.fetch(
                f"""
                SELECT e.key, e.description, e.status, e.assignment_field,
                       e.created_at, e.started_at, e.completed_at,
                       e.winner_variant,
                       (
                         SELECT COUNT(*) FROM experiment_assignments a
                         WHERE a.experiment_id = e.id
                       ) AS assignments
                  FROM experiments e
                  {where}
              ORDER BY e.created_at DESC
                """,
                *args,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    try:
        rows = _run(_run_list())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("(no experiments)")
        return

    click.echo(
        f"{'KEY':<32} {'STATUS':<10} {'FIELD':<12} {'ASSIGNS':<8} {'WINNER':<16}"
    )
    for r in rows:
        color = {
            "running": "green",
            "draft": "yellow",
            "paused": "yellow",
            "complete": "cyan",
        }.get(r["status"], "white")
        winner = r["winner_variant"] or "—"
        line = (
            f"{r['key']:<32} {r['status']:<10} "
            f"{r['assignment_field']:<12} {r['assignments']:<8} {winner:<16}"
        )
        click.secho(line, fg=color)


@experiments_group.command("show")
@click.argument("key")
@click.option("--json", "json_output", is_flag=True)
def experiments_show(key: str, json_output: bool) -> None:
    """Show full details of an experiment including variants and metadata."""

    async def _run_show():
        import asyncpg
        conn = await asyncpg.connect(_dsn())
        try:
            row = await conn.fetchrow(
                """
                SELECT id::text AS id, key, description, status, variants,
                       assignment_field, created_at, started_at,
                       completed_at, winner_variant
                  FROM experiments
                 WHERE key = $1
                """,
                key,
            )
            if not row:
                return None
            data = dict(row)
            # variants comes back as str/jsonb depending on asyncpg build.
            v = data["variants"]
            if isinstance(v, str):
                try:
                    data["variants"] = json.loads(v)
                except json.JSONDecodeError:
                    pass
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM experiment_assignments WHERE experiment_id = $1::uuid",
                data["id"],
            )
            data["assignment_count"] = int(count or 0)
            return data
        finally:
            await conn.close()

    try:
        row = _run(_run_show())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not row:
        click.echo(f"(no experiment named {key!r})", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(row, indent=2, default=str))
        return

    click.secho(f"{row['key']}", fg="cyan")
    click.echo(f"  description       {row['description']}")
    click.echo(f"  status            {row['status']}")
    click.echo(f"  assignment_field  {row['assignment_field']}")
    click.echo(f"  assignments       {row['assignment_count']}")
    click.echo(f"  created_at        {row['created_at']}")
    click.echo(f"  started_at        {row['started_at'] or '—'}")
    click.echo(f"  completed_at      {row['completed_at'] or '—'}")
    click.echo(f"  winner_variant    {row['winner_variant'] or '—'}")
    click.echo("  variants:")
    for v in row.get("variants") or []:
        cfg = v.get("config", {})
        cfg_str = json.dumps(cfg, separators=(",", ":")) if cfg else "{}"
        click.echo(
            f"    - {v.get('key', '?'):<16} weight={v.get('weight', '?'):<3} config={cfg_str}"
        )


@experiments_group.command("create")
@click.option("--key", required=True, help="Stable slug for this experiment.")
@click.option("--description", required=True, help="Human description.")
@click.option(
    "--variants", required=True,
    help=(
        "JSON list of variants — each is {key, weight, config}. Example: "
        "'[{\"key\":\"control\",\"weight\":50,\"config\":{}}, "
        "{\"key\":\"variant_a\",\"weight\":50,\"config\":{\"writer_model\":\"glm-4.7-5090\"}}]'"
    ),
)
@click.option(
    "--assignment-field", default="task_id", show_default=True,
    help="Which context field to hash for sticky assignment.",
)
@click.option(
    "--start", is_flag=True,
    help="Skip draft and create directly in 'running' status.",
)
def experiments_create(
    key: str,
    description: str,
    variants: str,
    assignment_field: str,
    start: bool,
) -> None:
    """Create a new experiment row.

    Variants are validated by ExperimentService.create() — must be ≥2,
    weights must sum to ~100, each entry needs key/weight/config.
    """
    try:
        parsed = json.loads(variants)
    except json.JSONDecodeError as e:
        click.echo(f"Error: invalid --variants JSON: {e}", err=True)
        sys.exit(1)

    async def _run_create():
        pool = await _open_pool()
        try:
            svc = _make_service(pool)
            return await svc.create(
                key=key,
                description=description,
                variants=parsed,
                assignment_field=assignment_field,
                status="running" if start else "draft",
            )
        finally:
            await pool.close()

    try:
        new_id = _run(_run_create())
    except ValueError as e:
        # ExperimentService.create raises ValueError on validation failure.
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    state = "running" if start else "draft"
    click.secho(f"Created experiment {key!r} (id={new_id}, status={state})", fg="green")


@experiments_group.command("start")
@click.argument("key")
def experiments_start(key: str) -> None:
    """Flip an experiment from draft → running.

    Sets started_at to NOW() if it isn't already set. No-op + warning if
    the experiment doesn't exist or is already running/complete.
    """
    async def _run_start():
        import asyncpg
        conn = await asyncpg.connect(_dsn())
        try:
            result = await conn.execute(
                """
                UPDATE experiments
                   SET status = 'running',
                       started_at = COALESCE(started_at, NOW())
                 WHERE key = $1 AND status = 'draft'
                """,
                key,
            )
            return result
        finally:
            await conn.close()

    try:
        result = _run(_run_start())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if result.endswith(" 0"):
        click.echo(
            f"(no draft experiment named {key!r} — check `poindexter experiments show {key}`)",
            err=True,
        )
        sys.exit(1)
    click.secho(f"{key}: started", fg="green")


@experiments_group.command("pause")
@click.argument("key")
def experiments_pause(key: str) -> None:
    """Flip an experiment from running → paused.

    Existing assignments stay observable. New ``assign()`` calls return
    None so the pipeline falls back to default (un-experimented) config.
    """
    async def _run_pause():
        import asyncpg
        conn = await asyncpg.connect(_dsn())
        try:
            result = await conn.execute(
                "UPDATE experiments SET status = 'paused' WHERE key = $1 AND status = 'running'",
                key,
            )
            return result
        finally:
            await conn.close()

    try:
        result = _run(_run_pause())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if result.endswith(" 0"):
        click.echo(f"(no running experiment named {key!r})", err=True)
        sys.exit(1)
    click.secho(f"{key}: paused", fg="yellow")


@experiments_group.command("assign")
@click.argument("subject_id")
@click.argument("experiment_key")
def experiments_assign(subject_id: str, experiment_key: str) -> None:
    """Manually assign a subject to a variant.

    Useful for backfilling assignments for tasks that ran before the
    experiment was started, or for testing the bucketing logic. Sticky
    — re-running with the same subject_id returns the prior assignment.
    """
    async def _run_assign():
        pool = await _open_pool()
        try:
            svc = _make_service(pool)
            return await svc.assign(
                experiment_key=experiment_key,
                subject_id=subject_id,
            )
        finally:
            await pool.close()

    try:
        variant = _run(_run_assign())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if variant is None:
        click.echo(
            f"(no assignment — experiment {experiment_key!r} is not running)",
            err=True,
        )
        sys.exit(1)
    click.secho(f"{subject_id} → {variant}", fg="green")


@experiments_group.command("report")
@click.argument("key")
@click.option("--json", "json_output", is_flag=True)
def experiments_report(key: str, json_output: bool) -> None:
    """Per-variant rollup of n + average numeric metrics.

    Aggregates whatever metric keys are present in the JSONB ``metrics``
    column on each assignment row. Only numeric values are averaged —
    string / bool / nested values are skipped.
    """
    async def _run_report():
        pool = await _open_pool()
        try:
            svc = _make_service(pool)
            return await svc.summary(key)
        finally:
            await pool.close()

    try:
        report = _run(_run_report())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not report:
        click.echo(
            f"(no data — experiment {key!r} has no assignments yet, or it doesn't exist)",
            err=True,
        )
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(report, indent=2, default=str))
        return

    click.secho(f"Experiment report: {key}", fg="cyan")
    click.echo()
    for variant, data in sorted(report.items()):
        n = data.get("n", 0)
        click.secho(f"  {variant}  (n={n})", fg="white", bold=True)
        metrics = data.get("metrics", {}) or {}
        if not metrics:
            click.echo("    (no numeric metrics recorded)")
            continue
        for metric, value in sorted(metrics.items()):
            click.echo(f"    {metric:<32} {value:.4f}")


@experiments_group.command("conclude")
@click.argument("key")
@click.option(
    "--winner", required=True,
    help="Which variant key won — must match one of the declared variants.",
)
def experiments_conclude(key: str, winner: str) -> None:
    """Mark an experiment complete with the winning variant.

    No auto-promotion — the operator promotes the winning config into
    production app_settings manually so wins can be reviewed.
    """
    async def _run_conclude():
        pool = await _open_pool()
        try:
            svc = _make_service(pool)
            await svc.conclude(experiment_key=key, winner_variant=winner)
        finally:
            await pool.close()

    try:
        _run(_run_conclude())
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.secho(f"{key}: concluded (winner={winner})", fg="cyan")
