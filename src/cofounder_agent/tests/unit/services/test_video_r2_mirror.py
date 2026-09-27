"""Unit tests for the video R2 mirror pass (Glad-Labs/poindexter#1085).

The video RSS feed advertises ``video/{post_id}.mp4`` for every approved,
published long-form video whose asset has no stamped URL. Until this pass
existed nothing wrote that key after the task-keyed cutover (#1460), so every
enclosure since then 404ed. These tests pin the contract of the step that now
writes it: what gets uploaded, what only gets stamped, what gets parked, and
that the pass selects exactly the feed's items.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock, patch

import pytest

from poindexter.services import video_r2_mirror as vm
from poindexter.services.r2_upload_service import (
    ObjectStoreUnavailable,
    R2UploadService,
    video_episode_key,
)
from poindexter.services.site_config import SiteConfig

_BASE = "https://cdn.test"


class _Conn:
    """The pass's lock connection: answers the advisory lock/unlock calls."""

    def __init__(self, lock_result: Any = True) -> None:
        self.lock_result = lock_result
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        if "pg_try_advisory_lock" in sql:
            return self.lock_result
        return True


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _Pool:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        execute_result: str = "UPDATE 1",
        lock_result: Any = True,
        fetch_raises: Exception | None = None,
    ) -> None:
        self.rows = rows or []
        self.execute_result = execute_result
        self.fetch_raises = fetch_raises
        self.conn = _Conn(lock_result)
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetched.append((sql, args))
        if self.fetch_raises:
            raise self.fetch_raises
        return self.rows

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return self.execute_result

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)

    def stamps(self) -> list[tuple[Any, ...]]:
        return [a for (s, a) in self.executed if "SET url = $2" in s]

    def marks(self) -> list[tuple[Any, ...]]:
        return [a for (s, a) in self.executed if "'r2_mirror', jsonb_build_object" in s]


class _R2:
    """Object-store stand-in. ``objects`` maps key → size (absent = no object);
    ``head_error`` makes every HEAD fail the way an unreachable store does."""

    def __init__(
        self,
        objects: dict[str, int] | None = None,
        *,
        upload_ok: bool = True,
        head_error: Exception | None = None,
    ) -> None:
        self.objects = dict(objects or {})
        self.upload_ok = upload_ok
        self.head_error = head_error
        self.uploads: list[tuple[str, str, str]] = []
        self.heads: list[str] = []

    async def object_size(self, key: str) -> int | None:
        self.heads.append(key)
        if self.head_error:
            raise self.head_error
        return self.objects.get(key)

    def object_url(self, key: str) -> str:
        return f"{_BASE}/{key}"

    async def upload_to_r2(self, path: str, key: str, content_type: str) -> str | None:
        self.uploads.append((path, key, content_type))
        return f"{_BASE}/{key}" if self.upload_ok else None


def _row(tmp_path, *, post_id: str = "post-1", local: bytes | None = b"x" * 40,
         file_size_bytes: int | None = None, mirror_state: Any = None,
         title: str = "A post") -> dict[str, Any]:
    path = tmp_path / f"{post_id}-task.mp4"
    if local is not None:
        path.write_bytes(local)
    return {
        "asset_id": f"asset-{post_id}",
        "post_id": post_id,
        "title": title,
        "storage_path": str(path),
        "file_size_bytes": file_size_bytes if file_size_bytes is not None else (len(local) if local is not None else None),
        "url": "",
        "mirror_state": mirror_state,
    }


def _sc(**over: str) -> SiteConfig:
    base = {"storage_public_url": _BASE, "video_r2_mirror_enabled": "true"}
    base.update(over)
    return SiteConfig(initial_config=base)


# ---------------------------------------------------------------------------
# mirror_video_asset — the state table in the module docstring
# ---------------------------------------------------------------------------


async def test_absent_object_with_local_render_is_uploaded_and_stamped(tmp_path):
    pool, r2, row = _Pool(), _R2(), _row(tmp_path)

    out = await vm.mirror_video_asset(pool, r2, row)

    assert out.status == "uploaded"
    assert r2.uploads == [(row["storage_path"], "video/post-1.mp4", "video/mp4")]
    # Stamped by asset id with the URL upload_to_r2 returned.
    assert pool.stamps() == [("asset-post-1", f"{_BASE}/video/post-1.mp4")]
    assert pool.marks() == []


def test_stamp_sets_provider_and_clears_an_earlier_mark():
    sql = vm._STAMP_SQL
    assert "storage_provider = 'cloudflare_r2'" in sql
    assert "- 'r2_mirror'" in sql
    assert "type = 'video'" in sql  # never stamps a Short or another medium


