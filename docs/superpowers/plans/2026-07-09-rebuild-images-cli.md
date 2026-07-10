# `tasks rebuild-images` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `poindexter tasks rebuild-images <task_id>` — one command that rebuilds every image (featured + all inline) on an `awaiting_approval` draft by re-planning prompts from the article text, preferring generated images, failing loud on stock fallback unless `--allow-stock`.

**Architecture:** A new content-module service (`ImageRebuildService`) orchestrates the three existing image atoms (`plan_image_markers` → `generate_images` → `inject_images`) plus a featured-image regen, gates on image source, and writes the result back to the latest `pipeline_versions` row. A thin HTTP route (`POST /api/tasks/{id}/rebuild-images`) constructs it from `app.state` deps; a thin CLI command posts to that route — the same adapter shape as the sibling `tasks regen-image`.

**Tech Stack:** Python 3, FastAPI, Click, asyncpg, pytest (async). Spec: `docs/superpowers/specs/2026-07-09-rebuild-images-cli-design.md`.

## Global Constraints

- **Async-everywhere.** Every service/route method is `async`; never block the event loop.
- **Adapter contract.** Business logic lives in `ImageRebuildService`; the route and CLI hold no logic or raw SQL. Raw SQL is allowed only inside the service (mirrors `PostEditService`).
- **Config in DB, not migrations.** The new setting `post_edit_rebuild_images_timeout_s` goes in `services/settings_defaults.py` (`DEFAULTS` dict), never a migration file.
- **Fail loud, no silent fallback.** Wrong status → `ValueError`; missing image service → `RuntimeError`; a stock/empty slot without `--allow-stock` → abort with a clear message and persist nothing.
- **Docs + tests default.** Every task ends green; the user-facing command updates `docs/operations/post-editing.md`.
- **Scope:** `awaiting_approval` drafts only; featured + all inline included; no hard attempt-cap (bump `regen_images_attempts` for observability only).
- **Reuse, don't reinvent.** Call the existing atoms and `_try_image_gen`/`_try_pexels`; do not reimplement image logic. `_try_image_gen` already uploads to R2 and returns the final servable URL — the service does **not** upload separately.

---

## File Structure

- **Create** `src/cofounder_agent/modules/content/image_rebuild_service.py` — `RebuildResult` dataclass + `ImageRebuildService.rebuild_all_images`. Sole owner of the rebuild orchestration + `pipeline_versions` write.
- **Modify** `src/cofounder_agent/routes/task_publishing_routes.py` — `RebuildImagesRequest` model, `_rebuild_result_json`, and the `rebuild_task_images` route.
- **Modify** `src/cofounder_agent/poindexter/cli/tasks.py` — `tasks rebuild-images` command + `_emit_rebuild_result`.
- **Modify** `src/cofounder_agent/services/settings_defaults.py` — add `post_edit_rebuild_images_timeout_s`.
- **Create** `src/cofounder_agent/tests/unit/modules/content/test_image_rebuild_service.py`.
- **Modify** `src/cofounder_agent/tests/unit/routes/test_task_publishing_routes.py`.
- **Modify** `src/cofounder_agent/tests/unit/cli/test_tasks_edit_cli.py`.
- **Modify** `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`.
- **Modify** `docs/operations/post-editing.md`.

All `pytest` / `git` commands below run from `src/cofounder_agent/` unless the path says otherwise.

---

## Task 1: `ImageRebuildService` — happy path (guard, strip, re-plan, generate, inject, persist)

**Files:**

- Create: `src/cofounder_agent/modules/content/image_rebuild_service.py`
- Test: `src/cofounder_agent/tests/unit/modules/content/test_image_rebuild_service.py`

**Interfaces:**

