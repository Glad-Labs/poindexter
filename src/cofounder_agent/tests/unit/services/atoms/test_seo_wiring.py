"""Wiring + registry + prompt-resolution tests for the seo.* atom chain (#362/#734).

#734 collapsed the three serial atoms into seo.generate_all_metadata.
These tests verify the graph_def reflects that change and that both the
individual atoms AND the new combined atom are discoverable.
"""
from services import atom_registry
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as G
from services.prompt_manager import get_prompt_manager


def _node_atom(nid):
    return next(n["atom"] for n in G["nodes"] if n["id"] == nid)


def test_seo_collapsed_node_present_and_old_serial_nodes_gone():
    """The graph_def should have seo_all_metadata, not the three serial nodes."""
    ids = {n["id"] for n in G["nodes"]}
    # New combined node
    assert "seo_all_metadata" in ids
    assert _node_atom("seo_all_metadata") == "seo.generate_all_metadata"
    # Old serial nodes removed from the graph_def
    assert "seo_title" not in ids
    assert "seo_description" not in ids
    assert "seo_keywords" not in ids
    # Old stage also gone
    assert "generate_seo_metadata" not in ids


def test_seo_collapsed_edges():
    """One in-edge from qa_aggregate and one out-edge to generate_media_scripts."""
    edges = {(e["from"], e["to"]) for e in G["edges"]}
    assert ("qa_aggregate", "seo_all_metadata") in edges
    assert ("seo_all_metadata", "generate_media_scripts") in edges
    # Old serial edges gone
    assert ("qa_aggregate", "seo_title") not in edges
    assert ("seo_title", "seo_description") not in edges
    assert ("seo_description", "seo_keywords") not in edges
    assert ("seo_keywords", "generate_media_scripts") not in edges
    # Old stage edge gone
    assert ("qa_aggregate", "generate_seo_metadata") not in edges
    assert ("generate_seo_metadata", "generate_media_scripts") not in edges


def test_atoms_discoverable_in_registry():
    """Both the individual atoms AND the combined atom must be in the registry."""
    atom_registry.discover()
    names = {a.name for a in atom_registry.list_atoms()}
    # Combined atom (used by graph_def)
    assert "seo.generate_all_metadata" in names
    # Individual atoms (retained as standalone importable units)
    assert {"seo.generate_title", "seo.generate_description", "seo.extract_keywords"} <= names


def test_seo_prompts_resolve():
    pm = get_prompt_manager()
    title = pm.get_prompt("atoms.seo.generate_title", topic="t", primary_keyword="k", content="c")
    desc = pm.get_prompt("atoms.seo.generate_description", seo_title="s", topic="t", content="c")
    kw = pm.get_prompt("atoms.seo.extract_keywords", seo_title="s", topic="t", content="c")
    combined = pm.get_prompt(
        "atoms.seo.generate_all_metadata",
        topic="t", primary_keyword="k", content="c",
    )
    assert "k" in title and "title" in title.lower()
    assert "s" in desc and "description" in desc.lower()
    assert "keyword" in kw.lower()
    # Combined prompt must ask for JSON with all three keys
    assert "title" in combined.lower()
    assert "description" in combined.lower()
    assert "keywords" in combined.lower()
