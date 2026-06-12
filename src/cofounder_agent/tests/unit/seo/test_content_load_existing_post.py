import pytest
from modules.content.atoms import content_load_existing_post as atom

class _Conn:
    def __init__(self, post, opp):
        self._post, self._opp = post, opp
    async def fetchrow(self, sql, *args):
        return self._post if "FROM posts" in sql else self._opp
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

class _Pool:
    def __init__(self, post, opp): self._c = _Conn(post, opp)
    def acquire(self): return self._c

class _DB:
    def __init__(self, pool): self.pool = pool

@pytest.mark.asyncio
async def test_hydrates_content_title_and_target_query():
    post = {"id": "11111111-1111-1111-1111-111111111111", "title": "Old Title",
            "slug": "old-title", "content": "# Body\n\nUnchanged.",
            "seo_title": "Old SEO", "seo_description": "old desc",
            "seo_keywords": "a, b", "tag_ids": []}
    opp = {"id": "22222222-2222-2222-2222-222222222222",
           "target_query": "best gpu 2026", "current_position": 7.2, "ctr": 0.004}
    state = {"post_id": post["id"], "database_service": _DB(_Pool(post, opp))}
    out = await atom.run(state)
    assert out["content"] == "# Body\n\nUnchanged."     # body carried, never regenerated
    assert out["title"] == "Old Title"
    assert out["post_slug"] == "old-title"
    assert out["target_query"] == "best gpu 2026"
    assert out["seo_opportunity_id"] == opp["id"]
    assert out["topic"] == "Old Title"                  # topic seeds the optimizer fallback

def test_atom_meta_keys_subset_of_pipeline_state():
    from services.template_runner import PipelineState
    keys = set(PipelineState.__annotations__)
    assert set(atom.ATOM_META.produces) <= keys
    assert set(atom.ATOM_META.requires) <= keys
