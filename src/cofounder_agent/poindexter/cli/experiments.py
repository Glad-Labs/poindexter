"""``poindexter experiments`` — operator surface for the Phase 1 lab harness.

The CLI is the canonical operator surface for the variant-experiments
harness landed in PR #699 (schema) + #702 (runner). See the design doc
``docs/architecture/2026-05-28-phase-1-variant-experiments-design.md``
for the wider picture; this file is the operator-facing thin adapter over
``services.experiment_admin`` — which owns the SQL against the
``experiments`` + ``experiment_variants`` tables + the
``experiment_variant_scorecard_v1`` view — per the transport-adapter
contract (#1340). The CLI opens the pool, calls the service, formats the
result, and translates ``ExperimentAdminError`` into ``click.ClickException``.

Subcommands:

- ``poindexter experiments list``                            — table of experiments
- ``poindexter experiments create <key> --niche= --description=``
                                                             — insert a draft
- ``poindexter experiments add-variant <key> --label=...``   — add a variant to a draft
- ``poindexter experiments activate <key>``                  — flip draft to active
- ``poindexter experiments status <key>``                    — render the scorecard
- ``poindexter experiments conclude <key> --winner=L --note=...``
                                                             — mark concluded, record winner

Design constraints (enforced by ``services.experiment_admin``, surfaced
here as friendly CLI errors):

- **One active experiment per niche** — ``activate`` checks the partial
  unique index from PR #699 in the application layer too so the operator
  gets a friendly "another experiment is active, conclude it first"
  instead of an asyncpg ``UniqueViolationError``.
- **Add-variant gated on draft status** — ``draft`` is the only mutable
  state for variants. ``active`` / ``paused`` / ``concluded`` reject the
  add-variant call so an in-flight experiment can't have its arms
  changed under the runner (the design allows mid-experiment variant
  additions but Phase 1 keeps the simpler rule per "one axis varying"
  scientific-method posture).
- **Activate requires >=2 variants** — a one-variant "experiment" is
  meaningless; uniform random over a singleton always picks the same arm.
- **Conclude records but does not promote** — Phase 1 ships manual
  promotion per the design doc's line 200-203. The CLI persists the
  winner_variant_label + conclusion_note, then prints the next-step
  guidance the operator follows to actually promote the winning config
  (Langfuse production label for prompt variants, ``app_settings.cost_tier.standard.model``
  for model variants, etc.). No silent code path moves prod defaults.

Patterns match ``poindexter/cli/topics.py``: Click group, lazy
``import asyncpg`` inside each ``_impl()``, DSN via
``poindexter.cli._bootstrap.resolve_dsn``, async impl wrapped in
``asyncio.run(_impl())``. Tests under
``tests/unit/cli/test_experiments_cli.py`` patch ``asyncpg.create_pool``
+ the niche service the same way ``test_topics_cli.py`` does.
"""

from __future__ import annotations

import asyncio
import json

import click

from poindexter.cli._bootstrap import resolve_dsn as _dsn
from services import experiment_admin

# ---------------------------------------------------------------------------
# Status / objective constants — kept in lockstep with the CHECK constraints
# in migration 20260529_000342_phase1_experiments_harness_foundation.py
# ---------------------------------------------------------------------------

_VALID_STATUSES = ("draft", "active", "paused", "concluded")
_VALID_OBJECTIVE_FUNCTIONS = (
    "views_7d", "views_24h", "approval_rate",
    "views_per_dollar", "composite_score",
)


# ---------------------------------------------------------------------------
# Group root
# ---------------------------------------------------------------------------


@click.group(
    name="experiments",
    help=(
        "Operator commands for the Phase 1 variant-experiments harness.\n\n"
        "Manages rows in ``experiments`` + ``experiment_variants``. The "
        "writer atom samples active variants via ``services.experiment_runner.pick_variant``; "
        "this CLI is the surface that creates, activates, observes, and "
        "concludes them. See "
        "``docs/architecture/2026-05-28-phase-1-variant-experiments-design.md``."
    ),
)
def experiments_group() -> None:
    pass


# ---------------------------------------------------------------------------
# experiments list
# ---------------------------------------------------------------------------


