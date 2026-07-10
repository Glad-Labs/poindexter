"""Runtime-parity gate — a seeded+migrated graph_def must still pass the
load-time contract gate against the live atom registry.

The 2026-07-10 canonical_blog outage (PR #2250, fixed by #2261): a contract
change bumped ``content.plan_image_markers``'s fingerprint (``5f20eda72266`` ->
``1bad0eccc418``) and regenerated the committed CI snapshot
(``graph_def_contract_fingerprints.json``) — which SILENCED the PR-time gate
``assert_specs_match_contract_fingerprints`` — but shipped NO reseed migration.
The baseline's FROZEN stamp for that atom stayed stale, so the runtime gate
``assert_graph_def_current`` raised ``GraphContractError`` at load time and
halted every ``canonical_blog`` run. No test exercised the SEEDED row, so it
reached prod.

``assert_seeded_graph_defs_current`` is the missing gate: given every active
``pipeline_templates`` row on a fresh baseline+migrations DB, it re-checks each
STAMPED (frozen) graph_def against the live registry. A FULLY-UNSTAMPED row is
the deliberate output of a graph_def reseed migration — the boot self-heal
(``ensure_active_graph_defs_stamped``) stamps it from the live registry on the
next boot, so on a fresh DB it can never be stale — those are skipped (exactly
what the real, non-masking self-heal does). This is the pure-logic unit layer;
the CI teeth are ``tests/integration_db/test_seeded_graph_defs_current.py``,
which runs this against a real fresh DB after baseline + every migration.
"""

from __future__ import annotations

import pytest

import services.pipeline_architect as pa
from plugins.atom import AtomMeta


def _meta(name: str, *, produces: tuple[str, ...] = ()) -> AtomMeta:
    """A minimal atom meta. ``produces`` feeds ``contract_fingerprint``, so
    varying it is how these tests reproduce a real contract drift (#2250 added
    ``featured_image_subject`` to ``content.plan_image_markers``'s produces)."""
    return AtomMeta(
        name=name,
        type="atom",
        version="1.0.0",
        description="d",
        requires=(),
        produces=produces,
        inputs=(),
        outputs=(),
    )


@pytest.fixture
def registry(monkeypatch):
    """Patch the two atom-registry seams ``assert_graph_def_current`` reaches
    through so the gate resolves against a fixed fake table instead of the live
    registry — and never triggers real ``discover()``. ``registry_is_empty`` ->
    ``False`` so the gate treats the (fake) registry as populated."""
    table: dict[str, AtomMeta] = {}
    monkeypatch.setattr(pa, "get_atom_meta", lambda n: table.get(n))
    monkeypatch.setattr(pa, "registry_is_empty", lambda: False)
    return table


def _stamped_node(node_id: str, atom: str, fp: str) -> dict:
    return {"id": node_id, "atom": atom, "_contract_fp": fp, "_atom_version": "1.0.0"}


def _spec(name: str, nodes: list[dict]) -> dict:
    return {"name": name, "entry": nodes[0]["id"], "nodes": nodes, "edges": []}


class TestAssertSeededGraphDefsCurrent:
    def test_flags_stamped_row_whose_atom_contract_drifted(self, registry):
        # The #2250 shape: the registry now produces an extra output (new
        # fingerprint), but the seeded row still carries the OLD stamp because no
        # reseed migration re-stamped it.
        current = _meta(
            "content.plan_image_markers", produces=("featured_image_subject",)
        )
        registry["content.plan_image_markers"] = current
        old_fp = _meta("content.plan_image_markers").contract_fingerprint()  # produces=()
        assert old_fp != current.contract_fingerprint()  # a genuine drift

        spec = _spec(
            "canonical_blog",
            [_stamped_node("plan_image_markers", "content.plan_image_markers", old_fp)],
        )

        with pytest.raises(pa.GraphContractError) as exc:
            pa.assert_seeded_graph_defs_current([("canonical_blog", spec)])
        # Names the offending template so the dev knows which reseed is missing.
        assert "canonical_blog" in str(exc.value)

    def test_passes_when_stamped_row_matches_registry(self, registry):
        meta = _meta("seo.optimize_metadata")
        registry["seo.optimize_metadata"] = meta
        spec = _spec(
            "seo_refresh",
            [
                _stamped_node(
                    "optimize_meta", "seo.optimize_metadata", meta.contract_fingerprint()
                )
            ],
        )
        pa.assert_seeded_graph_defs_current([("seo_refresh", spec)])  # no raise

    def test_skips_fully_unstamped_row(self, registry):
        # A reseed migration writes the raw spec UNSTAMPED; the boot self-heal
        # stamps it current on the next boot, so it can never be stale on a fresh
        # DB. The gate MUST skip it — otherwise every fresh-DB run trips
        # "unstamped" and main goes red. The atom is deliberately absent from the
        # fake registry to prove the gate never even resolves it.
        spec = _spec(
            "canonical_blog",
            [{"id": "plan_image_markers", "atom": "content.plan_image_markers"}],
        )
        assert pa.is_graph_def_fully_unstamped(spec) is True
        pa.assert_seeded_graph_defs_current([("canonical_blog", spec)])  # no raise

    def test_aggregates_every_stale_row(self, registry):
        registry["content.plan_image_markers"] = _meta(
            "content.plan_image_markers", produces=("featured_image_subject",)
        )
        registry["seo.optimize_metadata"] = _meta(
            "seo.optimize_metadata", produces=("meta_v2",)
        )
        stale_a = _spec(
            "canonical_blog",
            [
                _stamped_node(
                    "plan_image_markers",
                    "content.plan_image_markers",
                    _meta("content.plan_image_markers").contract_fingerprint(),
                )
            ],
        )
        stale_b = _spec(
            "seo_refresh",
            [
                _stamped_node(
                    "optimize_meta",
                    "seo.optimize_metadata",
                    _meta("seo.optimize_metadata").contract_fingerprint(),
                )
            ],
        )
        with pytest.raises(pa.GraphContractError) as exc:
            pa.assert_seeded_graph_defs_current(
                [("canonical_blog", stale_a), ("seo_refresh", stale_b)]
            )
        msg = str(exc.value)
        assert "canonical_blog" in msg and "seo_refresh" in msg

    def test_skips_nodeless_row(self, registry):
        # The column default is '{}'; a node-less active row has no contract to
        # verify (the load path falls back to the legacy factory), so it must not
        # trip the gate.
        pa.assert_seeded_graph_defs_current([("empty", {"name": "empty", "nodes": []})])
