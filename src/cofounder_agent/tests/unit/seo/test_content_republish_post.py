"""Tests for content.republish_post atom.

Verifies:
- meta_only invariant (UPDATE posts SET must NOT touch content column)
- R2 export is called
- ISR revalidation is called
- seo_opportunities baseline stamp is issued
- missing post_id raises RuntimeError
"""
import pytest
from modules.content.atoms import content_republish_post as atom


@pytest.mark.asyncio
async def test_republish_updates_meta_exports_and_stamps(monkeypatch):
    calls = {"update": None, "export": None, "reval": None, "stamp": None}

    class _Conn:
        async def execute(self, sql, *args):
            if "UPDATE posts" in sql:
                calls["update"] = (sql, args)
                assert "content" not in sql.lower().split("set", 1)[1]  # body untouched
            elif "UPDATE seo_opportunities" in sql:
                calls["stamp"] = args

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Conn()

    class _DB:
        pool = _Pool()

    async def fake_export(pool, slug, *, site_config):
        calls["export"] = slug
        return True

    async def fake_reval(slug, paths=None, tags=None, *, site_config):
        calls["reval"] = slug
        return True

    monkeypatch.setattr(atom, "export_post", fake_export)
    monkeypatch.setattr(atom, "trigger_isr_revalidate", fake_reval)

    state = {
        "post_id": "11111111-1111-1111-1111-111111111111",
        "post_slug": "old-title",
        "seo_title": "New SEO",
        "seo_description": "New desc",
        "seo_keywords": "a, b",
        "seo_opportunity_id": "22222222-2222-2222-2222-222222222222",
        "database_service": _DB(),
        "site_config": object(),
    }
    out = await atom.run(state)
    assert calls["export"] == "old-title"
    assert calls["reval"] == "old-title"
    assert out["status"] == "refreshed"
    assert calls["stamp"] is not None  # baseline + status stamped


@pytest.mark.asyncio
async def test_missing_post_id_raises():
    with pytest.raises(RuntimeError):
        await atom.run(
            {
                "post_slug": "x",
                "database_service": type("D", (), {"pool": object()})(),
            }
        )