async def test_object_already_there_with_the_same_size_is_stamped_not_uploaded(tmp_path):
    """An earlier upload whose stamp didn't land: record it, don't move 100 MB again."""
    pool = _Pool()
    r2 = _R2({"video/post-1.mp4": 40})

    out = await vm.mirror_video_asset(pool, r2, _row(tmp_path))

    assert out.status == "stamped"
    assert r2.uploads == []
    assert pool.stamps() == [("asset-post-1", f"{_BASE}/video/post-1.mp4")]


async def test_render_gone_but_bucket_holds_the_recorded_size_is_stamped(tmp_path):
    pool = _Pool()
    r2 = _R2({"video/post-1.mp4": 123})

    out = await vm.mirror_video_asset(
        pool, r2, _row(tmp_path, local=None, file_size_bytes=123),
    )

    assert out.status == "stamped"
    assert r2.uploads == []


async def test_different_object_under_a_present_render_is_replaced(tmp_path):
    """The approved render is the one the operator reviewed at Gate 2."""
    pool = _Pool()
    r2 = _R2({"video/post-1.mp4": 999})

    out = await vm.mirror_video_asset(pool, r2, _row(tmp_path))

    assert out.status == "uploaded"
    assert [u[1] for u in r2.uploads] == ["video/post-1.mp4"]


async def test_different_object_and_no_render_is_parked_as_mismatch(tmp_path):
    """Prod had two of these: pre-cutover renders under the key, sizes that
    don't match the approved render, and the render itself gone."""
    pool = _Pool()
    r2 = _R2({"video/post-1.mp4": 61_745_023})

    out = await vm.mirror_video_asset(
        pool, r2, _row(tmp_path, local=None, file_size_bytes=39_230_016),
    )

    assert out.status == "blocked"
    assert out.reason == vm.MISMATCH
    assert out.newly_blocked is True
    assert r2.uploads == [] and pool.stamps() == []
    (mark,) = pool.marks()
    assert mark == ("asset-post-1", vm.MISMATCH, "video/post-1.mp4", 61_745_023, 39_230_016)


async def test_no_object_and_no_render_is_parked_as_source_missing(tmp_path):
    pool = _Pool()
    out = await vm.mirror_video_asset(
        pool, _R2(), _row(tmp_path, local=None, file_size_bytes=29_245_162),
    )

    assert out.status == "blocked"
    assert out.reason == vm.SOURCE_MISSING
    (mark,) = pool.marks()
    assert mark == ("asset-post-1", vm.SOURCE_MISSING, "video/post-1.mp4", None, 29_245_162)


@pytest.mark.parametrize(
    "prior",
    [
        {"status": vm.SOURCE_MISSING, "checked_at": "2026-09-26T00:00:00+00:00"},
        json.dumps({"status": vm.SOURCE_MISSING}),  # asyncpg hands jsonb back as text
    ],
)
async def test_a_recheck_that_is_still_blocked_is_not_news(tmp_path, prior):
    pool = _Pool()
    out = await vm.mirror_video_asset(
        pool, _R2(), _row(tmp_path, local=None, mirror_state=prior),
    )

    assert out.status == "blocked"
    assert out.newly_blocked is False
    assert len(pool.marks()) == 1  # checked_at still refreshes


async def test_a_blocked_row_whose_reason_changed_is_news_again(tmp_path):
    pool = _Pool()
    r2 = _R2({"video/post-1.mp4": 5})
    out = await vm.mirror_video_asset(
        pool, r2,
        _row(tmp_path, local=None, file_size_bytes=10, mirror_state={"status": vm.SOURCE_MISSING}),
    )
    assert out.reason == vm.MISMATCH
    assert out.newly_blocked is True


async def test_an_empty_local_file_counts_as_no_render(tmp_path):
    """An empty file can't be the approved render; publishing it swaps one
    dead enclosure for another."""
    pool = _Pool()
    r2 = _R2()
    out = await vm.mirror_video_asset(pool, r2, _row(tmp_path, local=b""))

    assert out.status == "blocked"
    assert out.reason == vm.SOURCE_MISSING
    assert r2.uploads == []


async def test_a_failed_upload_records_nothing_so_the_next_cycle_retries(tmp_path):
    pool = _Pool()
    out = await vm.mirror_video_asset(pool, _R2(upload_ok=False), _row(tmp_path))

    assert out.status == "error"
    assert pool.executed == []


async def test_a_stamp_that_matches_no_row_is_an_error(tmp_path):
    pool = _Pool(execute_result="UPDATE 0")
    out = await vm.mirror_video_asset(pool, _R2(), _row(tmp_path))
    assert out.status == "error"
    assert "no row" in out.reason


