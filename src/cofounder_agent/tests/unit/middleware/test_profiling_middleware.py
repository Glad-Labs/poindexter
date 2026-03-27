"""
Unit tests for middleware/profiling_middleware.py — ProfileData and ProfilingMiddleware
"""

import time
from unittest.mock import MagicMock

import pytest

from middleware.profiling_middleware import ProfileData, ProfilingMiddleware

# ---------------------------------------------------------------------------
# ProfileData — data structure behaviour
# ---------------------------------------------------------------------------


class TestProfileData:
    def test_initial_state(self):
        p = ProfileData("/api/tasks", "GET")
        assert p.endpoint == "/api/tasks"
        assert p.method == "GET"
        assert p.status_code is None
        assert p.duration_ms == 0
        assert p.is_slow is False

    def test_complete_sets_status_code(self):
        p = ProfileData("/api/tasks", "POST")
        p.complete(201)
        assert p.status_code == 201

    def test_complete_measures_duration(self):
        p = ProfileData("/api/tasks", "GET")
        # Back-date start_time instead of sleeping so the test is deterministic.
        p.start_time = time.time() - 0.100  # 100ms in the past
        p.complete(200)
        assert p.duration_ms >= 50  # at least 50ms

    def test_complete_marks_slow_when_exceeds_threshold(self):
        p = ProfileData("/api/tasks", "GET")
        # Manually set start_time far in the past
        p.start_time = time.time() - 2.0  # 2 seconds ago
        p.complete(200)
        assert p.is_slow is True

    def test_complete_not_slow_when_under_threshold(self):
        p = ProfileData("/api/tasks", "GET")
        p.complete(200)
        # Should be very fast (sub-millisecond)
        assert p.is_slow is False

    def test_to_dict_contains_expected_keys(self):
        p = ProfileData("/api/posts", "GET")
        p.complete(200)
        d = p.to_dict()
        assert d["endpoint"] == "/api/posts"
        assert d["method"] == "GET"
        assert d["status_code"] == 200
        assert isinstance(d["duration_ms"], float)
        assert isinstance(d["is_slow"], bool)
        assert "timestamp" in d

    def test_to_dict_duration_rounded(self):
        p = ProfileData("/api/tasks", "GET")
        p.complete(200)
        # duration_ms should be rounded to 2 decimal places
        assert round(p.to_dict()["duration_ms"], 2) == p.to_dict()["duration_ms"]


# ---------------------------------------------------------------------------
# ProfilingMiddleware — dispatch behaviour
# ---------------------------------------------------------------------------


def _make_mw():
    app = MagicMock()
    return ProfilingMiddleware(app)


def _make_request(path="/api/tasks", method="GET"):
    req = MagicMock()
    req.url.path = path
    req.method = method
    return req


class TestProfilingMiddlewareDispatch:
    @pytest.mark.asyncio
    async def test_health_path_bypasses_profiling(self):
        mw = _make_mw()
        req = _make_request("/health")

        async def call_next(r):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        await mw.dispatch(req, call_next)
        assert len(mw.profiles) == 0  # no profile stored

    @pytest.mark.asyncio
    async def test_metrics_path_bypasses_profiling(self):
        mw = _make_mw()
        req = _make_request("/metrics")

        async def call_next(r):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        await mw.dispatch(req, call_next)
        assert len(mw.profiles) == 0

    @pytest.mark.asyncio
    async def test_normal_request_stores_profile(self):
        mw = _make_mw()
        req = _make_request("/api/tasks")

        async def call_next(r):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        await mw.dispatch(req, call_next)
        assert len(mw.profiles) == 1
        assert mw.profiles[0].endpoint == "/api/tasks"
        assert mw.profiles[0].status_code == 200

    @pytest.mark.asyncio
    async def test_exception_marks_profile_as_500(self):
        mw = _make_mw()
        req = _make_request("/api/tasks")

        async def call_next(r):
            raise RuntimeError("Boom")

        with pytest.raises(RuntimeError):
            await mw.dispatch(req, call_next)

        assert len(mw.profiles) == 1
        assert mw.profiles[0].status_code == 500
        assert mw.profiles[0].is_slow is True


# ---------------------------------------------------------------------------
# Profile storage limits
# ---------------------------------------------------------------------------


class TestProfileStorageLimits:
    @pytest.mark.asyncio
    async def test_profiles_capped_at_max_profiles(self):
        mw = _make_mw()
        mw.max_profiles = 5  # lower cap for testing

        async def call_next(r):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        for i in range(10):
            req = _make_request(f"/api/tasks/{i}")
            await mw.dispatch(req, call_next)

        assert len(mw.profiles) == 5

    @pytest.mark.asyncio
    async def test_slow_endpoints_tracked(self):
        mw = _make_mw()

        async def slow_call_next(r):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        req = _make_request("/api/tasks")

        await mw.dispatch(req, slow_call_next)

        # Manually mark as slow to test the tracking
        profile = mw.profiles[-1]
        profile.is_slow = True
        profile.duration_ms = 1500

        # Call _store_profile again to trigger slow tracking
        mw._store_profile(profile)

        assert "/api/tasks" in mw.slow_endpoints

    def test_slow_endpoint_history_capped_at_100(self):
        mw = _make_mw()

        for i in range(110):
            p = ProfileData("/api/slow", "GET")
            p.complete(200)
            p.is_slow = True
            p.duration_ms = 2000
            mw._store_profile(p)

        assert len(mw.slow_endpoints.get("/api/slow", [])) <= 100


# ---------------------------------------------------------------------------
# get_recent_profiles() / get_slow_endpoints()
# ---------------------------------------------------------------------------


class TestQueryMethods:
    @pytest.mark.asyncio
    async def test_get_recent_profiles_returns_dicts(self):
        mw = _make_mw()
        req = _make_request("/api/tasks")

        async def call_next(r):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        await mw.dispatch(req, call_next)
        profiles = mw.get_recent_profiles(limit=10)
        assert len(profiles) == 1
        assert isinstance(profiles[0], dict)
        assert profiles[0]["endpoint"] == "/api/tasks"

    @pytest.mark.asyncio
    async def test_get_recent_profiles_respects_limit(self):
        mw = _make_mw()

        async def call_next(r):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        for i in range(5):
            req = _make_request(f"/api/tasks/{i}")
            await mw.dispatch(req, call_next)

        profiles = mw.get_recent_profiles(limit=3)
        assert len(profiles) == 3

    def test_get_slow_endpoints_returns_stats(self):
        mw = _make_mw()

        for _ in range(3):
            p = ProfileData("/api/slow", "GET")
            p.complete(200)
            p.duration_ms = 1500
            p.is_slow = True
            mw._store_profile(p)

        slow = mw.get_slow_endpoints()
        assert "/api/slow" in slow
        assert slow["/api/slow"]["count"] == 3
        assert slow["/api/slow"]["avg_duration_ms"] == pytest.approx(1500, abs=1)

    def test_get_slow_endpoints_empty_when_none(self):
        mw = _make_mw()
        slow = mw.get_slow_endpoints()
        assert slow == {}