- Consumes: atoms `modules.content.atoms.content_plan_image_markers|content_generate_images|content_inject_images` (each `async def run(state: dict) -> dict`); `modules.content.stages.replace_inline_images._try_image_gen` (returns an already-uploaded URL or None), `_try_pexels` (returns `(url, photographer)` or None).
- Produces:
  - `@dataclass RebuildResult(task_id: str, ok: bool, detail: str, inline_total: int = 0, inline_generated: int = 0, featured_source: str = "none", stock_slots: list[str] = [], warnings: list[str] = [])`
  - `class ImageRebuildService(*, pool, site_config=None, image_service=None, database_service=None, platform=None)`
  - `async def rebuild_all_images(self, task_id: str, *, allow_stock: bool = False) -> RebuildResult`

- [ ] **Step 1: Write the happy-path + guard tests**

Create `test_image_rebuild_service.py`:

```python
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
            for p, src in zip(state["image_plans"], inline_sources)]}

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/unit/modules/content/test_image_rebuild_service.py -v`
Expected: FAIL — `ModuleNotFoundError: modules.content.image_rebuild_service`.

- [ ] **Step 3: Implement the service (happy path + guards; `_gate` is a permissive stub here, hardened in Task 2)**

Create `image_rebuild_service.py`:

```python
"""Bulk image rebuild for awaiting_approval drafts (spec 2026-07-09).

Re-plans every image (featured + inline) from the article text and
regenerates them, preferring generated images. Reuses the pipeline's own
image atoms; writes the result to the latest pipeline_versions row. Fail-loud
on stock fallback unless allow_stock (see rebuild_all_images / _gate).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from modules.content.atoms import (
    content_generate_images,
    content_inject_images,
    content_plan_image_markers,
)
from modules.content.stages.replace_inline_images import _try_image_gen, _try_pexels

logger = logging.getLogger(__name__)

_STATUS_SQL = "SELECT status FROM pipeline_tasks WHERE task_id = $1"
_TOPIC_SQL = "SELECT topic FROM pipeline_tasks WHERE task_id = $1"
_LATEST_SQL = (
    "SELECT content, version, featured_image_url FROM pipeline_versions "
    "WHERE task_id = $1 ORDER BY version DESC LIMIT 1"
)
_PERSIST_SQL = (
    "UPDATE pipeline_versions SET content = $1, featured_image_url = $2 "
    "WHERE task_id = $3 AND version = $4"
)
_BUMP_SQL = (
    "UPDATE pipeline_tasks SET regen_images_attempts = "
    "COALESCE(regen_images_attempts, 0) + 1 WHERE task_id = $1"
)
# An <img ...> tag plus an optional trailing <figcaption>…</figcaption> (Pexels).
_IMG_BLOCK_RE = re.compile(
    r"<img\b[^>]*>\s*(?:<figcaption>.*?</figcaption>)?",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class RebuildResult:
    task_id: str
    ok: bool
    detail: str
    inline_total: int = 0
    inline_generated: int = 0
    featured_source: str = "none"
    stock_slots: list[str] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)


class ImageRebuildService:
    """Rebuild all images on an awaiting_approval draft. Drafts only."""

    def __init__(
        self, *, pool: Any, site_config: Any = None, image_service: Any = None,
        database_service: Any = None, platform: Any = None,
    ) -> None:
        self._pool = pool
        self._site_config = site_config
        self._image_service = image_service
        self._db = database_service
        self._platform = platform

    async def rebuild_all_images(self, task_id: str, *, allow_stock: bool = False) -> RebuildResult:
        if self._image_service is None:
            raise RuntimeError("image service not available for rebuild")

        status = await self._pool.fetchval(_STATUS_SQL, task_id)
        if status != "awaiting_approval":
            raise ValueError(
                f"rebuild-images requires an awaiting_approval draft; "
                f"task {task_id} is {status!r}"
            )
        row = await self._pool.fetchrow(_LATEST_SQL, task_id)
        if not row:
            raise ValueError(f"no pipeline_versions row for task {task_id}")
        content = row["content"] or ""
        version = int(row["version"])
        topic = await self._pool.fetchval(_TOPIC_SQL, task_id) or ""

        stripped = _IMG_BLOCK_RE.sub("", content)

        plan_out = await content_plan_image_markers.run(
            {"content": stripped, "topic": topic, "site_config": self._site_config}
        )
        planned_content = plan_out.get("content", stripped)
        image_plans = plan_out.get("image_plans", [])
        featured_plan = plan_out.get("featured_image_plan") or {}

        gen_out = await content_generate_images.run(
            {"image_plans": image_plans, "topic": topic, "task_id": task_id,
             "post_id": None, "site_config": self._site_config,
             "image_service": self._image_service, "platform": self._platform}
        )
        image_results = gen_out.get("image_results", [])
        featured_url, featured_source = await self._gen_featured(featured_plan, topic, task_id)

        gate = self._gate(image_results, featured_url, featured_source, allow_stock)
        if gate is not None:
            gate.task_id = task_id
            return gate  # abort — draft unchanged

        inj_out = await content_inject_images.run(
            {"content": planned_content, "image_results": image_results,
             "task_id": task_id, "database_service": None}  # we persist to pipeline_versions ourselves
        )
        new_content = inj_out.get("content", planned_content)
        inline_generated = sum(1 for r in image_results if r.get("source") == "image_gen")

        await self._pool.execute(_PERSIST_SQL, new_content, featured_url, task_id, version)
        await self._pool.execute(_BUMP_SQL, task_id)
        await self._audit(task_id, image_results, featured_source, allow_stock)

        return RebuildResult(
            task_id, ok=True,
            detail=(f"rebuilt {len(image_results)} inline + featured "
                    f"({inline_generated} generated); draft updated (v{version})"),
            inline_total=len(image_results), inline_generated=inline_generated,
            featured_source=featured_source,
        )

    async def _gen_featured(self, featured_plan: dict, topic: str, task_id: str) -> tuple[str | None, str]:
        """Featured image via the same two-strategy as inline: image-gen then Pexels.

        `_try_image_gen` returns an already-uploaded R2 URL (or None); no separate
        upload step is needed.
        """
        desc = (featured_plan.get("prompt") if isinstance(featured_plan, dict) else "") or topic
        url = await _try_image_gen(
            "featured", desc, topic,
            site_config=self._site_config, task_id=task_id, platform=self._platform,
        )
        if url:
            return url, "image_gen"
        pex = await _try_pexels(desc, topic, self._image_service)
        if pex:
            return pex[0], "pexels"
        return None, "none"

    def _gate(self, image_results, featured_url, featured_source, allow_stock) -> RebuildResult | None:
        """Permissive stub — proceed always. Hardened into the fail-loud gate in Task 2."""
        return None

    async def _audit(self, task_id, image_results, featured_source, allow_stock) -> None:
        if self._platform is None:
            return
        await self._platform.audit.write(
            "post_images_rebuild",
            source="image_rebuild_service",
            details={
                "task_id": task_id,
                "inline_sources": [r.get("source") for r in image_results],
                "featured_source": featured_source,
                "allow_stock": allow_stock,
            },
            task_id=task_id,
            severity="info",
        )
```

