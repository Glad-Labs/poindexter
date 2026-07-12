"""Unit tests for the content.inject_affiliate_links atom."""

from modules.content.atoms import content_inject_affiliate_links as atom


class _Cfg:
    def __init__(self, on=True):
        self._on = on

    def get_bool(self, k, d=False):
        return self._on if k == "affiliate_injection_enabled" else d

    def get(self, k, d=""):
        return "/go" if k == "affiliate_redirect_base_url" else d

    def get_int(self, k, d=0):
        return 3 if k == "affiliate_max_links_per_post" else d


class _Pool:
    def __init__(self, rows, last_used_rows=None):
        self._rows = rows
        self._last_used_rows = last_used_rows or []

    async def fetch(self, sql, *a, **k):
        if "posts" in sql:
            return self._last_used_rows
        return self._rows


def _db(rows, last_used_rows=None):
    class _DB:  # mimics state["database_service"].pool
        pool = _Pool(rows, last_used_rows)

    return _DB()


async def test_noop_when_disabled():
    state = {"content": "We use Mercury.", "site_config": _Cfg(on=False),
             "database_service": _db([])}
    assert await atom.run(state) == {}


async def test_injects_when_enabled():
    rows = [{"code": "mercury", "url": "https://x", "display_text": "Mercury",
             "platform": "", "keywords": ["Mercury"]}]
    state = {"content": "We use Mercury for banking.", "site_config": _Cfg(),
             "database_service": _db(rows)}
    out = await atom.run(state)
    assert out["content"] == "We use [Mercury](/go/mercury) for banking."


async def test_noop_when_no_pool():
    state = {"content": "We use Mercury.", "site_config": _Cfg()}
    assert await atom.run(state) == {}


async def test_noop_when_no_links():
    state = {"content": "We use Mercury.", "site_config": _Cfg(),
             "database_service": _db([])}
    assert await atom.run(state) == {}


async def test_computes_last_used_when_two_or_more_active_links():
    """Proves the atom actually issues the LRU (posts-joined) query for 2+
    active links — checking only the injected output isn't enough here,
    since a shared "Corsair" keyword with an empty last_used dict produces
    output indistinguishable from a correctly-populated one for this pair."""
    rows = [
        {"code": "a", "url": "https://x", "display_text": "A", "platform": "", "keywords": ["Corsair"]},
        {"code": "b", "url": "https://y", "display_text": "B", "platform": "", "keywords": ["Corsair"]},
    ]
    calls: list[str] = []

    class _TrackingPool:
        async def fetch(self, sql, *a, **k):
            calls.append(sql)
            if "posts" in sql:
                return [{"code": "a", "last_used": None}, {"code": "b", "last_used": None}]
            return rows

    class _DB:
        pool = _TrackingPool()

    state = {"content": "I love my Corsair gear.", "site_config": _Cfg(), "database_service": _DB()}
    await atom.run(state)
    assert any("posts" in c for c in calls)


async def test_skips_last_used_query_for_single_active_link():
    rows = [{"code": "mercury", "url": "https://x", "display_text": "Mercury",
             "platform": "", "keywords": ["Mercury"]}]

    class _NoLastUsedPool:
        async def fetch(self, sql, *a, **k):
            assert "posts" not in sql  # must never run the LRU query for 1 link
            return rows

    class _DB:
        pool = _NoLastUsedPool()

    state = {"content": "We use Mercury.", "site_config": _Cfg(), "database_service": _DB()}
    out = await atom.run(state)
    assert out["content"] == "We use [Mercury](/go/mercury)."


def test_atom_metadata():
    assert atom.ATOM_META.name == "content.inject_affiliate_links"
    assert atom.ATOM_META.requires == ("content",)
    assert atom.ATOM_META.produces == ("content",)
