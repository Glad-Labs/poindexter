# Media-Gated Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the dormant per-medium gate engine so a post generates + has its media reviewed _before_ it publishes — `approve → media generates → operator reviews → publish`.

**Architecture:** Reuse `services/gates/post_approval_gates.py` (the per-medium gate state machine) as the media-gate home. On post-creation (the `approved` state in `publish_post_from_task`), create the media gate sequence; a new polling Job drives `advance_workflow` to generate media per medium and gate the `approved → published` transition on all gates clearing. The pipeline gates (`pipeline_tasks.awaiting_gate`) stay as-is for text approval.

**Tech Stack:** Python 3.13, asyncpg, FastAPI worker, the plugin Job system (`services/jobs/*.py` + `plugins/registry.py`), pytest (`poetry run pytest`), the existing `post_approval_gates`, `media_approvals`, and `generate_podcast_episode`/`generate_video_episode` services.

**Spec:** `docs/superpowers/specs/2026-05-31-media-gated-publish-design.md`

**Decisions locked (from spec):** D1 reject→revise/regenerate; D2 text-only posts publish immediately at `final`; D3 driver = polling Job; D4 cost-estimate gate deferred.

**Conventions for every task:** run tests with `cd src/cofounder_agent && poetry run pytest <path> -v`. Commit after each green task. This repo forbids merge commits — work on a feature branch / linear commits, normal push (no `--no-ff`). Match existing file style.

---

### Task 1: `media_gate_sequence()` — map `media_to_generate` → ordered gate names

Pure function. No DB. The smallest, most-testable unit; everything else depends on it.

**Files:**

- Modify: `src/cofounder_agent/services/gates/post_approval_gates.py` (add function near `CANONICAL_GATE_NAMES`)
- Test: `src/cofounder_agent/tests/unit/services/gates/test_media_gate_sequence.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from services.gates.post_approval_gates import media_gate_sequence


@pytest.mark.parametrize("media,expected", [
    (["podcast", "video"], ["podcast", "video", "final"]),
    (["video", "podcast", "short"], ["podcast", "video", "short", "final"]),  # canonical order
    (["podcast"], ["podcast", "final"]),
    ([], ["final"]),                       # text-only still gets a final gate (D2 fast-path in driver)
    (["video", "bogus"], ["video", "final"]),  # unknown media dropped
])
def test_media_gate_sequence(media, expected):
    assert media_gate_sequence(media) == expected
```

- [ ] **Step 2: Run it, verify it fails** — `poetry run pytest tests/unit/services/gates/test_media_gate_sequence.py -v` → FAIL (ImportError).

- [ ] **Step 3: Implement** — in `post_approval_gates.py`, after `MEDIUM_GATE_NAMES`/`CANONICAL_GATE_NAMES`:

```python
def media_gate_sequence(media_to_generate: list[str]) -> list[str]:
    """Map a post's media_to_generate to the ordered gate names.

    Medium gates appear in canonical (MEDIUM_GATE_NAMES) order regardless of
    input order; unknown media are dropped; 'final' is always appended so
    every post has an explicit publish checkpoint.
    """
    wanted = {m for m in (media_to_generate or [])}
    gates = [m for m in MEDIUM_GATE_NAMES if m in wanted]
    gates.append("final")
    return gates
```

- [ ] **Step 4: Run it, verify it passes** (5 cases).
- [ ] **Step 5: Commit** — `feat(gates): media_gate_sequence — media_to_generate -> ordered gate names`.

---

### Task 2: `resolve_media_to_generate()` — read the niche policy at approval time

Expose the niche-policy lookup (today done at publish) so it can be called at post-creation.

**Files:**

- Create: `src/cofounder_agent/services/media_policy.py`
- Test: `src/cofounder_agent/tests/unit/services/test_media_policy.py`

- [ ] **Step 1: Write the failing test** (uses the shared `db_pool` fixture in `tests/unit/conftest.py`)

```python
import pytest
from services.media_policy import resolve_media_to_generate


@pytest.mark.asyncio
async def test_resolves_from_niche(db_pool):
    await db_pool.execute(
        "INSERT INTO niches (slug, default_media_to_generate) VALUES ($1, $2)",
        "ai-ml", ["podcast", "video"],
    )
    assert await resolve_media_to_generate(db_pool, "ai-ml") == ["podcast", "video"]


@pytest.mark.asyncio
async def test_missing_niche_returns_empty(db_pool):
    assert await resolve_media_to_generate(db_pool, "does-not-exist") == []
```

