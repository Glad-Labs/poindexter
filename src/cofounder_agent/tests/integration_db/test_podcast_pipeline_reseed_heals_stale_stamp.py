"""The ``podcast.persist`` reseed migration must heal an ALREADY-STAMPED row.

Why this needs its own test: ``test_seeded_graph_defs_current`` applies the
baseline + every migration to a fresh DB and then runs the runtime gate — but
``0000_baseline.seeds.sql`` seeds ``podcast_pipeline`` **un-stamped**, so the
boot self-heal (``ensure_active_graph_defs_stamped``) stamps it from the live
registry and that gate goes green whether or not a reseed migration exists.

Prod is the other case. Its ``podcast_pipeline`` row has been running and
carries a real ``_contract_fp`` (``8a9a5cd81cbe`` for ``podcast.persist`` as of
this change), and the self-heal deliberately leaves any stamped row alone so
the drift gate can still catch genuine drift. So a contract change with no
reseed halts the pipeline at load in prod while every CI gate stays green —
the #1876 failure mode, which took out the whole Stage-2 video lane.

This test reproduces the prod shape: stamp the row stale, run the migration,
assert the row now satisfies the runtime gate.
"""

from __future__ import annotations

import importlib
import json

import pytest

import poindexter.services.pipeline_architect as pa
from poindexter.services.atom_registry import discover, registry_is_empty

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_MIGRATION = (
    "poindexter.services.migrations."
    "20260919_001943_reseed_podcast_pipeline_for_the_podcastpersist_"
    "audio_qa_carry_forward_contract"
)
_STALE_FP = "8a9a5cd81cbe"  # the fingerprint prod carried before this change


async def test_reseed_restamps_a_row_that_already_carried_a_stamp(test_pool):
    discover()
    if registry_is_empty():
        pytest.skip("atom registry unavailable — nothing to fingerprint against")

    async with test_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT graph_def FROM pipeline_templates "
            "WHERE slug = 'podcast_pipeline' AND active = true",
        )
        assert row is not None, "podcast_pipeline must be seeded active"
        spec = json.loads(row["graph_def"]) if isinstance(
            row["graph_def"], str,
        ) else row["graph_def"]

        # Put the row into the PROD shape: stamped, and stale.
        stale = json.loads(json.dumps(spec))
        for node in stale["nodes"]:
            if node["atom"] == "podcast.persist":
                node["_contract_fp"] = _STALE_FP
                node["_atom_version"] = "1.0.0"
        await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb "
            "WHERE slug = 'podcast_pipeline' AND active = true",
            json.dumps(stale),
        )

        # The gate must REJECT that row — otherwise this test proves nothing.
        with pytest.raises(pa.GraphContractError):
            pa.assert_graph_def_current(stale)

    # Run the migration exactly as the runner would.
    await importlib.import_module(_MIGRATION).up(test_pool)

    async with test_pool.acquire() as conn:
        after = await conn.fetchrow(
            "SELECT graph_def, version FROM pipeline_templates "
            "WHERE slug = 'podcast_pipeline' AND active = true",
        )
    healed = json.loads(after["graph_def"]) if isinstance(
        after["graph_def"], str,
    ) else after["graph_def"]

    # Re-stamped (not merely rewritten un-stamped: an un-stamped row also
    # fails the gate, so the self-heal half of the migration has to have run).
    persist = next(n for n in healed["nodes"] if n["atom"] == "podcast.persist")
    assert persist.get("_contract_fp"), "reseed left the row un-stamped"
    assert persist["_contract_fp"] != _STALE_FP
    pa.assert_graph_def_current(healed)
    assert after["version"] == 3

    # Topology is untouched — only the terminal atom's declared inputs moved.
    assert [n["atom"] for n in healed["nodes"]] == [
        "podcast.load_script", "podcast.render", "qa.audio", "podcast.persist",
    ]
