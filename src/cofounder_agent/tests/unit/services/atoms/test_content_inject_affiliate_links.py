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
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *a, **k):
        return self._rows


def _db(rows):
    class _DB:  # mimics state["database_service"].pool
        pool = _Pool(rows)

    return _DB()


async def test_noop_when_disabled():
    state = {"content": "We use Mercury.", "site_config": _Cfg(on=False),
             "database_service": _db([])}
    assert await atom.run(state) == {}


async def test_injects_when_enabled():
    rows = [{"code": "mercury", "keyword": "Mercury", "url": "https://x",
             "display_text": "Mercury"}]
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


def test_atom_metadata():
    assert atom.ATOM_META.name == "content.inject_affiliate_links"
    assert atom.ATOM_META.requires == ("content",)
    assert atom.ATOM_META.produces == ("content",)
