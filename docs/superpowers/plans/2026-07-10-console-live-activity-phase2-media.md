# Console Live Activity — Phase 2: Media Render Producers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land GPU-heavy image and media (video/podcast) renders in the console SYSTEM PULSE's "In Production" column as `kind='media'` ledger rows with real, honest progress — closing the "media render-progress instrumentation" gap Phase 1 explicitly deferred (memory `project_console_live_activity` gap (b)).

**Architecture:** The Phase-1 `live_activity` ledger + `/api/activity` contract stay frozen (spec: "A→C is a producer swap, not a contract change"). This phase only adds **producers**. To avoid three divergent copies of the scheduler's begin→sidecar-heartbeat→finish-in-finally bracket, we first extract that bracket into a reusable `live_activity.track()` async context manager, then rent it from three render chokepoints — `image_service.generate_image` (Z-Image via `:9836`), `_media_render.render_from_state` (WAN i2v + ffmpeg video, with per-shot progress threaded through `render_shot_list`), and `_narration_render.render_narration` (podcast + video-narration TTS). The shipped scheduler seam is refactored to rent the same `track()` so there is one bracket implementation, not two (its existing test suite is the regression net).

**Tech Stack:** Python 3.13 · asyncpg · asyncio · pytest (`tests/unit`) · the existing `services/live_activity.py` ledger + `services/gpu_scheduler.gpu.lock` + `services/podcast_service.PodcastService` + the LangGraph media atoms.

## Global Constraints

- **Best-effort observability, never load-bearing:** every ledger write swallows its own error (logs, continues) and MUST NEVER break the render/job/pipeline it shadows. A failed `begin` yields a `None` id and the whole bracket degrades to a silent no-op. (spec: "observability; it must never become load-bearing")
- **No fabricated progress:** `progress_pct` is honest — video pct is **shot position** (`shot_index / total_shots`), never time-elapsed; image and audio are single blocking calls, so they get **liveness only** (a `step` label, no invented pct). (`feedback_no_dummy_data`)
- **Frozen contract:** do NOT reshape the `live_activity` table or the `/api/activity` response. This is a producer swap. (spec, memory `project_console_live_activity` "How to apply")
- **DB-first config, no new keys needed:** reuse the existing `live_activity_heartbeat_seconds` (default 30) and `live_activity_freshness_seconds` (default 120); the heartbeat MUST stay shorter than the freshness window. (`feedback_db_first_config`)
- **No raw SQL at call sites:** producers call `live_activity` helpers, never inline SQL. (adapter/service-layer contract)
- **Backcompat:** new params are optional and default to a no-op, so every existing caller and test of the modified functions keeps working. (`feedback_backcompat_now_required`)
- **Docs + tests default:** every task ships contract tests; the spec + memory get updated in the final task. (`feedback_docs_and_tests_default`)
- **All work on a branch off `origin/main`; every task commits; PR at the end; linear history.** (`feedback_all_changes_via_pr`, `feedback_linear_history_no_merge_commits`)

---

### Task 1: `live_activity.track()` context manager + `ActivityHandle` + shared heartbeat resolver

The reusable bracket the scheduler seam currently hand-rolls, plus a small handle so a producer can push progress and mark a returned-failure. This is the seam all later tasks (and the refactored scheduler) rent.

**Files:**

- Modify: `src/cofounder_agent/services/live_activity.py` (add `ActivityHandle`, `track`, `resolve_heartbeat_seconds`)
- Test: `src/cofounder_agent/tests/unit/services/test_live_activity_track.py`

**Interfaces:**

- Consumes: existing `begin` / `update` / `finish` / `heartbeat` (same module).
- Produces:
  - `class ActivityHandle` with `activity_id: int | None`, `status: str` (default `"ok"`), `async def update(self, *, step=None, pct=None) -> None`, `def fail(self) -> None`.
  - `@contextlib.asynccontextmanager async def track(pool, *, kind: str, ref_id: str | None, title: str, detail: dict | None = None, heartbeat_seconds: float = 30.0) -> AsyncIterator[ActivityHandle]`.
  - `def resolve_heartbeat_seconds(site_config: Any, *, default: float = 30.0) -> float`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_live_activity_track.py
"""live_activity.track() — the reusable begin→heartbeat→finish bracket every
producer rents. Best-effort throughout; a None id (begin failed) degrades the
whole bracket to a silent no-op."""
import asyncio
import contextlib

import pytest

from services import live_activity

pytestmark = pytest.mark.asyncio


async def test_track_brackets_begin_and_finish_ok(monkeypatch):
    calls = []

    async def fake_begin(pool, **kw):
        calls.append(("begin", kw["kind"]))
        return 11

    async def fake_finish(pool, aid, **kw):
        calls.append(("finish", aid, kw.get("status")))

    monkeypatch.setattr(live_activity, "begin", fake_begin)
    monkeypatch.setattr(live_activity, "finish", fake_finish)

    async with live_activity.track(object(), kind="media", ref_id="t1", title="X") as act:
        assert act.activity_id == 11
    assert calls == [("begin", "media"), ("finish", 11, "ok")]


async def test_track_marks_fail_on_handle_fail(monkeypatch):
    calls = []
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(7))
    async def fake_finish(pool, aid, **kw):
        calls.append((aid, kw.get("status")))
    monkeypatch.setattr(live_activity, "finish", fake_finish)

    async with live_activity.track(object(), kind="media", ref_id="t", title="X") as act:
        act.fail()
    assert calls == [(7, "fail")]


async def test_track_marks_fail_and_reraises_on_exception(monkeypatch):
    calls = []
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(3))
    async def fake_finish(pool, aid, **kw):
        calls.append((aid, kw.get("status")))
    monkeypatch.setattr(live_activity, "finish", fake_finish)

    with pytest.raises(RuntimeError):
        async with live_activity.track(object(), kind="media", ref_id="t", title="X"):
            raise RuntimeError("boom")
    assert calls == [(3, "fail")]


async def test_track_heartbeats_a_slow_body(monkeypatch):
    beats = []
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(9))
    monkeypatch.setattr(live_activity, "finish", lambda pool, aid, **kw: _ret(None))
    async def fake_update(pool, aid, **kw):
        beats.append(aid)
    monkeypatch.setattr(live_activity, "update", fake_update)

    async with live_activity.track(object(), kind="media", ref_id="t", title="X",
                                   heartbeat_seconds=0.01):
        await asyncio.sleep(0.05)
    assert len(beats) >= 1 and set(beats) == {9}


