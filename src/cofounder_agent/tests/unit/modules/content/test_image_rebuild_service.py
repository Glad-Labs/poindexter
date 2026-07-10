"""Unit tests for ImageRebuildService (rebuild-images CLI, spec 2026-07-09)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from modules.content.image_rebuild_service import ImageRebuildService, RebuildResult


class FakePool:
    def __init__(self, *, content, version=1, status="awaiting_approval", topic="RAG echo chamber"):
        self._content, self._version, self._status, self._topic = content, version, status, topic
        self.executed: list[tuple] = []

    async def fetchval(self, sql, *args):
        if "status" in sql:
            return self._status
        if "topic" in sql:
            return self._topic
        return None

    async def fetchrow(self, sql, *args):
        return {"content": self._content, "version": self._version,
                "featured_image_url": "https://old/featured.jpg"}

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def _patch_atoms(monkeypatch, *, inline_sources, plan_prompt="a retrieval loop"):
    """Patch the three atoms + featured helpers with deterministic fakes.

    inline_sources: one source string per inline slot, e.g. ["image_gen","pexels"].
    """
    import modules.content.image_rebuild_service as mod

    async def fake_plan(state):
        n = len(inline_sources)
        body = state["content"] + "".join(f"\n[IMAGE-{i+1}: s{i+1}]" for i in range(n))
        return {"content": body,
                "image_plans": [{"num": str(i + 1), "desc": f"s{i+1}"} for i in range(n)],
                "featured_image_plan": {"source": "image_gen", "style": "x", "prompt": plan_prompt}}

    async def fake_generate(state):
        return {"image_results": [
            {"num": p["num"], "url": None if src == "none" else f"https://cdn/{p['num']}.webp",
             "alt_text": p["desc"], "source": src}
            for p, src in zip(state["image_plans"], inline_sources, strict=True)]}

    async def fake_inject(state):
        body = state["content"]
        for r in state["image_results"]:
            body = body.replace(f"[IMAGE-{r['num']}: s{r['num']}]",
                                f'<img src="{r["url"]}" alt="{r["alt_text"]}" />')
        return {"content": body, "inline_images_replaced": len(state["image_results"])}

    monkeypatch.setattr(mod.content_plan_image_markers, "run", fake_plan)
    monkeypatch.setattr(mod.content_generate_images, "run", fake_generate)
    monkeypatch.setattr(mod.content_inject_images, "run", fake_inject)
    # Featured two-strategy: image_gen succeeds by default (returns an uploaded URL).
    monkeypatch.setattr(mod, "_try_image_gen", AsyncMock(return_value="https://cdn/featured.webp"))
    monkeypatch.setattr(mod, "_try_pexels", AsyncMock(return_value=None))


async def test_happy_path_persists_content_and_featured(monkeypatch):
    _patch_atoms(monkeypatch, inline_sources=["image_gen", "image_gen"])
    pool = FakePool(content='intro <img src="old1.jpg" /> mid <img src="old2.jpg" /> end', version=3)
    svc = ImageRebuildService(pool=pool, site_config=object(), image_service=object())

    res = await svc.rebuild_all_images("task-1", allow_stock=False)

    assert isinstance(res, RebuildResult) and res.ok is True
    assert res.inline_total == 2 and res.inline_generated == 2
    assert res.featured_source == "image_gen"
    updates = [e for e in pool.executed if "UPDATE pipeline_versions" in e[0]]
    assert updates, "expected a pipeline_versions write"
    new_content, featured_url, tid, version = updates[0][1]
    assert "old1.jpg" not in new_content and "old2.jpg" not in new_content  # stripped
    assert new_content.count("<img") == 2  # freshly injected
    assert featured_url == "https://cdn/featured.webp"
    assert tid == "task-1" and version == 3
    assert any("regen_images_attempts" in e[0] for e in pool.executed)  # observability bump


async def test_wrong_status_raises(monkeypatch):
    _patch_atoms(monkeypatch, inline_sources=["image_gen"])
    pool = FakePool(content="x", status="published")
    svc = ImageRebuildService(pool=pool, site_config=object(), image_service=object())
    with pytest.raises(ValueError, match="awaiting_approval"):
        await svc.rebuild_all_images("task-1")


async def test_missing_image_service_raises():
    pool = FakePool(content="x")
    svc = ImageRebuildService(pool=pool, site_config=object())  # no image_service
    with pytest.raises(RuntimeError, match="image service"):
        await svc.rebuild_all_images("task-1")


async def test_stock_inline_aborts_without_allow_stock(monkeypatch):
    _patch_atoms(monkeypatch, inline_sources=["image_gen", "pexels"])
    pool = FakePool(content="a <img src='o.jpg' /> b", version=1)
    svc = ImageRebuildService(pool=pool, site_config=object(), image_service=object())

    res = await svc.rebuild_all_images("t1", allow_stock=False)

    assert res.ok is False and res.task_id == "t1"
    assert res.stock_slots == ["inline:2"]
    assert not [e for e in pool.executed if "UPDATE pipeline_versions" in e[0]]  # nothing persisted


async def test_stock_featured_aborts_without_allow_stock(monkeypatch):
    import modules.content.image_rebuild_service as mod
    _patch_atoms(monkeypatch, inline_sources=["image_gen"])
    monkeypatch.setattr(mod, "_try_image_gen", AsyncMock(return_value=None))       # image-gen down
    monkeypatch.setattr(mod, "_try_pexels", AsyncMock(return_value=("p.jpg", "Ann")))  # falls to stock
    pool = FakePool(content="a <img src='o.jpg' /> b", version=1)
    svc = ImageRebuildService(pool=pool, site_config=object(), image_service=object())

    res = await svc.rebuild_all_images("t1", allow_stock=False)

    assert res.ok is False and "featured" in res.stock_slots
    assert not [e for e in pool.executed if "UPDATE pipeline_versions" in e[0]]


async def test_allow_stock_persists_the_mix(monkeypatch):
    _patch_atoms(monkeypatch, inline_sources=["image_gen", "pexels"])
    pool = FakePool(content="a <img src='o.jpg' /> b", version=2)
    svc = ImageRebuildService(pool=pool, site_config=object(), image_service=object())

    res = await svc.rebuild_all_images("t1", allow_stock=True)

    assert res.ok is True and res.inline_generated == 1
    assert [e for e in pool.executed if "UPDATE pipeline_versions" in e[0]]


async def test_empty_slot_aborts_even_with_allow_stock(monkeypatch):
    _patch_atoms(monkeypatch, inline_sources=["image_gen", "none"])  # slot 2 produced nothing
    pool = FakePool(content="a <img src='o.jpg' /> b", version=1)
    svc = ImageRebuildService(pool=pool, site_config=object(), image_service=object())

    res = await svc.rebuild_all_images("t1", allow_stock=True)

    assert res.ok is False
    assert not [e for e in pool.executed if "UPDATE pipeline_versions" in e[0]]
