"""``poindexter model-eval`` — operator surface for the model-eval loop.

Thin adapter over ``services.model_eval`` per the transport-adapter contract
(#1340): open a pool, build a ``SiteConfig``, delegate to the service, format
the result. No business logic or raw SQL here.

Subcommands (Plan 1 — reranker vertical slice):

- ``poindexter model-eval run --challenger <model> [--challenger <model2>]``
      bake the current ``rag_rerank_model`` champion off against challengers.
- ``poindexter model-eval status``
      show the latest recorded metric per model for the slot.

Pattern matches ``poindexter/cli/experiments.py``: Click group, lazy
``import asyncpg`` inside each ``_impl()``, DSN via
``poindexter.cli._bootstrap.resolve_dsn``, async impl wrapped in
``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import click

from poindexter.cli._bootstrap import resolve_dsn as _dsn

_DEFAULT_SLOT = "rag_rerank_model"


@click.group(
    name="model-eval",
    help=(
        "Champion-challenger model evaluation. Plan 1 covers the reranker "
        "slot; run a bakeoff and surface a promotion proposal."
    ),
)
def model_eval_group() -> None:
    pass


async def _load_cfg(pool: Any) -> Any:
    """Build a SiteConfig from app_settings; tolerate an unreachable DB so
    operator CLI flags still flow (mirrors schedule.py::_load_site_config)."""
    from services.site_config import SiteConfig

    cfg = SiteConfig(pool=pool)
    try:
        await cfg.load(pool)
    except Exception:  # noqa: BLE001
        # silent-ok: settings load is best-effort; run/status fail loud later
        # on a missing champion / Langfuse creds (the same DB), so a swallow
        # here can't mask a real misconfig — it only defers the loud error.
        pass
    return cfg


@model_eval_group.command("run")
@click.option(
    "--challenger",
    "challengers",
    multiple=True,
    required=True,
    help="Candidate model id to test against the champion. Repeatable.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of text.")
def model_eval_run(challengers: tuple[str, ...], json_output: bool) -> None:
    """Bake the current reranker champion (``rag_rerank_model``) off against
    ``--challenger``s. (Plan 1 covers the reranker slot; Plan 3 generalizes.)"""

    async def _impl() -> tuple[Any, Any]:
        import asyncpg

        from services.model_eval.bakeoff import run_reranker_bakeoff

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            cfg = await _load_cfg(pool)
            return await run_reranker_bakeoff(
                pool=pool, site_config=cfg, challengers=list(challengers)
            )
        finally:
            await pool.close()

    report, proposal = asyncio.run(_impl())

    if json_output:
        click.echo(
            json.dumps(
                {
                    "slot": report.slot,
                    "metric": report.metric_name,
                    "champion": report.champion,
                    "champion_score": report.champion_score,
                    "best_challenger": report.best_challenger,
                    "best_challenger_score": report.best_challenger_score,
                    "winner": report.winner,
                    "margin": report.margin,
                    "beats_margin": report.beats_margin,
                    "proposal_kind": proposal.kind if proposal else None,
                },
                indent=2,
                default=str,
            )
        )
        return

    click.echo(f"slot={report.slot}  metric={report.metric_name}")
    click.echo(f"  champion   {report.champion}: {report.champion_score:.4f}")
    if report.best_challenger is not None and report.best_challenger_score is not None:
        click.echo(
            f"  challenger {report.best_challenger}: "
            f"{report.best_challenger_score:.4f}  (margin {report.margin:+.2%})"
        )
    click.echo(f"  winner: {report.winner}  beats_margin={report.beats_margin}")
    if proposal is not None:
        click.echo(f"\nPromotion proposal ({proposal.kind}):\n")
        click.echo(proposal.body)
    else:
        click.echo("\nNo promotion — champion holds.")


@model_eval_group.command("status")
@click.option(
    "--slot",
    type=click.Choice([_DEFAULT_SLOT]),
    default=_DEFAULT_SLOT,
    show_default=True,
    help="Model slot to report.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of text.")
def model_eval_status(slot: str, json_output: bool) -> None:
    """Show the latest recorded metric per model for ``--slot``."""

    async def _impl() -> dict[str, float]:
        import asyncpg

        from services.model_eval.harness import LangfuseEvalHarness
        from services.model_eval.scorers.reranker import RerankerScorer

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            cfg = await _load_cfg(pool)
            harness = LangfuseEvalHarness(site_config=cfg)
            return harness.latest_by_model(slot, RerankerScorer.primary_metric)
        finally:
            await pool.close()

    latest = asyncio.run(_impl())

    if json_output:
        click.echo(json.dumps(latest, indent=2, default=str))
        return
    if not latest:
        click.echo("(no recorded eval runs)")
        return
    for model, value in sorted(latest.items(), key=lambda kv: kv[1], reverse=True):
        click.echo(f"  {value:.4f}  {model}")