- [ ] **Step 2: Run it, verify it fails** — ImportError.
- [ ] **Step 3: Implement** (mirror the `SELECT default_media_to_generate FROM niches` in `services/jobs/backfill_podcasts.py`)

```python
"""Resolve a post's media plan from its niche, callable at approval time."""
from __future__ import annotations
from typing import Any


async def resolve_media_to_generate(pool: Any, niche_slug: str | None) -> list[str]:
    if not niche_slug:
        return []
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT default_media_to_generate FROM niches WHERE slug = $1",
            niche_slug,
        )
    if not row or not row["default_media_to_generate"]:
        return []
    return list(row["default_media_to_generate"])
```

- [ ] **Step 4: Run it, verify it passes.**
- [ ] **Step 5: Commit** — `feat(media): resolve_media_to_generate from niche policy`.

> NOTE: confirm the `niches` column with `\d niches` (`default_media_to_generate` is `text[]`, migration `20260519_134736`). If posts carry the niche by id, add a slug lookup; the Task 3 caller passes whatever the post row has.

---

### Task 3: create the media gate sequence at post-creation

**Files:**

- Modify: `src/cofounder_agent/services/publish_service.py` — inside `publish_post_from_task` (def at line 486), right after the post INSERT that sets status `approved`.
- Test: `src/cofounder_agent/tests/unit/services/test_publish_creates_gates.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from services import publish_service
from services.gates.post_approval_gates import get_gates_for_post


@pytest.mark.asyncio
async def test_approve_creates_media_gates_and_does_not_publish(db_pool, awaiting_task_factory):
    task_id, niche = await awaiting_task_factory(media=["podcast", "video"])
    result = await publish_service.publish_post_from_task(task_id, pool=db_pool)
    post_id = result["post_id"]
    gates = [g["gate_name"] for g in await get_gates_for_post(db_pool, post_id)]
    assert gates == ["podcast", "video", "final"]
    status = await db_pool.fetchval("SELECT status FROM posts WHERE id::text=$1", post_id)
    assert status == "approved"  # NOT published — gates pending
```

- [ ] **Step 2: Run it, verify it fails** (no gates created today).
- [ ] **Step 3: Implement** — after the post INSERT in `publish_post_from_task`:

```python
from services.media_policy import resolve_media_to_generate
from services.gates.post_approval_gates import create_gates_for_post, media_gate_sequence

media = await resolve_media_to_generate(pool, niche_slug)  # niche_slug already on the task/post row
gates = media_gate_sequence(media)
await create_gates_for_post(pool, post_id, gates)
```

Ensure a post with any medium gate is left at `approved` (not flipped to `published`) — the existing `_post_has_pending_gates`/`_gates_block_distribution` guards already defer distribution; the driver (Task 5) performs publish when gates clear.

- [ ] **Step 4: Run it, verify it passes.**
- [ ] **Step 5: Commit** — `feat(publish): create media gate sequence on post-creation`.

> NOTE: read `publish_post_from_task` (publish_service.py:486+) to place the hook and find the `niche_slug` already on the row. Keep back-compat: a text-only post (`media_gate_sequence == ["final"]`) must still reach `published` (Task 5 auto-advances `final`).

---

### Task 4: split media generation OUT of the post-publish hooks

Today `generate_podcast_episode`/`generate_video_episode` fire as post-publish hooks (publish_service.py ~1242-1356, ~1756-1775). Move their invocation to the driver (Task 5). Leave the generator functions untouched.

**Files:**

- Modify: `src/cofounder_agent/services/publish_service.py` (the media-gen hook blocks)
- Test: `src/cofounder_agent/tests/unit/services/test_publish_no_media_on_publish.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_publish_does_not_generate_media_for_gated_post(db_pool, awaiting_task_factory, monkeypatch):
    called = {"podcast": 0, "video": 0}
    monkeypatch.setattr("services.podcast_service.generate_podcast_episode",
                        lambda *a, **k: called.__setitem__("podcast", called["podcast"] + 1))
    monkeypatch.setattr("services.video_service.generate_video_episode",
                        lambda *a, **k: called.__setitem__("video", called["video"] + 1))
    task_id, _ = await awaiting_task_factory(media=["podcast", "video"])
    await publish_service.publish_post_from_task(task_id, pool=db_pool)
    assert called == {"podcast": 0, "video": 0}  # media gen is the driver's job now
```

