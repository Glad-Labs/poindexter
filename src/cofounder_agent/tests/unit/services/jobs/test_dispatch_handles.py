"""Unit tests for the shared media/podcast dispatch helpers.

Focus: :func:`claim_media_dispatch`, the per-``(post, medium)`` single-flight
advisory-lock guard both dispatch lanes wrap their irreversible upload in. The
concurrency behaviour (two overlapping passes upload once) is exercised
end-to-end in ``test_media_distribute`` / ``test_podcast_distribute``; here we
pin the guard's own contract in isolation.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.jobs import dispatch_handles as dh
from services.jobs.dispatch_handles import claim_media_dispatch


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