async def test_track_none_id_is_a_silent_noop(monkeypatch):
    finishes = []
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(None))
    async def fake_finish(pool, aid, **kw):
        finishes.append(aid)
    monkeypatch.setattr(live_activity, "finish", fake_finish)
    async def boom_update(pool, aid, **kw):
        raise AssertionError("update must not be called on a None-id row")
    monkeypatch.setattr(live_activity, "update", boom_update)

    async with live_activity.track(object(), kind="media", ref_id="t", title="X",
                                   heartbeat_seconds=0.01) as act:
        assert act.activity_id is None
        await act.update(step="x")     # no-op — id is None
        await asyncio.sleep(0.03)      # no heartbeat spins
    # finish is still CALLED (with None), and finish(None) itself no-ops.
    assert finishes == [None]


async def test_handle_update_delegates(monkeypatch):
    seen = {}
    monkeypatch.setattr(live_activity, "begin", lambda pool, **kw: _ret(5))
    monkeypatch.setattr(live_activity, "finish", lambda pool, aid, **kw: _ret(None))
    async def fake_update(pool, aid, *, step=None, pct=None):
        seen["args"] = (aid, step, pct)
    monkeypatch.setattr(live_activity, "update", fake_update)

    async with live_activity.track(object(), kind="media", ref_id="t", title="X") as act:
        await act.update(step="shot 2/5", pct=40)
    assert seen["args"] == (5, "shot 2/5", 40)


def test_resolve_heartbeat_seconds():
    assert live_activity.resolve_heartbeat_seconds(None) == 30.0

    class _C:
        def get(self, k, d=None):
            return "5" if k == "live_activity_heartbeat_seconds" else d
    assert live_activity.resolve_heartbeat_seconds(_C()) == 5.0

    class _Bad:
        def get(self, k, d=None):
            return "not-a-number"
    assert live_activity.resolve_heartbeat_seconds(_Bad()) == 30.0


async def _ret(v):
    return v
```

> NOTE: `_ret` is a tiny coroutine factory so `monkeypatch.setattr(..., lambda ...: _ret(v))` yields an awaitable — matching the existing `test_live_activity_heartbeat.py` monkeypatch style where the replacement is an `async def`. (Where a test needs the call recorded, it uses a real `async def fake_*` instead.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_live_activity_track.py -v`
Expected: FAIL — `module 'services.live_activity' has no attribute 'track'`.

- [ ] **Step 3: Add the resolver, handle, and context manager**

Add `import contextlib` and `from collections.abc import AsyncIterator` to the imports of `services/live_activity.py` (it already imports `asyncio`, `json`, `logging`, and `Any`). Then append:

```python
def resolve_heartbeat_seconds(site_config: Any, *, default: float = 30.0) -> float:
    """Heartbeat cadence (seconds) for a producer's ``track()`` row, read off
    the ``site_config`` DI seam (``live_activity_heartbeat_seconds``). MUST stay
    shorter than ``live_activity_freshness_seconds`` so a still-running
    multi-minute render is never hidden by ``get_live_activity``'s freshness
    window. Falls back to ``default`` when site_config is absent/unparseable."""
    if site_config is None:
        return default
    try:
        return float(site_config.get("live_activity_heartbeat_seconds", default))
    except (TypeError, ValueError):
        return default


class ActivityHandle:
    """Mutable handle yielded by ``track()``. Lets a producer push progress
    (``update``) and mark a returned-failure (``fail``) on the row it brackets.
    Best-effort throughout: ``update`` delegates to the swallowing module helper
    and no-ops on a None id (begin failed)."""

    def __init__(self, pool: Any, activity_id: int | None) -> None:
        self._pool = pool
        self.activity_id = activity_id
        self.status = "ok"

    async def update(self, *, step: str | None = None, pct: int | None = None) -> None:
        await update(self._pool, self.activity_id, step=step, pct=pct)

    def fail(self) -> None:
        """Mark the row to finish 'fail' without raising — for a producer that
        returns a failure result (e.g. render success=False) rather than
        throwing."""
        self.status = "fail"


@contextlib.asynccontextmanager
async def track(
    pool: Any,
    *,
    kind: str,
    ref_id: str | None,
    title: str,
    detail: dict | None = None,
    heartbeat_seconds: float = 30.0,
) -> AsyncIterator[ActivityHandle]:
    """Bracket a long-running producer with a best-effort live_activity row.

    Opens the row (``begin``), launches a sidecar ``heartbeat`` that keeps
    ``updated_at`` fresh for the whole body (so the read's freshness window
    never hides a still-running multi-minute render), and closes the row
    (``finish``) on EVERY exit path — clean success ('ok'), a returned-failure
    the caller flags via ``handle.fail()``, or an exception ('fail', then
    re-raise). The heartbeat is always torn down in the ``finally``.

    Best-effort: a failed ``begin`` yields a handle whose ``activity_id`` is
    None, so ``update``/``finish`` no-op and no heartbeat spins — the producer
    runs exactly as before, just invisible in the pulse. This is observability;
    it must never break the render it shadows.
    """
    aid = await begin(pool, kind=kind, ref_id=ref_id, title=title, detail=detail)
    handle = ActivityHandle(pool, aid)
    hb_task = (
        asyncio.create_task(heartbeat(pool, aid, interval_seconds=heartbeat_seconds))
        if aid is not None
        else None
    )
    try:
        yield handle
    except BaseException:
        handle.status = "fail"
        raise
    finally:
        if hb_task is not None:
            hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb_task
        await finish(pool, handle.activity_id, status=handle.status)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_live_activity_track.py tests/unit/services/test_live_activity_heartbeat.py -v`
Expected: PASS (new track tests + the existing heartbeat tests both green).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/live_activity.py \
        src/cofounder_agent/tests/unit/services/test_live_activity_track.py
git commit -m "feat(activity): live_activity.track() reusable begin/heartbeat/finish bracket"
```

---

### Task 2: Refactor the scheduler seam to rent `track()` (one bracket, not two)

The shipped `_invoke_job_with_activity` hand-rolls the exact bracket `track()` now owns. Refactor it to rent `track()` so there is a single implementation. `test_scheduler_activity.py` (5 tests) is the regression net — behaviour must not change.

**Files:**

- Modify: `src/cofounder_agent/plugins/scheduler.py` (`_heartbeat_interval_seconds` ~line 165, `_invoke_job_with_activity` ~line 184-231)
- Test: `src/cofounder_agent/tests/unit/plugins/test_scheduler_activity.py` (existing — must stay green; no new test)

**Interfaces:**

- Consumes: `live_activity.track` + `live_activity.resolve_heartbeat_seconds` (Task 1).
- Produces: `_invoke_job_with_activity(*, pool, job, cfg) -> Any` — same signature; internally uses `track()`.

- [ ] **Step 1: Run the existing suite to capture the green baseline**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_scheduler_activity.py -v`
Expected: PASS (5 tests) — this is the behaviour the refactor must preserve.

