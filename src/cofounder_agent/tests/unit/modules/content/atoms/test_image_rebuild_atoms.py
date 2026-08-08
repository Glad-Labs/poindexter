"""Unit tests for the image_rebuild graph's four glue atoms.

The reused atoms (content.plan_image_markers / content.generate_images /
content.inject_images) have their own test files; these cover the atoms the
image_rebuild template added: load_draft_for_image_rebuild,
rebuild_featured_image, image_rebuild_gate, persist_draft_images.
"""
from __future__ import annotations

import pytest

import modules.content.atoms.content_image_rebuild_gate as content_image_rebuild_gate
import modules.content.atoms.content_load_draft_for_image_rebuild as content_load_draft_for_image_rebuild
import modules.content.atoms.content_persist_draft_images as content_persist_draft_images
import modules.content.atoms.content_rebuild_featured_image as content_rebuild_featured_image


class FakePool:
    def __init__(self, *, status: str | None = "awaiting_approval", topic="RAG echo chamber",
                 content="body", version=3):
        self._status, self._topic = status, topic
        self._content, self._version = content, version
        self.executed: list[tuple] = []

    async def fetchval(self, sql, *args):
        if "SELECT status" in sql:
            return self._status
        if "SELECT topic" in sql:
            return self._topic
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, sql, *args):
        return {"content": self._content, "version": self._version}

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


class FakeDb:
    def __init__(self, pool):
        self.pool = pool


# ---------------------------------------------------------------------------
# content.load_draft_for_image_rebuild
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_draft_strips_img_blocks_and_hydrates():
    body = (
        'Intro para.\n\n<img src="https://cdn/a.webp" alt="x" width="1024" height="1024" />\n'
        'Middle.\n\n<img src="https://cdn/b.jpg" alt="y" />\n'
        "<figcaption>Photo by Z on Pexels</figcaption>\n\nOutro."
    )
    pool = FakePool(content=body, version=7)
    out = await content_load_draft_for_image_rebuild.run(
        {"target_task_id": "draft-1", "database_service": FakeDb(pool), "allow_stock": True}
    )
    assert "<img" not in out["content"]
    assert "<figcaption>" not in out["content"]
    assert "Intro para." in out["content"] and "Outro." in out["content"]
    assert out["topic"] == "RAG echo chamber"
    assert out["target_version"] == 7
    assert out["allow_stock"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "in_progress", "awaiting_gate", "on_hold", "dry_run"])
async def test_load_draft_rejects_non_awaiting_target(status):
    """A genuinely unexpected target status (task still actively in
    flight) — NOT one of the benign "operator already moved on" statuses
    below — stays a plain fail-loud RuntimeError."""
    pool = FakePool(status=status)
    with pytest.raises(RuntimeError, match="awaiting_approval"):
        await content_load_draft_for_image_rebuild.run(
            {"target_task_id": "draft-1", "database_service": FakeDb(pool)}
        )


@pytest.mark.asyncio
async def test_load_draft_rejects_nonexistent_target():
    """target_task_id naming a task_id with no pipeline_tasks row at all
    (status=None) is a caller/dispatch bug, not a benign race — must stay
    the plain fail-loud path, not get swept into "moved on" just because
    None doesn't match any known still-active status string."""
    pool = FakePool(status=None)
    with pytest.raises(RuntimeError, match="awaiting_approval") as exc_info:
        await content_load_draft_for_image_rebuild.run(
            {"target_task_id": "draft-1", "database_service": FakeDb(pool)}
        )
    assert "moved on" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    "approved", "rejected", "rejected_retry", "rejected_final", "published",
    "cancelled", "completed", "failed", "superseded", "archived",
])
async def test_load_draft_flags_benign_race_for_moved_on_target(status):
    """poindexter#846: when the operator (or another system) already
    decided the draft's fate in the queue gap before the rebuild task
    claimed it, the atom still raises (the graph must halt) but tags the
    message with the stable "target draft moved on" marker
    content_router_service matches to record
    status='cancelled'/severity='info' instead of a real failure — this is
    not the same RuntimeError as a genuinely broken target.

    Covers every status in pipeline_tasks_status_check other than
    awaiting_approval and the still-actively-in-flight set (see the
    sibling test above) — deliberately exhaustive: the original
    poindexter#846 fix (rejected/approved/published only) missed
    rejected_final, causing repeated real GlitchTip noise for a draft an
    operator had already finally rejected. An include-list needs a
    matching edit every time a new terminal status is added; this
    parametrization is the regression guard against that drift repeating.
    """
    pool = FakePool(status=status)
    with pytest.raises(RuntimeError, match="target draft moved on") as exc_info:
        await content_load_draft_for_image_rebuild.run(
            {"target_task_id": "draft-1", "database_service": FakeDb(pool)}
        )
    assert status in str(exc_info.value)
    assert "awaiting_approval" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_load_draft_requires_target_and_db():
    with pytest.raises(RuntimeError, match="target_task_id"):
        await content_load_draft_for_image_rebuild.run({"database_service": FakeDb(FakePool())})