async def test_a_failed_stamp_is_an_error_not_a_crash(tmp_path):
    pool = _Pool()

    async def _boom(*_a: Any) -> str:
        raise RuntimeError("db gone")

    pool.execute = _boom  # type: ignore[method-assign]
    out = await vm.mirror_video_asset(pool, _R2(), _row(tmp_path))
    assert out.status == "error"
    assert "db gone" in out.reason


async def test_an_unanswerable_head_propagates_so_the_pass_can_stop(tmp_path):
    with pytest.raises(ObjectStoreUnavailable):
        await vm.mirror_video_asset(
            _Pool(), _R2(head_error=ObjectStoreUnavailable("403")), _row(tmp_path),
        )


# ---------------------------------------------------------------------------
# run_video_r2_mirror — the pass
# ---------------------------------------------------------------------------


async def _run(pool: _Pool, r2: _R2, sc: SiteConfig | None = None, *, limit: int = 20):
    findings: list[dict[str, Any]] = []
    with patch.object(vm, "R2UploadService", Mock(return_value=r2)), patch.object(
        vm, "emit_finding", Mock(side_effect=lambda **kw: findings.append(kw)),
    ):
        result = await vm.run_video_r2_mirror(pool, sc or _sc(), limit=limit)
    return result, findings


async def test_disabled_pass_touches_nothing():
    pool = _Pool()
    result, _ = await _run(pool, _R2(), _sc(video_r2_mirror_enabled="false"))
    assert result.skipped == "video_r2_mirror_enabled=false"
    assert pool.fetched == [] and pool.conn.calls == []


async def test_no_public_url_means_no_feed_and_no_pass():
    pool = _Pool()
    result, _ = await _run(pool, _R2(), _sc(storage_public_url=""))
    assert "storage_public_url" in result.skipped
    assert pool.fetched == []


async def test_an_overlapping_pass_skips_instead_of_uploading_twice():
    pool = _Pool(lock_result=False)
    result, _ = await _run(pool, _R2())
    assert result.skipped == "another mirror pass is running"
    assert pool.fetched == []


async def test_the_pass_selects_under_the_lock_and_releases_it(tmp_path):
    pool = _Pool([_row(tmp_path)])
    result, _ = await _run(pool, _R2(), _sc(video_r2_mirror_recheck_hours="6"), limit=7)

    assert result.count("uploaded") == 1
    assert result.delivered == 1
    ((sql, args),) = pool.fetched
    assert sql == vm._CANDIDATES_SQL
    assert args == (6, 7)  # recheck hours, then the cycle cap
    lock_sqls = [s for (s, _a) in pool.conn.calls]
    assert lock_sqls[0].startswith("SELECT pg_try_advisory_lock")
    assert lock_sqls[-1].startswith("SELECT pg_advisory_unlock")
    assert pool.conn.calls[0][1] == (vm._VIDEO_MIRROR_LOCK_NS, vm._VIDEO_MIRROR_LOCK_KEY)


async def test_the_lock_is_released_even_when_the_pass_fails():
    pool = _Pool(fetch_raises=RuntimeError("query failed"))
    result, _ = await _run(pool, _R2())
    assert result.skipped.startswith("pass failed")
    assert any("pg_advisory_unlock" in s for (s, _a) in pool.conn.calls)


async def test_an_unreachable_store_stops_the_pass_after_the_first_item(tmp_path):
    pool = _Pool([_row(tmp_path, post_id="p1"), _row(tmp_path, post_id="p2")])
    r2 = _R2(head_error=ObjectStoreUnavailable("HEAD video/p1.mp4 failed: 503"))

    result, findings = await _run(pool, r2)

    assert r2.heads == ["video/p1.mp4"]  # p2 never tried
    assert result.outcomes == []
    assert result.skipped.startswith("object store unavailable")
    assert pool.executed == [] and findings == []


async def test_one_bad_item_does_not_halt_the_rest(tmp_path):
    good = _row(tmp_path, post_id="good")
    bad: dict[str, Any] = {}  # malformed row: raises before any handled step
    pool = _Pool([bad, good])

    result, _ = await _run(pool, _R2())

    assert [o.status for o in result.outcomes] == ["error", "uploaded"]


