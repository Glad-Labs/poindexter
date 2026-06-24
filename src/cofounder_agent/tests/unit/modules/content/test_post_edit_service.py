"""Unit tests for PostEditService (poindexter#523).

Drafts-only body/image editing. Each test drives one service method against a
minimal fake pool that records ``execute`` calls and returns a canned
latest-``pipeline_versions``-row from ``fetchrow``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from modules.content.post_edit_service import EditResult, PostEditService


class _FakeAuditCap:
    """Stand-in for ``platform.audit`` — records ``write`` calls (no DB)."""

    def __init__(self):
        self.write = AsyncMock(return_value=None)


class _FakePlatform:
    """Minimal ``Platform`` handle exposing only the ``audit`` capability."""

    def __init__(self):
        self.audit = _FakeAuditCap()


class FakePool:
    """Minimal asyncpg-pool stand-in: canned latest-version row + execute log."""

    def __init__(self, content: str = "body", version: int = 1, task_status: str = "awaiting_approval"):
        self._content = content
        self._version = version
        self._task_status = task_status
        self.executed: list[tuple] = []

    async def fetchrow(self, sql, *args):
        if "pipeline_versions" in sql:
            return {"content": self._content, "version": self._version}
        return None

    async def fetchval(self, sql, *args):
        if "pipeline_tasks" in sql:
            return self._task_status
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


# ---------------------------------------------------------------------------
# edit_body
# ---------------------------------------------------------------------------


async def test_edit_body_find_replace_writes_latest_version():
    pool = FakePool(content="Intro [memory/note.md] tail", version=5)
    svc = PostEditService(pool=pool)

    result = await svc.edit_body("task-1", find="[memory/note.md] ", replace="")

    assert isinstance(result, EditResult)
    assert result.ok is True
    assert result.field == "body"
    updates = [e for e in pool.executed if "UPDATE pipeline_versions" in e[0]]
    assert updates, "expected an UPDATE to pipeline_versions"
    sql, args = updates[0]
    assert args[0] == "Intro tail"   # new content (find removed)
    assert args[1] == "task-1"       # task_id bind
    assert args[2] == 5              # version bind


async def test_edit_body_new_content_overwrites():
    pool = FakePool(content="old", version=2)
    svc = PostEditService(pool=pool)

    result = await svc.edit_body("t9", new_content="brand new body")

    assert result.ok and result.field == "body"
    updates = [e for e in pool.executed if "UPDATE pipeline_versions" in e[0]]
    assert updates[0][1][0] == "brand new body"


async def test_edit_body_missing_find_raises():
    svc = PostEditService(pool=FakePool(content="no token here"))
    with pytest.raises(ValueError, match="find string not present"):
        await svc.edit_body("t1", find="ZZZ", replace="x")


async def test_edit_body_audits_through_platform_handle():
    """The audit row routes through ``platform.audit.write`` (capability seam),
    not a direct ``services.audit_log`` import (module-purity boundary)."""
    pool = FakePool(content="old body", version=3)
    platform = _FakePlatform()
    svc = PostEditService(pool=pool, platform=platform)

    await svc.edit_body("t7", new_content="new body")

    platform.audit.write.assert_awaited_once()
    args, kwargs = platform.audit.write.call_args
    assert args[0] == "post_edit_body"          # event_type (positional)
    assert kwargs["source"] == "post_edit_service"
    assert kwargs["task_id"] == "t7"
    assert kwargs["details"]["task_id"] == "t7"


async def test_edit_body_without_platform_skips_audit():
    """No handle wired → audit drops, edit still persists (best-effort posture)."""
    pool = FakePool(content="old body", version=1)
    svc = PostEditService(pool=pool)  # no platform

    result = await svc.edit_body("t1", new_content="new body")

    assert result.ok  # the edit itself still succeeded without a handle


async def test_edit_body_requires_an_edit_arg():
    svc = PostEditService(pool=FakePool())
    with pytest.raises(ValueError, match="new_content or find/replace"):
        await svc.edit_body("t1")


# ---------------------------------------------------------------------------
# replace_image
# ---------------------------------------------------------------------------


async def test_replace_inline_image_rewrites_nth_src():
    body = '<p>a</p><img src="old1.png"><p>b</p><img src="old2.png">'
    pool = FakePool(content=body, version=2)
    svc = PostEditService(pool=pool)

    result = await svc.replace_image("t1", which="inline:2", url="new2.png")

    assert result.ok and result.field == "inline:2" and result.new_url == "new2.png"
    updates = [e for e in pool.executed if "UPDATE pipeline_versions SET content" in e[0]]
    assert updates, "expected a content UPDATE"
    new_body = updates[0][1][0]
    assert 'src="new2.png"' in new_body   # 2nd img rewritten
    assert 'src="old1.png"' in new_body   # 1st img untouched


async def test_replace_inline_missing_index_raises():
    pool = FakePool(content='<img src="only.png">', version=1)
    svc = PostEditService(pool=pool)
    with pytest.raises(ValueError, match="inline image #3 not found"):
        await svc.replace_image("t1", which="inline:3", url="x.png")


async def test_replace_featured_updates_version_column():
    pool = FakePool(content="body", version=4)
    svc = PostEditService(pool=pool)

    result = await svc.replace_image("t1", which="featured", url="https://cdn/x.png")

    assert result.ok and result.field == "featured"
    assert result.new_url == "https://cdn/x.png"
    featured = [e for e in pool.executed if "featured_image_url" in e[0]]
    assert featured and featured[0][1][0] == "https://cdn/x.png"


async def test_replace_image_bad_which_raises():
    svc = PostEditService(pool=FakePool())
    with pytest.raises(ValueError, match="featured.*inline"):
        await svc.replace_image("t1", which="banner", url="x.png")


# ---------------------------------------------------------------------------
# regen_image
# ---------------------------------------------------------------------------


class _FakeImageSvc:
    """Writes dummy bytes to output_path and reports success/failure."""

    def __init__(self, ok: bool = True):
        self._ok = ok

    async def generate_image(self, *, prompt, output_path, negative_prompt=""):
        if self._ok:
            with open(output_path, "wb") as f:
                f.write(b"PNGDATA")
        return self._ok


async def test_regen_image_generates_then_replaces(monkeypatch):
    pool = FakePool(content="body", version=1)

    async def fake_upload(self, path, task_id):
        return "https://cdn/generated/new.webp"

    monkeypatch.setattr(PostEditService, "_upload_image", fake_upload, raising=True)
    svc = PostEditService(pool=pool, image_service=_FakeImageSvc())

    result = await svc.regen_image("t1", which="featured", prompt="a teal robot")

    assert result.ok and result.field == "featured"
    assert result.new_url == "https://cdn/generated/new.webp"
    assert any("featured_image_url" in e[0] for e in pool.executed)


async def test_regen_image_requires_image_service():
    svc = PostEditService(pool=FakePool())
    with pytest.raises(RuntimeError, match="image service not available"):
        await svc.regen_image("t1", which="featured", prompt="x")


async def test_regen_image_raises_when_generation_fails():
    svc = PostEditService(pool=FakePool(), image_service=_FakeImageSvc(ok=False))
    with pytest.raises(RuntimeError, match="produced no output"):
        await svc.regen_image("t1", which="featured", prompt="x")


# ---------------------------------------------------------------------------
# _sync_published_post_featured — published-post featured image propagation
# ---------------------------------------------------------------------------


async def test_sync_published_post_featured_updates_posts_row(monkeypatch):
    """For a published task, posts.featured_image_url must be updated so the
    static export reads the new URL. Regression test for the bug where only
    pipeline_versions was updated."""
    pool = FakePool(content="body", version=4, task_status="published")

    async def _fake_rebuild(p, *, site_config):
        return {"success": True}

    monkeypatch.setattr(
        "services.static_export_service.export_full_rebuild", _fake_rebuild,
    )

    svc = PostEditService(pool=pool)  # no site_config → warns, no rebuild
    warnings = await svc._sync_published_post_featured("task-123", "https://cdn/new.png")

    posts_updates = [e for e in pool.executed if "UPDATE posts" in e[0]]
    assert posts_updates, "expected UPDATE posts for published task"
    _, args = posts_updates[0]
    assert args[0] == "https://cdn/new.png"
    assert args[1] == "task-123"
    assert any("no site_config" in w for w in warnings)


async def test_sync_published_post_featured_skips_non_published():
    """Non-published tasks must not touch the posts table."""
    pool = FakePool(content="body", version=4, task_status="awaiting_approval")
    svc = PostEditService(pool=pool)

    warnings = await svc._sync_published_post_featured("task-123", "https://cdn/new.png")

    posts_updates = [e for e in pool.executed if "UPDATE posts" in e[0]]
    assert not posts_updates, "must not UPDATE posts for non-published task"
    assert warnings == []


async def test_sync_published_post_featured_triggers_rebuild(monkeypatch):
    """When the task is published and site_config is wired, export_full_rebuild is called."""
    pool = FakePool(content="body", version=4, task_status="published")
    rebuild_calls: list = []

    async def _fake_rebuild(p, *, site_config):
        rebuild_calls.append(site_config)
        return {"success": True}

    monkeypatch.setattr("services.static_export_service.export_full_rebuild", _fake_rebuild)

    from services.site_config import SiteConfig

    sc = SiteConfig(initial_config={})
    svc = PostEditService(pool=pool, site_config=sc)
    warnings = await svc._sync_published_post_featured("task-abc", "https://cdn/img.png")

    assert len(rebuild_calls) == 1, "export_full_rebuild should be called exactly once"
    assert any("rebuild triggered" in w for w in warnings)


async def test_regen_image_propagates_warnings_from_published_post(monkeypatch):
    """Warnings from replace_image (e.g. rebuild triggered) surface in the regen result."""
    pool = FakePool(content="body", version=1, task_status="published")

    async def fake_upload(self, path, task_id):
        return "https://cdn/generated/new.webp"

    monkeypatch.setattr(PostEditService, "_upload_image", fake_upload, raising=True)

    async def _fake_rebuild(p, *, site_config):
        return {"success": True}

    monkeypatch.setattr("services.static_export_service.export_full_rebuild", _fake_rebuild)

    from services.site_config import SiteConfig

    sc = SiteConfig(initial_config={})
    svc = PostEditService(pool=pool, image_service=_FakeImageSvc(), site_config=sc)
    result = await svc.regen_image("t1", which="featured", prompt="a teal robot")

    assert result.ok
    assert any("rebuild" in w for w in result.warnings), (
        "rebuild warning should propagate through regen_image → replace_image"
    )
