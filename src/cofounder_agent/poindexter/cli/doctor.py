"""``poindexter doctor`` — unified health check-graph CLI (#527, v1).

Aggregates the per-probe health results the brain persists to
``brain_knowledge`` into a single operator surface: a 0-100 health score,
a systemic-vs-local flag, root-cause grouping (a DB-down root shows ONE
failure with its dependents suppressed under it), and a brain-freshness
meta-check so a dead brain shows up loudly instead of as a false-healthy
snapshot.

Diagnose-only by default (OpenClaw posture). ``--fix`` runs the existing
``REMEDIATIONS`` action for each ``fail`` check that has one, then re-reads
and re-reports.

The aggregation/scoring logic lives in
``services/doctor.py`` — this module is the thin CLI shell (DB pool,
human/JSON rendering, exit codes, ``--fix`` wiring).

Exit codes (scriptable):

* ``0`` healthy — no warn/fail;
* ``1`` degraded — at least one warn/fail (but no root down, not systemic);
* ``2`` critical — a ROOT check failed OR the report is systemic.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from services.doctor import (
    ROOTS,
    DoctorReport,
    run_doctor,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# DB pool — same bootstrap-first resolution every other DB-touching CLI uses
# (cf. ``poindexter migrate`` / ``poindexter tasks reject-batch``). The doctor
# reads ``brain_knowledge`` + ``audit_log`` + ``app_settings`` directly; there
# is no HTTP endpoint for it, so it talks to the DB like the migration CLI.
# ---------------------------------------------------------------------------


def _ensure_brain_on_path() -> None:
    """Add the repo root to ``sys.path`` so the ``brain`` package resolves.

    The CLI lives at ``src/cofounder_agent/poindexter/cli/doctor.py`` and the
    ``brain/`` package is at the repo root. Mirrors
    ``poindexter migrate``'s ``_ensure_brain_on_path``. Needed so ``--fix``
    can reach ``brain.health_probes`` (REMEDIATIONS + the restart helper).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "brain" / "bootstrap.py").is_file():
            p = str(parent)
            if p not in sys.path:
                sys.path.insert(0, p)
            return


async def _make_pool():
    """Build an asyncpg pool against the bootstrap-resolved DSN."""
    import asyncpg

    from poindexter.cli._bootstrap import resolve_dsn

    return await asyncpg.create_pool(resolve_dsn(), min_size=1, max_size=2)


# ---------------------------------------------------------------------------
# --fix — reuse the brain's existing remediation, no re-implementation.
# ---------------------------------------------------------------------------


def _apply_fixes(report: DoctorReport) -> list[tuple[str, bool, str]]:
    """Run the brain remediation for each fixable ``fail`` check.

    Returns ``(check_name, ok, message)`` per attempted fix. Reuses the
    SAME helpers the brain's self-healing loop uses — ``REMEDIATIONS`` (the
    probe-name -> action map) and ``_restart_container`` (the docker-restart
    primitive) — so the CLI's ``--fix`` is bit-for-bit the action the brain
    would have taken, not a re-implementation that could drift.

    Only ``fail`` checks with a ``remediation`` key are touched. ``warn`` /
    ``suppressed`` / ``stale`` are left alone (a suppressed symptom is fixed
    by fixing its root; warns aren't urgent enough to auto-restart).
    """
    _ensure_brain_on_path()
    try:
        from health_probes import REMEDIATIONS, _restart_container
    except Exception as e:  # noqa: BLE001
        return [("(import)", False, f"brain health_probes not importable: {e}")]

    results: list[tuple[str, bool, str]] = []
    for check in report.checks:
        if check.status != "fail" or check.remediation is None:
            continue
        action = REMEDIATIONS.get(check.remediation)
        if not action:
            results.append((check.name, False, "no REMEDIATIONS entry"))
            continue
        if action.get("type") == "restart_container":
            ok, msg = _restart_container(action["container"])
            results.append((check.name, ok, msg))
        elif action.get("type") == "restart_multiple":
            msgs = []
            any_ok = False
            for container in action.get("containers", []):
                c_ok, c_msg = _restart_container(container)
                any_ok = any_ok or c_ok
                msgs.append(c_msg)
            results.append((check.name, any_ok, "; ".join(msgs)))
        else:
            results.append(
                (check.name, False, f"unsupported action type {action.get('type')!r}")
            )
    return results


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------

