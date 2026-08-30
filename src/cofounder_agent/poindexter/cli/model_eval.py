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

from poindexter.cli._bootstrap import close_cli_pool, open_cli_pool

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
    "--slot",
    type=click.Choice(["reranker", "critic", "self-review"]),
    default="reranker",
    show_default=True,
    help=(
        "Which model slot to evaluate. 'reranker' bakes off rag_rerank_model; "
        "'critic' calibrates the QA judge (pipeline_critic_model) on the "
        "posts-derived critic golden set (poindexter#985); 'self-review' "
        "measures the contradiction DETECTOR "
        "(writer_self_review_review_model) against real posts with an "
        "injected contradiction (poindexter#1031)."
    ),
)
@click.option(
    "--challenger",
    "challengers",
    multiple=True,
    help=(
        "Candidate model id to test against the champion. Repeatable. "
        "Required for the reranker slot; optional for the critic slot "
        "(a champion-only run is the calibration baseline)."
    ),
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of text.")
def model_eval_run(slot: str, challengers: tuple[str, ...], json_output: bool) -> None:
    """Bake the slot's current champion off against ``--challenger``s.

    NB the critic slot makes real LLM calls through the dispatcher — on an
    operator install where provider URLs are docker-network hostnames, run
    it in-container: ``docker exec poindexter-worker poindexter model-eval
    run --slot critic``.
    """
    if slot == "reranker" and not challengers:
        raise click.UsageError("--challenger is required for the reranker slot.")

    async def _impl() -> tuple[Any, Any]:

        pool = await open_cli_pool()
        try:
            cfg = await _load_cfg(pool)
            if slot == "self-review":
                from services.model_eval.bakeoff import run_self_review_bakeoff

                return await run_self_review_bakeoff(
                    pool=pool, site_config=cfg, challengers=list(challengers),
                )
            if slot == "critic":
                from plugins.kernel_platform import KernelPlatform
                from services.llm_providers.dispatcher import dispatch_complete
                from services.model_eval.bakeoff import run_critic_bakeoff

                async def _noop_audit(*_a: Any, **_kw: Any) -> None:
                    return None

                platform = KernelPlatform(
                    site_config=cfg,
                    pool=pool,
                    dispatch=dispatch_complete,
                    audit_write=_noop_audit,
                )
                return await run_critic_bakeoff(
                    pool=pool,
                    site_config=cfg,
                    platform=platform,
                    challengers=list(challengers),
                )
            from services.model_eval.bakeoff import run_reranker_bakeoff

            return await run_reranker_bakeoff(
                pool=pool, site_config=cfg, challengers=list(challengers)
            )
        finally:
            await close_cli_pool(pool)

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
    if slot == "critic":
        click.echo("\nPer-judge detail (good-approve / bad-veto / unusable):")
        for r in report.results:
            d = r.detail
            click.echo(
                f"  {r.model}: balanced={r.value:.2f}  "
                f"good={d.get('good_approve_rate')}  "
                f"bad={d.get('bad_veto_rate')}  "
                f"by_kind={d.get('veto_rate_by_kind')}  "
                f"unusable={d.get('unusable_reviews')}"
            )
    if proposal is not None:
        click.echo(f"\nPromotion proposal ({proposal.kind}):\n")
        click.echo(proposal.body)
    else:
        click.echo("\nNo promotion — champion holds.")


@model_eval_group.command("status")
@click.option(
    "--slot",
    type=click.Choice([_DEFAULT_SLOT, "pipeline_critic_model"]),
    default=_DEFAULT_SLOT,
    show_default=True,
    help="Model slot to report.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of text.")
def model_eval_status(slot: str, json_output: bool) -> None:
    """Show the latest recorded metric per model for ``--slot``."""

    async def _impl() -> dict[str, float]:

        from services.model_eval.harness import LangfuseEvalHarness

        if slot == "pipeline_critic_model":
            from services.model_eval.scorers.critic import CriticScorer as _Scorer
        else:
            from services.model_eval.scorers.reranker import RerankerScorer as _Scorer

        pool = await open_cli_pool()
        try:
            cfg = await _load_cfg(pool)
            harness = LangfuseEvalHarness(site_config=cfg)
            return await harness.latest_by_model(slot, _Scorer.primary_metric)
        finally:
            await close_cli_pool(pool)

    latest = asyncio.run(_impl())

    if json_output:
        click.echo(json.dumps(latest, indent=2, default=str))
        return
    if not latest:
        click.echo("(no recorded eval runs)")
        return
    for model, value in sorted(latest.items(), key=lambda kv: kv[1], reverse=True):
        click.echo(f"  {value:.4f}  {model}")
