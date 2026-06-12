import pytest

from modules.content.atoms import seo_optimize_metadata as atom


@pytest.mark.asyncio
async def test_optimizes_toward_target_query(monkeypatch):
    async def fake_llm(state, key, **kw):
        assert kw["target_query"] == "best gpu 2026"   # query threaded into the prompt
        return '{"title": "Best GPU 2026: Tested", "description": "We benchmarked every card."}'
    monkeypatch.setattr(atom.sc, "run_seo_llm", fake_llm)
    state = {"content": "body", "title": "Old", "topic": "Old", "target_query": "best gpu 2026",
             "seo_title": "Old SEO", "seo_description": "old", "site_config": object()}
    out = await atom.run(state)
    assert out["seo_title"].startswith("Best GPU 2026")
    assert len(out["seo_description"]) <= 160
    assert out["stages"]["seo_metadata_optimized"] is True

@pytest.mark.asyncio
async def test_query_empty_falls_back_to_topic(monkeypatch):
    seen = {}
    async def fake_llm(state, key, **kw):
        seen["primary_keyword"] = kw["target_query"] or kw["primary_keyword"]
        return '{"title": "T", "description": "D"}'
    monkeypatch.setattr(atom.sc, "run_seo_llm", fake_llm)
    state = {"content": "b", "title": "Old", "topic": "GPU guide", "target_query": "",
             "tags": ["gpu"], "site_config": object()}
    await atom.run(state)
    assert seen["primary_keyword"] in ("gpu", "GPU guide")   # falls back, never empties

@pytest.mark.asyncio
async def test_llm_failure_preserves_existing_meta(monkeypatch):
    async def boom(*a, **k): raise RuntimeError("ollama down")
    monkeypatch.setattr(atom.sc, "run_seo_llm", boom)
    state = {"content": "b", "title": "Old", "topic": "Old", "target_query": "q",
             "seo_title": "KEEP ME", "seo_description": "KEEP DESC", "site_config": object()}
    out = await atom.run(state)
    # meta_only safety: a failed optimization must NOT blank the live post's meta
    assert out["seo_title"] == "KEEP ME"
    assert out["seo_description"] == "KEEP DESC"