_STATUS_COLOR = {
    "ok": "green",
    "warn": "yellow",
    "fail": "red",
    "suppressed": "white",
    "stale": "magenta",
}

# Render groups in this order so the eye lands on the worst first.
_GROUP_ORDER = ("fail", "warn", "suppressed", "stale", "ok")


def _score_color(score: int) -> str:
    if score >= 90:
        return "green"
    if score >= 70:
        return "yellow"
    return "red"


def _render_human(report: DoctorReport) -> None:
    click.secho(
        f"Health score: {report.score}/100",
        fg=_score_color(report.score),
        bold=True,
    )
    if report.brain_stale:
        click.secho(
            "  ⚠ brain looks DOWN — probe results are stale, this snapshot is "
            "not trustworthy",
            fg="magenta",
            bold=True,
        )
    if report.systemic:
        click.secho(
            "  ⚠ SYSTEMIC — multiple independent subsystems degraded at once "
            "(not a local blip)",
            fg="red",
            bold=True,
        )
    click.echo()

    by_group: dict[str, list] = {g: [] for g in _GROUP_ORDER}
    for check in report.checks:
        by_group.setdefault(check.status, []).append(check)

    for group in _GROUP_ORDER:
        checks = by_group.get(group) or []
        if not checks:
            continue
        click.secho(f"{group.upper()} ({len(checks)})", fg=_STATUS_COLOR.get(group, "white"), bold=True)
        for check in checks:
            line = f"  {check.name:<22} {check.detail[:80]}"
            if check.status == "suppressed" and check.root:
                line += f"  [root: {check.root}]"
            if check.remediation and check.status == "fail":
                line += "  [fixable]"
            click.secho(line, fg=_STATUS_COLOR.get(group, "white"))
        click.echo()


def _exit_code(report: DoctorReport) -> int:
    """Map a report to a scriptable exit code.

    ``2`` critical — a ROOT failed, OR the report is systemic, OR the brain
    is stale (a stale brain means we can't see the real state — treat as
    critical so a cron noticing exit 2 pages).
    ``1`` degraded — any warn/fail otherwise.
    ``0`` healthy.
    """
    if report.brain_stale or report.systemic:
        return 2
    for check in report.checks:
        if check.status == "fail" and check.name in ROOTS:
            return 2
    for check in report.checks:
        if check.status in ("fail", "warn"):
            return 1
    return 0


# ---------------------------------------------------------------------------
# Command.
# ---------------------------------------------------------------------------


@click.command(
    name="doctor",
    help=(
        "Aggregate every health probe into one report: a 0-100 score, a "
        "systemic-vs-local flag, root-cause grouping, and a brain-freshness "
        "meta-check.\n\n"
        "Reads the results the brain already persists (does not re-run probes). "
        "Diagnose-only by default; pass --fix to run known remediations.\n\n"
        "Exit codes: 0 healthy, 1 degraded (any warn/fail), 2 critical (a root "
        "failed, systemic, or brain stale)."
    ),
)
@click.option("--json", "json_output", is_flag=True, help="Emit the full DoctorReport as JSON.")
@click.option(
    "--fix",
    is_flag=True,
    help=(
        "Run the existing brain REMEDIATIONS action for each fixable 'fail' "
        "check (e.g. restart a stuck container), then re-read and re-report."
    ),
)
def doctor_command(json_output: bool, fix: bool) -> None:
    async def _impl() -> DoctorReport:
        pool = await _make_pool()
        try:
            report = await run_doctor(pool)
            if fix:
                fixes = _apply_fixes(report)
                if not json_output:
                    if fixes:
                        click.secho("Remediations attempted:", fg="cyan", bold=True)
                        for name, ok, msg in fixes:
                            mark = "✓" if ok else "✗"
                            click.secho(f"  {mark} {name}: {msg}", fg="green" if ok else "red")
                    else:
                        click.echo("No fixable 'fail' checks (nothing to remediate).")
                    click.echo()
                # Re-read so the report reflects post-fix state.
                report = await run_doctor(pool)
            return report
        finally:
            await pool.close()

    try:
        report = _run(_impl())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        _render_human(report)

    sys.exit(_exit_code(report))


# Exposed as a group alias so app.py can ``add_command(doctor_group, ...)``
# uniformly with the other surfaces. It's a single command, not a group, but
# the spec wires it via ``main.add_command(doctor_group, name="doctor")``.
doctor_group = doctor_command


__all__ = ["doctor_command", "doctor_group"]
