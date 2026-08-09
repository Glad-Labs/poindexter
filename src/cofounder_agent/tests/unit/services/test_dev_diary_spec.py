"""dev_diary composes SHARED atoms, and preserves what stage.finalize_task did.

The invariant this file exists to hold: **every node in dev_diary except its
writer is a node canonical_blog also runs.** That is what makes "fix an atom
once, every graph improves" true rather than aspirational. When it was false,
a fix landed on a path that no longer executed and dev_diary published for
three months without a meta description (see ``test_dev_diary_seo``).

Also pinned: the migration must not quietly change dev_diary's *behaviour*
while reorganising its nodes. Replacing the coarse ``stage.finalize_task`` is
only safe if the replacement chain still lands ``awaiting_approval`` and still
evaluates the auto-publish gate the stage ran inline.
"""
from __future__ import annotations

import pytest

from services.atom_registry import discover, get_atom_meta
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF
from services.dev_diary_spec import DEV_DIARY_GRAPH_DEF

# The single node dev_diary is allowed to own: its writer. A build-in-public
# status report is narrated from a PR/commit bundle, not researched, so there is
# no shared writer to rent. Anything else appearing here is the divergence
# regressing.
_DEV_DIARY_ONLY = {"atoms.narrate_bundle"}


def _atoms(spec: dict) -> set[str]:
    return {n["atom"] for n in spec["nodes"]}


def _ids(spec: dict) -> list[str]:
    return [n["id"] for n in spec["nodes"]]


class TestEveryNodeButTheWriterIsShared:
    def test_no_unshared_nodes_beyond_the_writer(self):
        unshared = _atoms(DEV_DIARY_GRAPH_DEF) - _atoms(CANONICAL_BLOG_GRAPH_DEF)
        assert unshared == _DEV_DIARY_ONLY, (
            "dev_diary grew a node canonical_blog does not run. Either compose "
            "the shared atom instead, or add it to _DEV_DIARY_ONLY with a "
            "reason — a second implementation of a shared concern is how the "
            "meta-description regression happened."
        )

    def test_the_writer_is_still_dev_diary_specific(self):
        assert _DEV_DIARY_ONLY <= _atoms(DEV_DIARY_GRAPH_DEF)

    def test_no_legacy_coarse_stage_nodes_remain(self):
        """``stage.finalize_task`` and ``stage.generate_seo_metadata`` are the
        two coarse stages this spec replaced. Both still exist on disk (other
        callers/tests reference them), so nothing stops a future edit from
        reintroducing one here."""
        atoms = _atoms(DEV_DIARY_GRAPH_DEF)
        assert "stage.finalize_task" not in atoms
        assert "stage.generate_seo_metadata" not in atoms


class TestBehaviourPreservedFromFinalizeTask:
    """stage.finalize_task did three things; the replacement chain must do all
    three. Dropping any one is a silent capability loss, not a refactor."""

    def test_persists_the_awaiting_approval_record(self):
        """Per-post operator sign-off (``feedback_human_approval``) depends on
        the task landing at awaiting_approval, not published."""
        assert "content.persist_task" in _atoms(DEV_DIARY_GRAPH_DEF)

    def test_records_a_pipeline_version(self):
        assert "content.record_pipeline_version" in _atoms(DEV_DIARY_GRAPH_DEF)

    def test_still_evaluates_the_auto_publish_gate(self):
        """``stage.finalize_task`` imported ``auto_publish_gate.evaluate``
        inline. dev_diary has live opt-in keys (dev_diary_auto_publish_threshold
        = 69, _dry_run = false, _min_clean_runs = 3), so omitting this node
        would have revoked a capability the operator had switched on — while
        every test still passed."""
        assert "content.evaluate_auto_publish" in _atoms(DEV_DIARY_GRAPH_DEF)

    def test_auto_publish_is_evaluated_after_the_task_is_persisted(self):
        ids = _ids(DEV_DIARY_GRAPH_DEF)
        assert ids.index("persist_task") < ids.index("evaluate_auto_publish")


class TestQaRailsStayOff:
    def test_no_qa_rails(self):
        """Deliberate, not an omission: a status report makes no external
        claims, so there is nothing for the fact-check rails to check. If this
        ever needs to change, it is a product decision — the rails are one
        line each to add."""
        assert not [a for a in _atoms(DEV_DIARY_GRAPH_DEF) if a.startswith("qa.")]

    def test_no_social_drafts(self):
        """Adding social.generate_drafts would start posting dev-diary promos —
        a new outward-facing behaviour, not part of a node-sharing migration."""
        assert "social.generate_drafts" not in _atoms(DEV_DIARY_GRAPH_DEF)


class TestSpecIsStructurallySound:
    def test_validates_against_the_live_registry(self):
        import services.pipeline_architect as pa

        discover()
        ok, errors = pa._validate_spec(DEV_DIARY_GRAPH_DEF)
        assert ok, f"spec does not validate: {errors}"

    def test_every_atom_resolves(self):
        discover()
        missing = [
            n["atom"] for n in DEV_DIARY_GRAPH_DEF["nodes"]
            if get_atom_meta(n["atom"]) is None
        ]
        assert missing == []

    def test_edges_form_one_linear_chain_to_end(self):
        ids = _ids(DEV_DIARY_GRAPH_DEF)
        edges = DEV_DIARY_GRAPH_DEF["edges"]
        assert [e["from"] for e in edges] == ids
        assert [e["to"] for e in edges] == ids[1:] + ["END"]
        assert DEV_DIARY_GRAPH_DEF["entry"] == ids[0]

    @pytest.mark.parametrize("key", ["name", "entry", "nodes", "edges"])
    def test_required_top_level_keys(self, key):
        assert DEV_DIARY_GRAPH_DEF.get(key)


class TestSeedMigrationMatchesTheSpec:
    """The migration is what actually reaches prod; a spec edit that forgets to
    bump the reseed leaves the DB on the old graph — exactly the failure this
    whole change is about."""

    def test_migration_seeds_this_spec_object(self):
        import importlib

        mod = importlib.import_module(
            "services.migrations."
            "20260809_180123_reseed_dev_diary_graph_def_onto_shared_canonical_atoms"
        )
        # down() must restore a spec that is NOT the current one, else the
        # rollback is a no-op that silently pretends to revert.
        assert mod._V2_SPEC != DEV_DIARY_GRAPH_DEF
        assert "stage.finalize_task" in {n["atom"] for n in mod._V2_SPEC["nodes"]}
