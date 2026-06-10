"""Unit tests for niche_service.get_active_niche_slugs (#729).

``asyncio_mode = "auto"`` (pyproject.toml) auto-marks coroutine tests,
so no ``@pytest.mark.asyncio`` is needed.
"""

from services.niche_service import get_active_niche_slugs


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, _query):
        return self._rows


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, rows=None, raises=None):
        self._rows = rows or []
        self._raises = raises

    def acquire(self):
        if self._raises:
            raise self._raises
        return _FakeAcquire(_FakeConn(self._rows))


async def test_active_niche_slugs_returns_active_set():
    pool = _FakePool(
        [{"slug": "glad-labs"}, {"slug": "dev_diary"}, {"slug": None}]
    )
    assert await get_active_niche_slugs(pool) == {"glad-labs", "dev_diary"}


async def test_active_niche_slugs_none_pool_is_empty():
    assert await get_active_niche_slugs(None) == set()


async def test_active_niche_slugs_failopen_on_error():
    pool = _FakePool(raises=RuntimeError("db down"))
    assert await get_active_niche_slugs(pool) == set()