@experiments_group.command("list")
@click.option(
    "--status",
    type=click.Choice(_VALID_STATUSES),
    default=None,
    help="Filter to a single status. Omit for all.",
)
@click.option(
    "--niche",
    default=None,
    help="Filter to a single niche_slug. Omit for all niches.",
)
@click.option(
    "--json", "json_output", is_flag=True,
    help="Emit JSON (one array of objects) instead of the tabular default.",
)
def experiments_list(
    status: str | None, niche: str | None, json_output: bool,
) -> None:
    """List experiments with key/niche/status + variant + outcome counts.

    Tabular by default; pass ``--json`` for machine-readable output.
    Sorted most-recent first so the operator's latest work surfaces at
    the top.
    """
    async def _impl():
        import asyncpg
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            return await experiment_admin.list_experiments(
                pool, status=status, niche=niche,
            )
        finally:
            await pool.close()

    rows = asyncio.run(_impl())

    if json_output:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("(no experiments)")
        return

    click.echo(
        f"{'KEY':<48} {'NICHE':<14} {'STATUS':<10} "
        f"{'VARS':>4} {'RUNS':>5} {'WINNER':<10}"
    )
    for r in rows:
        winner = r["winner_variant_label"] or "-"
        click.echo(
            f"{r['key']:<48} {r['niche_slug']:<14} {r['status']:<10} "
            f"{r['variant_count']:>4} {r['outcome_count']:>5} {winner:<10}"
        )


# ---------------------------------------------------------------------------
# experiments create
# ---------------------------------------------------------------------------


@experiments_group.command("create")
@click.argument("key")
@click.option(
    "--niche", "niche_slug", required=True,
    help="Niche slug this experiment runs on (must exist in ``niches``).",
)
@click.option(
    "--description", default="",
    help="Free-text description of the hypothesis being tested.",
)
@click.option(
    "--objective",
    type=click.Choice(_VALID_OBJECTIVE_FUNCTIONS),
    default="views_7d", show_default=True,
    help=(
        "Which metric the scorecard ranks variants by. ``views_7d`` is "
        "the design default — see the design doc's reward-stack section."
    ),
)
def experiments_create(
    key: str, niche_slug: str, description: str, objective: str,
) -> None:
    """Create a new draft experiment for ``<key>`` on ``--niche``.

    The experiment lands in ``draft`` status — add variants with
    ``add-variant``, then ``activate`` once two or more are configured.

    Rejects:

    - Duplicate ``<key>`` (caught at the DB UNIQUE constraint level;
      surfaced as a clean message).
    - Unknown niche (caught by NicheService lookup before the INSERT).
    """
    async def _impl():
        import asyncpg
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            return await experiment_admin.create_experiment(
                pool,
                key=key,
                niche_slug=niche_slug,
                description=description,
                objective=objective,
            )
        finally:
            await pool.close()

    try:
        new_id = asyncio.run(_impl())
    except experiment_admin.ExperimentAdminError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Created experiment {key!r} (id={new_id}, niche={niche_slug}, "
        f"objective={objective}, status=draft).\n"
        f"Next: poindexter experiments add-variant {key} --label=A ..."
    )


# ---------------------------------------------------------------------------
# experiments add-variant
# ---------------------------------------------------------------------------


@experiments_group.command("add-variant")
@click.argument("key")
@click.option(
    "--label", required=True,
    help=(
        "Case-sensitive variant label (e.g. ``A``, ``B``, ``control``, "
        "``treatment``). Must be unique within the experiment."
    ),
)
@click.option(
    "--prompt-template", "prompt_template_key", default=None,
    help=(
        "Override the prompt-template key for this variant. NULL means "
        "inherit the niche default (held-constant axis)."
    ),
)
@click.option(
    "--prompt-version", "prompt_template_version", type=int, default=None,
    help="Override the prompt template version (integer).",
)
@click.option(
    "--writer-model", default=None,
    help=(
        "Override the writer model for this variant (e.g. ``gemma4:31b``). "
        "NULL means inherit the niche default."
    ),
)
@click.option(
    "--rag-config", default=None,
    help=(
        "JSON object merged into the niche-default rag_config. Use for "
        "RAG-axis variants only (held-constant in Phase 1 prompt/model A/Bs)."
    ),
)
@click.option(
    "--weight", type=float, default=1.0, show_default=True,
    help=(
        "Allocation weight. Ignored by Phase 1 (uniform random); on the "
        "schema for Phase 2's bandit."
    ),
)
def experiments_add_variant(
    key: str,
    label: str,
    prompt_template_key: str | None,
    prompt_template_version: int | None,
    writer_model: str | None,
    rag_config: str | None,
    weight: float,
) -> None:
    """Add a variant to a draft experiment.

    Rejects:

    - Unknown experiment ``<key>``.
    - Experiment not in ``draft`` status (active / paused / concluded
      experiments can't take new variants — the runner's allocation
      is locked once activated, per the scientific-method posture).
    - Duplicate ``--label`` within the experiment (DB UNIQUE
      ``(experiment_id, label)`` violation).
    - Malformed ``--rag-config`` JSON.
    """
    rag_config_parsed: dict = {}
    if rag_config:
        try:
            parsed = json.loads(rag_config)
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"invalid --rag-config JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise click.ClickException(
                "--rag-config must be a JSON object, got "
                f"{type(parsed).__name__}"
            )
        rag_config_parsed = parsed

    async def _impl():
        import asyncpg
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            return await experiment_admin.add_variant(
                pool,
                key=key,
                label=label,
                weight=weight,
                prompt_template_key=prompt_template_key,
                prompt_template_version=prompt_template_version,
                writer_model=writer_model,
                rag_config=rag_config_parsed,
            )
        finally:
            await pool.close()

    try:
        new_id = asyncio.run(_impl())
    except experiment_admin.ExperimentAdminError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Added variant {label!r} to {key!r} (id={new_id}, "
        f"model={writer_model or '<inherit>'}, "
        f"prompt={prompt_template_key or '<inherit>'}"
        + (f"/v{prompt_template_version}" if prompt_template_version else "")
        + ")."
    )


