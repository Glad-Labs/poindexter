"""The image_rebuild graph_def must compile cleanly.

Mirrors tests/unit/seo/test_seo_refresh_spec.py: asserts the spec passes BOTH
validators the seed path exercises:
  - ``_validate_spec`` — reachability + DAG (every atom's requires are
    satisfiable from upstream produces or PipelineState-seeded initial state);
  - ``build_graph_from_spec`` — the #753 schema gate (every atom's
    produces/requires key is declared in PipelineState, else ValueError).
"""

import pytest

from services.atom_registry import discover
from services.image_rebuild_spec import IMAGE_REBUILD_GRAPH_DEF
from services.pipeline_architect import _validate_spec, build_graph_from_spec


@pytest.fixture(autouse=True)
def _ensure_atoms_discovered():
    discover()


def test_spec_passes_reachability_and_dag():
    ok, errors = _validate_spec(IMAGE_REBUILD_GRAPH_DEF)
    assert ok, errors


def test_spec_passes_schema_gate():
    # build_graph_from_spec raises ValueError on any undeclared produces/requires
    # key (#753). pool=None is fine — no node executes at compile time.
    graph = build_graph_from_spec(IMAGE_REBUILD_GRAPH_DEF, pool=None)
    assert graph is not None


def test_reuses_the_canonical_image_atoms():
    atoms = {n["atom"] for n in IMAGE_REBUILD_GRAPH_DEF["nodes"]}
    # The rebuild composes the SAME image atoms canonical_blog runs — the whole
    # point of the requeue design (no forked image logic).
    assert "content.plan_image_markers" in atoms
    assert "content.generate_images" in atoms
    assert "content.inject_images" in atoms
    # And never touches the writer.
    assert "content.generate_draft" not in atoms


def test_gate_runs_before_any_write():
    """The fail-loud gate must precede inject/persist so an aborted rebuild
    leaves the target draft byte-for-byte unchanged."""
    order = [n["id"] for n in IMAGE_REBUILD_GRAPH_DEF["nodes"]]
    assert order.index("gate") < order.index("inject") < order.index("persist")
    edges = {(e["from"], e["to"]) for e in IMAGE_REBUILD_GRAPH_DEF["edges"]}
    assert ("gate", "inject") in edges and ("inject", "persist") in edges


def test_finalize_is_terminal():
    """persist writes the draft, then finalize (atoms.set_task_status) closes out
    the rebuild JOB row — see test_image_rebuild_terminal_contract.py for the
    regression guard on *why* (the in_progress-stranding loop)."""
    edges = {(e["from"], e["to"]) for e in IMAGE_REBUILD_GRAPH_DEF["edges"]}
    assert ("persist", "finalize") in edges
    assert ("finalize", "END") in edges