async def test_newly_blocked_rows_raise_one_finding_for_the_set(tmp_path):
    rows = [
        _row(tmp_path, post_id="p-gone", local=None, file_size_bytes=10, title="Gone"),
        _row(tmp_path, post_id="p-other", local=None, file_size_bytes=20, title="Other"),
        _row(tmp_path, post_id="p-ok"),
    ]
    r2 = _R2({"video/p-other.mp4": 99})

    result, findings = await _run(_Pool(rows), r2)

    assert [o.status for o in result.outcomes] == ["blocked", "blocked", "uploaded"]
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "video_r2_mirror_blocked"
    assert f["severity"] == "warn"
    assert f["extra"]["post_ids"] == ["p-gone", "p-other"]
    assert f["extra"]["reasons"] == {"p-gone": vm.SOURCE_MISSING, "p-other": vm.MISMATCH}
    assert "poindexter media reject" in f["body"]  # a recovery the operator can run
    assert f["dedup_key"].startswith("video_r2_mirror_blocked:")


async def test_a_recheck_that_stays_blocked_raises_nothing(tmp_path):
    row = _row(tmp_path, local=None, mirror_state={"status": vm.SOURCE_MISSING})
    _, findings = await _run(_Pool([row]), _R2())
    assert findings == []


async def test_summary_reports_counts_and_a_partial_stop():
    r = vm.MirrorPassResult(outcomes=[vm.MirrorOutcome("p", "uploaded")], skipped="store down")
    assert r.summary() == "uploaded 1, stamped 0, blocked 0, errors 0; stopped: store down"
    assert vm.MirrorPassResult(skipped="off").summary() == "skipped (off)"


# ---------------------------------------------------------------------------
# The selection is the feed's selection
# ---------------------------------------------------------------------------


def test_candidates_are_the_feeds_unstamped_long_form_items():
    sql = vm._CANDIDATES_SQL
    # The video feed's own gates (routes/video_routes.py::video_feed).
    assert "p.status = 'published'" in sql
    assert "'video' = ANY(p.media_to_generate)" in sql
    assert "ma.medium = 'video'" in sql
    assert "ma.status = 'approved'" in sql
    assert "mas.type = 'video'" in sql
    assert "DISTINCT ON (p.id)" in sql
    # Only rows the feed would render through its fallback.
    assert "COALESCE(feed.url, '') = ''" in sql
    # Parked rows wait for their re-check.
    assert "make_interval(hours => $1)" in sql
    # Newest approval first, so an old backlog can't starve a fresh approval.
    assert "ORDER BY feed.approved_at DESC" in sql


def test_shorts_are_never_mirrored():
    """Shorts have no RSS surface, so a bucket copy would have no reader."""
    assert "video_short" not in vm._CANDIDATES_SQL
    assert "video_short" not in vm._STAMP_SQL
    assert "video_short" not in vm._MARK_SQL


def test_the_mirror_key_is_the_feed_key():
    assert video_episode_key("abc") == "video/abc.mp4"


def test_the_stamped_url_is_the_url_the_feed_already_advertised():
    """So stamping changes nothing in the rendered feed: the dead enclosure
    starts resolving at the URL subscribers already hold."""
    sc = SiteConfig(initial_config={"storage_public_url": "https://pub.example/"})
    feed_base = sc.get("storage_public_url").rstrip("/")  # video_routes._r2_url
    feed_url = f"{feed_base}/{video_episode_key('p1')}"
    assert R2UploadService(site_config=sc).object_url(video_episode_key("p1")) == feed_url


async def test_the_real_service_runs_the_pass_end_to_end(tmp_path, monkeypatch):
    """No fakes for the service seams: a real R2UploadService over a stubbed
    boto3 client, so the key, the content type and the stamped URL are the
    ones production would use."""
    from unittest.mock import MagicMock

    row = _row(tmp_path)
    s3 = MagicMock()
    err = type("ClientError", (Exception,), {})("not found")
    err.response = {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}
    s3.head_object.side_effect = err
    boto3 = MagicMock()
    boto3.client.return_value = s3
    monkeypatch.setitem(__import__("sys").modules, "boto3", boto3)

    sc = SiteConfig(initial_config={
        "storage_public_url": _BASE,
        "storage_access_key": "k",
        "storage_endpoint": "https://s3.example",
        "storage_bucket": "b",
    })

    async def _secret(key: str, default: str = "") -> str:
        return "secret" if key == "storage_secret_key" else default

    monkeypatch.setattr(sc, "get_secret", _secret)
    pool = _Pool([row])
    with patch.object(vm, "emit_finding", Mock()):
        result = await vm.run_video_r2_mirror(pool, sc, limit=5)

    assert result.count("uploaded") == 1
    args, kwargs = s3.upload_file.call_args
    assert args[1:] == ("b", "video/post-1.mp4")
    assert kwargs["ExtraArgs"]["ContentType"] == "video/mp4"
    assert pool.stamps() == [("asset-post-1", f"{_BASE}/video/post-1.mp4")]