- [ ] **Step 2: Delegate the interval resolver to the shared one**

Replace the body of `_heartbeat_interval_seconds` (keep the static method + signature so its callers/tests are unchanged) with a delegation to the shared resolver:

```python
    @staticmethod
    def _heartbeat_interval_seconds(cfg: dict) -> float:
        """Heartbeat cadence (seconds) for long-job liveness, read off the
        dispatcher-seeded ``_site_config`` in the job's cfg. Delegates to
        ``live_activity.resolve_heartbeat_seconds`` so the default + parse rule
        live in one place (Phase-2 media producers resolve the same way)."""
        sc = cfg.get("_site_config") if isinstance(cfg, dict) else None
        return live_activity.resolve_heartbeat_seconds(sc)
```

- [ ] **Step 3: Rewrite `_invoke_job_with_activity` to rent `track()`**

```python
    @staticmethod
    async def _invoke_job_with_activity(*, pool: Any, job: Any, cfg: dict) -> Any:
        """Run a job bracketed by a best-effort live_activity row (unless the
        job opts out via ``activity_silent = True`` — the reaper and other
        high-frequency housekeeping jobs set this so they don't spam the
        ledger). Rents the shared ``live_activity.track()`` bracket: begin +
        sidecar heartbeat (so a multi-minute job stays visible past the read's
        freshness window) + finish on every exit path. The ledger writes never
        affect the job's own result or exception path."""
        if getattr(job, "activity_silent", False):
            return await job.run(pool, cfg)
        async with live_activity.track(
            pool,
            kind="job",
            ref_id=job.name,
            title=getattr(job, "description", None) or job.name,
            heartbeat_seconds=PluginScheduler._heartbeat_interval_seconds(cfg),
        ) as act:
            result = await job.run(pool, cfg)
            if not result.ok:
                act.fail()
            return result
```

(Delete the old hand-rolled `aid = ...` / `hb_task = ...` / `try/except/finally` body. `asyncio` and `from services import live_activity` are already imported at the top of `scheduler.py`.)

- [ ] **Step 4: Run the existing suite to verify no regression**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/plugins/test_scheduler_activity.py -v`
Expected: PASS (all 5 — begin/finish bracket, silent-skip, heartbeat-fires, cancel-on-raise all preserved).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/plugins/scheduler.py
git commit -m "refactor(activity): scheduler seam rents live_activity.track() (one bracket)"
```

---

### Task 3: Image-gen media producer — `image_service.generate_image`

Bracket the Z-Image `:9836` render with a `kind='media'` row so image generation (inline images, featured image, CLI/MCP regen — every `generate_image` caller) shows in the pulse. Single blocking call → liveness only (`step="generating"`, no fabricated pct); an honest `fail` status when the render returns/raises false.

**Files:**

- Modify: `src/cofounder_agent/services/image_service.py` (rename the existing `generate_image` body to `_generate_image_impl`; add a thin instrumented `generate_image` wrapper; add a `from services import live_activity` import)
- Test: `src/cofounder_agent/tests/unit/services/test_image_service_activity.py`

**Interfaces:**

- Consumes: `live_activity.track` + `live_activity.resolve_heartbeat_seconds` (Task 1); `getattr(self._site_config, "_pool", None)` (the pool seam `_ensure_pexels_key` already uses, image_service.py:572/664).
- Produces: `ImageService.generate_image(...) -> bool` — unchanged signature/return, now bracketed; `ImageService._generate_image_impl(...) -> bool` — the original body verbatim.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/test_image_service_activity.py
"""image_service.generate_image is bracketed by a best-effort kind='media'
live_activity row so Z-Image renders show in the console pulse. The ledger is
observability — it must never change the render's own bool result, and a
ledger failure must not break the render."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services import image_service as image_service_mod
from services.image_service import ImageService
from services.site_config import SiteConfig

pytestmark = pytest.mark.asyncio


def _svc():
    # SiteConfig with no real pool → track()'s begin swallows → aid None →
    # the bracket is a silent no-op, but the wrapper still delegates + returns.
    return ImageService(SiteConfig(initial_config={}))


async def test_generate_image_opens_media_row_and_finishes_ok():
    svc = _svc()
    seen = {}

    class _FakeCtx:
        async def __aenter__(self):
            return SimpleNamespace(update=AsyncMock(), fail=lambda: seen.__setitem__("failed", True))
        async def __aexit__(self, *exc):
            return False

    def fake_track(pool, **kw):
        seen["kw"] = kw
        return _FakeCtx()

    with patch.object(image_service_mod.live_activity, "track", fake_track), \
         patch.object(ImageService, "_generate_image_impl", AsyncMock(return_value=True)):
        ok = await svc.generate_image("a cat", "/tmp/x.png", task_id="t9")
    assert ok is True
    assert seen["kw"]["kind"] == "media"
    assert seen["kw"]["ref_id"] == "t9"
    assert "cat" in seen["kw"]["title"]
    assert "failed" not in seen           # a success does NOT call handle.fail()


async def test_generate_image_marks_fail_when_impl_returns_false():
    svc = _svc()
    failed = {}

    class _FakeCtx:
        async def __aenter__(self):
            return SimpleNamespace(update=AsyncMock(), fail=lambda: failed.__setitem__("x", True))
        async def __aexit__(self, *exc):
            return False

    with patch.object(image_service_mod.live_activity, "track", lambda pool, **kw: _FakeCtx()), \
         patch.object(ImageService, "_generate_image_impl", AsyncMock(return_value=False)):
        ok = await svc.generate_image("a cat", "/tmp/x.png")
    assert ok is False
    assert failed == {"x": True}


async def test_generate_image_survives_ledger_failure():
    """A track() that blows up must not break the render — the wrapper still
    returns the impl's bool. (Real begin swallows; this asserts the wrapper
    doesn't depend on the ledger succeeding.)"""
    svc = _svc()
    with patch.object(image_service_mod.live_activity, "resolve_heartbeat_seconds",
                      side_effect=RuntimeError("cfg boom")), \
         patch.object(ImageService, "_generate_image_impl", AsyncMock(return_value=True)) as impl:
        # resolve_heartbeat_seconds raising is the harshest ledger-path failure;
        # the render must still run and return.
        with pytest.raises(RuntimeError):
            await svc.generate_image("a cat", "/tmp/x.png")
        # Guard: if the wrapper is written defensively (recommended), this
        # becomes ok is True with impl called. See NOTE.