- [ ] **Step 4: Run to verify the happy-path + guard tests pass**

Run: `poetry run pytest tests/unit/modules/content/test_image_rebuild_service.py -v`
Expected: PASS (`test_happy_path_persists_content_and_featured`, `test_wrong_status_raises`, `test_missing_image_service_raises`).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/image_rebuild_service.py src/cofounder_agent/tests/unit/modules/content/test_image_rebuild_service.py
git commit -m "feat(content): ImageRebuildService rebuilds all draft images (happy path)"
```

---

## Task 2: Fail-loud stock gate + `--allow-stock`

**Files:**

- Modify: `src/cofounder_agent/modules/content/image_rebuild_service.py` (`_gate`)
- Test: `src/cofounder_agent/tests/unit/modules/content/test_image_rebuild_service.py`

**Interfaces:**

- Consumes: `image_results` (list of `{num,url,alt_text,source}`), `featured_url: str | None`, `featured_source: str`, `allow_stock: bool`.
- Produces: `ImageRebuildService._gate(...) -> RebuildResult | None` — `None` proceeds; a `RebuildResult(ok=False, stock_slots=[…])` aborts with nothing persisted.

- [ ] **Step 1: Write the gate tests**

Append to `test_image_rebuild_service.py`:

```python
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
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `poetry run pytest tests/unit/modules/content/test_image_rebuild_service.py -k "stock or empty_slot" -v`
Expected: FAIL — the stub `_gate` returns `None`, so aborts don't happen (persist occurs / wrong `ok`).