# ---------------------------------------------------------------------------
# experiments activate
# ---------------------------------------------------------------------------


@experiments_group.command("activate")
@click.argument("key")
def experiments_activate(key: str) -> None:
    """Flip a draft experiment to active.

    Sets ``status = 'active'`` and stamps ``activated_at = NOW()``.
    Once active, the writer-atom hook (PR #702) starts sampling this
    experiment's variants on tasks for the experiment's niche.

    Rejects:

    - Unknown ``<key>``.
    - Experiment not in ``draft`` (already active / paused / concluded).
    - Fewer than 2 variants (a one-variant experiment is meaningless —
      uniform random over a singleton always picks the same arm).
    - Another experiment already active on the same niche (the partial
      unique index from PR #699 enforces this at the DB layer; we
      check at the application layer too so the operator gets a
      friendly error message that points at ``conclude`` instead of
      an asyncpg ``UniqueViolationError`` traceback).
    """
    async def _impl():
        import asyncpg
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            await experiment_admin.activate_experiment(pool, key=key)
        finally:
            await pool.close()

    try:
        asyncio.run(_impl())
    except experiment_admin.ActiveExperimentConflict as exc:
        raise click.ClickException(
            f"niche {exc.niche_slug!r} already has an active experiment: "
            f"{exc.conflict_key!r}. Conclude it first with "
            f"`poindexter experiments conclude {exc.conflict_key} "
            "--winner=... --note=...`"
        ) from exc
    except experiment_admin.ExperimentAdminError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Activated {key!r}. The runner will now sample its variants on "
        "the niche's tasks. Watch progress with "
        f"`poindexter experiments status {key}`."
    )


# ---------------------------------------------------------------------------
# experiments status — scorecard render
# ---------------------------------------------------------------------------