```

> NOTE for implementer: the harshest failure a ledger helper could throw is `resolve_heartbeat_seconds`/`track` raising _before_ the body. `track()`'s own `begin` already swallows, so the realistic failure mode is fully covered by the silent-no-op path. Keep the wrapper simple (do NOT wrap `resolve_heartbeat_seconds` in its own try) — `resolve_heartbeat_seconds` cannot raise on a real `SiteConfig` (its `.get` returns a str/default and the parse is guarded). Delete the third test's `pytest.raises` block if you judge it over-defensive; the first two tests are the contract. (Kept here to force the implementer to consciously decide the wrapper stays lean.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_service_activity.py -v`
Expected: FAIL — `ImageService` has no attribute `_generate_image_impl` (and `image_service.live_activity` unresolved).

- [ ] **Step 3: Rename the body + add the instrumented wrapper**

In `services/image_service.py`:

1. Add to the imports near the top: `from services import live_activity`.
2. Rename the existing method definition line (currently `async def generate_image(` at ~line 1004) to `async def _generate_image_impl(` — **leave the entire ~280-line body byte-for-byte unchanged.**
3. Insert a new instrumented `generate_image` wrapper **immediately above** `_generate_image_impl`:

```python
    async def generate_image(
        self,
        prompt: str,
        output_path: str,
        negative_prompt: str | None = None,
        num_inference_steps: int | None = None,
        guidance_scale: float | None = None,
        task_id: str | None = None,
        model: "ImageModel | None" = None,
    ) -> bool:
        """Generate an image, bracketed by a best-effort ``kind='media'``
        live_activity row so the Z-Image ``:9836`` render (a ~5-70s GPU burn,
        cold model load included) shows in the console SYSTEM PULSE instead of
        being invisible. The row is liveness-only — a single blocking render
        exposes no mid-progress, so we never fabricate a pct
        (``feedback_no_dummy_data``). Delegates the real work to
        ``_generate_image_impl`` unchanged; the ledger never alters its bool
        result."""
        pool = getattr(self._site_config, "_pool", None)
        _prompt = (prompt or "").strip()
        async with live_activity.track(
            pool,
            kind="media",
            ref_id=str(task_id) if task_id else None,
            title=f"Image · {_prompt[:60]}" if _prompt else "Image",
            detail={"medium": "image", "provider": "image_gen"},
            heartbeat_seconds=live_activity.resolve_heartbeat_seconds(self._site_config),
        ) as act:
            await act.update(step="generating")
            ok = await self._generate_image_impl(
                prompt,
                output_path,
                negative_prompt=negative_prompt,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                task_id=task_id,
                model=model,
            )
            if not ok:
                act.fail()
            return ok
```