- [ ] **Step 3: Implement `_gate`**

Replace the stub `_gate` in `image_rebuild_service.py`:

```python
    def _gate(self, image_results, featured_url, featured_source, allow_stock) -> RebuildResult | None:
        """Fail-loud gate. Returns an abort result, or None to proceed.

        - Any slot that produced NOTHING (url is None / featured None) always
          aborts — an empty slot cannot be persisted, even with --allow-stock.
        - Otherwise, any non-image_gen slot (Pexels stock) aborts unless
          allow_stock is set.
        (task_id on the abort result is stamped by the caller.)
        """
        empty = [f"inline:{r['num']}" for r in image_results if not r.get("url")]
        if featured_url is None:
            empty.append("featured")
        if empty:
            return RebuildResult(
                task_id="", ok=False,
                detail=(f"image generation produced nothing for {len(empty)} slot(s): "
                        f"{', '.join(empty)}. Check the image-gen server "
                        f"(image_gen_server_url); draft unchanged."),
                stock_slots=empty,
            )
        stock = [f"inline:{r['num']}" for r in image_results if r.get("source") != "image_gen"]
        if featured_source != "image_gen":
            stock.append("featured")
        if stock and not allow_stock:
            return RebuildResult(
                task_id="", ok=False,
                detail=(f"image-gen unavailable for {len(stock)} slot(s): "
                        f"{', '.join(stock)}. Refusing Pexels stock fallback "
                        f"(pass --allow-stock to accept). Draft unchanged."),
                stock_slots=stock,
            )
        return None
```

- [ ] **Step 4: Run the full service suite**

