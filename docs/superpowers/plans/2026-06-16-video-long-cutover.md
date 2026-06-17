# Video-side cutover — drop `video_long` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the `media_assets.type='video_long'` routing key into `video` across the writer, distributor, reconciliation, and data — with a de-dup migration and a unique guard — so there is one video producer, one row per post, and zero double-upload risk to YouTube.

**Architecture:** Two PRs. **PR1** is behaviorally inert (extract YouTube payload helpers; add a `DISTINCT ON` de-dup to the dispatch query) and mergeable anytime. **PR2** is the atomic cutover: writer rename, distributor identity-join, recurrence-safe producers, a de-dup+relabel+unique-index migration, reconciliation rewritten to re-dispatch Stage-2 instead of producing video directly, deletion of the backfill jobs, and vocabulary cleanup.

**Tech Stack:** Python 3.12 async, asyncpg, Postgres, pytest (`unit` + `integration_db` tiers), LangGraph atoms, the `app_settings`/`SiteConfig` DI seam.

**Spec:** [`docs/superpowers/specs/2026-06-15-video-long-cutover-design.md`](../specs/2026-06-15-video-long-cutover-design.md)
**Issue:** [Glad-Labs/glad-labs-stack#1460](https://github.com/Glad-Labs/glad-labs-stack/issues/1460) · **Closes:** #573, #668, #569

---

## Conventions for every task

- **Run tests from `src/cofounder_agent`** (per the repo pytest harness). Worktree invocation: `poetry run pytest <path> -q`.
- **Commit messages:** no backticks in `-m` (bash substitutes them — use `git commit -F -` with a heredoc for multi-line). End every commit body with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **Branch:** PR1 on the current branch `claude/angry-lamarr-fd059a`. PR2 on a fresh branch off `origin/main` **after PR1 merges** (it depends on PR1's `youtube_payload` module).
- **Never run prod dispatch / flip flags.** Verification is in-process only.
- After touching any file under `modules/content/`, expect the test-backend module-purity + silent-except + `services.md` regen guards — run the relevant unit dir before pushing.

---

## Prod-verified facts the plan relies on (do not re-derive)

- `pipeline_distributions` FKs `pipeline_tasks(task_id)` (CASCADE) — **nothing FKs `media_assets.id`**, so deleting asset rows is FK-safe.
- `media_assets.type` has **no CHECK constraint**; columns include `source`, `created_at`, `updated_at`, `platform_video_ids` (jsonb).
- Dup shape: one post has 18 video assets (8 `video_long` + 1 `video` + 9 `video_short`); 8 posts have 2 `video` rows; the 2 clean `video_long` posts carry the YouTube id on the **pipeline** (`video_long`) row.
- Every existing video approval is already `dispatched_at IS NOT NULL` and mostly grandfathered → existing rows are upload-safe; risk is going-forward only.
- `video_long` appears in **0** `media_to_generate` arrays.
- Migration interface: `async def up(pool)` / `async def down(pool)`; light imports only.

---

# PR1 — Safe prep (behaviorally inert)

## Task 1: Extract YouTube payload helpers to a shared module

**Files:**

- Create: `src/cofounder_agent/services/jobs/youtube_payload.py`
- Modify: `src/cofounder_agent/services/jobs/backfill_videos.py` (import from new home; drop local defs)
- Modify: `src/cofounder_agent/services/jobs/media_distribute.py:168-173` (import from new home)
- Create: `src/cofounder_agent/tests/unit/services/jobs/test_youtube_payload.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/jobs/test_youtube_payload.py`:

```python
"""Unit tests for the shared YouTube-payload helpers (extracted from
backfill_videos in the #1460 PR1 prep so media_distribute stops importing
from a job that PR2 deletes)."""
from __future__ import annotations

from services.jobs.youtube_payload import (
    _build_youtube_description,
    _parse_seo_keywords,
    _strip_markup,
)


def test_strip_markup_removes_tags_and_collapses_ws():
    assert _strip_markup("<p>hi   <b>there</b></p>") == "hi there"
    assert _strip_markup("") == ""


def test_parse_seo_keywords_caps_and_trims():
    assert _parse_seo_keywords("a, b ,, c") == ["a", "b", "c"]
    assert _parse_seo_keywords("") == []
    # >30 keywords are capped at 30.
    many = ",".join(f"k{i}" for i in range(40))
    assert len(_parse_seo_keywords(many)) == 30


def test_build_youtube_description_composes_and_strips_angle_brackets():
    out = _build_youtube_description(
        seo_description="A <b>great</b> post",
        body="Body with x > 0 and a <a href='#'>link</a>.",
        site_config=None,            # no site_url → back-link omitted, never raises
        slug="my-post",
    )
    assert "<" not in out and ">" not in out
    assert out.startswith("A great post")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/services/jobs/test_youtube_payload.py -q`
Expected: FAIL — `ModuleNotFoundError: services.jobs.youtube_payload`.

- [ ] **Step 3: Create the new module**

Create `services/jobs/youtube_payload.py` by moving — verbatim — `_strip_markup`, `_parse_seo_keywords`, `_build_youtube_description`, and the constants `_YOUTUBE_DESCRIPTION_BUDGET`, `_YOUTUBE_MAX_TAGS`, `_YOUTUBE_TAGS_JOINED_LIMIT` from `backfill_videos.py` (current lines 49-51, 79-181). Module header:

```python
"""Shared YouTube upload-payload builders (description + tags).

Extracted from services/jobs/backfill_videos.py (glad-labs-stack#1460 PR1) so
the surviving distributor (media_distribute) no longer imports them from a job
that PR2 deletes. Pure string helpers — no DB, no heavy deps.

YouTube Data API v3 hard caps (NOT operator-tunable): description ≤ 5000 chars
(we compose to ≤ 4800 for headroom); tags ≤ 30 and ≤ 500 joined chars.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_YOUTUBE_DESCRIPTION_BUDGET = 4800
_YOUTUBE_MAX_TAGS = 30
_YOUTUBE_TAGS_JOINED_LIMIT = 500

# ... (paste _strip_markup, _parse_seo_keywords, _build_youtube_description verbatim) ...

__all__ = ["_build_youtube_description", "_parse_seo_keywords", "_strip_markup"]
```

- [ ] **Step 4: Re-point `backfill_videos.py`**

Delete the moved defs/constants from `backfill_videos.py`. Add near its other imports (after line 33):

```python
from services.jobs.youtube_payload import (
    _build_youtube_description,
    _parse_seo_keywords,
)
```

(`_strip_markup` was only used by the two moved helpers, so it no longer needs importing here.)

- [ ] **Step 5: Re-point `media_distribute.py`**

In `_dispatch_asset` (currently lines 168-173) replace the `from services.jobs.backfill_videos import (...)` block with:

```python
    from services.jobs.youtube_payload import (
        _build_youtube_description,
        _parse_seo_keywords,
    )
```

Update the adjacent comment that says "(Shared home: services/jobs/backfill_videos.)" → "(Shared home: services/jobs/youtube_payload.)".

- [ ] **Step 6: Run tests**

Run:

```
poetry run pytest tests/unit/services/jobs/test_youtube_payload.py tests/unit/services/jobs/test_media_distribute.py -q
```

Expected: PASS. If a `tests/unit/services/jobs/test_backfill_videos.py` exists and re-tested these helpers via the old import path, point its imports at `services.jobs.youtube_payload` too (it is deleted wholesale in PR2-T7, so minimal edits only).

- [ ] **Step 7: Commit**

```bash
git add services/jobs/youtube_payload.py services/jobs/backfill_videos.py \
        services/jobs/media_distribute.py tests/unit/services/jobs/test_youtube_payload.py
git commit -F - <<'EOF'
refactor(media): extract YouTube payload helpers to youtube_payload (#1460)

PR1 prep for the video_long cutover: move _build_youtube_description /
_parse_seo_keywords / _strip_markup out of backfill_videos (deleted in PR2)
into a shared services/jobs/youtube_payload module. media_distribute imports
from the new home. Behaviorally inert.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 2: Add `DISTINCT ON` de-dup to the dispatch query

**Files:**

- Modify: `src/cofounder_agent/services/jobs/media_distribute.py:111-136` (`_APPROVED_UNDISPATCHED_SQL`)
- Create: `src/cofounder_agent/tests/integration_db/test_media_distribute_dedup.py`

**Why:** Post-cutover the dispatch join can match >1 `video` asset per post. `DISTINCT ON (post_id, medium)` with the canonical priority (`platform_video_ids` present → `source='pipeline'` → newest) collapses them to one, so a post is dispatched once. Inert today (all approvals dispatched), correct under both the current CASE join and PR2's identity join.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration_db/test_media_distribute_dedup.py`:

```python
"""#1460 PR1: the approved-undispatched dispatch query must return ONE row per
(post, medium) even when a post has multiple matching video assets — else the
post double-uploads to YouTube post-cutover. Real-DB test: the de-dup is SQL."""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_approved_undispatched_dedups_to_one_per_post(test_txn) -> None:
    from services.jobs.media_distribute import _APPROVED_UNDISPATCHED_SQL

    post_id = await test_txn.fetchval(
        """
        INSERT INTO posts (id, title, slug, content, excerpt, seo_keywords, status, published_at)
        VALUES (gen_random_uuid(), 'Dedup Post', 'dedup-post-1460',
                'body', 'excerpt', 'k1,k2', 'published', NOW())
        RETURNING id
        """,
    )
    # One approved, undispatched, non-grandfather approval for the long medium.
    await test_txn.execute(
        """
        INSERT INTO media_approvals (post_id, medium, status, decided_by, dispatched_at)
        VALUES ($1, 'video', 'approved', 'operator', NULL)
        """,
        post_id,
    )
    # Two competing long-form assets (pre-cutover types): the pipeline one with a
    # YouTube id should win; both have a non-empty storage_path so both are eligible.
    await test_txn.execute(
        """
        INSERT INTO media_assets (post_id, type, source, storage_provider, storage_path, platform_video_ids)
        VALUES
          ($1, 'video',      'reconciliation', 'local', '/tmp/recon.mp4', NULL),
          ($1, 'video_long', 'pipeline',       'local', '/tmp/pipe.mp4',  '{"youtube":"abc123"}'::jsonb)
        """,
        post_id,
    )

    rows = await test_txn.fetch(_APPROVED_UNDISPATCHED_SQL, ["video", "video_short"], 50)
    mine = [r for r in rows if r["post_id"] == str(post_id)]
    assert len(mine) == 1, f"expected 1 row for the post, got {len(mine)}"
    assert mine[0]["storage_path"] == "/tmp/pipe.mp4"  # YouTube-id pipeline row wins
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/integration_db/test_media_distribute_dedup.py -m integration_db -q`
Expected: FAIL — two rows returned (current query has no `DISTINCT ON`). (If no DB is reachable the tier skips; run against the local Docker Postgres.)

- [ ] **Step 3: Rewrite `_APPROVED_UNDISPATCHED_SQL`**

Replace the constant (lines 111-136) with:

```python
_APPROVED_UNDISPATCHED_SQL = """
    SELECT * FROM (
        SELECT DISTINCT ON (ma.post_id, ma.medium)
               ma.post_id::text AS post_id,
               ma.medium,
               ma.created_at AS _appr_created,
               p.title, p.content, p.excerpt, p.seo_keywords, p.slug,
               mas.id::text AS asset_id,
               mas.task_id,
               mas.storage_path
          FROM media_approvals ma
          JOIN posts p ON p.id = ma.post_id
          JOIN media_assets mas
            ON mas.post_id = ma.post_id
           AND mas.type = CASE ma.medium
                              WHEN 'video' THEN 'video_long'
                              WHEN 'video_short' THEN 'video_short'
                          END
         WHERE ma.status = 'approved'
           AND ma.dispatched_at IS NULL
           AND COALESCE(ma.decided_by, '') NOT LIKE '%grandfather%'
           AND ma.medium = ANY($1::text[])
           AND COALESCE(mas.storage_path, '') <> ''
         ORDER BY ma.post_id, ma.medium,
                  (mas.platform_video_ids IS NOT NULL
                   AND mas.platform_video_ids::text NOT IN ('', 'null', '{}')) DESC,
                  (mas.source = 'pipeline') DESC,
                  mas.created_at DESC
    ) t
    ORDER BY t._appr_created ASC
    LIMIT $2
"""
```

(The consumer reads `post_id`/`medium`/`asset_id`/`task_id`/`storage_path`/`title`/`content`/`excerpt`/`seo_keywords`/`slug` — all still present; `_appr_created` is internal.)

- [ ] **Step 4: Run the integration test + the unit suite**

Run:

```
poetry run pytest tests/integration_db/test_media_distribute_dedup.py -m integration_db -q
poetry run pytest tests/unit/services/jobs/test_media_distribute.py -q
```

Expected: both PASS (the unit tests mock `fetch`, so the SQL change doesn't affect them).

- [ ] **Step 5: Commit**

```bash
git add services/jobs/media_distribute.py tests/integration_db/test_media_distribute_dedup.py
git commit -F - <<'EOF'
fix(media): dedup approved-undispatched dispatch query to one row per post (#1460)

PR1 prep: DISTINCT ON (post_id, medium) with canonical priority (platform id >
pipeline source > newest) so a post never double-dispatches when multiple video
assets match. Inert today (all approvals dispatched); defuses the post-cutover
double-upload path before PR2 flips the join to identity.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

- [ ] **Step 6: Open PR1**

```bash
git push -u origin claude/angry-lamarr-fd059a
gh pr create --repo Glad-Labs/glad-labs-stack --base main \
  --title "Video cutover PR1 (inert): youtube_payload extract + dispatch dedup (#1460)" \
  --body "PR1 of the #1460 video_long cutover. Behaviorally inert: extracts the YouTube payload helpers to services/jobs/youtube_payload, and adds DISTINCT ON to media_distribute's dispatch query. Safe to merge ahead of the atomic PR2. Epic: poindexter#689."
```

**STOP — let PR1 go green and merge before starting PR2.** PR2 imports `youtube_payload`.

---

# PR2 — Atomic cutover

> Start on a fresh branch off `origin/main` after PR1 merges:
> `git fetch origin && git switch -c claude/video-long-cutover-pr2 origin/main`

## Task 3: Writer — `media.persist` writes `video` not `video_long`

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/media_persist.py:69-72` (`_TARGETS`)
- Modify: `src/cofounder_agent/tests/unit/services/atoms/test_media_persist.py:75`

- [ ] **Step 1: Update the failing test first**

In `test_media_persist.py`, change the assertion on line 75 from:

```python
    assert kinds == {"video_long", "video_short"}
```

to:

```python
    assert kinds == {"video", "video_short"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/unit/services/atoms/test_media_persist.py::test_persist_moves_renders_and_records_both_assets -q`
Expected: FAIL — atom still records `video_long`.

- [ ] **Step 3: Change `_TARGETS`**

In `media_persist.py` replace the long-form tuple (line 70):

```python
_TARGETS: tuple[tuple[str, str, str, str, str], ...] = (
    ("long_video_path", "video_shot_list", "video", "", "16:9"),
    ("short_video_path", "short_shot_list", "video_short", "_short", "9:16"),
)
```

Update the comment above (line 66-68) to drop the "video_long" wording: the long video records `media_assets.type='video'` at `{task_id}.mp4`.

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/unit/services/atoms/test_media_persist.py -q`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add modules/content/atoms/media_persist.py tests/unit/services/atoms/test_media_persist.py
git commit -F - <<'EOF'
feat(media): media.persist writes type=video, not video_long (#1460)

First step of the atomic cutover: the pipeline now records the long render as
media_assets.type='video' (filename unchanged, {task_id}.mp4).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 4: Distributor — identity type→medium map + identity join + link-time dup-skip

**Files:**

- Modify: `src/cofounder_agent/services/jobs/media_distribute.py` (`_TYPE_TO_MEDIUM` ~73-76, join in `_APPROVED_UNDISPATCHED_SQL`, link loop ~329-362, new `_EXISTING_VIDEO_SQL`)
- Modify: `src/cofounder_agent/tests/unit/services/jobs/test_media_distribute.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_media_distribute.py`:

```python
@pytest.mark.asyncio
async def test_type_to_medium_is_identity():
    assert md._TYPE_TO_MEDIUM == {"video": "video", "video_short": "video_short"}


@pytest.mark.asyncio
async def test_link_skips_when_post_already_has_video_asset(monkeypatch):
    """A second task-keyed render for a post that already has a video asset must
    NOT be linked (would violate the unique guard / double the row) — skip + finding."""
    job = MediaDistributeJob()
    pool = _FakePool(
        [{"id": "a-dup", "task_id": "abc", "type": "video"}],
        post_id="post-1",
    )
    # Simulate "post already has a video asset": _EXISTING_VIDEO_SQL → truthy.
    pool.fetchval = AsyncMock(side_effect=["post-1", 1])  # resolve post, then exists=1
    findings = []
    monkeypatch.setattr(md, "emit_finding", lambda **kw: findings.append(kw))
    pending = AsyncMock(return_value="pending")
    with patch.object(md, "record_pending", pending):
        out = await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})
    pending.assert_not_called()             # never seeded a second approval
    assert out.changes_made == 0
    assert findings and findings[0]["kind"] == "duplicate_video_asset"
```

Update the existing `test_links_assets_and_seeds_gate2_approvals` so its assets use the post-cutover type `video` (not `video_long`) and `fetchval` returns the post id then a falsy existence check. Change the unlinked rows to `type: "video"` / `type: "video_short"`, and set:

```python
    pool.fetchval = AsyncMock(side_effect=["post-1", None, "post-1", None])
```

(resolve post, exists-check falsy, per asset).

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/unit/services/jobs/test_media_distribute.py -q`
Expected: FAIL — `_TYPE_TO_MEDIUM` still maps `video_long`; no `_EXISTING_VIDEO_SQL`; link loop doesn't skip.

- [ ] **Step 3: Identity map + join**

`_TYPE_TO_MEDIUM` (lines 73-76):

```python
_TYPE_TO_MEDIUM: dict[str, str] = {
    "video": "video",
    "video_short": "video_short",
}
```

In `_APPROVED_UNDISPATCHED_SQL` (the version from PR1-T2), replace the CASE join with identity:

```python
          JOIN media_assets mas
            ON mas.post_id = ma.post_id
           AND mas.type = ma.medium
```

- [ ] **Step 4: Link-time dup-skip**

Add the existence query near the other SQL constants:

```python
# A post must hold at most one video-family asset per type (enforced by
# uniq_media_assets_post_video_type). Before back-stamping a freshly-rendered
# task-keyed asset onto a post, make sure the post doesn't already have one —
# else the link UPDATE would violate the unique index and loop forever.
_EXISTING_VIDEO_SQL = """
    SELECT 1 FROM media_assets
     WHERE post_id = $1::uuid AND type = $2
     LIMIT 1
"""
```

Add `from utils.findings import emit_finding` to the imports. In the link loop (after `post_id` is resolved, before `_LINK_SQL`):

```python
            try:
                already = await pool.fetchval(_EXISTING_VIDEO_SQL, post_id, row["type"])
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[MEDIA_DISTRIBUTE] existing-asset check failed for post %s: %s",
                    post_id, exc,
                )
                already = None
            if already:
                # Redundant render: the post already has this video type. Leave
                # the task-keyed asset unlinked (it never reaches dispatch) and
                # surface it so the operator can prune the orphan render.
                emit_finding(
                    source="media_distribute",
                    kind="duplicate_video_asset",
                    severity="warning",
                    title=f"Redundant {row['type']} render for an already-covered post",
                    body=(
                        f"Post {post_id} already has a {row['type']} media_asset; the "
                        f"task-keyed render {asset_id} (task {task_id}) was left unlinked "
                        f"to honor the one-video-per-post guard. Prune the orphan render."
                    ),
                    dedup_key=f"duplicate_video_asset:{asset_id}",
                    extra={"post_id": str(post_id), "asset_id": str(asset_id), "type": row["type"]},
                )
                continue
```

Update the module docstring lines 12-15 (the `video_long`/`video_short` vocabulary note) to say the asset _types_ are now `video`/`video_short`, identical to the approvals medium except the long form's medium is still `video`.

- [ ] **Step 5: Run tests**

Run: `poetry run pytest tests/unit/services/jobs/test_media_distribute.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/jobs/media_distribute.py tests/unit/services/jobs/test_media_distribute.py
git commit -F - <<'EOF'
feat(media): media_distribute identity type-map + join + link-time dup guard (#1460)

_TYPE_TO_MEDIUM is now identity {video, video_short}; the approved-undispatched
join is mas.type = ma.medium. Link step skips (and emits a duplicate_video_asset
finding for) a second render when the post already holds that video type, so the
unique guard never loops.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 5: Recurrence-safe producer — `record_media_asset` upsert for video family

**Files:**

- Modify: `src/cofounder_agent/services/media_asset_recorder.py:136-176`
- Modify: `src/cofounder_agent/tests/unit/services/test_media_asset_recorder.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/services/test_media_asset_recorder.py` (mirror its existing fake-pool style; if the file builds a pool helper, reuse it — otherwise use this self-contained double):

```python
@pytest.mark.asyncio
async def test_video_family_insert_uses_on_conflict(monkeypatch):
    """Video-family rows with a post_id go through the ON CONFLICT upsert path so
    a re-link/re-stamp is idempotent under the (post_id,type) unique guard."""
    from unittest.mock import AsyncMock, MagicMock
    from services.media_asset_recorder import record_media_asset

    captured = {}
    class _Conn:
        async def fetchval(self, sql, *args):
            captured["sql"] = sql
            return "id-1"
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class _Pool:
        def acquire(self):
            return _Conn()
    await record_media_asset(
        pool=_Pool(), post_id="00000000-0000-0000-0000-000000000001",
        asset_type="video", storage_path="/tmp/x.mp4",
    )
    assert "ON CONFLICT" in captured["sql"]


@pytest.mark.asyncio
async def test_image_insert_has_no_on_conflict():
    from unittest.mock import AsyncMock, MagicMock
    from services.media_asset_recorder import record_media_asset
    captured = {}
    class _Conn:
        async def fetchval(self, sql, *args):
            captured["sql"] = sql
            return "id-2"
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class _Pool:
        def acquire(self):
            return _Conn()
    await record_media_asset(
        pool=_Pool(), post_id="00000000-0000-0000-0000-000000000001",
        asset_type="inline_image", storage_path="/tmp/x.png",
    )
    assert "ON CONFLICT" not in captured["sql"]
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/unit/services/test_media_asset_recorder.py -q`
Expected: FAIL — current INSERT never contains `ON CONFLICT`.

- [ ] **Step 3: Branch the INSERT**

In `record_media_asset`, replace the single `conn.fetchval(INSERT ... RETURNING id, ...)` (lines 137-169) with a video-family branch. The base column list/values are identical; only video-family + non-null `post_id` appends an upsert clause:

```python
        _COLS = """
                type, source, storage_provider, url, storage_path,
                metadata, post_id, task_id, provider_plugin,
                width, height, duration_ms, file_size_bytes,
                mime_type, cost_usd, electricity_kwh
        """
        _VALS = "$1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16"
        # Video-family rows are guarded by uniq_media_assets_post_video_type
        # (#1460). Upsert so a re-link/re-stamp refreshes the existing row instead
        # of raising. Images/podcast keep the plain INSERT (no such guard; multiple
        # inline_image rows per post are legitimate).
        if post_id is not None and asset_type in ("video", "video_short"):
            conflict = (
                " ON CONFLICT (post_id, type) "
                "WHERE post_id IS NOT NULL AND type IN ('video','video_short') "
                "DO UPDATE SET url = EXCLUDED.url, storage_path = EXCLUDED.storage_path, "
                "file_size_bytes = EXCLUDED.file_size_bytes, width = EXCLUDED.width, "
                "height = EXCLUDED.height, duration_ms = EXCLUDED.duration_ms, "
                "updated_at = NOW()"
            )
        else:
            conflict = ""
        async with pool.acquire() as conn:
            row_id = await conn.fetchval(
                f"INSERT INTO media_assets ({_COLS}) VALUES ({_VALS}){conflict} RETURNING id",
                asset_type, source, storage_provider, public_url or "", storage_path or "",
                _json_dumps(metadata), post_id, task_id, provider_plugin or "",
                width, height, duration_ms, file_size_bytes, mime_type, cost_usd, electricity_kwh,
            )
            return str(row_id) if row_id else None
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/unit/services/test_media_asset_recorder.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/media_asset_recorder.py tests/unit/services/test_media_asset_recorder.py
git commit -F - <<'EOF'
feat(media): record_media_asset upserts video-family rows under the unique guard (#1460)

Video/video_short rows with a post_id use ON CONFLICT (post_id,type) DO UPDATE so
re-stamping is idempotent; images/podcast keep the plain INSERT.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 6: Migration 1 — add `media_pipeline_redispatch_count` column

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_add_media_pipeline_redispatch_count.py` (generate via the script)

- [ ] **Step 1: Generate the migration file**

Run from `src/cofounder_agent`:

```
poetry run python scripts/new-migration.py "add media_pipeline_redispatch_count to pipeline_tasks"
```

This stamps a `YYYYMMDD_HHMMSS_add_media_pipeline_redispatch_count.py` skeleton.

- [ ] **Step 2: Fill the body**

```python
"""Add pipeline_tasks.media_pipeline_redispatch_count (#1460).

The media_reconciliation watchdog re-dispatches Stage-2 video (clears
media_pipeline_dispatched_at) instead of generating video directly. This column
caps re-dispatch attempts per task so a permanently-failing render can't loop.

Idempotent + light-env safe.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UP = """
ALTER TABLE pipeline_tasks
    ADD COLUMN IF NOT EXISTS media_pipeline_redispatch_count integer NOT NULL DEFAULT 0
"""

_DOWN = "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS media_pipeline_redispatch_count"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_UP)
    logger.info("Migration add_media_pipeline_redispatch_count: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DOWN)
```

- [ ] **Step 3: Lint + smoke the migration**

Run from `src/cofounder_agent`:

```
poetry run python scripts/ci/migrations_lint.py
poetry run python scripts/ci/migrations_smoke.py
```

Expected: both PASS (light imports; idempotent DDL).

- [ ] **Step 4: Commit**

```bash
git add services/migrations/*_add_media_pipeline_redispatch_count.py
git commit -F - <<'EOF'
feat(db): add pipeline_tasks.media_pipeline_redispatch_count (#1460)

Caps reconciliation video re-dispatch attempts per task.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 7: Migration 2 — de-dup + relabel + unique index (with backup)

**Files:**

- Create: `src/cofounder_agent/services/migrations/<ts>_dedup_and_collapse_video_long.py`
- Create: `src/cofounder_agent/tests/integration_db/test_dedup_collapse_video_long.py`

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration_db/test_dedup_collapse_video_long.py`:

```python
"""#1460 de-dup migration: per post, one video-family survivor by smart priority
(YouTube id > pipeline > newest), video_long relabeled to video, losers archived,
and a partial unique guard that blocks future dup rows. Real-DB (the logic is SQL)."""
from __future__ import annotations

import importlib
import pytest

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio(loop_scope="session")]


def _mig():
    # Resolve the timestamped module by suffix so the test isn't pinned to HHMMSS.
    import pkgutil, services.migrations as m
    name = next(
        n for _, n, _ in pkgutil.iter_modules(m.__path__)
        if n.endswith("_dedup_and_collapse_video_long")
    )
    return importlib.import_module(f"services.migrations.{name}")


async def test_dedup_keeps_youtube_then_pipeline_then_newest(test_txn):
    mig = _mig()
    post_id = await test_txn.fetchval(
        "INSERT INTO posts (id, title, slug, content, status, published_at) "
        "VALUES (gen_random_uuid(),'P','dedup-mig-1460','b','published',NOW()) RETURNING id"
    )
    await test_txn.execute(
        """
        INSERT INTO media_assets (post_id, type, source, storage_provider, storage_path, platform_video_ids, created_at)
        VALUES
          ($1,'video_long','pipeline','local','/p/yt.mp4', '{"youtube":"yt"}'::jsonb, NOW() - interval '2 day'),
          ($1,'video',     'reconciliation','local','/p/recon.mp4', NULL, NOW()),
          ($1,'video_short','pipeline','local','/p/s1.mp4', NULL, NOW() - interval '1 day'),
          ($1,'video_short','pipeline','local','/p/s2.mp4', NULL, NOW())
        """,
        post_id,
    )
    # Run the migration's de-dup statements on THIS connection (rolled back).
    await test_txn.execute(mig._DEDUP_BACKUP_DDL)
    await test_txn.execute(mig._DEDUP_LOSERS_TEMP)
    await test_txn.execute(mig._DEDUP_ARCHIVE)
    await test_txn.execute(mig._DEDUP_DELETE)
    await test_txn.execute(mig._RELABEL)

    rows = await test_txn.fetch(
        "SELECT type, storage_path FROM media_assets WHERE post_id=$1 ORDER BY type", post_id
    )
    kinds = sorted(r["type"] for r in rows)
    assert kinds == ["video", "video_short"]                 # one of each family
    long_row = next(r for r in rows if r["type"] == "video")
    assert long_row["storage_path"] == "/p/yt.mp4"           # YouTube-id row won + relabeled
    short_row = next(r for r in rows if r["type"] == "video_short")
    assert short_row["storage_path"] == "/p/s2.mp4"          # newest short won
    # Losers archived.
    archived = await test_txn.fetchval(
        "SELECT count(*) FROM media_assets_dedup_backup WHERE post_id=$1", post_id
    )
    assert archived == 2


async def test_unique_guard_blocks_second_video_row(test_txn):
    import asyncpg
    post_id = await test_txn.fetchval(
        "INSERT INTO posts (id, title, slug, content, status, published_at) "
        "VALUES (gen_random_uuid(),'P','uniq-mig-1460','b','published',NOW()) RETURNING id"
    )
    await test_txn.execute(
        "INSERT INTO media_assets (post_id, type, source, storage_provider) "
        "VALUES ($1,'video','pipeline','local')", post_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await test_txn.execute(
            "INSERT INTO media_assets (post_id, type, source, storage_provider) "
            "VALUES ($1,'video','reconciliation','local')", post_id,
        )
```

(The second test relies on the unique index already existing — `schema_loaded` runs this migration against the disposable DB at session start.)

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/integration_db/test_dedup_collapse_video_long.py -m integration_db -q`
Expected: FAIL — migration module doesn't exist yet.

- [ ] **Step 3: Generate + fill the migration**

```
poetry run python scripts/new-migration.py "dedup and collapse video_long"
```

Fill `<ts>_dedup_and_collapse_video_long.py`:

```python
"""De-dup video-family media_assets + collapse video_long -> video (#1460).

The video-side cutover. media_assets has no (post_id,type) uniqueness, so two
producers (pipeline 'video_long' + reconciliation 'video') and earlier dup
reconciliation stamps left several posts with multiple video-family rows.
Collapsing the names without de-dup would let media_distribute double-upload to
YouTube. This migration, in one transaction:

  1. Backs up every row it will delete into media_assets_dedup_backup (recovery).
  2. Per post, keeps ONE survivor per family — long={video,video_long},
     short={video_short} — by smart priority: has a platform_video_id >
     source='pipeline' > newest. Deletes the losers.
  3. Relabels surviving 'video_long' -> 'video'.
  4. Creates the partial unique guard uniq_media_assets_post_video_type so dup
     rows can never recur (the root cause).

FK-safe: nothing references media_assets.id. Idempotent + light-env safe; on a
fresh baseline DB there are no video rows so it is a no-op (the index is still
created).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEDUP_BACKUP_DDL = "CREATE TABLE IF NOT EXISTS media_assets_dedup_backup (LIKE media_assets)"

_DEDUP_LOSERS_TEMP = """
CREATE TEMP TABLE _video_dedup_losers ON COMMIT DROP AS
SELECT id FROM (
    SELECT id,
           row_number() OVER (
               PARTITION BY post_id,
                            CASE WHEN type IN ('video','video_long') THEN 'long' ELSE 'short' END
               ORDER BY
                   (platform_video_ids IS NOT NULL
                    AND platform_video_ids::text NOT IN ('', 'null', '{}')) DESC,
                   (source = 'pipeline') DESC,
                   created_at DESC NULLS LAST,
                   id DESC
           ) AS rn
      FROM media_assets
     WHERE post_id IS NOT NULL
       AND type IN ('video', 'video_long', 'video_short')
) ranked
WHERE rn > 1
"""

_DEDUP_ARCHIVE = """
INSERT INTO media_assets_dedup_backup
SELECT * FROM media_assets WHERE id IN (SELECT id FROM _video_dedup_losers)
"""

_DEDUP_DELETE = "DELETE FROM media_assets WHERE id IN (SELECT id FROM _video_dedup_losers)"

_RELABEL = "UPDATE media_assets SET type='video', updated_at=NOW() WHERE type='video_long'"

_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS uniq_media_assets_post_video_type
    ON media_assets (post_id, type)
 WHERE post_id IS NOT NULL AND type IN ('video', 'video_short')
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_DEDUP_BACKUP_DDL)
            await conn.execute(_DEDUP_LOSERS_TEMP)
            archived = await conn.execute(_DEDUP_ARCHIVE)
            deleted = await conn.execute(_DEDUP_DELETE)
            relabeled = await conn.execute(_RELABEL)
            await conn.execute(_UNIQUE_INDEX)
    logger.info(
        "Migration dedup_and_collapse_video_long: archived=%s deleted=%s relabeled=%s",
        archived, deleted, relabeled,
    )


async def down(pool) -> None:
    """Drop only the unique guard. The data de-dup is intentionally NOT auto-
    reversed — recover deleted rows from media_assets_dedup_backup if needed."""
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS uniq_media_assets_post_video_type")
```

- [ ] **Step 4: Run integration tests + lint + smoke**

```
poetry run pytest tests/integration_db/test_dedup_collapse_video_long.py -m integration_db -q
poetry run python scripts/ci/migrations_lint.py
poetry run python scripts/ci/migrations_smoke.py
```

Expected: PASS. (Smoke runs the migration on a fresh baseline DB → no-op de-dup, index created.)

- [ ] **Step 5: Commit**

```bash
git add services/migrations/*_dedup_and_collapse_video_long.py \
        tests/integration_db/test_dedup_collapse_video_long.py
git commit -F - <<'EOF'
feat(db): dedup video assets, collapse video_long->video, add unique guard (#1460)

Per post keep one survivor per video family (YouTube id > pipeline > newest),
archive losers to media_assets_dedup_backup, relabel video_long->video, and add
the partial unique index uniq_media_assets_post_video_type. FK-safe; no-op on a
fresh DB.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 8: Reconciliation — re-dispatch Stage-2 instead of producing video directly

**Files:**

- Modify: `src/cofounder_agent/services/jobs/media_reconciliation.py`
- Modify: `src/cofounder_agent/tests/unit/services/jobs/test_media_reconciliation_job.py`

**Behavior change:** Podcast passes are untouched. For video: stop the Pass-1 `video` row-stamp and delete `_regen_video`; detect video drift from DB asset presence (not the legacy R2 `{post_id}.mp4` HEAD); on drift, clear the source task's `media_pipeline_dispatched_at` (capped by `media_pipeline_redispatch_count`) so `dispatch_media_pipeline` re-runs Stage-2. No-`task_id` posts can't be re-dispatched and only surface in the `media_drift` finding.

- [ ] **Step 1: Write the failing tests**

These test `_redispatch_video` in isolation (fully concrete, no dependency on the
job's larger run() fixtures) plus the removal of the direct-regen path. Add:

```python
from unittest.mock import AsyncMock
import pytest
from services.site_config import SiteConfig


class _RedispatchPool:
    """Minimal pool double for _redispatch_video: fetchrow resolves the task +
    its redispatch count; execute returns a command tag."""
    def __init__(self, task_row, exec_tag="UPDATE 1"):
        self._task_row = task_row
        self.execute = AsyncMock(return_value=exec_tag)

    async def fetchrow(self, *args):
        return self._task_row


@pytest.mark.asyncio
async def test_redispatch_video_clears_marker_under_cap():
    """Resolvable task below the cap → clear the marker (UPDATE 1) → True."""
    from services.jobs.media_reconciliation import MediaReconciliationJob
    job = MediaReconciliationJob()
    job._site_config = SiteConfig(initial_config={"media_pipeline_redispatch_max": "3"})
    pool = _RedispatchPool({"task_id": "t1", "media_pipeline_redispatch_count": 0})
    ok = await job._redispatch_video(pool, {"id": "post-1"})
    assert ok is True
    pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_redispatch_video_respects_attempt_cap():
    """count >= cap → do NOT clear the marker → False."""
    from services.jobs.media_reconciliation import MediaReconciliationJob
    job = MediaReconciliationJob()
    job._site_config = SiteConfig(initial_config={"media_pipeline_redispatch_max": "2"})
    pool = _RedispatchPool({"task_id": "t1", "media_pipeline_redispatch_count": 2})
    ok = await job._redispatch_video(pool, {"id": "post-1"})
    assert ok is False
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_redispatch_video_no_task_id_is_fail_loud_false():
    """No resolvable pipeline_task_id → cannot re-dispatch → False (surfaced in
    the media_drift finding, not silently healed)."""
    from services.jobs.media_reconciliation import MediaReconciliationJob
    job = MediaReconciliationJob()
    job._site_config = SiteConfig(initial_config={"media_pipeline_redispatch_max": "3"})
    pool = _RedispatchPool(None)  # fetchrow → no row
    ok = await job._redispatch_video(pool, {"id": "post-1"})
    assert ok is False
    pool.execute.assert_not_called()


def test_regen_video_path_removed():
    """The direct video-generation path is gone — the pipeline is the sole video
    producer now."""
    from services.jobs.media_reconciliation import MediaReconciliationJob
    assert not hasattr(MediaReconciliationJob, "_regen_video")
```

Keep the existing podcast tests in the file green (podcast behavior is unchanged).

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/unit/services/jobs/test_media_reconciliation_job.py -q`
Expected: FAIL — `_regen_video` still present; no `_redispatch_video`.

- [ ] **Step 3: Implement the rewrite**

In `media_reconciliation.py`:

a. **Delete** `_regen_video` (lines 795-828) and the Stage-2 demotion helpers/branches that only existed to protect direct video regen (`_has_stage2_video`, `demoted_video`, the `regen_video` list + its loop). Keep `_regen_podcast`, `_record_media_asset` (podcast still stamps), `_seed_approval_gate`.

b. **`_check_post_media`:** keep the podcast HEAD + `podcast_exists`/`podcast_url`. Replace video detection — pass `existing_pairs` in and compute:

```python
        row["video_missing"] = wants_video and (post_id, "video") not in existing_pairs
        # video is produced task-keyed by Stage-2 now; no R2 {post_id}.mp4 stamp.
        row["video_exists"] = False
        row["video_url"] = ""
```

Update its signature to `(self, client, r2_base, cdn_ver, row, existing_pairs)` and the `asyncio.gather` call site to pass `existing_pairs`.

c. **Pass 1 (row-stamp):** keep the podcast branch; **remove** the `video` stamp branch (lines 393-402) and the `stamped_video` counter usages (set `stamped_video = 0` constant or drop from metrics).

d. **Pass 2:** replace the video-regen list/loop with a re-dispatch loop:

```python
        redispatch_max = int((getattr(self, "_site_config", None)
                              and self._site_config.get("media_pipeline_redispatch_max", "3")) or 3)
        redispatched = 0
        for r in missing_video:
            if not _in_regen_window(r):
                continue
            try:
                if await self._redispatch_video(pool, r):
                    redispatched += 1
            except Exception as e:  # noqa: BLE001
                logger.exception("media_reconciliation: video re-dispatch for %s raised: %s", r["id"], e)
```

e. **Add `_redispatch_video`:**

```python
    _RESOLVE_TASK_SQL = """
        SELECT pt.task_id, pt.media_pipeline_redispatch_count
          FROM pipeline_tasks pt
          JOIN posts p ON p.metadata->>'pipeline_task_id' = pt.task_id
         WHERE p.id = $1::uuid
         LIMIT 1
    """
    _CLEAR_MARKER_SQL = """
        UPDATE pipeline_tasks
           SET media_pipeline_dispatched_at = NULL,
               media_pipeline_redispatch_count = media_pipeline_redispatch_count + 1
         WHERE task_id = $1
           AND media_pipeline_redispatch_count < $2
    """

    async def _redispatch_video(self, pool: Any, post_row: dict[str, Any]) -> bool:
        """Re-run Stage-2 for a drifted video post by clearing its dispatch
        marker (capped). dispatch_media_pipeline re-claims it next cycle. Posts
        with no resolvable pipeline_task_id can't be re-dispatched — they only
        surface in the media_drift finding (fail-loud, not silent)."""
        cap = int((getattr(self, "_site_config", None)
                   and self._site_config.get("media_pipeline_redispatch_max", "3")) or 3)
        row = await pool.fetchrow(self._RESOLVE_TASK_SQL, post_row["id"])
        if not row or not row["task_id"]:
            logger.warning(
                "media_reconciliation: no pipeline_task_id for post %s — cannot "
                "re-dispatch video (surfaced in finding)", post_row["id"],
            )
            return False
        if row["media_pipeline_redispatch_count"] >= cap:
            logger.warning(
                "media_reconciliation: post %s hit video re-dispatch cap (%d)",
                post_row["id"], cap,
            )
            return False
        result = await pool.execute(self._CLEAR_MARKER_SQL, row["task_id"], cap)
        return str(result).strip().endswith(" 1")
```

f. Update the `media_drift` finding body/metrics: replace "regen video" wording with "re-dispatched video" and report `redispatched`.

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/unit/services/jobs/test_media_reconciliation_job.py -q`
Expected: PASS (podcast tests still green; new video tests pass).

- [ ] **Step 5: Commit**

```bash
git add services/jobs/media_reconciliation.py tests/unit/services/jobs/test_media_reconciliation_job.py
git commit -F - <<'EOF'
feat(media): reconciliation re-dispatches Stage-2 video instead of regenerating (#1460)

Video drift now clears the source task's media_pipeline_dispatched_at (capped via
media_pipeline_redispatch_count) so the pipeline is the sole video producer; the
direct _regen_video / R2 video row-stamp paths are removed. No-task_id posts
surface in the media_drift finding rather than being silently healed. Podcast
paths unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 9: Delete the backfill jobs + deregister

**Files:**

- Delete: `src/cofounder_agent/services/jobs/backfill_videos.py`, `src/cofounder_agent/services/jobs/backfill_podcasts.py`
- Delete: their test files under `tests/unit/services/jobs/` if present (`test_backfill_videos.py`, `test_backfill_podcasts.py`)
- Modify: `src/cofounder_agent/plugins/registry.py:764-767`

- [ ] **Step 1: Confirm no live importers**

Run: `poetry run python -c "import subprocess"` — no; instead grep:

```
poetry run pytest -q  # not yet
```

Use Grep for `backfill_videos`/`backfill_podcasts` across `src/cofounder_agent` (excluding the files being deleted + this plan). The only non-test importer was `media_distribute` → already re-pointed in PR1-T1. Expected remaining hits: `plugins/registry.py` (the two `_SAMPLES` tuples) and `docs/`.

- [ ] **Step 2: Deregister from `_SAMPLES`**

In `plugins/registry.py` remove the two tuples (lines 766-767) and the now-stale comment block above them (lines 764-765 describing the 2026-05-20 audit). Leave the `dispatch_media_pipeline` / podcast lane registrations intact.

- [ ] **Step 3: Delete the job + test files**

```bash
git rm services/jobs/backfill_videos.py services/jobs/backfill_podcasts.py
git rm tests/unit/services/jobs/test_backfill_videos.py tests/unit/services/jobs/test_backfill_podcasts.py   # if present
```

- [ ] **Step 4: Verify nothing imports them**

Run:

```
poetry run pytest tests/unit/plugins/test_registry.py tests/unit/services/jobs -q
```

Expected: PASS, no import errors. (If a registry sample-count test pins the number of `_SAMPLES`, update its expected count by −2.)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -F - <<'EOF'
chore(media): delete backfill_videos + backfill_podcasts (closes #668) (#1460)

Subsumed by media_distribute (video) and the podcast lane / reconciliation
approval-seed (podcast). Helpers already moved to youtube_payload in PR1.
Deregistered from plugins/registry _SAMPLES.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 10: Vocabulary cleanup — drop type-valued `video_long` (closes #573, #569)

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/posts.py:51-52` (+ `--media` help ~411 + comment ~44-49)
- Modify: `src/cofounder_agent/services/media_asset_recorder.py:179-189` (mime map + docstring conventions line 19-21)
- Modify: `src/cofounder_agent/routes/video_routes.py:75-77, 98, 104-105` (docstring + query)
- Modify: `src/cofounder_agent/services/jobs/media_reconciliation.py` (read-side `('video','video_long')` sets at ~342-343, 460-468 if any survive Task 8)
- Tests: `tests/unit/.../test_*` referencing these

> **Do NOT touch** state-channel names: `video_long_script` (media_load_scripts, media_render_narration, media_transcribe_narration, qa_audio, generate_media_scripts, task_metadata, template_runner:481), `long_video_path`. These are PipelineState channels, not `media_assets.type`.

- [ ] **Step 1: Write/adjust failing tests**

Add to `tests/unit/.../test_posts_cli.py` (or wherever `CANONICAL_MEDIA_NAMES` is asserted; create a small test if none):

```python
def test_canonical_media_names_drops_video_long():
    from poindexter.cli.posts import CANONICAL_MEDIA_NAMES
    assert "video_long" not in CANONICAL_MEDIA_NAMES
    assert CANONICAL_MEDIA_NAMES == ("podcast", "video", "video_short")
```

If `tests/unit/services/test_media_asset_recorder.py` asserts the mime map, add:

```python
def test_mime_map_has_no_video_long():
    from services.media_asset_recorder import _DEFAULT_MIME_TYPES
    assert "video_long" not in _DEFAULT_MIME_TYPES
    assert _DEFAULT_MIME_TYPES["video"] == "video/mp4"
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/unit/services/test_media_asset_recorder.py -q` and the CLI test.
Expected: FAIL.

- [ ] **Step 3: Apply the edits**

- `cli/posts.py`: `CANONICAL_MEDIA_NAMES = ("podcast", "video", "video_short")`; drop `video_long` from the `--media` help text (line ~411); update the comment (44-49) that referenced the deleted `backfill_videos` `video_long` array filter.
- `media_asset_recorder.py`: remove the `"video_long": "video/mp4",` entry from `_DEFAULT_MIME_TYPES`; update the docstring conventions list (line 19-21) to `video`, `video_short`, `podcast`, `featured_image`, `inline_image`.
- `routes/video_routes.py`: docstring 74-77 → say the long-form type is now `video`; query line 98 `AND mas.type IN ('video', 'video_long')` → `AND mas.type = 'video'`; line 104-105 drop the `OR 'video_long' = ANY(media_to_generate)` clause (keep `'video' = ANY(media_to_generate)`).
- `media_reconciliation.py`: in the `existing_pairs` builder (Task 8 may have already touched this), simplify `if t in ("video", "video_long")` → `if t == "video"` and drop `video_long` from any remaining read-side tuple. (Post-relabel there are no `video_long` rows.)

- [ ] **Step 4: Run the touched suites**

```
poetry run pytest tests/unit/services/test_media_asset_recorder.py \
  tests/unit/services/jobs/test_media_reconciliation_job.py \
  tests/unit/routes -q
```

Plus the CLI test. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -F - <<'EOF'
chore(media): drop type-valued video_long strings (closes #573, #569) (#1460)

CLI media vocabulary, mime map, video feed query, and reconciliation read-side
now use 'video' only. State-channel names (video_long_script, long_video_path)
deliberately untouched.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Task 11: Docs + full verification

**Files:**

- Modify: `docs/architecture/podcast-pipeline-stage3.md` (§11 mark shipped)

- [ ] **Step 1: Update the design doc**

In `podcast-pipeline-stage3.md` §11, change the header/status from "Deferred" to shipped, with the PR refs, and flip the `⏸` rows in the §4 tables to `✅`. Note the de-dup migration + unique guard + reconciliation re-dispatch as the mechanism.

- [ ] **Step 2: Full unit + migration gate**

Run from `src/cofounder_agent`:

```
poetry run pytest tests/unit/services/jobs tests/unit/services/atoms \
  tests/unit/services/test_media_asset_recorder.py tests/unit/routes -q
poetry run pytest tests/unit/services/migrations -q
poetry run python scripts/ci/migrations_lint.py
poetry run python scripts/ci/migrations_smoke.py
```

Expected: all PASS. Also run the integration_db additions if a local DB is up:

```
poetry run pytest tests/integration_db/test_dedup_collapse_video_long.py tests/integration_db/test_media_distribute_dedup.py -m integration_db -q
```

- [ ] **Step 3: In-process cutover verification (NO prod dispatch)**

Against a **copy/disposable** DB (never prod), or rely on the integration_db tier which replays migrations on a fresh DB, confirm:

- `SELECT count(*) FROM media_assets WHERE type='video_long'` → `0`.
- No post has >1 row of a video family:
  `SELECT post_id,count(*) FROM media_assets WHERE type IN ('video','video_short') AND post_id IS NOT NULL GROUP BY post_id,type HAVING count(*)>1` → empty.
- `SELECT count(*) FROM media_assets_dedup_backup` → matches the losers archived.
- The unique index exists: `\d media_assets` shows `uniq_media_assets_post_video_type`.

Then exercise the writer in-process (mirrors `test_media_persist`): run `media.persist` with a fake render and assert a `video` row was recorded. **Do not** run `media_distribute`/`dispatch_media_pipeline` against prod.

- [ ] **Step 4: Commit docs**

```bash
git add docs/architecture/podcast-pipeline-stage3.md
git commit -F - <<'EOF'
docs(media): mark §11 video-side cutover shipped (#1460)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

- [ ] **Step 5: Open PR2**

```bash
git push -u origin claude/video-long-cutover-pr2
gh pr create --repo Glad-Labs/glad-labs-stack --base main \
  --title "Video cutover PR2 (atomic): drop video_long (ATTENDED) (#1460)" \
  --body "$(cat <<'BODY'
The all-or-nothing video-side cutover (#1460). Writer writes type=video;
media_distribute uses identity type-map + join + link-time dup guard;
record_media_asset upserts under a new partial unique index; a migration de-dups
existing video assets (backup → smart-priority survivor → relabel → unique
index); reconciliation re-dispatches Stage-2 instead of producing video; the
backfill jobs are deleted (closes #668); type-valued video_long strings dropped
(closes #573, #569).

ATTENDED cutover: after merge, apply migrations, restart workers, watch one
media_distribute cycle (expect zero new uploads — existing rows are
dispatched+grandfathered). Verified in-process; no prod dispatch run.

Epic: poindexter#689. Closes #573, #668, #569.
BODY
)"
```

---

## Self-review notes (author)

- **Spec coverage:** writer (T3), distributor map/join/dedup (T2,T4), data migration de-dup+relabel+index (T7), unique guard producers (T4,T5), reconciliation re-dispatch (T8), backfill deletion (T9), vocabulary (T10), CTA (already shipped — no task), verification/rollback (T11). All §5/§6 items mapped.
- **No-task_id fail-loud (§6.5):** T8 `_redispatch_video` returns False + warns; surfaced via finding.
- **FK safety (§10):** resolved — no incoming FK to `media_assets`; T7 deletes freely.
- **Type consistency:** unique index name `uniq_media_assets_post_video_type` and column `media_pipeline_redispatch_count` are used identically across T4/T5/T6/T7/T8.
- **Open risk:** integration_db tests require a reachable Postgres; they skip (not fail) without one, so CI must run them against the DB-backed runner to actually exercise the SQL de-dup.
