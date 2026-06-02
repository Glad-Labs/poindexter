"""The caption_images node is wired between source_featured_image and qa_critic."""
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF as S


def test_caption_images_node_present():
    ids = [n["id"] for n in S["nodes"]]
    assert "caption_images" in ids


def test_caption_images_wired_between_featured_and_qa():
    edges = {(e["from"], e["to"]) for e in S["edges"]}
    assert ("source_featured_image", "caption_images") in edges
    assert ("caption_images", "qa_critic") in edges
    # the old direct edge is gone
    assert ("source_featured_image", "qa_critic") not in edges