Run: `poetry run pytest tests/unit/modules/content/test_image_rebuild_service.py -v`
Expected: PASS (all happy-path, guard, and gate tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/image_rebuild_service.py src/cofounder_agent/tests/unit/modules/content/test_image_rebuild_service.py
git commit -m "feat(content): fail-loud stock gate + --allow-stock for rebuild-images"
```

---

## Task 3: HTTP route `POST /api/tasks/{id}/rebuild-images`

**Files:**

- Modify: `src/cofounder_agent/routes/task_publishing_routes.py` (model near line 1310; route after `regen_task_image`, ~line 1430)
- Test: `src/cofounder_agent/tests/unit/routes/test_task_publishing_routes.py`

**Interfaces:**

- Consumes: `ImageRebuildService.rebuild_all_images` (Tasks 1–2); `_resolve_full_task_id`, `get_database_dependency`, `get_site_config_dependency`, `verify_api_token`, `services.image_service.get_image_service`. Construction mirrors `_build_edit_service` (`pool=db_service.pool`, `platform=app.state.kernel_platform`).
- Produces: `POST /api/tasks/{task_id}/rebuild-images` accepting `{"allow_stock": bool}`, returning `{ok, task_id, detail, inline_total, inline_generated, featured_source, stock_slots}`.

- [ ] **Step 1: Write the route wiring tests**

In `test_task_publishing_routes.py`, inside `_client_with_fake_service`, add a method to the `FakeSvc` class (alongside `regen_image`):

```python
            async def rebuild_all_images(self, task_id, **kw):
                calls["rebuild_all_images"] = (task_id, kw)
                from modules.content.image_rebuild_service import RebuildResult
                return RebuildResult(task_id, ok=True, detail="rebuilt 2 inline + featured",
                                     inline_total=2, inline_generated=2, featured_source="image_gen")
```

And, next to the existing `monkeypatch.setattr(_pub_mod, "PostEditService", FakeSvc)`, add:

```python
        monkeypatch.setattr(_pub_mod, "ImageRebuildService", FakeSvc)
```

Add these tests to the same class:

```python
    def test_rebuild_images_routes_to_service(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(f"/{VALID_TASK_ID}/rebuild-images", json={"allow_stock": False})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["inline_generated"] == 2 and body["featured_source"] == "image_gen"
        tid, kw = calls["rebuild_all_images"]
        assert tid == VALID_TASK_ID and kw == {"allow_stock": False}

    def test_rebuild_images_allow_stock_threads_through(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(f"/{VALID_TASK_ID}/rebuild-images", json={"allow_stock": True})
        assert r.status_code == 200, r.text
        assert calls["rebuild_all_images"][1] == {"allow_stock": True}

    def test_rebuild_images_unknown_task_404(self):
        mock_db = make_mock_db()  # get_task returns None
        client = TestClient(_build_app(mock_db))
        r = client.post(f"/{VALID_TASK_ID}/rebuild-images", json={"allow_stock": False})
        assert r.status_code == 404
```

(`FakeSvc.__init__(self, **kw)` already accepts any kwargs, so it stands in for `ImageRebuildService` too. `_client_with_fake_service` already patches `services.image_service.get_image_service` to return `object()`, so the route's image-service line succeeds.)

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/unit/routes/test_task_publishing_routes.py -k rebuild_images -v`
Expected: FAIL — 404/route-not-found (route not defined yet).

- [ ] **Step 3: Add the import, model, JSON helper, and route**

At the top of `task_publishing_routes.py`, next to the existing `from modules.content.post_edit_service import ...`:

```python
from modules.content.image_rebuild_service import ImageRebuildService
```

After `RegenImageRequest` (line 1310):

```python
class RebuildImagesRequest(BaseModel):
    allow_stock: bool = False
```

After `_edit_result_json` (line 1358):

```python
def _rebuild_result_json(res) -> dict[str, Any]:
    return {
        "ok": res.ok,
        "task_id": res.task_id,
        "detail": res.detail,
        "inline_total": res.inline_total,
        "inline_generated": res.inline_generated,
        "featured_source": res.featured_source,
        "stock_slots": res.stock_slots,
    }
```

After `regen_task_image` (line 1430):

```python
@publishing_router.post("/{task_id}/rebuild-images", summary="Rebuild all images on a draft")
async def rebuild_task_images(
    task_id: str,
    body: RebuildImagesRequest,
    request: Request,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config_dep=Depends(get_site_config_dependency),
):
    """Rebuild every image (featured + inline) on an awaiting_approval draft by
    re-planning from the article text. Fail-loud on stock fallback unless allow_stock."""
    full_id = await _resolve_full_task_id(db_service, task_id)
    from services.image_service import get_image_service
    try:
        image_service = get_image_service(site_config=site_config_dep)
    except Exception as e:  # noqa: BLE001 — mirror _build_edit_service's need_image 503
        raise HTTPException(status_code=503, detail=f"image service unavailable: {e}") from e
    svc = ImageRebuildService(
        pool=db_service.pool, site_config=site_config_dep, image_service=image_service,
        database_service=db_service, platform=getattr(request.app.state, "kernel_platform", None),
    )
    try:
        res = await svc.rebuild_all_images(full_id, allow_stock=body.allow_stock)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return _rebuild_result_json(res)
```

- [ ] **Step 4: Run the route tests**

Run: `poetry run pytest tests/unit/routes/test_task_publishing_routes.py -k rebuild_images -v`
Expected: PASS (routes-to-service, allow_stock threading, 404).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/routes/task_publishing_routes.py src/cofounder_agent/tests/unit/routes/test_task_publishing_routes.py
git commit -m "feat(api): POST /api/tasks/{id}/rebuild-images route"
```

---

## Task 4: CLI `tasks rebuild-images` + timeout setting + docs

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/tasks.py` (after `tasks_regen_image`, line 741)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (after `post_edit_regen_image_timeout_s`, line 896)
- Modify: `docs/operations/post-editing.md`
- Test: `src/cofounder_agent/tests/unit/cli/test_tasks_edit_cli.py`, `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Consumes: `_post_edit(path, payload, timeout_key)` (existing); the route from Task 3.
- Produces: `poindexter tasks rebuild-images <task_id> [--allow-stock]`; setting `post_edit_rebuild_images_timeout_s = "600"`.

- [ ] **Step 1: Write the CLI + settings tests**

Add to `test_settings_defaults.py`:

```python
def test_rebuild_images_timeout_default_present():
    from services.settings_defaults import DEFAULTS
    assert DEFAULTS["post_edit_rebuild_images_timeout_s"] == "600"
```

Add to `test_tasks_edit_cli.py` (mirror the regen-image CLI test — patch `_post_edit`):

```python
def test_rebuild_images_posts_expected_payload(monkeypatch):
    from click.testing import CliRunner
    from poindexter.cli import tasks as tasks_mod

    seen = {}

    def fake_post_edit(path, payload, timeout_key=None):
        seen["path"], seen["payload"], seen["timeout_key"] = path, payload, timeout_key
        return {"ok": True, "detail": "rebuilt 2 inline + featured",
                "inline_generated": 2, "inline_total": 2, "featured_source": "image_gen",
                "stock_slots": []}

    monkeypatch.setattr(tasks_mod, "_post_edit", fake_post_edit)
    result = CliRunner().invoke(tasks_mod.tasks_rebuild_images, ["abc12345", "--allow-stock"])
    assert result.exit_code == 0, result.output
    assert seen["path"] == "/api/tasks/abc12345/rebuild-images"
    assert seen["payload"] == {"allow_stock": True}
    assert seen["timeout_key"] == "post_edit_rebuild_images_timeout_s"


def test_rebuild_images_abort_exits_nonzero(monkeypatch):
    from click.testing import CliRunner
    from poindexter.cli import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "_post_edit", lambda *a, **k: {
        "ok": False, "detail": "image-gen unavailable for 1 slot(s): featured. …",
        "stock_slots": ["featured"]})
    result = CliRunner().invoke(tasks_mod.tasks_rebuild_images, ["abc12345"])
    assert result.exit_code == 1
    assert "image-gen unavailable" in result.output
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/unit/cli/test_tasks_edit_cli.py -k rebuild_images tests/unit/services/test_settings_defaults.py::test_rebuild_images_timeout_default_present -v`
Expected: FAIL — `tasks_rebuild_images` / the default don't exist.

- [ ] **Step 3: Add the setting, the CLI command, and the docs**

In `settings_defaults.py`, right after the `post_edit_regen_image_timeout_s` entry (line 896):

```python
    # Seconds the CLI waits for POST /api/tasks/{id}/rebuild-images. Rebuilds
    # every image (featured + all inline) sequentially, so it must exceed a
    # single regen by the image count — generous default.
    'post_edit_rebuild_images_timeout_s': '600',
```

In `tasks.py`, after `tasks_regen_image` (line 741):

```python
def _emit_rebuild_result(data: dict) -> None:
    if data.get("ok"):
        click.secho(f"✅ {data.get('detail', 'rebuilt images')}", fg="green")
        if data.get("stock_slots"):
            click.secho(f"   stock slots (allowed): {', '.join(data['stock_slots'])}", fg="yellow")
    else:
        click.secho(f"✋ {data.get('detail', 'rebuild aborted')}", fg="red")
        sys.exit(1)


@tasks_group.command("rebuild-images")
@click.argument("task_id")
@click.option("--allow-stock", is_flag=True,
              help="Accept Pexels stock when image-gen can't produce a slot (default: fail loud).")
def tasks_rebuild_images(task_id: str, allow_stock: bool) -> None:
    """Rebuild ALL images on an awaiting_approval draft (featured + inline).

    Re-plans each image prompt from the article text and regenerates — no
    prompts typed. Prefers generated images; without --allow-stock it aborts
    (changing nothing) if any slot would fall back to a Pexels stock photo.
    """
    _emit_rebuild_result(
        _post_edit(
            f"/api/tasks/{task_id}/rebuild-images",
            {"allow_stock": allow_stock},
            timeout_key="post_edit_rebuild_images_timeout_s",
        ),
    )
```

In `docs/operations/post-editing.md`, add this under the image-editing commands (near `regen-image`):

```markdown
### `poindexter tasks rebuild-images <task_id> [--allow-stock]`

Rebuilds **every** image on an `awaiting_approval` draft — the featured image
and all inline images — by re-planning each image's prompt from the article
text (no prompts typed) and regenerating. Prefers generated (SDXL/Z-Image)
images. Without `--allow-stock` it **aborts and changes nothing** if any slot
would fall back to a Pexels stock photo (image-gen unavailable); pass
`--allow-stock` to accept the fallback. Drafts only. Re-planning may change how
many inline images the post has.
```

- [ ] **Step 4: Run the CLI + settings tests**

Run: `poetry run pytest tests/unit/cli/test_tasks_edit_cli.py -k rebuild_images tests/unit/services/test_settings_defaults.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/tasks.py src/cofounder_agent/services/settings_defaults.py docs/operations/post-editing.md src/cofounder_agent/tests/unit/cli/test_tasks_edit_cli.py src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(cli): poindexter tasks rebuild-images + timeout setting + docs"
```

---

## Final verification

- [ ] **Run the full touched surface**

Run (from `src/cofounder_agent/`):

```bash
poetry run pytest \
  tests/unit/modules/content/test_image_rebuild_service.py \
  tests/unit/routes/test_task_publishing_routes.py \
  tests/unit/cli/test_tasks_edit_cli.py \
  tests/unit/services/test_settings_defaults.py -q
```

Expected: all PASS.

- [ ] **Lint the adapter-purity ratchet** (no net-new inline SQL in routes/CLI — the SQL lives in the service):

Run: `python scripts/ci/adapter_purity_lint.py`
Expected: PASS.

- [ ] **End-to-end smoke** (real draft, real image-gen): on the RAG draft `f274dc44`, run `poindexter tasks rebuild-images f274dc44` and confirm it either regenerates all four images (image-gen healthy) or fails loud naming the stock slots (image-gen down). Verify the draft's `pipeline_versions.content` `<img>` srcs changed and `featured_image_url` updated. This is the `verify` skill's job at execution time.

---

## Notes / known limitations (v1)

- **Featured styling:** the featured image is regenerated via the inline two-strategy (`_try_image_gen`/`_try_pexels`), so it uses inline-image styling rather than the dedicated featured/hero styling of the `source_featured_image` stage. Matching featured hero-styling is a noted follow-up, not v1 scope.
- **Orphaned uploads on abort:** a fail-loud abort can leave generated-but-unused images in R2 + `media_assets` rows (generation happens before the gate). Accepted for v1 (R2 is cheap; `media_assets` is an append log).
- **Related follow-up:** first-class `tasks add-image` / `remove-image` — [glad-labs-stack#2233](https://github.com/Glad-Labs/glad-labs-stack/issues/2233).

```

```
