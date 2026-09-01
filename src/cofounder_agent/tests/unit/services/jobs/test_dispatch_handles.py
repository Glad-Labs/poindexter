"""Unit tests for the shared media/podcast dispatch helpers.

Two contracts:

* :func:`claim_media_dispatch`, the per-``(post, medium)`` single-flight
  advisory-lock guard both dispatch lanes wrap their irreversible upload in. The
  concurrency behaviour (two overlapping passes upload once) is exercised
  end-to-end in ``test_media_distribute`` / ``test_podcast_distribute``; here we
  pin the guard's own contract in isolation.
* :func:`persist_platform_handles`, which captures what landed on the platform.
  Its distribution row is keyed ``(task_id, target, medium)`` — the medium is
  load-bearing, not decoration: one task delivers BOTH a long-form video and a
  Short to ``target='youtube'``, and the old two-column key made the second
  upsert overwrite the first (five of twelve prod rows; migration
  20260901_173133).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from services.jobs import dispatch_handles as dh
from services.jobs.dispatch_handles import (
    PlatformDispatchResult,
    claim_media_dispatch,
    persist_platform_handles,
)


class _LockConn:
    """asyncpg-conn stand-in routing ``fetchval`` on the SQL.

    ``lock_result`` is returned by ``pg_try_advisory_lock`` (True granted, False
    contended, None = mock/degraded pool). ``still`` is returned by the
    still-undispatched re-check (1 eligible, None already dispatched).
    ``unlock_raises`` makes ``pg_advisory_unlock`` raise so the swallow path is
    testable. Every call is appended to ``calls`` as ``(kind, args)``.
    """

    def __init__(self, *, lock_result: Any = True, still: Any = 1,
                 unlock_raises: bool = False) -> None:
        self._lock_result = lock_result
        self._still = still
        self._unlock_raises = unlock_raises
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "pg_try_advisory_lock" in sql:
            self.calls.append(("lock", args))
            return self._lock_result
        if "pg_advisory_unlock" in sql:
            self.calls.append(("unlock", args))
            if self._unlock_raises:
                raise RuntimeError("unlock boom")
            return True
        self.calls.append(("recheck", args))
        return self._still


class _LockPool:
    def __init__(self, conn: _LockConn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _kinds(conn: _LockConn) -> list[str]:
    return [c[0] for c in conn.calls]


@pytest.mark.asyncio
async def test_proceed_grants_rechecks_and_unlocks():
    """Lock granted + row still eligible → yield True, and the lock is released
    on exit (acquire → re-check → unlock, in order)."""
    conn = _LockConn(lock_result=True, still=1)
    seen = None
    async with claim_media_dispatch(_LockPool(conn), post_id="p1", medium="video") as proceed:
        seen = proceed
    assert seen is True
    assert _kinds(conn) == ["lock", "recheck", "unlock"]


@pytest.mark.asyncio
async def test_contended_skips_without_recheck_or_unlock():
    """A literal False from pg_try_advisory_lock means another session holds it:
    yield False, and NEVER re-check or unlock (we don't own the lock)."""
    conn = _LockConn(lock_result=False)
    seen = None
    async with claim_media_dispatch(_LockPool(conn), post_id="p1", medium="video") as proceed:
        seen = proceed
    assert seen is False
    assert _kinds(conn) == ["lock"]  # no recheck, no unlock


@pytest.mark.asyncio
async def test_already_dispatched_skips_but_still_unlocks():
    """Lock granted but the re-check finds the row already dispatched (a
    concurrent pass stamped it and released between our batch SELECT and now):
    yield False, but we DID acquire so we must unlock."""
    conn = _LockConn(lock_result=True, still=None)
    seen = None
    async with claim_media_dispatch(_LockPool(conn), post_id="p1", medium="video") as proceed:
        seen = proceed
    assert seen is False
    assert _kinds(conn) == ["lock", "recheck", "unlock"]


@pytest.mark.asyncio
async def test_lock_released_when_body_raises():
    """The upload/stamp body raising must not leak the advisory lock — the
    finally unlocks even on exception."""
    conn = _LockConn(lock_result=True, still=1)
    with pytest.raises(RuntimeError, match="body boom"):
        async with claim_media_dispatch(_LockPool(conn), post_id="p1", medium="video") as proceed:
            assert proceed is True
            raise RuntimeError("body boom")
    assert "unlock" in _kinds(conn)


@pytest.mark.asyncio
async def test_none_lock_result_treated_as_proceed_not_contended():
    """A mock/degraded pool returns None (not a real bool); only a literal False
    is contention, so None proceeds (and still re-checks + unlocks)."""
    conn = _LockConn(lock_result=None, still=1)
    seen = None
    async with claim_media_dispatch(_LockPool(conn), post_id="p1", medium="video") as proceed:
        seen = proceed
    assert seen is True
    assert _kinds(conn) == ["lock", "recheck", "unlock"]


@pytest.mark.asyncio
async def test_unlock_failure_is_swallowed():
    """A failing pg_advisory_unlock must not bubble — the session lock frees on
    disconnect anyway, so a raise here can't wedge the caller."""
    conn = _LockConn(lock_result=True, still=1, unlock_raises=True)
    async with claim_media_dispatch(_LockPool(conn), post_id="p1", medium="video") as proceed:
        assert proceed is True
    # Reaching here (no exception) is the assertion.
    assert _kinds(conn) == ["lock", "recheck", "unlock"]


@pytest.mark.asyncio
async def test_lock_key_is_namespace_plus_post_and_medium():
    """The lock is scoped per (post, medium): the namespace constant + the
    ``<post_id>:<medium>`` key, so a post's long form and short form don't block
    each other but two passes on the SAME (post, medium) serialize."""
    conn = _LockConn()
    async with claim_media_dispatch(_LockPool(conn), post_id="p9", medium="video_short"):
        pass
    lock_args = next(args for kind, args in conn.calls if kind == "lock")
    assert lock_args == (dh._MEDIA_DISPATCH_LOCK_NS, "p9:video_short")
    # Distinct from the social-approve namespace and the GPU key.
    assert dh._MEDIA_DISPATCH_LOCK_NS not in (0x50AC, 7_777_777_777)


def test_still_undispatched_sql_gates_on_undelivered_approved():
    """The re-check must confirm the row is STILL approved + never dispatched —
    the guard against a stale batch entry from a pass that already delivered."""
    sql = dh._STILL_UNDISPATCHED_SQL
    assert "dispatched_at IS NULL" in sql
    assert "status = 'approved'" in sql


@pytest.mark.asyncio
async def test_guard_is_exported_and_used_by_both_lanes():
    """Both dispatch lanes must import the SAME guard (single source of truth)."""
    from services.jobs import media_distribute, podcast_distribute

    assert media_distribute.claim_media_dispatch is claim_media_dispatch
    assert podcast_distribute.claim_media_dispatch is claim_media_dispatch
    assert "claim_media_dispatch" in dh.__all__


# --------------------------------------------------------------------------
# persist_platform_handles
# --------------------------------------------------------------------------


class _RecordingConn:
    """Captures every ``execute`` so the two writes can be told apart by SQL."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, *args: Any) -> None:
        self.calls.append((sql, *args))

    def _of(self, needle: str) -> list[tuple[Any, ...]]:
        return [c for c in self.calls if needle in c[0]]


def _ok(platform: str = "youtube", external_id: str = "VID1") -> PlatformDispatchResult:
    return PlatformDispatchResult(
        platform=platform, success=True, external_id=external_id,
        url=f"https://www.youtube.com/watch?v={external_id}",
    )


@pytest.mark.asyncio
async def test_distribution_row_carries_the_medium():
    """The medium the caller dispatched is written to the row, in the position
    the upsert's conflict target reads."""
    conn = _RecordingConn()
    await persist_platform_handles(
        conn, post_id="post-1", medium="video_short",
        asset_id="asset-1", task_id="task-1", results=[_ok()],
    )
    sql, *args = conn._of("pipeline_distributions")[0]
    assert args[:3] == ["task-1", "youtube", "video_short"]
    assert args[3] == "VID1"


@pytest.mark.asyncio
async def test_upsert_conflict_target_includes_medium():
    """The regression guard. ``ON CONFLICT (task_id, target)`` is what made a
    post's Short overwrite its long form; the key must name the medium."""
    assert "ON CONFLICT (task_id, target, medium)" in dh._RECORD_DISTRIBUTION_SQL
    assert "ON CONFLICT (task_id, target)" not in dh._RECORD_DISTRIBUTION_SQL


@pytest.mark.asyncio
async def test_long_form_and_short_write_two_distinct_rows():
    """One task, one target, two renders → two distribution writes that differ
    only in medium + handle. Under the old key these were one row and the
    second upload's id was lost."""
    conn = _RecordingConn()
    await persist_platform_handles(
        conn, post_id="post-1", medium="video",
        asset_id="asset-long", task_id="task-1", results=[_ok(external_id="LONG")],
    )
    await persist_platform_handles(
        conn, post_id="post-1", medium="video_short",
        asset_id="asset-short", task_id="task-1", results=[_ok(external_id="SHORT")],
    )
    rows = conn._of("pipeline_distributions")
    assert [(r[1], r[2], r[3], r[4]) for r in rows] == [
        ("task-1", "youtube", "video", "LONG"),
        ("task-1", "youtube", "video_short", "SHORT"),
    ]
    # …and each render's handle lands on its OWN asset row, not one shared one.
    merges = conn._of("platform_video_ids")
    assert [m[1] for m in merges] == ["asset-long", "asset-short"]
    assert [json.loads(m[2]) for m in merges] == [
        {"youtube": "LONG"}, {"youtube": "SHORT"},
    ]


@pytest.mark.asyncio
async def test_failed_or_handleless_results_write_nothing():
    """No id means nothing to record — a failed upload must not leave a row
    claiming it published."""
    conn = _RecordingConn()
    await persist_platform_handles(
        conn, post_id="post-1", medium="video", asset_id="a", task_id="t",
        results=[
            PlatformDispatchResult(platform="youtube", success=False),
            PlatformDispatchResult(platform="youtube", success=True, external_id=None),
        ],
    )
    assert conn.calls == []


@pytest.mark.asyncio
async def test_missing_task_id_still_merges_the_asset_handle(caplog):
    """task_id is the distribution row's NOT NULL FK, so without it the row is
    skipped — but the handle must still reach media_assets, which is the source
    that did not lose data when the distribution rows collided."""
    conn = _RecordingConn()
    with caplog.at_level("WARNING"):
        await persist_platform_handles(
            conn, post_id="post-1", medium="video_short",
            asset_id="asset-1", task_id=None, results=[_ok()],
        )
    assert conn._of("pipeline_distributions") == []
    assert len(conn._of("platform_video_ids")) == 1
    assert "video_short" in caplog.text
