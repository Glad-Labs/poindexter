"""Tests for the media_pipeline + podcast_pipeline graph_def specs.

Pins the fix for the #1876 ``qa.audio`` contract drift that halted the entire
Stage-2 video lane (``dispatch_media_pipeline`` rejected every dispatch because
the stored graph_def stamp d24ed9f4d409 != current 5e1038ae4850). The source
specs must stay RAW (unstamped) so the boot self-heal re-stamps them to the
current contracts on every boot.
"""

from __future__ import annotations


def test_specs_contain_qa_audio_node_and_are_raw():
    """Both specs carry the drifted qa.audio node and are RAW.

    Raw (no ``_contract_fp`` on any node) is the shape the boot self-heal
    accepts — a pre-stamped spec could not un-stick a stale stamp.
    """
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF
    from services.podcast_pipeline_spec import PODCAST_PIPELINE_GRAPH_DEF

    for spec in (MEDIA_PIPELINE_GRAPH_DEF, PODCAST_PIPELINE_GRAPH_DEF):
        nodes = spec["nodes"]
        assert any(n.get("atom") == "qa.audio" for n in nodes), (
            "spec must contain the qa.audio node"
        )
        assert all("_contract_fp" not in n for n in nodes), (
            "spec must be raw (unstamped) so the boot self-heal re-stamps it"
        )
