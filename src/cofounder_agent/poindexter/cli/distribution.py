"""CLI surface for distribution attribution.

Delegates to :mod:`services.distribution_yield` — no SQL or business logic
here, per the transport-adapter contract.
"""
from __future__ import annotations

import sys

import click

from poindexter.cli._dataplane import run_service
from services.distribution_yield import surface_yield


@click.group(name="distribution")
def distribution_group() -> None:
    """Inspect what each distribution surface delivered."""


@distribution_group.command("yield")
@click.option(
    "--days", default=30, show_default=True, type=int, help="Window to report on"
)
def yield_report(days: int) -> None:
    """Placements vs returns per distribution surface.

    ``placements`` is what we put out (posted promos, crossposts, uploads);
    ``tagged`` is views that arrived carrying that surface's attribution tag;
    ``referrer`` is the older, lossier signal from the browser's referrer
    header, shown alongside because it is the only evidence covering the
    period before tagging existed.
    """
    try:
        rows = run_service(lambda p: surface_yield(p, days=days))
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if not rows:
        click.echo(f"No distribution activity in the last {days} days.")
        return

    click.echo(f"Distribution yield — last {days} days\n")
    click.echo(
        f"{'surface':<14}{'medium':<13}{'placements':>11}"
        f"{'tagged':>9}{'referrer':>10}{'per 100':>9}"
    )
    click.echo("-" * 66)
    for row in rows:
        per = row.views_per_placement
        # Per-100-placements rather than a bare ratio: at these volumes the raw
        # figure is 0.01-ish for everything, which reads as "all zero" and
        # hides the differences that are actually there.
        per_txt = "—" if per is None else f"{per * 100:.1f}"
        click.echo(
            f"{row.surface:<14}{row.medium or '—':<13}{row.placements:>11}"
            f"{row.tagged_views:>9}{row.referrer_views:>10}{per_txt:>9}"
        )

    totals_tagged = sum(r.tagged_views for r in rows)
    totals_placements = sum(r.placements for r in rows)
    click.echo("-" * 66)
    click.echo(
        f"{'TOTAL':<14}{'':<13}{totals_placements:>11}{totals_tagged:>9}"
        f"{sum(r.referrer_views for r in rows):>10}"
    )
    if totals_tagged == 0 and totals_placements > 0:
        # Say it out loud rather than leaving a column of zeroes to be read as
        # a verdict. Two very different things produce this table.
        click.echo(
            "\nNo tagged views yet. Either the tagged links have not been "
            "clicked, or tagging has not reached this surface — check "
            "distribution_ref_enabled and whether the beacon Worker is "
            "deployed with the ref blob."
        )


__all__ = ["distribution_group"]