@experiments_group.command("status")
@click.argument("key")
@click.option(
    "--json", "json_output", is_flag=True,
    help="Emit the scorecard rows as JSON instead of the tabular default.",
)
def experiments_status(key: str, json_output: bool) -> None:
    """Render the scorecard for an experiment.

    Reads ``experiment_variant_scorecard_v1`` (the view created by
    PR #699). Per-variant rows include: label, posts attempted,
    approval rate, mean edit distance, mean views 24h / 7d, mean cost,
    total cost. Rows are sorted by approval rate desc (with NULL last
    for variants that haven't been sampled yet) so the leader surfaces
    at the top.

    Print includes the experiment's ``objective_function`` so the
    operator knows which column to interpret as "winning" — Phase 1
    ranks manually; the view exposes everything; the objective tells
    you which one matters most.
    """
    async def _impl():
        import asyncpg
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            return await experiment_admin.get_scorecard(pool, key=key)
        finally:
            await pool.close()

    try:
        exp, rows = asyncio.run(_impl())
    except experiment_admin.ExperimentAdminError as exc:
        raise click.ClickException(str(exc)) from exc

    if json_output:
        click.echo(
            json.dumps(
                {"experiment": exp, "variants": rows},
                indent=2, default=str,
            )
        )
        return

    click.echo(f"Experiment: {exp['key']}")
    click.echo(f"  niche             {exp['niche_slug']}")
    click.echo(f"  status            {exp['status']}")
    click.echo(f"  objective         {exp['objective_function']}")
    click.echo(f"  description       {exp['description'] or '-'}")
    click.echo(f"  created_at        {exp['created_at']}")
    click.echo(f"  activated_at      {exp['activated_at'] or '-'}")
    click.echo(f"  concluded_at      {exp['concluded_at'] or '-'}")
    if exp["winner_variant_label"]:
        click.echo(f"  winner            {exp['winner_variant_label']}")
    if exp["conclusion_note"]:
        click.echo(f"  conclusion_note   {exp['conclusion_note']}")
    click.echo("")
    if not rows:
        click.echo("(no variants defined yet)")
        return

    header = (
        f"{'LABEL':<14} {'ACT':<4} {'RUNS':>5} {'APPR':>5} "
        f"{'APPR%':>6} {'EDIT%':>6} {'V24':>6} {'V7D':>6} "
        f"{'$/POST':>8} {'$TOTAL':>8}"
    )
    click.echo(header)
    for r in rows:
        active_marker = "Y" if r["variant_active"] else "n"
        appr_pct = (
            f"{float(r['approval_rate_pct']):.1f}"
            if r["approval_rate_pct"] is not None else "-"
        )
        edit_pct = (
            f"{float(r['avg_edit_distance_pct']) * 100:.1f}"
            if r["avg_edit_distance_pct"] is not None else "-"
        )
        v24 = (
            f"{float(r['avg_views_24h']):.1f}"
            if r["avg_views_24h"] is not None else "-"
        )
        v7d = (
            f"{float(r['avg_views_7d']):.1f}"
            if r["avg_views_7d"] is not None else "-"
        )
        per_post = (
            f"{float(r['avg_cost_per_post']):.4f}"
            if r["avg_cost_per_post"] is not None else "-"
        )
        total = (
            f"{float(r['total_cost']):.2f}"
            if r["total_cost"] is not None else "-"
        )
        click.echo(
            f"{r['variant_label']:<14} {active_marker:<4} "
            f"{int(r['posts_attempted'] or 0):>5} "
            f"{int(r['posts_approved'] or 0):>5} "
            f"{appr_pct:>6} {edit_pct:>6} {v24:>6} {v7d:>6} "
            f"{per_post:>8} {total:>8}"
        )


# ---------------------------------------------------------------------------
# experiments conclude
# ---------------------------------------------------------------------------


@experiments_group.command("conclude")
@click.argument("key")
@click.option(
    "--winner", required=True,
    help=(
        "Variant label of the winning arm (e.g. ``A``, ``gemma4-31b``). "
        "Must match an existing variant on the experiment."
    ),
)
@click.option(
    "--note", default="",
    help=(
        "Free-text rationale (e.g. ``B won 73% approval — promoting``). "
        "Stored on the experiments row for the historical record."
    ),
)
def experiments_conclude(key: str, winner: str, note: str) -> None:
    """Mark an experiment concluded and record the winning variant.

    Updates ``status = 'concluded'``, ``concluded_at = NOW()``,
    ``winner_variant_label = <winner>``, ``conclusion_note = <note>``
    on the experiments row. Atomic — either every column is set or
    the row stays untouched.

    Rejects:

    - Unknown ``<key>``.
    - Experiment already concluded.
    - ``--winner`` doesn't match any variant on the experiment.

    Phase 1 is **manual promotion** — this command records the
    operator's decision but does NOT mutate any niche / app_settings
    default. The CLI prints the next-step guidance after the update
    so the operator knows what to flip in Langfuse / app_settings to
    actually promote the winning config to production.
    """
    async def _impl():
        import asyncpg
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            return await experiment_admin.conclude_experiment(
                pool, key=key, winner=winner, note=note,
            )
        finally:
            await pool.close()

    try:
        exp, variant = asyncio.run(_impl())
    except experiment_admin.ExperimentAdminError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Concluded {key!r} (niche={exp['niche_slug']}). "
        f"Winner: {winner!r}."
    )
    if note:
        click.echo(f"Note: {note}")
    # Next-step guidance — Phase 1 is manual promotion. Tell the operator
    # WHERE to flip the winning config to make it the production default.
    click.echo("")
    click.echo("Phase 1 is manual promotion — to roll the winner to production:")
    if variant["writer_model"]:
        click.echo(
            f"  * Writer model winner: {variant['writer_model']!r}. "
            "Update app_settings.cost_tier.standard.model "
            "(or the appropriate tier) to promote."
        )
    if variant["prompt_template_key"]:
        ver = variant["prompt_template_version"]
        click.echo(
            f"  * Prompt winner: {variant['prompt_template_key']}"
            + (f"/v{ver}" if ver else "")
            + ". Promote in Langfuse by moving the production label to "
            "this version."
        )
    if not variant["writer_model"] and not variant["prompt_template_key"]:
        click.echo(
            "  * (Winning variant has no overrides — was a control/RAG-only "
            "test. No production flip required.)"
        )


__all__ = ["experiments_group"]