# ---------------------------------------------------------------------------
# content.rebuild_featured_image
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_featured_prefers_image_gen(monkeypatch):
    calls: dict = {}

    async def fake_try_image_gen(num, desc, *, site_config, task_id, platform=None):
        calls["desc"] = desc
        return "https://r2/hero.webp"

    monkeypatch.setattr(
        "modules.content.atoms._image_helpers.try_image_gen", fake_try_image_gen,
    )
    out = await content_rebuild_featured_image.run(
        {"topic": "GPU VRAM budgets",
         "featured_image_plan": {"prompt": "a stylized VRAM gauge"}}
    )
    assert out == {"featured_image_url": "https://r2/hero.webp", "featured_source": "image_gen"}
    assert calls["desc"] == "a stylized VRAM gauge"


@pytest.mark.asyncio
async def test_featured_falls_back_to_pexels(monkeypatch):
    """Stock fallback is opt-in as of 2026-07-28 — `allow_stock` (from
    `rebuild-images --allow-stock`) is the per-run override that permits it."""
    async def fake_try_image_gen(*a, **kw):
        return None

    async def fake_try_pexels(desc, topic, image_service):
        return ("https://pexels/p.jpg", "Ada")

    monkeypatch.setattr("modules.content.atoms._image_helpers.try_image_gen", fake_try_image_gen)
    monkeypatch.setattr("modules.content.atoms._image_helpers.try_pexels", fake_try_pexels)
    out = await content_rebuild_featured_image.run(
        {"topic": "GPU VRAM budgets", "image_service": object(), "allow_stock": True}
    )
    assert out == {"featured_image_url": "https://pexels/p.jpg", "featured_source": "pexels"}


@pytest.mark.asyncio
async def test_featured_refuses_pexels_without_allow_stock(monkeypatch):
    """Without the override the atom must not reach for stock at all — this
    site was missed by the first pass of the gate and could still ship a
    silent stock hero while the content-pipeline paths refused to."""
    searched = {"called": False}

    async def fake_try_image_gen(*a, **kw):
        return None

    async def fake_try_pexels(*a, **kw):
        searched["called"] = True
        return ("https://pexels/p.jpg", "Ada")

    monkeypatch.setattr("modules.content.atoms._image_helpers.try_image_gen", fake_try_image_gen)
    monkeypatch.setattr("modules.content.atoms._image_helpers.try_pexels", fake_try_pexels)
    out = await content_rebuild_featured_image.run(
        {"topic": "GPU VRAM budgets", "image_service": object()}
    )
    assert out == {"featured_image_url": "", "featured_source": "none"}
    assert searched["called"] is False


@pytest.mark.asyncio
async def test_featured_reports_none_without_raising(monkeypatch):
    async def fake_try_image_gen(*a, **kw):
        return None

    async def fake_try_pexels(*a, **kw):
        return None

    monkeypatch.setattr("modules.content.atoms._image_helpers.try_image_gen", fake_try_image_gen)
    monkeypatch.setattr("modules.content.atoms._image_helpers.try_pexels", fake_try_pexels)
    out = await content_rebuild_featured_image.run(
        {"topic": "GPU VRAM budgets", "image_service": object()}
    )
    assert out == {"featured_image_url": "", "featured_source": "none"}


# ---------------------------------------------------------------------------
# content.image_rebuild_gate
# ---------------------------------------------------------------------------

def _results(*sources):
    return [{"num": str(i + 1), "url": f"https://cdn/{i}.webp" if s else None,
             "source": s or "none"} for i, s in enumerate(sources)]


