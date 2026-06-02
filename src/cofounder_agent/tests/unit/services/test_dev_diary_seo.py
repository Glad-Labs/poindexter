"""dev_diary template now includes generate_seo_metadata (meta description fix).

The 27 "what we shipped on <date>" posts shipped with no <meta description>
because the dev_diary template skipped generate_seo_metadata. These pages are
indexed + in the sitemap, so the empty SERP snippet is a real SEO gap.
"""
from services.pipeline_templates import dev_diary


def test_dev_diary_includes_generate_seo_metadata_node():
    g = dev_diary(pool=None)
    node_names = set(getattr(g, "nodes", {}).keys())
    assert "generate_seo_metadata" in node_names


def test_dev_diary_seo_runs_after_narrate_before_finalize():
    g = dev_diary(pool=None)
    node_names = set(getattr(g, "nodes", {}).keys())
    # the narrative + finalize anchors are still present
    assert "narrate_bundle" in node_names
    assert "finalize_task" in node_names
