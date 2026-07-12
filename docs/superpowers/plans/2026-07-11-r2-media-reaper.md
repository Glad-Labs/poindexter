# R2 Media Orphan-Reaper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, dry-run-first scheduled job that deletes unreferenced `images/`, `video/`, and legacy `podcast/` objects from the R2 media bucket, keeping it under the free tier without operator hand-work.

**Architecture:** A standalone `Job` (`services/jobs/media_orphan_sweep.py`) modeled on `services/jobs/static_export_orphan_sweep.py`. It builds a "keep-set" of referenced object keys (non-terminal posts + `media_assets` + feed XML), lists the bucket via two new `R2UploadService` methods, and deletes objects that are under a swept prefix, unreferenced, and older than a grace window — bounded by a per-run cap, a circuit breaker, and a hard prefix guard. Deletion only happens when `app_settings.media_orphan_sweep_armed=true`; otherwise it reports what it _would_ delete.

**Tech Stack:** Python 3.13, asyncpg, boto3 (S3-compatible R2), pytest (`unittest.mock`), the plugin `Job` protocol + `PluginScheduler`.

## Global Constraints

- **DB-first config** (feedback_db_first_config): all tunables are `app_settings` keys seeded in `services/settings_defaults.py`; no literals in code beyond the fallback defaults passed to `SiteConfig.get*`.
- **Fail-soft job** (`plugins/job.py`): `run()` MUST NOT raise on routine failure — return `JobResult(ok=False, detail=...)`.
- **No silent caps** (feedback_no_silent_defaults): when the per-run cap truncates, the remainder is reported in `detail`/metrics, never dropped silently.
- **Design source of truth:** `docs/superpowers/specs/2026-07-11-r2-media-reaper-design.md`.
- **Tests in a fresh worktree:** this worktree has no venv. Run pytest with the **main checkout's** poetry env and disable the repo's `--forked` addopts, per memory `reference_run_worktree_tests`. From `src/cofounder_agent`: `poetry run pytest <target> -o addopts="" -p no:cacheprovider -q` — and if `poetry run` resolves to the empty worktree venv, invoke the main checkout's interpreter at `C:/Users/mattm/glad-labs-website/src/cofounder_agent` directly.
- **Public-mirror safety:** all files in this plan ship to the public `poindexter` mirror (they're under `src/cofounder_agent/`), so NO operator specifics (bucket name, `gladlabs.io`, host paths) in code or docstrings — refer to "the media bucket" / `storage_bucket`.
- **Commit after each task** with a `feat:`/`test:` message ending with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

### Task 1: R2UploadService — `list_objects` + `get_object_text`

`list_keys` returns only keys, but the reaper needs each object's `Size` (for reclaim metrics) and `LastModified` (for the grace window), plus the feed XML text (`get_json` only parses JSON). Add two fail-soft public methods that reuse the existing `_s3_client_and_bucket()` builder.

**Files:**

- Modify: `src/cofounder_agent/services/r2_upload_service.py` (add two methods to `class R2UploadService`, after `list_keys`, ~line 356)
- Test: `src/cofounder_agent/tests/unit/services/test_r2_upload_service.py`

**Interfaces:**

- Produces:
  - `async def list_objects(self, prefix: str) -> list[dict]` → each item `{"key": str, "size": int, "last_modified": datetime | None}`; `[]` on error/missing config.
  - `async def get_object_text(self, r2_key: str) -> str | None` → decoded UTF-8 text, or `None` on missing/err.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/services/test_r2_upload_service.py`:

```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.r2_upload_service import R2UploadService
from services.site_config import SiteConfig


@pytest.mark.asyncio
async def test_list_objects_paginates_and_maps_size_and_mtime():
    svc = R2UploadService(site_config=SiteConfig())
    dt = datetime(2026, 7, 1, tzinfo=timezone.utc)
    fake_s3 = MagicMock()
    fake_s3.list_objects_v2.side_effect = [
        {"Contents": [{"Key": "images/a.webp", "Size": 10, "LastModified": dt}],
         "IsTruncated": True, "NextContinuationToken": "t1"},
        {"Contents": [{"Key": "images/b.webp", "Size": 20, "LastModified": dt}],
         "IsTruncated": False},
    ]
    with patch.object(R2UploadService, "_s3_client_and_bucket",
                      AsyncMock(return_value=(fake_s3, "bucket"))):
        objs = await svc.list_objects("images/")
    assert [o["key"] for o in objs] == ["images/a.webp", "images/b.webp"]
    assert objs[0]["size"] == 10
    assert objs[1]["last_modified"] == dt


@pytest.mark.asyncio
async def test_list_objects_returns_empty_when_no_client():
    svc = R2UploadService(site_config=SiteConfig())
    with patch.object(R2UploadService, "_s3_client_and_bucket",
                      AsyncMock(return_value=(None, None))):
        assert await svc.list_objects("images/") == []


@pytest.mark.asyncio
async def test_get_object_text_decodes_body():
    svc = R2UploadService(site_config=SiteConfig())
    fake_s3 = MagicMock()
    body = MagicMock()
    body.read.return_value = b"<rss>hi</rss>"
    fake_s3.get_object.return_value = {"Body": body}
    with patch.object(R2UploadService, "_s3_client_and_bucket",
                      AsyncMock(return_value=(fake_s3, "bucket"))):
        assert await svc.get_object_text("podcast/feed.xml") == "<rss>hi</rss>"


@pytest.mark.asyncio
async def test_get_object_text_none_on_error():
    svc = R2UploadService(site_config=SiteConfig())
    fake_s3 = MagicMock()
    fake_s3.get_object.side_effect = RuntimeError("nosuchkey")
    with patch.object(R2UploadService, "_s3_client_and_bucket",
                      AsyncMock(return_value=(fake_s3, "bucket"))):
        assert await svc.get_object_text("podcast/feed.xml") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/services/test_r2_upload_service.py -o addopts="" -q -k "list_objects or get_object_text"`
Expected: FAIL — `AttributeError: 'R2UploadService' object has no attribute 'list_objects'`.

- [ ] **Step 3: Implement the two methods**

In `services/r2_upload_service.py`, after the `list_keys` method (~line 356), add:

```python
    async def list_objects(self, prefix: str) -> list[dict]:
        """List objects under ``prefix`` with size + last_modified (paginated).

        Returns ``[{"key": str, "size": int, "last_modified": datetime|None}]``.
        Fail-soft: returns ``[]`` on error or missing config, same contract as
        ``list_keys`` — callers treat ``[]`` as "nothing to do".
        """
        s3, bucket = await self._s3_client_and_bucket()
        if not s3:
            return []
        out: list[dict] = []
        try:
            token: str | None = None
            while True:
                kwargs: dict = {"Bucket": bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = s3.list_objects_v2(**kwargs)
                for obj in resp.get("Contents") or []:
                    out.append(
                        {
                            "key": obj["Key"],
                            "size": int(obj.get("Size", 0)),
                            "last_modified": obj.get("LastModified"),
                        }
                    )
                if resp.get("IsTruncated") and resp.get("NextContinuationToken"):
                    token = resp["NextContinuationToken"]
                else:
                    break
            return out
        except Exception as e:
            logger.exception(
                "[STORAGE] list_objects failed for prefix %s: %s", prefix, e,
            )
            return []

    async def get_object_text(self, r2_key: str) -> str | None:
        """Download an object and return its decoded text (utf-8, replace).

        Returns ``None`` when creds/config are missing, the key is absent, or any
        error occurs. Used to read feed XML for the media reaper's keep-set.
        """
        s3, bucket = await self._s3_client_and_bucket()
        if not s3:
            return None
        try:
            resp = s3.get_object(Bucket=bucket, Key=r2_key)
            return resp["Body"].read().decode("utf-8", "replace")
        except Exception as e:
            logger.warning(
                "[STORAGE] get_object_text failed for %s: %s", r2_key, e,
            )
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/services/test_r2_upload_service.py -o addopts="" -q -k "list_objects or get_object_text"`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/r2_upload_service.py src/cofounder_agent/tests/unit/services/test_r2_upload_service.py
git commit -m "feat(storage): R2UploadService.list_objects + get_object_text for the media reaper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Keep-set + orphan-selection pure helpers

The correctness core, isolated as pure functions so it's exhaustively testable without any IO.

**Files:**

- Create: `src/cofounder_agent/services/jobs/media_orphan_sweep.py` (helpers only in this task; the `Job` class lands in Task 3)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_media_orphan_sweep.py`

**Interfaces:**

- Produces (consumed by Task 3):
  - `_build_reference_haystack(post_rows: list[dict], ma_rows: list[dict], feed_texts: list[str]) -> str`
  - `_is_referenced(key: str, haystack: str) -> bool`
  - `_select_orphans(objects: list[dict], haystack: str, prefixes: tuple[str, ...], *, now: datetime, grace_days: int) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/services/jobs/test_media_orphan_sweep.py`:

```python
"""Unit tests for services/jobs/media_orphan_sweep.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.jobs.media_orphan_sweep import (
    _build_reference_haystack,
    _is_referenced,
    _select_orphans,
)

NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


@pytest.mark.unit
def test_haystack_includes_all_reference_sources():
    posts = [{
        "content": "see ![](https://x/images/inline/abc.webp)",
        "featured_image_url": "https://x/images/featured/f1.jpg",
        "cover_image_url": "",
        "featured_image_data": '{"url": "https://x/images/featured/f2.jpg"}',
    }]
    ma = [{"url": "https://x/video/v1.mp4", "storage_path": "/tmp/v1.mp4"}]
    feeds = ["<rss><image>https://x/podcast/cover.jpg</image></rss>"]
    hay = _build_reference_haystack(posts, ma, feeds)
    for token in ("abc.webp", "f1.jpg", "f2.jpg", "v1.mp4", "cover.jpg"):
        assert token in hay


@pytest.mark.unit
def test_is_referenced_matches_full_key_and_basename():
    hay = "body https://images.example/images/inline/abc.webp more"
    assert _is_referenced("images/inline/abc.webp", hay) is True   # basename
    assert _is_referenced("images/inline/zzz.webp", hay) is False


@pytest.mark.unit
def test_select_orphans_applies_prefix_grace_and_reference():
    objs = [
        {"key": "images/inline/live.webp", "size": 1, "last_modified": NOW - timedelta(days=60)},
        {"key": "images/inline/dead.webp", "size": 2, "last_modified": NOW - timedelta(days=60)},
        {"key": "images/inline/fresh.webp", "size": 3, "last_modified": NOW - timedelta(days=1)},
        {"key": "static/keep.json", "size": 4, "last_modified": NOW - timedelta(days=60)},
    ]
    hay = "post body references live.webp only"
    orphans = _select_orphans(
        objs, hay, ("images/", "video/", "podcast/"), now=NOW, grace_days=14,
    )
    keys = {o["key"] for o in orphans}
    assert keys == {"images/inline/dead.webp"}  # live=referenced, fresh=grace, static=prefix
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/services/jobs/test_media_orphan_sweep.py -o addopts="" -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.jobs.media_orphan_sweep'`.

- [ ] **Step 3: Create the module with the helpers**

Create `services/jobs/media_orphan_sweep.py`:

```python
"""MediaOrphanSweepJob — reap unreferenced R2 media objects.

Image and video R2 keys carry a fresh UUID per (re)generation
(``images/featured/{id8}-{uuid8}.jpg``, ``images/inline/{uuid12}.png``,
``video/{uuid}.mp4``), so every regeneration orphans the prior object — and no
other path deletes them (retention policies are DB-table-scoped;
``static_export_orphan_sweep`` only touches ``static/`` JSON;
``media_reconciliation`` only *adds*). This job is the safety net.

Keep-set (an object is KEPT if its key or basename appears here): non-terminal
posts (content + image-url columns), ``media_assets`` (url + storage_path), and
the R2 feed XML (channel art + episode enclosures). Everything else under a
swept prefix that is older than the grace window is an orphan.

Safety: dry-run unless ``media_orphan_sweep_armed=true``; a grace window; a
per-run cap; an empty-keep-set circuit breaker; and a hard prefix guard on the
delete call site. See docs/superpowers/specs/2026-07-11-r2-media-reaper-design.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


def _build_reference_haystack(
    post_rows: list[dict], ma_rows: list[dict], feed_texts: list[str],
) -> str:
    """Concatenate every string that could reference an R2 object key."""
    parts: list[str] = []
    for r in post_rows:
        parts.append(str(r.get("content") or ""))
        parts.append(str(r.get("featured_image_url") or ""))
        parts.append(str(r.get("cover_image_url") or ""))
        parts.append(str(r.get("featured_image_data") or ""))
    for r in ma_rows:
        parts.append(str(r.get("url") or ""))
        parts.append(str(r.get("storage_path") or ""))
    parts.extend(t for t in feed_texts if t)
    return " ".join(parts)


def _is_referenced(key: str, haystack: str) -> bool:
    """True if the full key OR its basename appears in the haystack.

    Basename matching survives domain variance (custom image domain vs the
    ``*.r2.dev`` bucket URL) and extension rewrites.
    """
    if key and key in haystack:
        return True
    base = key.rsplit("/", 1)[-1]
    return bool(base) and base in haystack


def _select_orphans(
    objects: list[dict],
    haystack: str,
    prefixes: tuple[str, ...],
    *,
    now: datetime,
    grace_days: int,
) -> list[dict]:
    """Objects under a swept prefix, unreferenced, older than the grace window."""
    cutoff = now - timedelta(days=grace_days)
    orphans: list[dict] = []
    for obj in objects:
        key = obj["key"]
        if not any(key.startswith(p) for p in prefixes):
            continue  # hard prefix guard
        lm = obj.get("last_modified")
        if lm is not None and lm > cutoff:
            continue  # grace window — protects just-uploaded, not-yet-linked
        if _is_referenced(key, haystack):
            continue
        orphans.append(obj)
    return orphans
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/services/jobs/test_media_orphan_sweep.py -o addopts="" -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/jobs/media_orphan_sweep.py src/cofounder_agent/tests/unit/services/jobs/test_media_orphan_sweep.py
git commit -m "feat(jobs): media reaper keep-set + orphan-selection helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `MediaOrphanSweepJob.run()` + config + registration

Wire the helpers into a `Job`: read tunables from `SiteConfig`, build the keep-set from Postgres + feed XML, enumerate the bucket, select + cap orphans, delete only when armed, emit a finding + metrics. Seed the config defaults and register the job.

**Files:**

- Modify: `src/cofounder_agent/services/jobs/media_orphan_sweep.py` (add the `MediaOrphanSweepJob` class)
- Modify: `src/cofounder_agent/services/settings_defaults.py` (`DEFAULTS` ~line 1957; `METADATA` ~line 2179)
- Modify: `src/cofounder_agent/plugins/registry.py` (`_SAMPLES`, after the `MediaReconciliationJob` tuple ~line 672)
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_media_orphan_sweep.py`

**Interfaces:**

- Consumes (from Task 2): `_build_reference_haystack`, `_select_orphans`.
- Consumes (from Task 1): `R2UploadService.list_objects`, `get_object_text`, `delete_object`.
- Produces: `class MediaOrphanSweepJob` with `name="media_orphan_sweep"`, `async def run(pool, config) -> JobResult`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/services/jobs/test_media_orphan_sweep.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

from services.jobs.media_orphan_sweep import MediaOrphanSweepJob
from services.site_config import SiteConfig

_R2 = "services.jobs.media_orphan_sweep.R2UploadService"


def _pool(post_rows, ma_rows, *, fetch_error=None):
    conn = AsyncMock()
    if fetch_error is not None:
        conn.fetch = AsyncMock(side_effect=fetch_error)
    else:
        # run() issues the posts query first, then media_assets.
        conn.fetch = AsyncMock(side_effect=[post_rows, ma_rows])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _fake_r2(objects, *, feed_text="<rss/>"):
    inst = MagicMock()
    inst.get_object_text = AsyncMock(return_value=feed_text)
    inst.list_objects = AsyncMock(return_value=objects)
    inst.delete_object = AsyncMock(return_value=True)
    return inst


def _cfg(**overrides):
    base = {
        "media_orphan_sweep_armed": "false",
        "media_orphan_sweep_grace_days": "14",
        "media_orphan_sweep_max_deletes_per_run": "500",
        "media_orphan_sweep_prefixes": "images/,video/,podcast/",
    }
    base.update(overrides)
    return {"_site_config": SiteConfig(initial_config=base)}


_OLD = datetime(2026, 5, 1, tzinfo=timezone.utc)  # ~70d before NOW-ish


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dry_run_reports_but_deletes_nothing():
    objs = [
        {"key": "images/inline/dead.webp", "size": 100, "last_modified": _OLD},
        {"key": "images/inline/live.webp", "size": 100, "last_modified": _OLD},
    ]
    pool = _pool([{"content": "uses live.webp"}], [])
    r2 = _fake_r2(objs)
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(pool, _cfg())
    assert result.ok is True
    assert result.changes_made == 0
    r2.delete_object.assert_not_awaited()
    assert result.metrics["orphans_found"] == 1
    assert result.metrics["mode"] == "dry-run"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_armed_deletes_only_unreferenced_orphans():
    objs = [
        {"key": "images/inline/dead.webp", "size": 100, "last_modified": _OLD},
        {"key": "images/inline/live.webp", "size": 100, "last_modified": _OLD},
    ]
    pool = _pool([{"content": "uses live.webp"}], [])
    r2 = _fake_r2(objs)
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(pool, _cfg(media_orphan_sweep_armed="true"))
    assert result.changes_made == 1
    deleted = [c.args[0] for c in r2.delete_object.await_args_list]
    assert deleted == ["images/inline/dead.webp"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cap_truncates_and_reports_remainder():
    objs = [
        {"key": f"images/inline/g{i}.webp", "size": 10, "last_modified": _OLD}
        for i in range(5)
    ]
    pool = _pool([{"content": "no refs"}], [])
    r2 = _fake_r2(objs)
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(
            pool, _cfg(media_orphan_sweep_armed="true", media_orphan_sweep_max_deletes_per_run="2"),
        )
    assert result.changes_made == 2
    assert result.metrics["capped_remainder"] == 3
    assert "capped" in result.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_keep_set_circuit_breaker_aborts():
    pool = _pool([], [])  # no posts, no media_assets
    r2 = _fake_r2([], feed_text="")  # empty feed too
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(pool, _cfg(media_orphan_sweep_armed="true"))
    assert result.ok is False
    assert "keep-set" in result.detail
    r2.list_objects.assert_not_awaited()
    r2.delete_object.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_db_failure_fails_loud():
    pool = _pool([], [], fetch_error=RuntimeError("boom"))
    r2 = _fake_r2([])
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(pool, _cfg())
    assert result.ok is False
    assert "DB query failed" in result.detail
    r2.list_objects.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_site_config_fails_loud():
    pool = _pool([], [])
    result = await MediaOrphanSweepJob().run(pool, {})
    assert result.ok is False
    assert "_site_config" in result.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_feed_fetch_failure_limits_sweep_to_images():
    # get_object_text -> None means feeds unreadable; only images/ is swept.
    objs = [{"key": "video/orphan.mp4", "size": 999, "last_modified": _OLD}]
    pool = _pool([{"content": "text"}], [])
    r2 = _fake_r2(objs)
    r2.get_object_text = AsyncMock(return_value=None)
    with patch(_R2, MagicMock(return_value=r2)):
        result = await MediaOrphanSweepJob().run(pool, _cfg(media_orphan_sweep_armed="true"))
    # video/ was not swept, so the orphan under it is untouched.
    r2.list_objects.assert_awaited_once_with("images/")
    r2.delete_object.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/unit/services/jobs/test_media_orphan_sweep.py -o addopts="" -q`
Expected: FAIL — `ImportError: cannot import name 'MediaOrphanSweepJob'`.

- [ ] **Step 3: Add the `MediaOrphanSweepJob` class**

Append to `services/jobs/media_orphan_sweep.py`:

```python
class MediaOrphanSweepJob:
    name = "media_orphan_sweep"
    description = (
        "Delete unreferenced images/video/podcast objects from R2 "
        "(dry-run unless media_orphan_sweep_armed=true)"
    )
    schedule = "0 4 * * *"  # daily 04:00 operator-local
    idempotent = True

    _POSTS_SQL = """
        SELECT content,
               featured_image_url,
               cover_image_url,
               featured_image_data::text AS featured_image_data
          FROM posts
         WHERE status NOT IN ('rejected', 'deleted', 'archived')
    """
    _MEDIA_ASSETS_SQL = "SELECT url, storage_path FROM media_assets"

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config = config.get("_site_config")
        if site_config is None:
            return JobResult(
                ok=False, detail="no _site_config bound to job run", changes_made=0,
            )

        armed = site_config.get_bool("media_orphan_sweep_armed", False)
        grace_days = site_config.get_int("media_orphan_sweep_grace_days", 14)
        max_deletes = site_config.get_int(
            "media_orphan_sweep_max_deletes_per_run", 500,
        )
        prefixes = tuple(
            p.strip()
            for p in site_config.get(
                "media_orphan_sweep_prefixes", "images/,video/,podcast/",
            ).split(",")
            if p.strip()
        )
        if not prefixes:
            return JobResult(
                ok=True, detail="no swept prefixes configured", changes_made=0,
            )

        # Source of truth for the keep-set. A failure here aborts before any
        # storage call — never delete when we can't read what's referenced.
        try:
            async with pool.acquire() as conn:
                post_rows = [dict(r) for r in await conn.fetch(self._POSTS_SQL)]
                ma_rows = [dict(r) for r in await conn.fetch(self._MEDIA_ASSETS_SQL)]
        except Exception as e:  # noqa: BLE001
            logger.exception("media_orphan_sweep: keep-set query failed: %s", e)
            return JobResult(
                ok=False, detail=f"DB query failed: {e}", changes_made=0,
            )

        from services.r2_upload_service import R2UploadService

        r2 = R2UploadService(site_config=site_config)

        # Feed XML is part of the keep-set (channel art + enclosures). If it
        # can't be read we cannot verify podcast/ + video/, so we keep-more:
        # limit the sweep to images/ this cycle rather than risk deleting a
        # feed-only reference.
        feed_texts: list[str] = []
        feed_ok = True
        for feed_key in ("podcast/feed.xml", "video/feed.xml"):
            txt = await r2.get_object_text(feed_key)
            if txt is None:
                feed_ok = False
            else:
                feed_texts.append(txt)
        active_prefixes = prefixes if feed_ok else tuple(
            p for p in prefixes if p == "images/"
        )
        if not feed_ok:
            logger.warning(
                "media_orphan_sweep: feed fetch failed — limiting sweep to %s",
                active_prefixes,
            )

        haystack = _build_reference_haystack(post_rows, ma_rows, feed_texts)

        # Circuit breaker: an empty keep-set is never a normal state for a live
        # site — abort rather than treat every object as an orphan.
        if not haystack.strip():
            return JobResult(
                ok=False,
                detail="empty keep-set — aborting (circuit breaker)",
                changes_made=0,
            )
        if not active_prefixes:
            return JobResult(
                ok=True, detail="no prefixes to sweep this cycle", changes_made=0,
            )

        objects: list[dict] = []
        for prefix in active_prefixes:
            objects.extend(await r2.list_objects(prefix))

        now = datetime.now(timezone.utc)
        orphans = _select_orphans(
            objects, haystack, active_prefixes, now=now, grace_days=grace_days,
        )
        orphans.sort(key=lambda o: o["key"])
        orphan_bytes = sum(o["size"] for o in orphans)
        to_act = orphans[:max_deletes]
        capped_remainder = len(orphans) - len(to_act)

        def _prefix_of(key: str) -> str:
            return next((p for p in active_prefixes if key.startswith(p)), "?")

        by_prefix: dict[str, dict[str, int]] = {}
        for o in orphans:
            b = by_prefix.setdefault(_prefix_of(o["key"]), {"objects": 0, "bytes": 0})
            b["objects"] += 1
            b["bytes"] += o["size"]

        deleted = 0
        bytes_reclaimed = 0
        if armed:
            for o in to_act:
                key = o["key"]
                if not any(key.startswith(p) for p in active_prefixes):
                    continue  # belt-and-suspenders prefix guard
                if await r2.delete_object(key):
                    deleted += 1
                    bytes_reclaimed += o["size"]

        mode = "armed" if armed else "dry-run"
        shown_n = deleted if armed else len(orphans)
        shown_bytes = bytes_reclaimed if armed else orphan_bytes
        metrics = {
            "mode": mode,
            "scanned": len(objects),
            "orphans_found": len(orphans),
            "orphan_bytes": orphan_bytes,
            "deleted": deleted,
            "bytes_reclaimed": bytes_reclaimed,
            "capped_remainder": capped_remainder,
            "by_prefix": by_prefix,
        }
        verb = "reclaimed" if armed else "would reclaim"
        by_prefix_md = "\n".join(
            f"- `{p}` — {v['objects']} obj / {v['bytes'] / 1024 / 1024:.1f} MB"
            for p, v in sorted(by_prefix.items())
        ) or "- (none)"
        emit_finding(
            source="media_orphan_sweep",
            kind="media_orphan_sweep",
            severity="info",
            title=(
                f"Media reaper ({mode}): {verb} {shown_n} objects / "
                f"{shown_bytes / 1024 / 1024:.1f} MB"
            ),
            body=(
                f"## Media orphan sweep ({mode})\n\n"
                f"Scanned {len(objects)} objects under {list(active_prefixes)}; "
                f"{len(orphans)} orphaned ({orphan_bytes / 1024 / 1024:.1f} MB).\n\n"
                f"### By prefix\n{by_prefix_md}\n\n"
                + (
                    f"Deleted {deleted} this cycle"
                    + (f"; {capped_remainder} over the cap remain" if capped_remainder else "")
                    if armed
                    else "Dry-run — nothing deleted. Set "
                    "`media_orphan_sweep_armed=true` to arm."
                )
            ),
            dedup_key="media_orphan_sweep",
            extra=metrics,
        )

        detail = (
            f"{mode}: {'deleted' if armed else 'would delete'} {shown_n} orphan(s), "
            f"{shown_bytes // (1024 * 1024)} MB"
            + (" (capped this cycle; more remain)" if capped_remainder else "")
        )
        return JobResult(
            ok=True, detail=detail, changes_made=deleted, metrics=metrics,
        )
```

- [ ] **Step 4: Seed config defaults**

In `services/settings_defaults.py`, in the `DEFAULTS` dict after the `seo.refresh` block (after line 1956 `'seo.refresh.max_per_run': '3',`), add:

```python
    # ----- R2 media orphan-reaper (design 2026-07-11) -----
    # Dry-run by default: computes + reports orphans but deletes NOTHING until an
    # operator flips _armed=true after reviewing a dry-run cycle. Keep-set =
    # non-terminal posts + media_assets + feed XML. Grace window protects
    # just-uploaded, not-yet-linked objects; the cap bounds blast radius.
    'media_orphan_sweep_armed': 'false',
    'media_orphan_sweep_grace_days': '14',
    'media_orphan_sweep_max_deletes_per_run': '500',
    'media_orphan_sweep_prefixes': 'images/,video/,podcast/',
```

In the `METADATA` dict (after line 2178, the `media_redispatch_cap_reset_*` block), add:

```python
    'media_orphan_sweep_armed': {'owner': 'media_orphan_sweep', 'value_type': 'boolean'},
    'media_orphan_sweep_grace_days': {'owner': 'media_orphan_sweep', 'value_type': 'integer'},
    'media_orphan_sweep_max_deletes_per_run': {'owner': 'media_orphan_sweep', 'value_type': 'integer'},
    'media_orphan_sweep_prefixes': {'owner': 'media_orphan_sweep', 'value_type': 'string'},
```

- [ ] **Step 5: Register the job**

In `plugins/registry.py`, in `_SAMPLES` after the `MediaReconciliationJob` tuple (~line 672), add:

```python
        # Media orphan sweep — reaps unreferenced images/video/podcast objects
        # from R2 that pile up because image/video keys are UUID-per-regen and
        # nothing else deletes them. Dry-run unless media_orphan_sweep_armed=true;
        # keep-set = non-terminal posts + media_assets + feed XML. Design spec
        # 2026-07-11-r2-media-reaper-design.md.
        (
            "jobs",
            "services.jobs.media_orphan_sweep",
            "MediaOrphanSweepJob",
        ),
```

- [ ] **Step 6: Run the full job test file**

Run: `poetry run pytest tests/unit/services/jobs/test_media_orphan_sweep.py -o addopts="" -q`
Expected: PASS (all helper + job tests, 10 passed).

- [ ] **Step 7: Run the settings-defaults + registry tests to confirm no parity/registration breakage**

Run: `poetry run pytest tests/unit/services/test_settings_defaults.py tests/unit/plugins -o addopts="" -q`
Expected: PASS (if a specific test name differs, run `poetry run pytest tests/unit -o addopts="" -q -k "settings_default or registry"`). Fix any METADATA/DEFAULTS parity failure by ensuring all four keys appear in both maps.

- [ ] **Step 8: Commit**

```bash
git add src/cofounder_agent/services/jobs/media_orphan_sweep.py src/cofounder_agent/tests/unit/services/jobs/test_media_orphan_sweep.py src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/plugins/registry.py
git commit -m "feat(jobs): MediaOrphanSweepJob — dry-run-first R2 media reaper

Keep-set = non-terminal posts + media_assets + feed XML; grace window +
per-run cap + empty-keep-set circuit breaker + hard prefix guard. Registered
and config-seeded; armed via media_orphan_sweep_armed.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Documentation — R2 media lifecycle

Document what lives in the media bucket, how it's cleaned, and the reaper's dry-run→armed rollout, so the cleanup story is discoverable (public-mirror-safe: no operator specifics).

**Files:**

- Create: `src/cofounder_agent/../docs/architecture/r2-media-lifecycle.md` → i.e. `docs/architecture/r2-media-lifecycle.md`

- [ ] **Step 1: Write the doc**

Create `docs/architecture/r2-media-lifecycle.md`:

```markdown
# R2 media lifecycle

The media bucket (`storage_bucket`) holds pipeline-produced media: featured and
inline images (`images/featured/`, `images/inline/`), podcast audio
(`podcast/`), video (`video/`), plus the static export index (`static/`).

## Why orphans accumulate

Image and video object keys carry a fresh UUID per generation, so regenerating
a post's image or video writes a **new** object and leaves the old one behind.
Deterministic keys (podcast `{post_id}` paths, `static/` JSON) overwrite instead.

## Cleanup jobs

| Job                          | Scope                           | What it does                                                                                                           |
| ---------------------------- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `static_export_orphan_sweep` | `static/` JSON                  | Deletes per-post JSON for de-published slugs.                                                                          |
| `media_orphan_sweep`         | `images/`, `video/`, `podcast/` | Deletes objects not referenced by any non-terminal post, `media_assets` row, or feed XML, older than the grace window. |

## `media_orphan_sweep` behaviour

- **Keep-set:** an object is kept if its key or basename appears in any
  non-terminal post (`content`, `featured_image_url`, `cover_image_url`,
  `featured_image_data`), any `media_assets` row (`url`, `storage_path`), or the
  `podcast/feed.xml` / `video/feed.xml` documents.
- **Dry-run first:** with `media_orphan_sweep_armed=false` (default) it reports
  what it would delete via a `media_orphan_sweep` finding and JobResult metrics,
  deleting nothing. Flip `media_orphan_sweep_armed=true` to arm it.
- **Safety:** a grace window (`media_orphan_sweep_grace_days`, default 14) skips
  freshly-uploaded objects; a per-run cap (`media_orphan_sweep_max_deletes_per_run`,
  default 500) bounds blast radius; an empty keep-set aborts the run; and the
  delete call site is guarded to the configured `media_orphan_sweep_prefixes`.

## Follow-up

The upstream fix — deleting the prior object when an image is regenerated, so
orphans stop being created — is tracked separately. The reaper is the safety net.
```

- [ ] **Step 2: Commit**

```bash
git add docs/architecture/r2-media-lifecycle.md
git commit -m "docs(architecture): R2 media lifecycle + orphan-reaper behaviour

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Finalization (after all tasks)

- [ ] Run the whole new test surface once more:
      `poetry run pytest tests/unit/services/jobs/test_media_orphan_sweep.py tests/unit/services/test_r2_upload_service.py -o addopts="" -q` → all pass.
- [ ] **Dry-run validation against the real bucket** (the whole point of dry-run-first): with the job registered, let one scheduled cycle run (or invoke it once), then confirm the emitted `media_orphan_sweep` finding's "would reclaim" figure is in the expected ~6 GB range and the per-prefix split matches the audit (images/inline the largest). Do NOT arm yet.
- [ ] Push; the draft PR glad-labs-stack#2337 updates. Once CI is green, mark the PR ready for review.
- [ ] **Operator step (Matt):** review the dry-run finding, then flip `media_orphan_sweep_armed=true` (optionally a one-time supervised high-`max_deletes_per_run` pass to clear the ~4k backlog, then back to 500 for steady state).

## Self-Review

- **Spec coverage:** standalone job ✓ (T3); keep-set = posts+media_assets+feed ✓ (T2 helpers, T3 queries); scope images/video/podcast ✓ (config default); grace/cap/circuit-breaker/prefix-guard ✓ (T2 `_select_orphans` + T3 run); dry-run→armed ✓ (T3 `armed` + config); observability finding+metrics ✓ (T3); new R2 seams ✓ (T1); tests modeled on precedent ✓; delete-on-regen explicitly out of scope ✓ (T4 follow-up).
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type consistency:** `_build_reference_haystack` / `_is_referenced` / `_select_orphans` signatures match between T2 definition and T3 call; `list_objects` returns `{key,size,last_modified}` used consistently in `_select_orphans` and metrics; `JobResult` and `emit_finding` match their real signatures.

```

```