@pytest.mark.asyncio
async def test_gate_passes_all_generated():
    out = await content_image_rebuild_gate.run(
        {"image_results": _results("image_gen", "image_gen"),
         "featured_image_url": "https://r2/h.webp", "featured_source": "image_gen"}
    )
    assert out == {}


@pytest.mark.asyncio
async def test_gate_fails_empty_slot_even_with_allow_stock():
    with pytest.raises(RuntimeError, match="produced nothing.*inline:2"):
        await content_image_rebuild_gate.run(
            {"image_results": _results("image_gen", None),
             "featured_image_url": "https://r2/h.webp", "featured_source": "image_gen",
             "allow_stock": True}
        )


@pytest.mark.asyncio
async def test_gate_fails_missing_featured():
    with pytest.raises(RuntimeError, match="featured"):
        await content_image_rebuild_gate.run(
            {"image_results": _results("image_gen"),
             "featured_image_url": "", "featured_source": "none"}
        )


@pytest.mark.asyncio
async def test_gate_fails_stock_without_opt_in():
    with pytest.raises(RuntimeError, match="allow-stock"):
        await content_image_rebuild_gate.run(
            {"image_results": _results("image_gen", "pexels"),
             "featured_image_url": "https://r2/h.webp", "featured_source": "image_gen"}
        )


@pytest.mark.asyncio
async def test_gate_allows_stock_with_opt_in():
    out = await content_image_rebuild_gate.run(
        {"image_results": _results("pexels"),
         "featured_image_url": "https://pexels/h.jpg", "featured_source": "pexels",
         "allow_stock": True}
    )
    assert out == {}


# ---------------------------------------------------------------------------
# content.persist_draft_images
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_writes_target_version_and_bumps_attempts():
    pool = FakePool()
    out = await content_persist_draft_images.run(
        {"content": "new body", "target_task_id": "draft-1", "target_version": 7,
         "featured_image_url": "https://r2/h.webp",
         "image_results": _results("image_gen"), "featured_source": "image_gen",
         "database_service": FakeDb(pool)}
    )
    assert out == {}
    assert len(pool.executed) == 2
    persist_sql, persist_args = pool.executed[0]
    assert "UPDATE pipeline_versions" in persist_sql
    assert persist_args == ("new body", "https://r2/h.webp", "draft-1", 7)
    bump_sql, bump_args = pool.executed[1]
    assert "regen_images_attempts" in bump_sql and bump_args == ("draft-1",)


@pytest.mark.asyncio
async def test_persist_fails_loud_on_missing_inputs():
    with pytest.raises(RuntimeError, match="target_task_id"):
        await content_persist_draft_images.run(
            {"content": "x", "database_service": FakeDb(FakePool())}
        )


# --- screenshot slots are an intended source, not a stock downgrade --------
#
# poindexter#1002. The gate used to test `source != "image_gen"`, which
# classified a [SCREENSHOT: target] slot as a Pexels fallback and aborted the
# whole rebuild — so any draft containing a screenshot marker could never be
# rebuilt at all.


@pytest.mark.asyncio
async def test_gate_passes_screenshot_slot_without_allow_stock():
    out = await content_image_rebuild_gate.run(
        {"image_results": _results("image_gen", "screenshot"),
         "featured_image_url": "https://r2/h.webp", "featured_source": "image_gen"}
    )
    assert out == {}


@pytest.mark.asyncio
async def test_gate_passes_screenshot_featured_without_allow_stock():
    out = await content_image_rebuild_gate.run(
        {"image_results": _results("screenshot"),
         "featured_image_url": "https://r2/h.webp", "featured_source": "screenshot"}
    )
    assert out == {}


@pytest.mark.asyncio
async def test_gate_still_fails_pexels_slot_without_allow_stock():
    """The fix must not widen into "accept anything that isn't empty"."""
    with pytest.raises(RuntimeError, match="inline:2"):
        await content_image_rebuild_gate.run(
            {"image_results": _results("image_gen", "pexels"),
             "featured_image_url": "https://r2/h.webp", "featured_source": "image_gen"}
        )


@pytest.mark.asyncio
async def test_gate_still_fails_empty_screenshot_slot():
    """A screenshot that captured nothing is empty, not intended."""
    with pytest.raises(RuntimeError, match="produced nothing.*inline:2"):
        await content_image_rebuild_gate.run(
            {"image_results": _results("image_gen", None),
             "featured_image_url": "https://r2/h.webp", "featured_source": "image_gen",
             "allow_stock": True}
        )
