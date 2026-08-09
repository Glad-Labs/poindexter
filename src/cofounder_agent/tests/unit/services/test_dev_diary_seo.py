"""dev_diary's graph_def must carry an SEO node — asserted on the thing that RUNS.

This file used to import the ``dev_diary`` TEMPLATES **factory** and assert the
node existed there. It passed for three months while every dev_diary post
shipped without a ``<meta description>``, because the factory had stopped being
the executed path: ``pipeline_use_graph_def=true`` plus an active dev_diary
graph_def row wins in ``TemplateRunner.run``, and the stored spec never received
the 2026-06-02 fix. Measured damage before the v3 reseed — dev_diary posts
missing ``seo_description``: 5/25 May, 13/25 June, 15/25 July, 5/5 August,
against 0/40 for canonical_blog.

So: assert against ``DEV_DIARY_GRAPH_DEF``, which is what the seed migration
writes and the runner compiles. A test that pins a graph nobody executes is
worse than no test — it is a green light on a broken path.
"""
from __future__ import annotations

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF
from services.dev_diary_spec import DEV_DIARY_GRAPH_DEF


def _atoms(spec: dict) -> set[str]:
    return {n["atom"] for n in spec["nodes"]}


def _ids(spec: dict) -> list[str]:
    return [n["id"] for n in spec["nodes"]]


class TestSeoNodeIsOnTheExecutedGraph:
    def test_graph_def_has_an_seo_metadata_node(self):
        assert "seo.generate_all_metadata" in _atoms(DEV_DIARY_GRAPH_DEF)

    def test_it_is_the_same_atom_canonical_blog_uses(self):
        """The point of the migration: one implementation, not two.

        The retired ``stage.generate_seo_metadata`` reached neither the
        draft-grounding nor the topic-echo guard in ``atoms/_seo_common.py`` —
        it imports ``services.seo_content_generator`` instead.
        """
        assert "seo.generate_all_metadata" in _atoms(CANONICAL_BLOG_GRAPH_DEF)
        assert "stage.generate_seo_metadata" not in _atoms(DEV_DIARY_GRAPH_DEF)

    def test_seo_runs_after_the_writer_so_it_is_draft_grounded(self):
        """``seo.generate_all_metadata`` requires ``content``; only the writer
        produces it. Ordering it before ``narrate_bundle`` would leave the SEO
        call describing an empty draft."""
        ids = _ids(DEV_DIARY_GRAPH_DEF)
        assert ids.index("narrate_bundle") < ids.index("seo_all_metadata")

    def test_the_narrative_and_persist_anchors_are_still_present(self):
        atoms = _atoms(DEV_DIARY_GRAPH_DEF)
        assert "atoms.narrate_bundle" in atoms
        assert "content.persist_task" in atoms