- [ ] **Step 2: Run it, verify it fails** (media currently generates on publish).
- [ ] **Step 3: Implement** — remove the podcast/video generation calls from the post-publish hook blocks (keep R2 upload / RSS in the _distribution_ path for Task 6). Media-gen now lives only in the driver.
- [ ] **Step 4: Run it, verify it passes.** Also run `poetry run pytest tests/unit/services/test_publish_service*.py -q`.
- [ ] **Step 5: Commit** — `refactor(publish): media-gen is driver-owned, not a post-publish hook`.

---

### Task 5: the gate driver Job — generate media, then publish on clear

A polling Job (matching the `idle_worker`→Job migration pattern, e.g. `services/jobs/backfill_podcasts.py`).

**Files:**

- Create: `src/cofounder_agent/services/jobs/drive_media_gates.py`
- Modify: `src/cofounder_agent/plugins/registry.py` (register in `_SAMPLES`)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_drive_media_gates.py`

Driver logic per `approved` post: `adv = advance_workflow(pool, post_id)`. If `adv.ready_to_distribute` → publish (Task 6). If `adv.next_gate` is a medium gate → generate the medium if no artifact yet (else it's the operator's turn — no-op). If `adv.next_gate` is `final` → auto-approve it (D2/auto-advance) unless a human-final policy is set.

- [ ] **Step 1: Write failing tests** (three cases)

```python
@pytest.mark.asyncio
async def test_driver_generates_pending_medium(db_pool, approved_post_with_gates, monkeypatch):
    calls = []
    monkeypatch.setattr("services.podcast_service.generate_podcast_episode",
                        lambda *a, **k: calls.append("podcast"))
    await approved_post_with_gates(["podcast", "video"])  # both pending, no artifacts
    from services.jobs.drive_media_gates import drive_once
    await drive_once(db_pool)
    assert calls == ["podcast"]  # only the first pending medium fires this tick


@pytest.mark.asyncio
async def test_driver_publishes_when_all_gates_approved(db_pool, approved_post_with_gates):
    post_id = await approved_post_with_gates(["podcast"], approve_all=True)
    from services.jobs.drive_media_gates import drive_once
    await drive_once(db_pool)
    assert await db_pool.fetchval("SELECT status FROM posts WHERE id::text=$1", post_id) == "published"


@pytest.mark.asyncio
async def test_text_only_post_auto_publishes(db_pool, approved_post_with_gates):
    post_id = await approved_post_with_gates([])  # only the 'final' gate, pending
    from services.jobs.drive_media_gates import drive_once
    await drive_once(db_pool)
    assert await db_pool.fetchval("SELECT status FROM posts WHERE id::text=$1", post_id) == "published"
```

- [ ] **Step 2: Run them, verify they fail** (module missing).
- [ ] **Step 3: Implement** `drive_once(pool)` (testable core) + a thin `DriveMediaGatesJob` wrapper:

```python
"""Drive the per-medium gate workflow for approved posts (replaces the
retired IdleWorker tick). Generates media for pending medium gates, then
publishes once advance_workflow reports ready_to_distribute."""
from __future__ import annotations
from typing import Any

from services.gates.post_approval_gates import (
    advance_workflow, approve_gate, MEDIUM_GATE_NAMES,
)
from services.logger_config import get_logger

logger = get_logger(__name__)

# gate name -> media_assets.type (the 'short' gate stores 'video_short' assets)
_GATE_TO_ASSET = {"podcast": "podcast", "video": "video", "short": "video_short"}


async def _artifact_exists(pool, post_id: str, medium: str) -> bool:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT 1 FROM media_assets WHERE post_id::text=$1 AND type=$2 LIMIT 1",
            post_id, _GATE_TO_ASSET[medium],
        ) is not None


async def _approved_post_ids(pool) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id::text AS id FROM posts WHERE status='approved'")
    return [r["id"] for r in rows]


async def drive_once(pool: Any) -> dict:
    summary = {"generated": 0, "published": 0, "awaiting_operator": 0}
    for post_id in await _approved_post_ids(pool):
        adv = await advance_workflow(pool, post_id)
        if adv.ready_to_distribute:
            from services.publish_service import publish_now  # Task 6
            await publish_now(pool, post_id)
            summary["published"] += 1
            continue
        gate = adv.next_gate or {}
        name = gate.get("gate_name")
        if name in MEDIUM_GATE_NAMES:
            if not await _artifact_exists(pool, post_id, name):
                await _trigger_media_gen(pool, post_id, name)
                summary["generated"] += 1
            else:
                summary["awaiting_operator"] += 1
        elif name == "final":
            await approve_gate(pool, post_id, "final", approver="auto:driver")
    return summary


