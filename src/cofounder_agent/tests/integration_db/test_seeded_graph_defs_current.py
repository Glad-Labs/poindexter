"""Runtime-parity CI gate — the SEEDED+MIGRATED graph_defs must pass the
load-time contract gate against the live atom registry.

This is the guard that would have caught the 2026-07-10 ``canonical_blog``
outage (PR #2250, fixed by #2261). Two gates already existed and both missed it:

* The **PR-time** gate ``assert_specs_match_contract_fingerprints`` compares the
  in-tree specs against the committed snapshot ``graph_def_contract_fingerprints
  .json`` — #2250 regenerated that snapshot, silencing it.
* The **runtime** gate ``assert_graph_def_current`` compares the *stored*
  ``pipeline_templates.graph_def`` against the registry — it caught the drift,
  but only in prod at load time, because nothing exercised the seeded row in CI.

Nothing verified the seeded-then-migrated row, so a contract change that shipped
no reseed migration left the baseline's FROZEN stamp stale and halted every run.

This test closes the gap: it applies the baseline + every migration to a fresh
disposable Postgres (the session ``test_pool`` fixture), then runs the runtime
gate against the resulting ``pipeline_templates`` rows via
``assert_seeded_graph_defs_current``. A contract change with no reseed migration
trips it at PR time — exactly the #2250 miss — because the baseline seeds the
old stamp and, absent a reseed, nothing re-stamps it.

Why this can't live in ``scripts/ci/migrations_smoke.py``: that job is
dependency-light by design (asyncpg only), but the gate needs the full atom
registry (``modules.content.atoms.*`` + LangGraph) to compute current
fingerprints. The integration_db tier already has both a fresh-migrated DB and
the full env, and runs on every backend PR (``.github/workflows/integration-db
.yml``).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

import services.pipeline_architect as pa
from services.atom_registry import discover, registry_is_empty

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _active_graph_defs(test_pool) -> list[tuple[str, object]]:
    """``(slug, graph_def)`` for every active pipeline_templates row, decoded
    once (mirrors ``pipeline_templates.load_active_graph_def``: a JSONB column
    comes back as a str under asyncpg's default codec; dev_diary is stored
    double-encoded and decodes to a str, which the gate treats as non-gated)."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug, graph_def FROM pipeline_templates WHERE active = true "
            "ORDER BY slug"
        )
    out: list[tuple[str, object]] = []
    for row in rows:
        raw = row["graph_def"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        out.append((row["slug"], raw))
    return out


async def test_seeded_graph_defs_pass_runtime_contract_gate(test_pool) -> None:
    """PASSES on main: every seeded+migrated active graph_def is current against
    the live atom registry — no stale frozen stamp lurking without its reseed."""
    discover()
    if registry_is_empty():
        pytest.skip("atom registry yielded no atoms in this environment")

    rows = await _active_graph_defs(test_pool)
    assert rows, "expected active pipeline_templates rows on a fresh baseline DB"

    # Non-vacuous: at least one row must carry FROZEN stamps (else the gate would
    # trivially pass by skipping everything). The 20260726 gate-graduation reseed
    # un-stamped the last baseline-stamped row (seo_refresh) and therefore ends by
    # running ensure_active_graph_defs_stamped itself, so in this full-dep env
    # every active row regains registry stamps at migration time (only the
    # dependency-light smoke env leaves rows raw for the boot self-heal). If this
    # ever fails, the whole seeded set is unstamped — the gate has gone
    # temporarily blind and the failure is the signal to notice.
    gated = [
        slug
        for slug, spec in rows
        if isinstance(spec, dict)
        and spec.get("nodes")
        and not pa.is_graph_def_fully_unstamped(spec)
    ]
    assert gated, (
        "no frozen-stamped active graph_def to gate — every seeded row is "
        f"fully-unstamped (rows: {[s for s, _ in rows]}). The runtime-parity "
        "gate is only meaningful against stamped rows."
    )

    # The gate itself: raises GraphContractError naming any stale template.
    pa.assert_seeded_graph_defs_current(rows)


async def test_gate_flags_a_drifted_seeded_atom(test_pool, monkeypatch) -> None:
    """FAILS on a synthetic contract change with no reseed migration: drift one
    atom referenced by a frozen-stamped seeded row (registry moves, the seeded
    stamp stays) and confirm the gate raises — the #2250 reproduction against the
    REAL seeded data, so this stays regression-proof, not a one-time check."""
    discover()
    if registry_is_empty():
        pytest.skip("atom registry yielded no atoms in this environment")

    rows = await _active_graph_defs(test_pool)

    # Pick a frozen-stamped row + one atom it references. Drifting an atom on an
    # unstamped (pending-reseed) row wouldn't reproduce #2250: on a fresh DB the
    # boot self-heal would just re-stamp it current.
    target_atom = None
    target_slug = None
    for slug, spec in rows:
        if not isinstance(spec, dict) or pa.is_graph_def_fully_unstamped(spec):
            continue
        for node in spec.get("nodes", []):
            atom = node.get("atom")
            if isinstance(atom, str) and pa.get_atom_meta(atom) is not None:
                target_atom, target_slug = atom, slug
                break
        if target_atom:
            break
    if not target_atom:
        pytest.skip("no frozen-stamped seeded graph_def with a resolvable atom")

    # Sanity: the gate is green BEFORE the synthetic drift.
    pa.assert_seeded_graph_defs_current(rows)

    # Simulate the atom's I/O contract changing (as #2250 added an output to
    # content.plan_image_markers) with NO reseed migration to re-stamp the row.
    real_get = pa.get_atom_meta
    real_meta = real_get(target_atom)
    drifted = replace(real_meta, produces=(*real_meta.produces, "__synthetic_drift__"))
    monkeypatch.setattr(
        pa, "get_atom_meta", lambda n: drifted if n == target_atom else real_get(n)
    )

    with pytest.raises(pa.GraphContractError) as exc:
        pa.assert_seeded_graph_defs_current(rows)
    # Names the stale template AND the drifted atom so the fix (a reseed
    # migration) is obvious from the CI log.
    msg = str(exc.value)
    assert target_slug in msg
    assert target_atom in msg
