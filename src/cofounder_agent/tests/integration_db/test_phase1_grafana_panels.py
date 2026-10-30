"""Integration tests for the Phase 1 PR 4 Grafana lab-observability panels.

These tests guard the three panel SQL queries on the
``infrastructure/grafana/dashboards/pipeline-merged.json`` dashboard added
in PR 4 of the variant-experiments harness (per design doc
``docs/architecture/2026-05-28-phase-1-variant-experiments-design.md``
section "Operator surface"):

  1. Stat panel — active experiments count
     ``SELECT COUNT(*) FROM experiments WHERE status = 'active'``
  2. Table panel — per-variant scorecard for the most-recently-activated
     experiment, JOINed against ``lab_outcomes_v1`` for the wall-clock
     column
  3. Time series — rolling-7-day approval rate per active-experiment
     variant, powered by ``capability_outcomes.variant_id`` (PR 2) JOINed
     against ``experiment_variants`` for the label

WHY THIS TEST EXISTS

The ``scripts/ci/grafana_panels_lint.py`` script only EXPLAINs each
query; it does not exercise the query against synthetic data, so a
silent semantic bug (wrong column referenced, JOIN dropping rows,
window-function frame off-by-one) would pass the lint cleanly. This
test loads the dashboard JSON, extracts the three panel queries by
``id``, seeds a tiny two-variant experiment with deterministic
outcomes, and asserts each query returns the expected shape + values.

If a future PR breaks the panels, this test catches it before the
dashboard silently goes blank on the operator's monitor.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

# The session-scoped asyncio mark moved off the module level and onto the
# three async DB tests below: applied module-wide it wrongly tagged the four
# sync panel-structure tests, emitting a PytestWarning
# (Glad-Labs/poindexter#997). ``asyncio_mode = "auto"`` still auto-marks
# the coroutine tests; the explicit per-test mark only sets the loop scope so
# they share the session-scoped ``test_txn`` fixture loop.
pytestmark = [
    pytest.mark.integration_db,
]


# ---------------------------------------------------------------------------
# Panel locator — extract a panel's first rawSql by panel id
# ---------------------------------------------------------------------------


# Resolve the dashboard path relative to the repo root. From
# .../src/cofounder_agent/tests/integration_db/<this>.py:
#   parents[0] = integration_db
#   parents[1] = tests
#   parents[2] = cofounder_agent
#   parents[3] = src
#   parents[4] = repo root  (where infrastructure/ lives)
REPO_ROOT = Path(__file__).resolve().parents[4]
DASHBOARD_PATH = (
    REPO_ROOT / "infrastructure" / "grafana" / "dashboards" / "experiments-dryrun.json"
)


def _load_panels() -> dict[int, dict]:
    """Return a dict[panel_id] -> panel for every renderable panel in the
    pipeline-merged dashboard.

    Recurses into ``panels[].panels[]`` rows so panels nested under a row
    container are reachable by id, matching the walking strategy in
    ``scripts/ci/lib_grafana_panels.py::walk_panels``.
    """
    data = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    out: dict[int, dict] = {}
    stack = list(data.get("panels", []) or [])
    while stack:
        p = stack.pop()
        pid = p.get("id")
        if pid is not None:
            out[pid] = p
        stack.extend(p.get("panels", []) or [])
    return out


def _panel_sql(panel: dict) -> str:
    """Extract the panel's first rawSql target string."""
    for t in panel.get("targets") or []:
        sql = t.get("rawSql")
        if sql:
            return sql
    raise AssertionError(
        f"panel id={panel.get('id')} title={panel.get('title')!r} has no rawSql target"
    )


# Panel IDs in experiments-dryrun.json (relocated from pipeline-merged.json
# by the #654 dashboard restructure; re-pointed by #1082).
PANEL_ID_LAB_ROW = 15
PANEL_ID_ACTIVE_COUNT = 16
PANEL_ID_SCORECARD_TABLE = 17
PANEL_ID_APPROVAL_RATE_TIMESERIES = 18


def _lab_section_parked() -> bool:
    """True while the Lab Observability panels are parked (absent).

    glad-labs-stack#2052 parked the structurally-empty Lab section (the
    ``experiments`` table has held one stale row since 2026-05-29): the row
    was retitled "… — parked" and panels 16-18 were removed from the board.
    These tests guard the LIVE panels' SQL, so they go dormant with the
    panels — unparking the section (restoring the panels) automatically
    re-arms them. Without this guard the module hard-fails on KeyError and
    blocks every PR that triggers integration-db.
    """
    try:
        panels = _load_panels()
    except FileNotFoundError:
        # Dashboard file relocated/renamed — that's NOT parking; fall
        # through so the tests fail loud instead of silently skipping.
        return False
    return PANEL_ID_ACTIVE_COUNT not in panels


