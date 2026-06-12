"""Characterization spike: lock graph-entry behavior for seo_refresh.

This is a CHARACTERIZATION test — it passes on first run, confirming
existing behavior we depend on. It proves that _validate_spec treats
PipelineState keys as seedable initial state (the basis for entering a
graph from an existing post id rather than generating a draft).

Task 5 (SEO Harvest Loop Phase 2 #763) will read post_id back from:
    pipeline_versions.stage_data -> 'task_metadata' -> 'post_id'
or, for rows written after the posts.metadata seam (migration 20260528_021920):
    posts.metadata ->>'pipeline_task_id'
The canonical path at runtime is pipeline_versions.stage_data JSONB column,
projected as task_metadata through the content_tasks VIEW (see tasks_db.add_task
lines 269-273: stage_data['task_metadata'] is the JSON object, and the view
exposes it as the task_metadata column). post_id is nested inside task_metadata
(or directly as pipeline_versions.stage_data -> 'task_metadata' -> 'post_id').
"""

import pytest
from services.atom_registry import discover
from services.pipeline_architect import _validate_spec


@pytest.fixture(autouse=True)
def _ensure_atoms_discovered():
    """Ensure the atom catalog is populated before any test in this module.

    Mirrors the pattern used in test_pipeline_architect_schema.py and
    test_pipeline_architect_validate.py — atom_registry.discover() is
    idempotent so calling it here is safe even if another test already ran it.
    """
    discover()


def test_minimal_post_id_entry_spec_validates():
    """A one-node spec whose atom requires only PipelineState-declared keys
    must pass _validate_spec without any upstream produces.

    content.check_title_originality requires only 'title', which is declared
    in PipelineState. This proves PipelineState keys are treated as seedable
    initial state — the basis for post_id-driven graph entry in seo_refresh:
    the caller seeds {post_id, title, ...} into the initial state dict and the
    first atom can read those keys without needing a prior produces step.
    """
    spec = {
        "name": "seo_refresh_spike",
        "entry": "load",
        "nodes": [{"id": "load", "atom": "content.check_title_originality"}],
        "edges": [{"from": "load", "to": "END"}],
    }
    # content.check_title_originality requires only keys already in PipelineState,
    # so a one-node spec passes reachability — proving PipelineState keys are
    # treated as seedable initial state (the basis for post_id-driven entry).
    ok, errors = _validate_spec(spec)
    assert ok, errors
