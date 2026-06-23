"""CI gate: in-tree active graph_def specs must not reference an atom whose
contract has drifted from the committed fingerprint snapshot.

Background (the bug this prevents): PR #1876 changed the ``qa.audio`` atom's
``AtomMeta`` contract (fingerprint ``d24ed9f4d409`` → ``5e1038ae4850``) but
shipped no graph_def reseed. Both the ``media_pipeline`` and ``podcast_pipeline``
graph_defs reference a ``qa_audio`` node, so their stored stamps went stale and
the load-time drift gate (``pipeline_architect.assert_graph_def_current``) halted
the entire Stage-2 video lane in prod. A reseed migration fixed prod, but nothing
in CI caught the *next* atom-contract change that forgets to reseed.

This module is that pre-merge gate. CI can't see prod's ``pipeline_templates``
rows, so a committed snapshot of per-atom contract fingerprints
(``graph_def_contract_fingerprints.json``) stands in for the stored stamps: when
a developer edits an atom's contract, its live ``contract_fingerprint()`` drifts
from the snapshot and these tests go red, naming the stale graph_def + node and
telling the developer to re-seed via a migration (and refresh the snapshot).

Mirrors ``test_graph_def_contract*.py`` + ``test_graph_def_stamp_on_boot.py``.

Regenerate the snapshot after an INTENTIONAL atom-contract change::

    REGEN_GRAPH_DEF_FP=1 poetry run pytest \
        tests/unit/services/test_graph_def_contract_freshness.py::test__regenerate_snapshot
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

import services.pipeline_architect as pa
from plugins.atom import AtomMeta, FieldSpec
from services.atom_registry import discover
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF
from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF
from services.podcast_pipeline_spec import PODCAST_PIPELINE_GRAPH_DEF
from services.seo_refresh_spec import SEO_REFRESH_GRAPH_DEF

# Committed snapshot of per-atom contract fingerprints, keyed by atom name.
# Regenerate after an INTENTIONAL atom-contract change (see module docstring).
_SNAPSHOT_PATH = Path(__file__).with_name("graph_def_contract_fingerprints.json")


def _active_specs() -> dict:
    """The in-tree static graph_def specs seeded ``active=true`` into
    ``pipeline_templates``: ``canonical_blog`` (live content pipeline),
    ``media_pipeline`` / ``podcast_pipeline`` (Stage-2/3 media graphs), and
    ``seo_refresh`` (the gated meta-refresh loop, #763). ``dev_diary`` is
    excluded — it has no graph_def row (it runs off the legacy ``TEMPLATES``
    factory). Add any NEW active graph_def spec here so the gate covers it."""
    return {
        spec["name"]: spec
        for spec in (
            CANONICAL_BLOG_GRAPH_DEF,
            MEDIA_PIPELINE_GRAPH_DEF,
            PODCAST_PIPELINE_GRAPH_DEF,
            SEO_REFRESH_GRAPH_DEF,
        )
    }


def _load_snapshot() -> dict[str, str]:
    return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _meta(
    name: str,
    *,
    requires: tuple[str, ...] = (),
    produces: tuple[str, ...] = (),
    inputs: tuple[FieldSpec, ...] = (),
    outputs: tuple[FieldSpec, ...] = (),
) -> AtomMeta:
    return AtomMeta(
        name=name,
        type="atom",
        version="1.0.0",
        description="d",
        requires=requires,
        produces=produces,
        inputs=inputs,
        outputs=outputs,
    )


@pytest.fixture
def registry(monkeypatch):
    """A fixed fake atom table so the snapshot gate resolves against known
    fingerprints instead of the live registry (mirrors test_graph_def_contract)."""
    table = {
        "atoms.draft": _meta("atoms.draft", produces=("draft",)),
        "atoms.title": _meta("atoms.title", requires=("draft",), produces=("title",)),
    }
    monkeypatch.setattr(pa, "get_atom_meta", lambda n: table.get(n))
    return table


def _spec(name: str = "t") -> dict:
    return {
        "name": name,
        "nodes": [
            {"id": "a", "atom": "atoms.draft", "config": {}},
            {"id": "b", "atom": "atoms.title", "config": {}},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "END"}],
    }


class TestCurrentAtomFingerprints:
    def test_returns_fingerprint_per_referenced_atom(self, registry):
        fps = pa.current_atom_fingerprints({"t": _spec()})
        assert set(fps) == {"atoms.draft", "atoms.title"}
        assert fps["atoms.title"] == registry["atoms.title"].contract_fingerprint()

    def test_unions_atoms_across_specs(self, registry):
        s1 = {"name": "s1", "nodes": [{"id": "a", "atom": "atoms.draft"}], "edges": []}
        s2 = {"name": "s2", "nodes": [{"id": "b", "atom": "atoms.title"}], "edges": []}
        assert set(pa.current_atom_fingerprints({"s1": s1, "s2": s2})) == {
            "atoms.draft",
            "atoms.title",
        }


class TestAssertSpecsMatchSnapshot:
    def test_passes_when_snapshot_matches_live_registry(self, registry):
        specs = {"t": _spec()}
        snapshot = pa.current_atom_fingerprints(specs)
        pa.assert_specs_match_contract_fingerprints(specs, snapshot)  # no raise

    def test_drift_raises_naming_spec_node_and_atom(self, registry):
        specs = {"t": _spec()}
        snapshot = pa.current_atom_fingerprints(specs)
        snapshot["atoms.title"] = "deadbeefcafe"  # a stale stored stamp
        with pytest.raises(pa.GraphContractError) as excinfo:
            pa.assert_specs_match_contract_fingerprints(specs, snapshot)
        msg = str(excinfo.value)
        assert "t" in msg  # graph_def name
        assert "b" in msg  # node id
        assert "atoms.title" in msg  # atom
        assert "deadbeefcafe" in msg  # the stale fingerprint
        assert "re-seed" in msg.lower()  # remediation

    def test_atom_missing_from_registry_raises(self, registry):
        specs = {"t": _spec()}
        snapshot = pa.current_atom_fingerprints(specs)
        del registry["atoms.title"]
        with pytest.raises(pa.GraphContractError, match="atoms.title"):
            pa.assert_specs_match_contract_fingerprints(specs, snapshot)

    def test_atom_absent_from_snapshot_raises(self, registry):
        # A new node whose atom was never added to the snapshot must fail loud,
        # so adding a node also forces a snapshot refresh + reseed.
        specs = {"t": _spec()}
        snapshot = {"atoms.draft": registry["atoms.draft"].contract_fingerprint()}
        with pytest.raises(pa.GraphContractError, match="atoms.title"):
            pa.assert_specs_match_contract_fingerprints(specs, snapshot)

    def test_reports_every_drift_not_just_the_first(self, registry):
        specs = {"t": _spec()}
        snapshot = {"atoms.draft": "aaaaaaaaaaaa", "atoms.title": "bbbbbbbbbbbb"}
        with pytest.raises(pa.GraphContractError) as excinfo:
            pa.assert_specs_match_contract_fingerprints(specs, snapshot)
        msg = str(excinfo.value)
        assert "atoms.draft" in msg and "atoms.title" in msg


class TestActiveSpecsAgainstLiveRegistry:
    """The real gate: the in-tree active specs, checked against the live atom
    registry + the committed fingerprint snapshot. These run in CI (test-backend)
    with no DB — discovery resolves atoms from ``modules.content.atoms.*`` + the
    surfaced Stage plugins, exactly as the sibling spec tests do."""

    def test_each_active_spec_round_trips_stamp_and_assert(self):
        """Per the design note: each spec, stamped against the live registry,
        round-trips ``assert_graph_def_current``. This catches a node that names
        a renamed / deleted atom (``stamp_graph_def`` raises) — the failure mode
        the tautological stamp→assert pair *does* catch."""
        discover()
        for name, spec in _active_specs().items():
            try:
                pa.assert_graph_def_current(pa.stamp_graph_def(spec))
            except pa.GraphContractError as exc:  # pragma: no cover - failure path
                pytest.fail(f"graph_def {name!r} does not round-trip: {exc}")

    def test_active_specs_match_committed_snapshot(self):
        """THE freshness gate. Fails when an atom's live ``contract_fingerprint()``
        drifts from the committed snapshot — i.e. someone edited an atom contract
        without re-seeding the graph_defs that reference it (the #1876 cause)."""
        discover()
        try:
            pa.assert_specs_match_contract_fingerprints(_active_specs(), _load_snapshot())
        except pa.GraphContractError as exc:
            pytest.fail(
                f"{exc}\n\n"
                "An atom's contract changed without a graph_def reseed. To fix:\n"
                "  1. Re-seed the affected graph_def(s) via a new migration (or "
                "rely on the boot-time stamp self-heal, ensure_active_graph_defs_"
                "stamped).\n"
                "  2. Refresh the committed snapshot:\n"
                "     REGEN_GRAPH_DEF_FP=1 poetry run pytest "
                "tests/unit/services/test_graph_def_contract_freshness.py"
                "::test__regenerate_snapshot"
            )

    def test_committed_snapshot_has_no_stale_entries(self):
        """Every snapshot key is actually referenced by an active spec — so a
        removed node also forces a snapshot refresh (no orphan fingerprints)."""
        discover()
        referenced = {
            node["atom"]
            for spec in _active_specs().values()
            for node in spec["nodes"]
        }
        orphans = set(_load_snapshot()) - referenced
        assert not orphans, (
            f"snapshot has fingerprints for atoms no active spec references: "
            f"{sorted(orphans)} — regenerate the snapshot"
        )

    def test_simulated_atom_contract_change_is_caught(self, monkeypatch):
        """End-to-end proof the gate is NOT tautological: simulate the #1876
        change to ``qa.audio``'s I/O contract and confirm the gate trips against
        the *committed* snapshot, naming both graph_defs that reference it.

        Mirrors ``test_graph_def_contract_integration.test_stamped_passes_then_
        drift_refuses`` but for the snapshot gate."""
        discover()
        real = pa.get_atom_meta("qa.audio")
        if real is None:
            pytest.skip("qa.audio not in registry in this environment")
        assert real is not None  # narrowed for the type checker (skip raises above)
        original = pa.get_atom_meta
        drifted = replace(real, requires=real.requires + ("__synthetic_drift__",))
        monkeypatch.setattr(
            pa, "get_atom_meta",
            lambda n: drifted if n == "qa.audio" else original(n),
        )
        with pytest.raises(pa.GraphContractError) as excinfo:
            pa.assert_specs_match_contract_fingerprints(
                _active_specs(), _load_snapshot()
            )
        msg = str(excinfo.value)
        assert "qa.audio" in msg
        # Both Stage-2/3 graphs reference qa.audio → both named (the #1876 blast).
        assert "media_pipeline" in msg
        assert "podcast_pipeline" in msg
        assert "re-seed" in msg.lower()


@pytest.mark.skipif(
    os.environ.get("REGEN_GRAPH_DEF_FP") != "1",
    reason="set REGEN_GRAPH_DEF_FP=1 to regenerate the contract fingerprint snapshot",
)
def test__regenerate_snapshot():
    """Dev-only: rewrite the committed snapshot from the live registry. Runs
    under pytest so discovery + sys.path match the gate's exactly.

        REGEN_GRAPH_DEF_FP=1 poetry run pytest \\
            tests/unit/services/test_graph_def_contract_freshness.py::test__regenerate_snapshot
    """
    discover()
    fingerprints = pa.current_atom_fingerprints(_active_specs())
    assert fingerprints, "registry yielded no atoms — refusing to write empty snapshot"
    # newline="\n" so the snapshot is byte-identical across platforms (no CRLF
    # churn when regenerated on Windows).
    _SNAPSHOT_PATH.write_text(
        json.dumps(fingerprints, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
