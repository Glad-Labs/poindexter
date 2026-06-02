"""Wiring + registry + prompt-resolution tests for the seo.* atom chain (#362)."""
from services import atom_registry
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as G
from services.prompt_manager import get_prompt_manager


def _node_atom(nid):
    return next(n["atom"] for n in G["nodes"] if n["id"] == nid)


def test_seo_nodes_present_and_old_stage_gone():
    ids = {n["id"] for n in G["nodes"]}
    assert {"seo_title", "seo_description", "seo_keywords"} <= ids
    assert "generate_seo_metadata" not in ids
    assert _node_atom("seo_title") == "seo.generate_title"
    assert _node_atom("seo_description") == "seo.generate_description"
    assert _node_atom("seo_keywords") == "seo.extract_keywords"


def test_seo_chain_edges():
    edges = {(e["from"], e["to"]) for e in G["edges"]}
    assert ("qa_aggregate", "seo_title") in edges
    assert ("seo_title", "seo_description") in edges
    assert ("seo_description", "seo_keywords") in edges
    assert ("seo_keywords", "generate_media_scripts") in edges
    # old direct edges removed
    assert ("qa_aggregate", "generate_seo_metadata") not in edges
    assert ("generate_seo_metadata", "generate_media_scripts") not in edges


def test_atoms_discoverable_in_registry():
    atom_registry.discover()
    names = {a.name for a in atom_registry.list_atoms()}
    assert {"seo.generate_title", "seo.generate_description", "seo.extract_keywords"} <= names


def test_seo_prompts_resolve():
    pm = get_prompt_manager()
    title = pm.get_prompt("atoms.seo.generate_title", topic="t", primary_keyword="k", content="c")
    desc = pm.get_prompt("atoms.seo.generate_description", seo_title="s", topic="t", content="c")
    kw = pm.get_prompt("atoms.seo.extract_keywords", seo_title="s", topic="t", content="c")
    assert "k" in title and "title" in title.lower()
    assert "s" in desc and "description" in desc.lower()
    assert "keyword" in kw.lower()
