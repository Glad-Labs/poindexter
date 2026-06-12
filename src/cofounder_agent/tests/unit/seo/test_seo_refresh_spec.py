"""The seo_refresh graph_def must compile cleanly (SEO Harvest Loop Phase 2 #763).

Asserts the spec passes BOTH validators the seed path exercises:
  - ``_validate_spec`` — reachability + DAG (every atom's requires are
    satisfiable from upstream produces or PipelineState-seeded initial state);
  - ``build_graph_from_spec`` — the #753 schema gate (every atom's
    produces/requires key is declared in PipelineState, else ValueError).

This is the design's acceptance criterion #1.
"""

import pytest

from services.atom_registry import discover
from services.pipeline_architect import _validate_spec, build_graph_from_spec
from services.seo_refresh_spec import SEO_REFRESH_GRAPH_DEF


@pytest.fixture(autouse=True)
def _ensure_atoms_discovered():
    discover()


def test_spec_passes_reachability_and_dag():
    ok, errors = _validate_spec(SEO_REFRESH_GRAPH_DEF)
    assert ok, errors


def test_spec_passes_schema_gate():
    # build_graph_from_spec raises ValueError on any undeclared produces/requires
    # key (#753). pool=None is fine — no node executes at compile time.
    graph = build_graph_from_spec(SEO_REFRESH_GRAPH_DEF, pool=None)
    assert graph is not None


def test_never_regenerates_body():
    atoms = {n["atom"] for n in SEO_REFRESH_GRAPH_DEF["nodes"]}
    assert "content.generate_draft" not in atoms          # never regenerate the body
    assert "content.load_existing_post" in atoms          # hydrate instead
    assert "content.republish_post" in atoms              # meta_only republish
    assert "seo.optimize_metadata" in atoms


def test_approval_first_gate_present():
    gate = next(
        n for n in SEO_REFRESH_GRAPH_DEF["nodes"] if n["atom"] == "atoms.approval_gate"
    )
    assert gate["config"]["gate_name"] == "seo_refresh_gate"