async def _trigger_media_gen(pool, post_id: str, medium: str) -> None:
    # Pass the same args publish_service passed. Read the generator signatures
    # at services/podcast_service.py:937 and services/video_service.py:1130 and
    # reproduce the call (post title/content/script from the posts row).
    ...
```

- [ ] **Step 4: Run them, verify they pass.**
- [ ] **Step 5: Register the Job** in `plugins/registry.py` `_SAMPLES` with `plugin.job.drive_media_gates.interval_seconds` (default 300), mirroring `backfill_podcasts`. (Notifications already handled by the existing `gate_pending_summary_probe`.)
- [ ] **Step 6: Commit** — `feat(jobs): drive_media_gates — media-gen + publish-on-clear driver`.

> NOTE: `_trigger_media_gen` must pass the same arguments as the old publish hook. Read `generate_podcast_episode` (podcast_service.py:937) and `generate_video_episode` (video_service.py:1130) for exact signatures. The `_artifact_exists` guard keeps generation single-fire-per-tick (idempotent).

---

### Task 6: `publish_now()` — explicit publish+distribution entrypoint

Factor the "flip approved→published + fire distribution" tail of `publish_post_from_task` into a callable the driver (and `tasks publish`) use.

**Files:**

- Modify: `src/cofounder_agent/services/publish_service.py` (extract `publish_now(pool, post_id)`)
- Test: `src/cofounder_agent/tests/unit/services/test_publish_now.py`

- [ ] **Step 1: Write the failing test** — `publish_now` on an `approved` post with all gates approved flips it to `published` and fires distribution (patched distributor + status assert).
- [ ] **Step 2: Run it, verify it fails.**
- [ ] **Step 3: Implement** — move the distribution side-effects (RSS/social/devto, behind `_gates_block_distribution`) + the `status='published'` update into `publish_now(pool, post_id)`; `publish_post_from_task` no longer publishes gated posts. `tasks publish` route → `publish_now`.
- [ ] **Step 4: Run it, verify it passes** (+ the full publish test module).
- [ ] **Step 5: Commit** — `refactor(publish): publish_now() — distribution decoupled from post-creation`.

---

### Task 7: reject → revise/regenerate (D1) through the driver

**Files:**

- Modify: `src/cofounder_agent/services/jobs/drive_media_gates.py` (handle `revising`)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_drive_media_gates_revise.py`

- [ ] **Step 1: Write the failing test** — a medium gate in `revising` (operator called `revise_gate`) → driver deletes the stale `media_assets` row and regenerates; the gate returns toward `pending`.
- [ ] **Step 2: Run it, verify it fails.**
- [ ] **Step 3: Implement** — in `drive_once`, when the next gate for a medium is `revising`, delete the `media_assets` row for (post, `_GATE_TO_ASSET[medium]`) and re-trigger generation; the engine's `revise_gate` already resets the row.
- [ ] **Step 4: Run it, verify it passes.**
- [ ] **Step 5: Commit** — `feat(jobs): media gate revise -> regenerate`.

---

## Self-review notes

- **Spec coverage:** build items (a) media_to_generate@approval → Task 2; (b) gate-sequence on approval → Task 3; (c) media-gen driver not publish → Tasks 4+5; (d) media/publish split → Tasks 4+6; (e) driver → Task 5; (f) final→publish → Tasks 5+6. D1→Task 7, D2→Task 5, D3→Task 5 (Job), D4 deferred.
- **Back-compat:** pre-change posts have no gate rows → `_post_has_pending_gates` False → publish as before. Text-only posts (`["final"]`) auto-advance to `published` (Task 5 case c).
- **Type consistency:** `media_gate_sequence(list[str]) -> list[str]`; `advance_workflow(...) -> WorkflowAdvance(next_gate|ready_to_distribute|finished)`; the `short` gate ↔ `video_short` asset type mapping lives in `_GATE_TO_ASSET`.
- **Open implementer reads:** `publish_post_from_task` (publish_service.py:486+) for the hook + niche slug; `generate_podcast_episode`/`generate_video_episode` signatures; the `niches` column type; `plugins/registry.py` `_SAMPLES` shape.