pytestmark.append(
    pytest.mark.skipif(
        _lab_section_parked(),
        reason=(
            "Lab Observability section is parked (#2052) — panels 16-18 "
            "absent from experiments-dryrun.json; unpark to re-arm"
        ),
    )
)


# ---------------------------------------------------------------------------
# Dashboard JSON shape — the row + 3 panels exist and have the right types.
# Runs without a DB; cheap smoke test that the JSON didn't get corrupted.
# ---------------------------------------------------------------------------


def test_lab_observability_row_present():
    panels = _load_panels()
    row = panels[PANEL_ID_LAB_ROW]
    assert row["type"] == "row"
    assert "Lab Observability" in row["title"]
    assert "Variant Experiments" in row["title"]


def test_active_experiments_panel_present():
    panels = _load_panels()
    p = panels[PANEL_ID_ACTIVE_COUNT]
    assert p["type"] == "stat"
    assert "Active experiments" in p["title"]
    sql = _panel_sql(p)
    assert "experiments" in sql
    assert "active" in sql
    # Color thresholds per spec: green 0-2, yellow 3-5, red 6+
    steps = p["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert any(s["color"] == "green" for s in steps)
    assert any(s["color"] == "yellow" and s["value"] == 3 for s in steps)
    assert any(s["color"] == "red" and s["value"] == 6 for s in steps)


def test_scorecard_table_panel_present():
    panels = _load_panels()
    p = panels[PANEL_ID_SCORECARD_TABLE]
    assert p["type"] == "table"
    sql = _panel_sql(p)
    # Must read from the scorecard view, not from raw tables.
    assert "experiment_variant_scorecard_v1" in sql
    # Must filter to the most-recently-activated experiment.
    assert "activated_at" in sql
    # Must sort by approval rate descending.
    assert "ORDER BY" in sql.upper()
    assert "DESC" in sql.upper()


def test_approval_rate_timeseries_panel_present():
    panels = _load_panels()
    p = panels[PANEL_ID_APPROVAL_RATE_TIMESERIES]
    assert p["type"] == "timeseries"
    sql = _panel_sql(p)
    # Rolling 7-day window: must use a window function with INTERVAL '6 days'
    # PRECEDING (CURRENT ROW + 6 preceding = 7-day window).
    assert "OVER" in sql.upper() or "WINDOW" in sql.upper()
    assert "INTERVAL '6 days'" in sql
    # Must join the variant table for the label.
    assert "experiment_variants" in sql
    # Must filter to active experiments.
    assert "'active'" in sql


# ---------------------------------------------------------------------------
# SQL execution — the three queries run cleanly against a freshly migrated
# schema with synthetic data, and return the expected shape.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_active_experiments_panel_sql_runs(test_txn) -> None:
    """The stat-panel query returns a single COUNT row for active experiments."""
    panels = _load_panels()
    sql = _panel_sql(panels[PANEL_ID_ACTIVE_COUNT])

    # Two experiments: one active, one draft. Different niche slugs so the
    # one-active-per-niche partial unique index doesn't fire.
    await test_txn.execute(
        """
        INSERT INTO experiments (key, niche_slug, status, activated_at)
        VALUES ($1, 'panel-test-niche-a', 'active', NOW()),
               ($2, 'panel-test-niche-b', 'draft',  NULL)
        """,
        f"panel-test-active-{uuid4()}",
        f"panel-test-draft-{uuid4()}",
    )

    row = await test_txn.fetchrow(sql)
    # The query returns one column ("Active experiments"); should count at
    # least the one we just inserted. The fixture DB may have others, so
    # the assertion is >=1, not ==1.
    assert row is not None
    val = list(row.values())[0]
    assert val >= 1, f"expected at least 1 active experiment, got {val}"


@pytest.mark.asyncio(loop_scope="session")
async def test_scorecard_table_panel_sql_runs(test_txn) -> None:
    """The table-panel query joins scorecard + lab_outcomes_v1 and returns
    one row per variant for the most-recently-activated experiment."""
    panels = _load_panels()
    sql = _panel_sql(panels[PANEL_ID_SCORECARD_TABLE])

    # Seed an active experiment with two variants. The latest_active CTE in
    # the panel query picks by activated_at DESC, so we stamp it explicitly.
    exp_id = await test_txn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status, activated_at)
        VALUES ($1, 'scorecard-panel-niche', 'active', NOW())
        RETURNING id
        """,
        f"scorecard-panel-{uuid4()}",
    )
    variant_a = await test_txn.fetchval(
        """
        INSERT INTO experiment_variants (experiment_id, label)
        VALUES ($1, 'A') RETURNING id
        """,
        exp_id,
    )
    variant_b = await test_txn.fetchval(
        """
        INSERT INTO experiment_variants (experiment_id, label)
        VALUES ($1, 'B') RETURNING id
        """,
        exp_id,
    )

    # Tag a single capability_outcomes row to each variant so the LEFT JOIN
    # in the panel query actually has data on both sides.
    task_a = str(uuid4())
    task_b = str(uuid4())
    for task_id, variant_id in [(task_a, variant_a), (task_b, variant_b)]:
        await test_txn.execute(
            """
            INSERT INTO capability_outcomes
              (task_id, template_slug, node_name, atom_name,
               capability_tier, model_used,
               ok, halted, elapsed_ms, quality_score, metrics,
               niche_slug, variant_id)
            VALUES ($1, 'canonical_blog', 'atoms.test_writer',
                    'atoms.test_writer', 'standard_writer', 'test-model',
                    TRUE, FALSE, 1500, 87.5,
                    '{}'::jsonb,
                    'scorecard-panel-niche', $2)
            """,
            task_id, variant_id,
        )

    rows = await test_txn.fetch(sql)
    # Two variants ⇒ two rows.
    assert len(rows) == 2, (
        f"scorecard panel returned {len(rows)} rows for a 2-variant experiment; "
        "expected 2"
    )
    # Both labels present (order depends on approval %; both at 0% here).
    labels = {r["Variant"] for r in rows}
    assert labels == {"A", "B"}, f"missing variant labels: {labels}"
    # Wall-clock column reflects the elapsed_ms we seeded (1500ms = 1.5s).
    for r in rows:
        assert r["Mean Wall-Clock (s)"] == pytest.approx(1.5, rel=0.01)


@pytest.mark.asyncio(loop_scope="session")
async def test_approval_rate_timeseries_panel_sql_runs(test_txn) -> None:
    """The time-series panel query returns at least one (time, metric,
    value) row per variant per day with tagged outcomes."""
    panels = _load_panels()
    sql = _panel_sql(panels[PANEL_ID_APPROVAL_RATE_TIMESERIES])

    exp_id = await test_txn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status, activated_at)
        VALUES ($1, 'timeseries-panel-niche', 'active', NOW())
        RETURNING id
        """,
        f"timeseries-panel-{uuid4()}",
    )
    variant_a = await test_txn.fetchval(
        """
        INSERT INTO experiment_variants (experiment_id, label)
        VALUES ($1, 'control') RETURNING id
        """,
        exp_id,
    )

    # Two tagged outcomes, one approved, one not. With both in the same
    # 7-day window the rolling rate should land at 50%.
    task_approved = str(uuid4())
    task_rejected = str(uuid4())
    for task_id in (task_approved, task_rejected):
        await test_txn.execute(
            """
            INSERT INTO capability_outcomes
              (task_id, template_slug, node_name, atom_name,
               capability_tier, model_used,
               ok, halted, elapsed_ms, quality_score, metrics,
               niche_slug, variant_id)
            VALUES ($1, 'canonical_blog', 'atoms.test_writer',
                    'atoms.test_writer', 'standard_writer', 'test-model',
                    TRUE, FALSE, 1000, 80.0,
                    '{}'::jsonb,
                    'timeseries-panel-niche', $2)
            """,
            task_id, variant_a,
        )

    # Mark one as approved via published_post_edit_metrics. The panel joins
    # on task_id so the approver presence flips the FILTER.
    approved_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await test_txn.execute(
        """
        INSERT INTO published_post_edit_metrics
          (task_id, niche_slug, category, approver,
           pre_approve_hash, post_approve_hash,
           char_diff_count, line_diff_count,
           pre_approve_len, post_approve_len,
           approve_method, approved_at, metrics)
        VALUES ($1, 'timeseries-panel-niche', 'tech', 'test-operator',
                'h1', 'h2', 10, 1, 500, 510, 'manual', $2, '{}'::jsonb)
        """,
        task_approved, approved_at,
    )

    rows = await test_txn.fetch(sql)
    # At least one row for our variant. Other concurrent test rows may also
    # be present; filter to ours by the constructed metric string.
    ours = [r for r in rows if r["metric"].endswith("/ control")]
    assert ours, f"expected at least one timeseries row for variant 'control'; got {rows}"
    # The 7-day rolling rate for the only day with tagged outcomes
    # (1 approved / 2 runs) = 50.0%.
    latest = ours[-1]
    assert float(latest["value"]) == pytest.approx(50.0, abs=0.5), (
        f"expected rolling approval rate ≈ 50%, got {latest['value']}"
    )
