"""GET /api/activity route — the thin adapter returns the service contract."""
import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


class _FakeConfig:
    def get(self, k, d=None):
        return {
            "live_activity_freshness_seconds": "120",
            "live_activity_recent_limit": "20",
        }.get(k, d)


async def test_activity_shape(test_pool):
    # The route is a thin adapter; assert it returns the service contract keys.
    from routes.activity_routes import _read_activity

    out = await _read_activity(test_pool, site_config=_FakeConfig())  # type: ignore[arg-type]  # duck-typed stub
    assert set(out.keys()) == {"running", "recent", "summary"}
    assert "running_by_kind" in out["summary"]
