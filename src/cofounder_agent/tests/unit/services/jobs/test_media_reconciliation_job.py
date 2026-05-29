"""Unit tests for ``services/jobs/media_reconciliation.py``.

Sibling to ``test_static_export_reconciliation_job.py``. The watchdog
has four outcomes worth pinning:

1. **No published posts in lookback window** — fast-path noop.
2. **In sync** — every published post has both podcast + video on R2.
   No regen, no finding, ok=True.
3. **Drift with successful regen** — at least one post is missing
   media; the job regenerates within the per-cycle cap, emits a
   warning-severity finding, ok=True.
4. **Drift with regen failure** — regen path raised or returned a
   falsy URL; finding escalates to critical, ok=False.

DB pool, httpx HEAD checks, and the podcast/video service entrypoints
are all mocked. No real network, no real GPU, no real DB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.jobs.media_reconciliation import MediaReconciliationJob


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(rows: list[dict[str, Any]]) -> Any:
    """asyncpg pool stub: conn.fetch returns ``rows``, conn.execute swallowed.

    fetchrow defaults to returning the seeded storage_public_url so the
    URL resolver short-circuits to ``https://r2.test`` — matches the
    URL prefix the test's _patch_head helpers route on.
    """
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[dict(r) for r in rows])
    conn.fetchrow = AsyncMock(return_value={"value": "https://r2.test"})
    conn.execute = AsyncMock(return_value=None)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _post(*, id_: str = "post-1", title: str = "A title", **overrides):
    base = {
        "id": id_,
        "title": title,
        "content": "Body markdown.",
        "podcast_url": None,
        "video_url": None,
        "published_at": datetime.now(timezone.utc),
        # Default to the full glad-labs niche policy so existing tests
        # exercising drift + regen still report missing media. Tests
        # that need an exempt post (dev_diary-shape) pass an empty list
        # via overrides. Added 2026-05-20 (finding #195) when the
        # reconciliation job was migrated off slug-prefix filtering
        # onto the canonical ``posts.media_to_generate`` seam.
        "media_to_generate": ["podcast", "video", "video_short"],
    }
    base.update(overrides)
    return base


def _patch_head(
    *, podcast_status: int, video_status: int,
) -> Any:
    """Patch httpx.AsyncClient.head to return the requested status per URL.

    Routes by path prefix: ``/podcast/`` → ``podcast_status``,
    ``/video/`` → ``video_status``.
    """
    async def _stub_head(self, url, *a, **kw):  # noqa: ANN001, ARG001
        resp = MagicMock(spec=httpx.Response)
        if "/podcast/" in str(url):
            resp.status_code = podcast_status
        elif "/video/" in str(url):
            resp.status_code = video_status
        else:
            resp.status_code = 404
        return resp

    return patch.object(httpx.AsyncClient, "head", _stub_head)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMediaReconciliation:

    @pytest.mark.asyncio
    async def test_no_published_posts_in_window_is_noop(self):
        pool, _ = _make_pool([])
        job = MediaReconciliationJob()
        result = await job.run(pool, config={})
        assert result.ok is True
        assert "no published posts" in result.detail
        assert result.changes_made == 0
        assert result.metrics["scanned"] == 0

    @pytest.mark.asyncio
    async def test_in_sync_no_finding_no_regen(self):
        """Every published post has both podcast + video on R2 (HEAD 200).
        Job MUST NOT regen anything, MUST NOT emit a finding.
        """
        pool, _ = _make_pool([_post(id_="p1"), _post(id_="p2")])
        with _patch_head(podcast_status=200, video_status=200), \
             patch(
                 "services.jobs.media_reconciliation.emit_finding"
             ) as emit_mock:
            result = await MediaReconciliationJob().run(pool, config={})
        assert result.ok is True
        assert "in sync" in result.detail
        assert result.metrics["missing_podcast"] == 0
        assert result.metrics["missing_video"] == 0
        emit_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_podcast_triggers_regen_and_warning_finding(self):
        """R2 returns 404 for podcast key, 200 for video. Self-heal MUST
        regen the podcast, stamp posts.podcast_url, and emit a
        warning-severity finding.
        """
        pool, conn = _make_pool([_post(id_="p1")])
        # Stub the regen path — generate_podcast_episode is fire-and-
        # forget (returns None) and the R2UploadService stub returns a
        # URL on success.
        r2 = MagicMock()
        upload_mock = AsyncMock(return_value="https://r2.test/podcast/v2/p1.mp3")
        r2.upload_podcast_episode = upload_mock
        sc = MagicMock()
        sc.get.side_effect = lambda k, d="": d
        with _patch_head(podcast_status=404, video_status=200), \
             patch(
                 "services.podcast_service.generate_podcast_episode",
                 new=AsyncMock(return_value=None),
             ) as gen_mock, \
             patch(
                 "services.r2_upload_service.R2UploadService", return_value=r2,
             ), \
             patch(
                 "services.jobs.media_reconciliation.emit_finding"
             ) as emit_mock:
            result = await MediaReconciliationJob().run(
                pool, config={"_site_config": sc},
            )

        assert result.ok is True
        assert result.metrics["missing_podcast"] == 1
        assert result.metrics["regen_podcast_ok"] == 1
        assert result.metrics["regen_podcast_fail"] == 0
        gen_mock.assert_awaited_once()
        upload_mock.assert_awaited_once_with("p1")
        # 2026-05-14: media URLs are recorded in ``media_assets`` (one row
        # per (post_id, type)), not on the posts table. The recorder does
        # UPDATE-then-INSERT — the mock's ``execute`` returns None so the
        # UPDATE branch is treated as "no rows matched" and the INSERT
        # fires too, yielding 2 calls.
        assert conn.execute.await_count == 2
        update_call, insert_call = conn.execute.await_args_list
        assert "UPDATE media_assets" in update_call.args[0]
        assert update_call.args[0:1][0].startswith("\n                    UPDATE media_assets") \
            or "UPDATE media_assets" in update_call.args[0]
        assert update_call.args[1] == "https://r2.test/podcast/v2/p1.mp3"
        assert update_call.args[2] == "p1"
        assert update_call.args[3] == "podcast"
        assert "INSERT INTO media_assets" in insert_call.args[0]
        assert insert_call.args[1] == "p1"
        assert insert_call.args[2] == "podcast"
        assert insert_call.args[3] == "https://r2.test/podcast/v2/p1.mp3"
        # Finding emitted with warning severity (regen succeeded).
        emit_mock.assert_called_once()
        kwargs = emit_mock.call_args.kwargs
        assert kwargs["severity"] == "warning"
        assert kwargs["kind"] == "media_drift"

    @pytest.mark.asyncio
    async def test_missing_video_triggers_regen(self):
        """Video missing → generate_video_for_post + upload + stamp."""
        pool, conn = _make_pool([_post(id_="p2")])

        video_result = MagicMock()
        video_result.success = True

        r2 = MagicMock()
        upload_mock = AsyncMock(return_value="https://r2.test/video/p2.mp4")
        r2.upload_video_episode = upload_mock
        sc = MagicMock()
        sc.get.side_effect = lambda k, d="": d
        with _patch_head(podcast_status=200, video_status=404), \
             patch(
                 "services.video_service.generate_video_for_post",
                 new=AsyncMock(return_value=video_result),
             ) as gen_mock, \
             patch(
                 "services.r2_upload_service.R2UploadService", return_value=r2,
             ), \
             patch(
                 "services.jobs.media_reconciliation.emit_finding"
             ):
            result = await MediaReconciliationJob().run(
                pool, config={"_site_config": sc},
            )

        assert result.ok is True
        assert result.metrics["regen_video_ok"] == 1
        gen_mock.assert_awaited_once_with("p2", "A title", "Body markdown.")
        upload_mock.assert_awaited_once_with("p2")
        # UPDATE-then-INSERT recorder in _record_media_asset; mock's
        # execute() returns None so UPDATE is treated as "no match"
        # and the INSERT fires. See test_missing_podcast_* for shape.
        assert conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_regen_upload_failure_escalates_to_critical(self):
        """Upload returns None → regen failed; finding MUST be critical."""
        pool, _ = _make_pool([_post(id_="p3")])
        r2 = MagicMock()
        r2.upload_podcast_episode = AsyncMock(return_value=None)
        sc = MagicMock()
        sc.get.side_effect = lambda k, d="": d
        with _patch_head(podcast_status=404, video_status=200), \
             patch(
                 "services.podcast_service.generate_podcast_episode",
                 new=AsyncMock(return_value=None),
             ), \
             patch(
                 "services.r2_upload_service.R2UploadService", return_value=r2,
             ), \
             patch(
                 "services.jobs.media_reconciliation.emit_finding"
             ) as emit_mock:
            result = await MediaReconciliationJob().run(
                pool, config={"_site_config": sc},
            )
        assert result.ok is False
        assert result.metrics["regen_podcast_fail"] == 1
        emit_mock.assert_called_once()
        assert emit_mock.call_args.kwargs["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_per_cycle_cap_limits_regen_count(self):
        """Backlog of 10 missing podcasts but cap=2: only two get regen'd."""
        rows = [_post(id_=f"p{i}") for i in range(10)]
        pool, _ = _make_pool(rows)
        gen_calls: list[str] = []

        async def _capture_gen(post_id, *a, **kw):  # noqa: ANN001, ARG001
            gen_calls.append(post_id)

        r2 = MagicMock()
        r2.upload_podcast_episode = AsyncMock(return_value="https://r2.test/x.mp3")
        sc = MagicMock()
        sc.get.side_effect = lambda k, d="": d
        with _patch_head(podcast_status=404, video_status=200), \
             patch(
                 "services.podcast_service.generate_podcast_episode",
                 new=AsyncMock(side_effect=_capture_gen),
             ), \
             patch(
                 "services.r2_upload_service.R2UploadService", return_value=r2,
             ), \
             patch(
                 "services.jobs.media_reconciliation.emit_finding"
             ):
            result = await MediaReconciliationJob().run(
                pool,
                config={
                    "podcast_cap_per_cycle": 2,
                    "video_cap_per_cycle": 0,
                    "_site_config": sc,
                },
            )
        # All 10 are detected as missing, but only 2 get regen this cycle.
        assert result.metrics["missing_podcast"] == 10
        assert result.metrics["regen_podcast_ok"] == 2
        assert len(gen_calls) == 2

    @pytest.mark.asyncio
    async def test_regen_exception_counted_as_failure(self):
        """Generation path raising must NOT crash the job — it counts as
        a failure and contributes to the critical-severity escalation.
        """
        pool, _ = _make_pool([_post(id_="p_boom")])
        r2 = MagicMock()
        r2.upload_podcast_episode = AsyncMock(return_value=None)
        sc = MagicMock()
        sc.get.side_effect = lambda k, d="": d
        with _patch_head(podcast_status=404, video_status=200), \
             patch(
                 "services.podcast_service.generate_podcast_episode",
                 new=AsyncMock(side_effect=RuntimeError("TTS broke")),
             ), \
             patch(
                 "services.r2_upload_service.R2UploadService", return_value=r2,
             ), \
             patch(
                 "services.jobs.media_reconciliation.emit_finding"
             ) as emit_mock:
            result = await MediaReconciliationJob().run(
                pool, config={"_site_config": sc},
            )
        # Exception during regen counts as a failure (the regen path is
        # wrapped in try/except so the job itself doesn't raise).
        assert result.metrics["regen_podcast_fail"] == 1
        assert result.ok is False
        assert emit_mock.call_args.kwargs["severity"] == "critical"


    @pytest.mark.asyncio
    async def test_skips_when_no_r2_public_base_configured(self):
        """No config.r2_public_base AND no app_settings.storage_public_url → skip.

        2026-05-12 cleanup (poindexter#485): the old hardcoded
        ``_DEFAULT_R2_PUBLIC_BASE`` constant baked Matt's R2 bucket into
        a public OSS file. Pin the new behaviour: when neither source
        resolves, skip the job rather than probing somebody else's bucket.
        """
        # Pool: posts query returns rows so we actually hit the resolver path;
        # app_settings query returns None (storage_public_url unset).
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[_post(id_="p1")])
        conn.fetchrow = AsyncMock(return_value=None)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        with patch(
            "services.jobs.media_reconciliation.emit_finding"
        ) as emit_mock:
            result = await MediaReconciliationJob().run(pool, config={})

        assert result.ok is True
        assert "no R2 public base" in result.detail
        emit_mock.assert_called_once()
        kwargs = emit_mock.call_args.kwargs
        assert kwargs["dedup_key"] == "media_reconciliation_r2_public_base_unresolved"

    @pytest.mark.asyncio
    async def test_excludes_dev_diary_slug_prefix_by_default(self):
        """Captured 2026-05-15 — Matt: "the dev blogs don't need
        podcasts or videos". Posts whose slug starts with
        ``what-we-shipped-`` or ``daily-dev-diary-`` must NOT appear in
        the reconciliation SQL's result set, so they don't generate
        spurious media-drift alerts every 15 min.

        Pins the SQL filter behaviour by asserting that the
        ``NOT (slug ILIKE ANY($2::text[]))`` clause is in play AND that
        the parameter the job passes for ``$2`` includes both default
        prefixes."""
        async def _fetchrow_dispatch(sql, *_args, **_kwargs):
            # Route by SQL text so order-of-call doesn't matter.
            if "storage_public_url" in sql:
                return {"value": "https://r2.test"}
            if "media_reconciliation_exclude_slug_prefixes" in sql:
                return None  # defaults kick in
            return None

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(side_effect=_fetchrow_dispatch)
        conn.execute = AsyncMock(return_value=None)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        await MediaReconciliationJob().run(pool, config={})

        posts_calls = [
            c for c in conn.fetch.await_args_list
            if "FROM posts" in (c.args[0] if c.args else "")
        ]
        assert posts_calls, "expected at least one fetch against posts"
        prefix_arg = posts_calls[0].args[2]
        assert "what-we-shipped-%" in prefix_arg
        assert "daily-dev-diary-%" in prefix_arg
        sql = posts_calls[0].args[0]
        assert "NOT (slug ILIKE ANY" in sql

    @pytest.mark.asyncio
    async def test_exclude_prefixes_can_be_extended_via_app_settings(self):
        """Operator can extend the exclude list at runtime via the
        ``media_reconciliation_exclude_slug_prefixes`` app_settings
        row (comma-separated). Per ``feedback_db_first_config`` — no
        code change required to add a new exempt post type."""
        async def _fetchrow_dispatch(sql, *_args, **_kwargs):
            if "storage_public_url" in sql:
                return {"value": "https://r2.test"}
            if "media_reconciliation_exclude_slug_prefixes" in sql:
                return {"value": "what-we-shipped-, internal-only-, weekly-digest-"}
            return None

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(side_effect=_fetchrow_dispatch)
        conn.execute = AsyncMock(return_value=None)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        await MediaReconciliationJob().run(pool, config={})

        posts_calls = [
            c for c in conn.fetch.await_args_list
            if "FROM posts" in (c.args[0] if c.args else "")
        ]
        assert posts_calls
        prefix_arg = posts_calls[0].args[2]
        assert "what-we-shipped-%" in prefix_arg
        assert "internal-only-%" in prefix_arg
        assert "weekly-digest-%" in prefix_arg
        # Defaults NOT auto-merged in — operator setting fully replaces them.
        assert "daily-dev-diary-%" not in prefix_arg

    @pytest.mark.asyncio
    async def test_config_supplied_prefixes_win_over_app_settings(self):
        """PluginConfig ``config.exclude_slug_prefixes`` takes precedence
        over the app_settings row. Allows per-job overrides via the
        scheduler's config blob without touching global app_settings."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(return_value={"value": "https://r2.test"})
        conn.execute = AsyncMock(return_value=None)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        await MediaReconciliationJob().run(
            pool,
            config={"exclude_slug_prefixes": ["override-prefix-"]},
        )

        posts_calls = [
            c for c in conn.fetch.await_args_list
            if "FROM posts" in (c.args[0] if c.args else "")
        ]
        prefix_arg = posts_calls[0].args[2]
        assert prefix_arg == ["override-prefix-%"]


@pytest.mark.unit
@pytest.mark.asyncio
class TestMediaToGenerateFilter:
    """Pins the contract that closes finding #195.

    Pre-fix: `media_reconciliation` filtered posts by slug-prefix LIKE
    exclusion only. A post with ``media_to_generate=[]`` (the dev_diary
    seed value) whose slug didn't match the exclude list was reported
    as drift and the job tried to regenerate non-existent podcasts +
    videos. Symptom: alert #293 fired on post ``dcd86ea6...`` whose
    media policy was an empty array — the same slug-not-array filter
    pattern PR #482 fixed for the backfill jobs.

    Post-fix: SELECT filters `cardinality(media_to_generate) > 0` AND
    `_check_post_media` consults the per-row policy so HEAD checks
    fire only for the media types the post actually wants.
    """

    async def test_post_with_empty_media_to_generate_is_filtered_out(self):
        """SELECT must not return a post with empty media_to_generate.
        Even if the slug-prefix exclude doesn't match, an empty policy
        means 'no media expected'."""
        from services.jobs.media_reconciliation import MediaReconciliationJob

        conn = MagicMock()

        async def _fetchrow(query, *args):
            if "FROM app_settings" in query:
                return {"value": "https://r2.test", "is_secret": False}
            return None

        captured_sql: list[str] = []

        async def _fetch(query, *args):
            captured_sql.append(query)
            if "FROM posts" in query:
                # The fix's SQL filter MUST cull empty-array rows. If
                # the SELECT still returns them, this test catches the
                # regression.
                return []
            return []

        conn.fetchrow = AsyncMock(side_effect=_fetchrow)
        conn.fetch = AsyncMock(side_effect=_fetch)
        conn.execute = AsyncMock(return_value=None)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        result = await MediaReconciliationJob().run(pool, config={})

        assert result.ok is True
        # The post-fix SQL MUST include the cardinality filter; if a
        # future refactor drops it, this assert fails loud.
        posts_query = next(q for q in captured_sql if "FROM posts" in q)
        assert "cardinality" in posts_query, (
            "media_reconciliation SELECT must filter on "
            "cardinality(media_to_generate) > 0 so empty-policy posts "
            "(e.g. dev_diary) are never reported as drift. See finding "
            "#195."
        )

    async def test_check_post_media_skips_head_when_policy_empty(self):
        """``_check_post_media`` directly. A row whose
        ``media_to_generate`` is empty must report podcast_missing=False
        AND video_missing=False — and the HTTP client must NOT be hit."""
        from services.jobs.media_reconciliation import MediaReconciliationJob

        client = AsyncMock()
        client.head = AsyncMock()  # Should NEVER be called.

        row = {
            "id": "dcd86ea6-9d8e-4841-9543-a46a55d96283",
            "title": "Test post with empty media policy",
            "content": "body",
            "media_to_generate": [],
        }
        out = await MediaReconciliationJob()._check_post_media(
            client, "https://r2.test", "v2", row,
        )
        assert out["podcast_missing"] is False
        assert out["video_missing"] is False
        client.head.assert_not_awaited()

    async def test_check_post_media_only_heads_requested_types(self):
        """A row with ``media_to_generate=['podcast']`` should HEAD the
        podcast URL but NOT the video URL."""
        from services.jobs.media_reconciliation import MediaReconciliationJob

        # Stub HEAD to return 200 OK for any URL.
        async def _head(url, **kw):
            r = MagicMock()
            r.status_code = 200
            return r

        client = AsyncMock()
        client.head = AsyncMock(side_effect=_head)

        row = {
            "id": "post-with-podcast-only",
            "title": "podcast-only",
            "content": "body",
            "media_to_generate": ["podcast"],
        }
        out = await MediaReconciliationJob()._check_post_media(
            client, "https://r2.test", "v2", row,
        )
        # podcast was checked (200 OK), video was NOT checked.
        assert client.head.await_count == 1
        head_url = client.head.await_args.args[0]
        assert "/podcast/" in head_url
        assert out["podcast_missing"] is False  # 200 means present.
        assert out["video_missing"] is False  # Not requested → not missing.
