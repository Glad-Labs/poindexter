"""Unit tests for niche_service.get_known_niche_slugs (#729).

``asyncio_mode = "auto"`` (pyproject.toml) auto-marks coroutine tests,
so no ``@pytest.mark.asyncio`` is needed.

The #729 publish backstop allowlists *known* niches (any ``niches`` row),
not just ``active`` ones, so a deliberately discovery-inactive niche like
``dev_diary`` (website-post only, no topic sweep / media backfill) stays
publishable. These tests pin that contract — including that the query does
NOT filter on ``active``.
"""

from services.niche_service import get_known_niche_slugs


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.queries: list[str] = []

    async def fetch(self, query):
        self.queries.append(query)
        return self._rows


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, rows=None, raises=None, conn=None):
        self._conn = conn or _FakeConn(rows or [])
        self._raises = raises

    def acquire(self):
        if self._raises:
            raise self._raises
        return _FakeAcquire(self._conn)


async def test_known_niche_slugs_returns_all_slugs():
    pool = _FakePool(
        [{"slug": "glad-labs"}, {"slug": "dev_diary"}, {"slug": None}]
    )
    assert await get_known_niche_slugs(pool) == {"glad-labs", "dev_diary"}


async def test_known_niche_slugs_includes_inactive_niches():
    """#729 fix: the query must not filter on ``active`` -- a known but
    discovery-inactive niche (dev_diary) must still be returned so it
    remains publishable."""
    conn = _FakeConn([{"slug": "glad-labs"}, {"slug": "dev_diary"}])
    pool = _FakePool(conn=conn)

    result = await get_known_niche_slugs(pool)

    assert result == {"glad-labs", "dev_diary"}
    assert conn.queries, "expected a fetch call"
    assert "where active" not in conn.queries[-1].lower(), (
        "get_known_niche_slugs must select ALL niches, not just active ones "
        "(#729: known-but-inactive niches like dev_diary stay publishable)"
    )


async def test_known_niche_slugs_none_pool_is_empty():
    assert await get_known_niche_slugs(None) == set()


async def test_known_niche_slugs_failopen_on_error():
    pool = _FakePool(raises=RuntimeError("db down"))
    assert await get_known_niche_slugs(pool) == set()
