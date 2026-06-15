"""Graph-shape + compile tests for the media_pipeline graph_def (#689).

These guard the two things that silently break a graph_def pipeline:

1. **Node order** — ``render_narration`` must sit immediately after
   ``load_scripts`` (it needs the loaded scripts) and before
   ``transcribe_narration`` (which needs the narration audio it produces).
2. **Compile + schema** — ``build_graph_from_spec`` resolves every atom AND
   runs the #753 PipelineState-declaration check, so a compile here proves
   ``media.render_narration``'s output channels are declared (the #674 trap).
"""
from __future__ import annotations

import pytest


def test_media_pipeline_spec_has_narration_node():
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF

    ids = [n["id"] for n in MEDIA_PIPELINE_GRAPH_DEF["nodes"]]
    assert "render_narration" in ids
    # render_narration runs right after load_scripts, before transcribe.
    assert ids.index("render_narration") == ids.index("load_scripts") + 1
    assert ids.index("render_narration") < ids.index("transcribe_narration")


def test_media_pipeline_narration_edges_rewired():
    """load_scripts → render_narration → transcribe_narration (no skip edge)."""
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF

    edges = {(e["from"], e["to"]) for e in MEDIA_PIPELINE_GRAPH_DEF["edges"]}
    assert ("load_scripts", "render_narration") in edges
    assert ("render_narration", "transcribe_narration") in edges
    # the old direct edge must be gone, or the new node is bypassed.
    assert ("load_scripts", "transcribe_narration") not in edges


@pytest.mark.asyncio
async def test_media_pipeline_graph_compiles():
    """build_graph_from_spec resolves every atom + passes the #753 schema check."""
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF
    from services.pipeline_architect import build_graph_from_spec

    # pool=None is fine: atoms resolve via the registry; no DB needed to compile.
    graph = build_graph_from_spec(MEDIA_PIPELINE_GRAPH_DEF, pool=None)
    assert graph is not None
