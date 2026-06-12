"""End-to-end: the seo_refresh graph runs load → optimize → (gate off) → republish.

Drives the REAL graph_def through build_graph_from_spec + a MemorySaver-compiled
LangGraph, with the LLM call and R2/ISR propagation stubbed and the approval gate
disabled, asserting the live post's meta is updated, the static export + ISR
revalidation fire, and the opportunity row is stamped 'refreshed'. Body is never
written. Gate-ENABLED interrupt/resume is covered by the existing approval_gate
tests (modules/content/atoms/approval_gate.py) and not re-tested here.

Issue: Glad-Labs/poindexter#763 (SEO Harvest Loop Phase 2).
"""

import pytest
from langgraph.checkpoint.memory import MemorySaver

from modules.content.atoms import _seo_common, content_republish_post
from services.atom_registry import discover
from services.pipeline_architect import build_graph_from_spec
from services.seo_refresh_spec import SEO_REFRESH_GRAPH_DEF
from services.site_config import SiteConfig

_POST_ID = "11111111-1111-1111-1111-111111111111"
_OPP_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _discovered():
    discover()


class _Conn:
    def __init__(self, calls):
        self._calls = calls

    async def fetchrow(self, sql, *a):
        if "FROM posts" in sql:
            return {
                "id": _POST_ID, "title": "Old GPU Roundup", "slug": "gpu-roundup",
                "content": "# Body\n\nUnchanged body.", "seo_title": "Old SEO",
                "seo_description": "old desc", "seo_keywords": "gpu, review",
                "tag_ids": [],
            }
        return {
            "id": _OPP_ID, "target_query": "best gpu 2026",
            "current_position": 7.2, "ctr": 0.004,
        }

    async def execute(self, sql, *a):
        if "UPDATE posts" in sql:
            self._calls["update_post"] = a
            # meta_only invariant — the SET clause must not touch content.
            assert "content" not in sql.lower().split("set", 1)[1]
        elif "UPDATE seo_opportunities" in sql:
            self._calls["stamp"] = a

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, calls):
        self._calls = calls

    def acquire(self):
        return _Conn(self._calls)


class _DB:
    def __init__(self, calls):
        self.pool = _Pool(calls)


@pytest.mark.asyncio
async def test_seo_refresh_graph_runs_to_republish(monkeypatch):
    calls: dict = {}

    async def fake_llm(state, key, **kw):
        return (
            '{"title": "Best GPUs 2026: Tested & Ranked", '
            '"description": "Independent benchmarks of every current card."}'
        )

    monkeypatch.setattr(_seo_common, "run_seo_llm", fake_llm)

    async def fake_export(pool, slug, *, site_config):
        calls["export"] = slug
        return True

    async def fake_reval(slug, *, site_config):
        calls["reval"] = slug
        return True

    monkeypatch.setattr(content_republish_post, "export_post", fake_export)
    monkeypatch.setattr(content_republish_post, "trigger_isr_revalidate", fake_reval)

    sc = SiteConfig(initial_config={"pipeline_gate_seo_refresh_gate": "false"})
    db = _DB(calls)

    graph = build_graph_from_spec(SEO_REFRESH_GRAPH_DEF, pool=None)
    compiled = graph.compile(checkpointer=MemorySaver())
    config = {
        "configurable": {
            "thread_id": "t1",
            "__services__": {"database_service": db, "site_config": sc},
        }
    }
    final = await compiled.ainvoke({"post_id": _POST_ID, "task_id": "t1"}, config)

    assert calls.get("update_post") is not None, "republish must UPDATE the post meta"
    # $2 = seo_title — the optimized title was applied.
    assert "Best GPUs 2026" in calls["update_post"][1]
    assert calls.get("export") == "gpu-roundup"
    assert calls.get("reval") == "gpu-roundup"
    assert calls.get("stamp") is not None
    assert final.get("status") == "refreshed"