> NOTE: `_generate_image_impl` keeps the original positional/keyword signature; the wrapper forwards `negative_prompt`/`num_inference_steps`/`guidance_scale`/`task_id`/`model` as keywords to be explicit. Confirm the `ImageModel` type name is in scope for the wrapper annotation (it's the same annotation the original used — quoting it as above avoids any import-order issue).

- [ ] **Step 4: Run the new test + the existing image_service suite (no regression)**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_image_service_activity.py tests/unit/services/test_image_service.py tests/unit/services/test_image_providers_image_gen.py -v`
Expected: PASS — the new bracket tests pass and every existing `generate_image` caller test is unaffected (track with a pool-less SiteConfig is a silent no-op).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/image_service.py \
        src/cofounder_agent/tests/unit/services/test_image_service_activity.py
git commit -m "feat(activity): image-gen renders as a kind=media pulse row"
```

---

### Task 4: Per-shot progress in `render_shot_list` (the video progress signal)

Add an optional `progress_cb` to the shot-list renderer that fires `"shot i/N"` + honest position-pct as each shot renders. Decoupled — the renderer takes a plain async callback and never imports `live_activity`. Task 5 wires the callback to a media row.

**Files:**

- Modify: `src/cofounder_agent/services/video_renderers/shot_list_renderer.py` (`render_shot_list` signature ~line 835, `_render_pass` ~line 539, add a `_safe_progress` helper)
- Test: `src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_progress.py`

**Interfaces:**

- Consumes: nothing new (a caller-supplied `progress_cb`).
- Produces: `render_shot_list(..., progress_cb: ProgressCb | None = None)` and `_render_pass(shots, *, render_kwargs, progress_cb=None)`, where `ProgressCb = Callable[[str, int | None], Awaitable[None]]`; the callback is invoked once per shot as `await progress_cb(f"shot {i}/{total}", pct)` with `i` 1-based and `pct = clamp(round(100*i/total), 1, 99)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/video_renderers/test_shot_list_progress.py
"""render_shot_list emits per-shot progress through an optional progress_cb so
the media pulse row shows 'shot i/N' with honest position-pct. The callback is
best-effort — a raising callback must never break the render."""
from unittest.mock import AsyncMock, patch

import pytest

from services.video_renderers import shot_list_renderer as slr
from schemas.video_shot_list import VideoShotList

pytestmark = pytest.mark.asyncio

_SHOTS = {
    "version": 1, "aspect": "16:9", "total_duration_s": 9.0,
    "shots": [
        {"idx": i, "duration_s": 3.0, "intent": "x", "source": "image_gen",
         "prompt": f"p{i}", "narration_offset_s": 0.0}
        for i in range(3)
    ],
    "director_model": "ollama/test", "director_prompt_version": "v1",
    "director_decided_at": "2026-06-08T00:00:00+00:00",
}


async def test_progress_cb_fires_per_shot_with_position_pct():
    calls = []
    async def cb(step, pct):
        calls.append((step, pct))

    # Stub the per-shot render + the compositor so no GPU/ffmpeg is touched.
    ok_shot = slr.ShotRenderResult(idx=0, source="image_gen", success=True,
                                   clip_path="/tmp/c.png", duration_s=3.0)
    with patch.object(slr, "_render_one_shot", AsyncMock(return_value=ok_shot)), \
         patch.object(slr, "FFmpegLocalCompositor") as comp:
        comp.return_value.compose = AsyncMock(return_value=type(
            "C", (), {"success": True, "output_path": "/tmp/out.mp4",
                       "file_size_bytes": 1, "duration_s": 9.0})())
        await slr.render_shot_list(
            post_id="p1", shot_list=VideoShotList.model_validate(_SHOTS),
            audio_path="", output_path="/tmp/out.mp4",
            image_gen_url="http://ig:9836", site_config=None,
            progress_cb=cb,
        )
    assert calls == [("shot 1/3", 33), ("shot 2/3", 67), ("shot 3/3", 99)]


async def test_render_survives_a_raising_progress_cb():
    async def bad_cb(step, pct):
        raise RuntimeError("cb boom")
    ok_shot = slr.ShotRenderResult(idx=0, source="image_gen", success=True,
                                   clip_path="/tmp/c.png", duration_s=3.0)
    with patch.object(slr, "_render_one_shot", AsyncMock(return_value=ok_shot)), \
         patch.object(slr, "FFmpegLocalCompositor") as comp:
        comp.return_value.compose = AsyncMock(return_value=type(
            "C", (), {"success": True, "output_path": "/tmp/out.mp4",
                       "file_size_bytes": 1, "duration_s": 9.0})())
        result = await slr.render_shot_list(
            post_id="p1", shot_list=VideoShotList.model_validate(_SHOTS),
            audio_path="", output_path="/tmp/out.mp4",
            image_gen_url="http://ig:9836", site_config=None,
            progress_cb=bad_cb,
        )
    assert result.success is True   # a callback error never fails the render


async def test_no_progress_cb_is_backcompat():
    ok_shot = slr.ShotRenderResult(idx=0, source="image_gen", success=True,
                                   clip_path="/tmp/c.png", duration_s=3.0)
    with patch.object(slr, "_render_one_shot", AsyncMock(return_value=ok_shot)), \
         patch.object(slr, "FFmpegLocalCompositor") as comp:
        comp.return_value.compose = AsyncMock(return_value=type(
            "C", (), {"success": True, "output_path": "/tmp/out.mp4",
                       "file_size_bytes": 1, "duration_s": 9.0})())
        result = await slr.render_shot_list(
            post_id="p1", shot_list=VideoShotList.model_validate(_SHOTS),
            audio_path="", output_path="/tmp/out.mp4",
            image_gen_url="http://ig:9836", site_config=None,
        )
    assert result.success is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/video_renderers/test_shot_list_progress.py -v`
Expected: FAIL — `render_shot_list() got an unexpected keyword argument 'progress_cb'`.

- [ ] **Step 3: Add the callback type, `_safe_progress`, and thread it through**

At the top of `shot_list_renderer.py`, add to the typing imports:

```python
from collections.abc import Awaitable, Callable

ProgressCb = Callable[[str, "int | None"], Awaitable[None]]
```

Add the guard helper (near `_log_shot_audit`):

```python
async def _safe_progress(progress_cb: "ProgressCb | None", step: str, pct: "int | None") -> None:
    """Fire a caller-supplied progress callback, swallowing any error. The
    callback is a live-activity heartbeat — an observability write that must
    never take the render down."""
    if progress_cb is None:
        return
    try:
        await progress_cb(step, pct)
    except Exception as exc:  # noqa: BLE001 — best-effort progress, never fatal
        logger.debug("[SHOT_LIST] progress_cb swallowed: %s", exc)
```

Change `_render_pass` to accept + emit progress (add the `progress_cb` param and the per-shot emit at the TOP of the loop, so "shot i/N" shows while shot i is rendering):

```python
async def _render_pass(
    shots: list[Shot],
    *,
    render_kwargs: dict[str, Any],
    progress_cb: "ProgressCb | None" = None,
) -> list[_ShotState]:
    states: list[_ShotState] = []
    render_prior: str | None = None
    total = len(shots)
    for i, shot in enumerate(shots, start=1):
        pct = min(99, max(1, round(100 * i / total))) if total else None
        await _safe_progress(progress_cb, f"shot {i}/{total}", pct)
        result = await _render_one_shot(shot, prior_clip=render_prior, **render_kwargs)
        is_reused = bool(
            result.success and result.clip_path
            and result.clip_path == render_prior,
        )
        states.append(_ShotState(shot=shot, result=result, is_reused=is_reused))
        if result.success and result.clip_path:
            render_prior = result.clip_path
    return states
```

Add `progress_cb: "ProgressCb | None" = None` to the `render_shot_list` keyword-only signature (after `caption_path`), document it in the docstring's Args, and pass it into the `_render_pass` call (~line 937):

```python
    states = await _render_pass(
        capped_shots, render_kwargs=render_kwargs, progress_cb=progress_cb,
    )
```

Docstring Args addition:

```
        progress_cb: Optional async callback ``(step: str, pct: int | None)``
            fired once per shot as it renders (``"shot i/N"`` + honest
            position-pct, 1..99). Best-effort — a raising callback never fails
            the render. The Plan-4 media atom wires this to a live_activity
            ``media`` row so the console pulse shows real per-shot progress;
            ``None`` (the default, and the legacy caller) renders silently.
```

- [ ] **Step 4: Run the new test + the existing shot-list suite (no regression)**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/video_renderers/test_shot_list_progress.py tests/unit/services/video_renderers/test_shot_list_renderer.py -v`
Expected: PASS — progress fires as specified and the existing renderer behaviour is unchanged (progress_cb defaults None).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/video_renderers/shot_list_renderer.py \
        src/cofounder_agent/tests/unit/services/video_renderers/test_shot_list_progress.py
git commit -m "feat(activity): render_shot_list emits per-shot progress via optional progress_cb"
```

---

### Task 5: Video media producer — `_media_render.render_from_state`

Bracket the video render (the `gpu.lock` + `render_shot_list` block) with a `kind='media'` row and wire the Task-4 `progress_cb` to it, so the long/short video render shows in "In Production" with live "shot i/N · pct".

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/_media_render.py` (`render_from_state` ~line 133-159; add a `from services import live_activity` import)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_media_render_activity.py`

**Interfaces:**

- Consumes: `live_activity.track` + `live_activity.resolve_heartbeat_seconds` (Task 1); `render_shot_list(..., progress_cb=...)` (Task 4); the already-resolved `pool` (line 116-121), `site_config` (109), `task_id` (108).
- Produces: no signature change to `render_from_state` (same `(state, *, shot_list_key, output_key, narration_key, caption_key)`); the render now emits a media row.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/atoms/test_media_render_activity.py
"""_media_render.render_from_state brackets the video render with a best-effort
kind='media' live_activity row and threads a progress_cb into render_shot_list
so the console pulse shows live per-shot progress. Best-effort — a ledger
failure never changes the render's returned output."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.atoms import _media_render

pytestmark = pytest.mark.asyncio

_LONG = {
    "version": 1, "aspect": "16:9", "total_duration_s": 3.0,
    "shots": [{"idx": 0, "duration_s": 3.0, "intent": "x", "source": "image_gen",
               "prompt": "p", "narration_offset_s": 0.0}],
    "director_model": "ollama/test", "director_prompt_version": "v1",
    "director_decided_at": "2026-06-08T00:00:00+00:00",
}


class _FakeLock:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _patch_gpu_lock():
    with patch.object(_media_render.gpu, "lock", MagicMock(return_value=_FakeLock())):
        yield


def _ok_result(**kw):
    base = dict(success=True, output_path="/tmp/out.mp4", file_size_bytes=1,
                duration_s=3.0, shots_rendered=1, shots_total=1, error=None)
    base.update(kw)
    return SimpleNamespace(**base)


async def test_render_opens_media_row_and_passes_progress_cb():
    seen = {}

    class _FakeCtx:
        async def __aenter__(self):
            return SimpleNamespace(update=AsyncMock(), fail=lambda: seen.__setitem__("failed", True))
        async def __aexit__(self, *exc):
            return False

    def fake_track(pool, **kw):
        seen["kw"] = kw
        return _FakeCtx()

    mock_render = AsyncMock(return_value=_ok_result())
    with patch.object(_media_render.live_activity, "track", fake_track), \
         patch.object(_media_render, "render_shot_list", mock_render):
        out = await _media_render.render_from_state(
            {"task_id": "t5", "video_shot_list": _LONG},
            shot_list_key="video_shot_list", output_key="long_video_path",
        )
    assert out == {"long_video_path": "/tmp/out.mp4"}
    assert seen["kw"]["kind"] == "media"
    assert seen["kw"]["ref_id"] == "t5"
    assert "Video" in seen["kw"]["title"]
    # the progress_cb was threaded into render_shot_list
    assert callable(mock_render.await_args.kwargs["progress_cb"])
    assert "failed" not in seen           # a full render does not call fail()


async def test_render_failure_marks_media_row_fail():
    failed = {}

    class _FakeCtx:
        async def __aenter__(self):
            return SimpleNamespace(update=AsyncMock(), fail=lambda: failed.__setitem__("x", True))
        async def __aexit__(self, *exc):
            return False

    mock_render = AsyncMock(return_value=_ok_result(success=False, output_path=None,
                                                    shots_rendered=0, error="none"))
    with patch.object(_media_render.live_activity, "track", lambda pool, **kw: _FakeCtx()), \
         patch.object(_media_render, "render_shot_list", mock_render), \
         patch.object(_media_render, "emit_finding", MagicMock()):
        out = await _media_render.render_from_state(
            {"task_id": "t5", "video_shot_list": _LONG},
            shot_list_key="video_shot_list", output_key="long_video_path",
        )
    assert out == {"long_video_path": ""}
    assert failed == {"x": True}


async def test_progress_cb_updates_the_row():
    """The threaded progress_cb delegates to the handle's update — so a
    render_shot_list that fires it bumps the media row."""
    updates = []

    class _FakeCtx:
        async def __aenter__(self):
            h = SimpleNamespace(fail=lambda: None)
            async def _upd(*, step=None, pct=None):
                updates.append((step, pct))
            h.update = _upd
            return h
        async def __aexit__(self, *exc):
            return False

    async def render_and_report(*, progress_cb, **kw):
        await progress_cb("shot 1/1", 99)
        return _ok_result()

    with patch.object(_media_render.live_activity, "track", lambda pool, **kw: _FakeCtx()), \
         patch.object(_media_render, "render_shot_list", render_and_report):
        await _media_render.render_from_state(
            {"task_id": "t5", "video_shot_list": _LONG},
            shot_list_key="video_shot_list", output_key="long_video_path",
        )
    assert updates == [("shot 1/1", 99)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_media_render_activity.py -v`
Expected: FAIL — `module 'modules.content.atoms._media_render' has no attribute 'live_activity'` / `render_shot_list` called without `progress_cb`.

- [ ] **Step 3: Wrap the render in a media row + thread the progress_cb**

In `modules/content/atoms/_media_render.py`:

1. Add `from services import live_activity` to the imports.
2. Replace the `try: async with gpu.lock(...): result = await render_shot_list(...)` block (~line 133-159) with a version that opens a media row around it and threads a progress callback bound to the row:

```python
    lane = output_key.replace("_video_path", "") or output_key  # "long" / "short"
    total_shots = len(shot_list.shots)
    try:
        async with live_activity.track(
            pool,
            kind="media",
            ref_id=str(task_id) if task_id else None,
            title=f"Video · {lane}",
            detail={"medium": "video", "output_key": output_key,
                    "shots_total": total_shots},
            heartbeat_seconds=live_activity.resolve_heartbeat_seconds(site_config),
        ) as act:
            async def _progress(step: str, pct: int | None) -> None:
                await act.update(step=step, pct=pct)

            # Hold the GPU for the whole render. The render drives wan + image-gen
            # over HTTP and never went through the scheduler before (validation
            # findings 4b/7): the ~18GB writer/director stayed resident in Ollama
            # and starved wan+image-gen → "inference server unreachable" → render
            # failures. The "video" owner evicts Ollama on acquire, and the
            # cross-process pg_advisory_lock blocks the content pipeline for the
            # render's duration so they can't oversubscribe the 32GB card.
            async with gpu.lock(
                "video",
                model="shot_list_render",
                task_id=str(task_id or "") or None,
                phase="media_render",
            ):
                result = await render_shot_list(
                    post_id=str(task_id or ""),
                    shot_list=shot_list,
                    audio_path=narration,
                    output_path=out_path,
                    image_gen_url=image_gen_url,
                    site_config=site_config,
                    pool=pool,
                    width=width,
                    height=height,
                    ambient_path=ambient,
                    caption_path=caption,
                    progress_cb=_progress,
                )
            if not result.success:
                act.fail()
    except Exception as exc:  # noqa: BLE001 — a render must never halt the graph
        logger.exception("[media.render] %s render raised: %s", output_key, exc)
        emit_finding(
            source="media.render_video",
            kind="render_failed",
            title=f"{output_key}: render raised an exception",
            body=f"render_shot_list raised for task {task_id}: {exc}",
            severity="warn",
            dedup_key=f"render_failed:{task_id}:{output_key}",
            extra={"task_id": str(task_id or ""), "output_key": output_key},
        )
        return {output_key: ""}
```

(The `if not result.success` / `partial_render` / return blocks that FOLLOW this stay exactly as they are — the media row is already closed by the `async with` before they run.)

- [ ] **Step 4: Run the new test + the existing media-render suite (no regression)**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_media_render_activity.py tests/unit/services/atoms/test_media_render_video.py -v`
Expected: PASS — the new bracket/progress tests pass and every existing `render_from_state` test is unaffected (they run with no pool in state → track is a silent no-op; `render_shot_list` is mocked so the added `progress_cb` kwarg is simply captured).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_media_render.py \
        src/cofounder_agent/tests/unit/services/atoms/test_media_render_activity.py
git commit -m "feat(activity): video render as a kind=media pulse row with per-shot progress"
```

---

### Task 6: Audio media producer — `_narration_render.render_narration`

Bracket the podcast + video-narration TTS synth (both `podcast.render` and `media.render_narration` delegate here) with a `kind='media'` row so audio renders show in the pulse. Stage-level liveness (`step="synthesizing narration"`).

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/_narration_render.py` (`render_narration` ~line 101-133; add a `from services import live_activity` import)
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_narration_render_activity.py`

**Interfaces:**

- Consumes: `live_activity.track` + `live_activity.resolve_heartbeat_seconds` (Task 1); `getattr(site_config, "_pool", None)`.
- Produces: `render_narration(*, script, cta_key, site_config, task_id, key) -> str` — unchanged signature/return, now bracketed.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/atoms/test_narration_render_activity.py
"""_narration_render.render_narration brackets TTS synth with a best-effort
kind='media' live_activity row (podcast + video narration share this path).
Best-effort — a ledger failure never changes the returned path."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.atoms import _narration_render

pytestmark = pytest.mark.asyncio


class _Cfg:
    """SiteConfig stand-in: .get for CTA, ._pool for the ledger seam."""
    _pool = object()
    def get(self, k, d=None):
        return d
    def get_bool(self, k, d=False):
        return d


async def test_narration_opens_media_row_and_synthesizes():
    seen = {}

    class _FakeCtx:
        async def __aenter__(self):
            return SimpleNamespace(update=AsyncMock(), fail=lambda: seen.__setitem__("failed", True))
        async def __aexit__(self, *exc):
            return False

    def fake_track(pool, **kw):
        seen["kw"] = kw
        return _FakeCtx()

    fake_svc = SimpleNamespace(synthesize=AsyncMock(return_value=("/tmp/n.mp3", 12.0)))
    with patch.object(_narration_render.live_activity, "track", fake_track), \
         patch("services.podcast_service.PodcastService", return_value=fake_svc):
        out = await _narration_render.render_narration(
            script="hello world", cta_key="media.cta.podcast",
            site_config=_Cfg(), task_id="t6", key="t6",
        )
    assert out == "/tmp/n.mp3"
    assert seen["kw"]["kind"] == "media"
    assert seen["kw"]["ref_id"] == "t6"
    assert "Narration" in seen["kw"]["title"]


async def test_empty_script_skips_the_row():
    """An empty script is a fail-soft no-op BEFORE any render — it must not
    open a media row (nothing is rendering)."""
    opened = {}
    with patch.object(_narration_render.live_activity, "track",
                      lambda pool, **kw: opened.setdefault("x", True)):
        out = await _narration_render.render_narration(
            script="   ", cta_key="media.cta.podcast",
            site_config=_Cfg(), task_id="t6", key="t6",
        )
    assert out == ""
    assert opened == {}


async def test_synth_failure_marks_fail_and_returns_empty():
    failed = {}

    class _FakeCtx:
        async def __aenter__(self):
            return SimpleNamespace(update=AsyncMock(), fail=lambda: failed.__setitem__("x", True))
        async def __aexit__(self, *exc):
            return False

    fake_svc = SimpleNamespace(synthesize=AsyncMock(side_effect=RuntimeError("tts down")))
    with patch.object(_narration_render.live_activity, "track", lambda pool, **kw: _FakeCtx()), \
         patch("services.podcast_service.PodcastService", return_value=fake_svc):
        out = await _narration_render.render_narration(
            script="hello", cta_key="media.cta.podcast",
            site_config=_Cfg(), task_id="t6", key="t6",
        )
    assert out == ""            # fail-soft: synth failure returns empty, never raises
    assert failed == {"x": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_narration_render_activity.py -v`
Expected: FAIL — `module 'modules.content.atoms._narration_render' has no attribute 'live_activity'`.

- [ ] **Step 3: Bracket the synth**

In `modules/content/atoms/_narration_render.py`:

1. Add `from services import live_activity` to the imports.
2. Replace the synth section of `render_narration` (the `from services.podcast_service import PodcastService` + `try: path, _duration = await PodcastService(...).synthesize(...)` block) with a bracketed version. Keep the empty-script/`site_config is None` early-return ABOVE the row (an empty script isn't a render):

```python
    text = compose_narration_text(
        script=script, cta_key=cta_key, site_config=site_config,
    )
    if not text or site_config is None:
        return ""

    from services.podcast_service import PodcastService

    pool = getattr(site_config, "_pool", None)
    try:
        async with live_activity.track(
            pool,
            kind="media",
            ref_id=str(task_id) if task_id else None,
            title=f"Narration · {key}",
            detail={"medium": "audio", "cta_key": cta_key},
            heartbeat_seconds=live_activity.resolve_heartbeat_seconds(site_config),
        ) as act:
            await act.update(step="synthesizing narration")
            path, _duration = await PodcastService(site_config=site_config).synthesize(
                text, key=key,
            )
    except Exception as exc:  # noqa: BLE001 — TTS failure must not halt the graph
        logger.warning(
            "[_narration_render] synthesis failed (key=%s, task=%s): %s",
            key, task_id, exc,
        )
        return ""
    return path or ""
```

(`track()`'s exception path marks the row 'fail' then re-raises into this `except`, which fail-softly returns `""` exactly as before — the row is correctly closed 'fail'.)

- [ ] **Step 4: Run the new test + the existing narration suite (no regression)**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_narration_render_activity.py tests/unit/services/atoms/test_narration_render.py -v`
Expected: PASS — the new bracket tests pass and the existing narration behaviour (label-strip, CTA-append, fail-soft) is unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/_narration_render.py \
        src/cofounder_agent/tests/unit/services/atoms/test_narration_render_activity.py
git commit -m "feat(activity): podcast + video narration render as a kind=media pulse row"
```

---

### Task 7: Update the spec + memory, run the broad suite, open the PR

Close out: mark Phase 2 media instrumentation shipped in the design doc, update the project memory's deferred-gap note, run the touched suites together, and open the PR.

**Files:**

- Modify: `docs/superpowers/specs/2026-07-10-console-live-activity-design.md` (Producers "Phase 2" + Phasing "Phase 2" — mark media render progress shipped)
- Modify: `~/.claude/projects/C--Users-mattm-glad-labs-website/memory/project_console_live_activity.md` (gap (b) closed)

- [ ] **Step 1: Update the design doc**

In `docs/superpowers/specs/2026-07-10-console-live-activity-design.md`, under **Producers → Phase 2 — media depth** and under **Phasing → Phase 2**, note that media render progress is now instrumented: image-gen (`image_service.generate_image`, liveness), video (`_media_render.render_from_state` + per-shot `render_shot_list` progress), and audio (`_narration_render.render_narration`, liveness) all emit `kind='media'` rows via the shared `live_activity.track()` bracket. Keep the WALL pulse + persistent strip as still-deferred.

- [ ] **Step 2: Update the project memory**

In `project_console_live_activity.md`, change deferred-gap (b) from "image/media generation is not an instrumented producer" to record that image + video (per-shot) + audio renders are now instrumented as `kind='media'` rows through `live_activity.track()`, and that the scheduler seam was refactored to rent the same bracket. Add a one-line note that `live_activity.track()` is the reusable bracket future producers should rent (don't hand-roll begin/heartbeat/finish).

- [ ] **Step 3: Run the full touched-area suite**

Run:

```bash
cd src/cofounder_agent && poetry run pytest \
  tests/unit/services/test_live_activity_track.py \
  tests/unit/services/test_live_activity_heartbeat.py \
  tests/unit/services/test_live_activity_swallow.py \
  tests/unit/plugins/test_scheduler_activity.py \
  tests/unit/services/test_image_service_activity.py \
  tests/unit/services/test_image_service.py \
  tests/unit/services/video_renderers/test_shot_list_progress.py \
  tests/unit/services/video_renderers/test_shot_list_renderer.py \
  tests/unit/services/atoms/test_media_render_activity.py \
  tests/unit/services/atoms/test_media_render_video.py \
  tests/unit/services/atoms/test_narration_render_activity.py \
  tests/unit/services/atoms/test_narration_render.py \
  tests/unit/services/atoms/test_podcast_render.py \
  -q
```

Expected: all green.

- [ ] **Step 4: Lint the touched Python**

Run: `cd src/cofounder_agent && poetry run ruff check services/live_activity.py plugins/scheduler.py services/image_service.py services/video_renderers/shot_list_renderer.py modules/content/atoms/_media_render.py modules/content/atoms/_narration_render.py`
Expected: clean (no new F401/F841/etc.).

- [ ] **Step 5: Commit the docs + open the PR**

```bash
git add docs/superpowers/specs/2026-07-10-console-live-activity-design.md
git commit -m "docs(activity): Phase 2 media render producers shipped"
git push -u origin HEAD
gh pr create --repo Glad-Labs/glad-labs-stack --title "feat(activity): media render producers — image/video/podcast in the pulse" --body "<see PR body below>"
```

PR body outline: what (image-gen + video per-shot + podcast/narration now emit `kind='media'` live_activity rows; scheduler seam refactored to rent the shared `live_activity.track()` bracket), why (closes the Phase-1-deferred media render-progress gap so GPU-heavy renders show in the console SYSTEM PULSE "In Production" column), contract untouched (`live_activity` table + `/api/activity` unchanged — a producer swap), best-effort (every ledger write swallows; a failure never breaks a render), honest progress (video = shot position, image/audio = liveness — no fabricated pct), and the memory update.

---

## Self-Review

**Spec coverage** (design doc Phase 2 = "the `media_pipeline` render atoms emit `update(step, pct)` as shots render / audio mixes; video per-shot, podcast stage-level"):

- Image-gen render visible → Task 3 ✅ (the memory's concrete example — the `:9836` Z-Image warmup).
- Video per-shot progress → Tasks 4 + 5 ✅ ("shot i/N" + position-pct).
- Podcast / narration stage-level → Task 6 ✅.
- Contract frozen (no table/route change) → all tasks add producers only ✅.
- Best-effort, no fabricated pct, reuse existing settings → Global Constraints + every task ✅.
- Reusable bracket (no divergent copies) → Task 1 `track()` + Task 2 scheduler refactor ✅.

**Placeholder scan:** every code step shows the real code; no TBD/TODO. ✅

**Type consistency:** `track(pool, *, kind, ref_id, title, detail=None, heartbeat_seconds=30.0) -> AsyncIterator[ActivityHandle]`; `ActivityHandle.update(*, step=None, pct=None)` / `.fail()` / `.status`; `resolve_heartbeat_seconds(site_config, *, default=30.0) -> float`; `ProgressCb = Callable[[str, int | None], Awaitable[None]]`; `render_shot_list(..., progress_cb=None)` and `_render_pass(..., progress_cb=None)` — consistent across Tasks 1–6. ✅

## Deferred (explicitly NOT in this plan)

- **Persistent cross-mode strip** (Phase 1.5) and **WALL pulse** (Phase 2 console) — console consumers, unrelated to producers.
- **Media-server progress polling** — the `:9836`/wan servers expose no mid-render progress endpoint today, so image + audio are liveness-only. If a progress endpoint is added later, map it to `pct` in the existing seam (the `act.update` call is already there).
- **Repair-pass progress** — per-shot progress fires on the initial `_render_pass`; the bounded QA regen (`_repair_pass`) is kept fresh by the sidecar heartbeat but doesn't emit shot-level steps. Fine (honest liveness), a later polish if wanted.
- **`C` consolidation** (Phase 3) — APScheduler → Prefect.
