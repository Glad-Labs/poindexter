"""T7 — static-export affiliate surface.

Covers the two export-time seams the affiliate feature adds:

* ``_post_full`` derives ``has_affiliate_links`` from the rendered HTML (presence
  of the ``/go`` redirect path) and only attaches ``affiliate_disclosure_text``
  when a link is actually present — the frontend banner is thereby conditional.
* ``_export_affiliate_links`` uploads ``affiliate-links.json`` (the code→url map
  the /go Worker resolves) and MUST fail open — a DB hiccup on the affiliate map
  can never abort a site export.
"""

from __future__ import annotations

import json

import services.static_export_service as ses
from services.site_config import SiteConfig

_DISCLOSURE = "Some links are affiliate links — Glad Labs may earn a commission."


def _post(content: str) -> dict:
    return {"id": "1", "title": "T", "slug": "s", "content": content}


def test_post_full_flags_and_attaches_disclosure_when_link_present():
    out = ses._post_full(
        _post("Use [Mercury](/go/mercury) for banking."),
        affiliate_base="/go",
        disclosure_text=_DISCLOSURE,
    )
    assert out["has_affiliate_links"] is True
    assert out["affiliate_disclosure_text"] == _DISCLOSURE
    # summary fields still present (delegates to _post_summary)
    assert out["slug"] == "s"
    assert "<a href=\"/go/mercury\">" in out["content"]


def test_post_full_no_link_no_flag_no_disclosure():
    out = ses._post_full(
        _post("A plain paragraph with no affiliate links at all."),
        affiliate_base="/go",
        disclosure_text=_DISCLOSURE,
    )
    assert out["has_affiliate_links"] is False
    assert "affiliate_disclosure_text" not in out


def test_post_full_link_present_but_disclosure_text_empty():
    # A configured link but a blank disclosure string: still flag the post so
    # the frontend knows, but do not attach an empty banner string.
    out = ses._post_full(
        _post("Try [Grafana](/go/grafana) today."),
        affiliate_base="/go",
        disclosure_text="",
    )
    assert out["has_affiliate_links"] is True
    assert "affiliate_disclosure_text" not in out


def test_post_full_respects_custom_absolute_base():
    out = ses._post_full(
        _post("Sign up with [Mercury](https://go.gladlabs.io/mercury)."),
        affiliate_base="https://go.gladlabs.io",
        disclosure_text=_DISCLOSURE,
    )
    assert out["has_affiliate_links"] is True


def test_post_full_default_base_is_go():
    # Defaults matter — a caller that forgets to thread config still gets /go.
    out = ses._post_full(_post("Read [X](/go/x)."))
    assert out["has_affiliate_links"] is True


class _FakePool:
    def __init__(self, rows=None, raise_exc=None):
        self._rows = rows or []
        self._raise = raise_exc

    async def fetch(self, sql, *args):
        if self._raise is not None:
            raise self._raise
        return self._rows


async def test_export_affiliate_links_uploads_code_url_map(monkeypatch):
    captured: dict = {}

    async def _fake_upload(key, data, content_type="application/json", *, site_config=None):
        captured["key"] = key
        captured["data"] = data
        return f"https://cdn.example/{key}"

    monkeypatch.setattr(ses, "_upload_json", _fake_upload)

    pool = _FakePool(rows=[
        {"code": "mercury", "url": "https://mercury.com/r/glad-labs"},
        {"code": "gworkspace", "url": "https://referworkspace.app.goo.gl/uUQH"},
    ])
    await ses._export_affiliate_links(pool, site_config=SiteConfig(initial_config={}))

    assert captured["key"] == "affiliate-links.json"
    mapping = json.loads(captured["data"])
    assert mapping == {
        "mercury": {"url": "https://mercury.com/r/glad-labs"},
        "gworkspace": {"url": "https://referworkspace.app.goo.gl/uUQH"},
    }


async def test_export_affiliate_links_fails_open_on_db_error(monkeypatch):
    uploads: list = []

    async def _fake_upload(key, data, content_type="application/json", *, site_config=None):
        uploads.append(key)
        return "x"

    monkeypatch.setattr(ses, "_upload_json", _fake_upload)

    pool = _FakePool(raise_exc=RuntimeError("affiliate_links table missing"))
    # Must not raise — the export continues even if the affiliate map fails.
    await ses._export_affiliate_links(pool, site_config=SiteConfig(initial_config={}))
    assert uploads == []


async def test_export_affiliate_referrals_uploads_display_fields(monkeypatch):
    captured: dict = {}

    async def _fake_upload(key, data, content_type="application/json", *, site_config=None):
        captured["key"] = key
        captured["data"] = data
        return f"https://cdn.example/{key}"

    monkeypatch.setattr(ses, "_upload_json", _fake_upload)

    pool = _FakePool(rows=[
        {"code": "mercury", "name": "Mercury", "description": "Business banking.", "category": "service"},
        {"code": "prime-gaming", "name": "Prime Gaming", "description": "Free games monthly.", "category": "product"},
    ])
    await ses._export_affiliate_referrals(pool, site_config=SiteConfig(initial_config={}))

    assert captured["key"] == "affiliate-referrals.json"
    referrals = json.loads(captured["data"])
    assert referrals == [
        {"code": "mercury", "name": "Mercury", "description": "Business banking.", "category": "service"},
        {"code": "prime-gaming", "name": "Prime Gaming", "description": "Free games monthly.", "category": "product"},
    ]


async def test_export_affiliate_referrals_fails_open_on_db_error(monkeypatch):
    uploads: list = []

    async def _fake_upload(key, data, content_type="application/json", *, site_config=None):
        uploads.append(key)
        return "x"

    monkeypatch.setattr(ses, "_upload_json", _fake_upload)

    pool = _FakePool(raise_exc=RuntimeError("affiliate_links table missing"))
    # Must not raise — the export continues even if the referrals data fails.
    await ses._export_affiliate_referrals(pool, site_config=SiteConfig(initial_config={}))
    assert uploads == []


async def test_republish_affiliate_exports_uploads_both_files(monkeypatch):
    uploaded_keys: list = []

    async def _fake_upload(key, data, content_type="application/json", *, site_config=None):
        uploaded_keys.append(key)
        return f"https://cdn.example/{key}"

    monkeypatch.setattr(ses, "_upload_json", _fake_upload)

    pool = _FakePool(rows=[
        {"code": "mercury", "url": "https://mercury.com/r/glad-labs",
         "name": "Mercury", "description": "Business banking.", "category": "service"},
    ])
    await ses.republish_affiliate_exports(pool, site_config=SiteConfig(initial_config={}))

    assert uploaded_keys == ["affiliate-links.json", "affiliate-referrals.json"]
