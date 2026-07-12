"""Unit tests for CSV bulk-import (no real DB, no real LLM)."""

from __future__ import annotations

import csv
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.affiliate_import import (
    _derive_display_and_keywords, _map_category, _map_is_active, import_csv, slugify_code,
)


def test_slugify_code_is_deterministic():
    name = "ASUS ROG Astral NVIDIA GeForce RTX 5090 32GB GDDR7 OC Edition"
    assert slugify_code(name) == slugify_code(name)
    assert slugify_code(name) == "asus-rog-astral-nvidia-geforce-rtx"


def test_slugify_code_strips_punctuation_and_truncates():
    assert slugify_code("Corsair HX1500i (2025) Fully Modular!!") == "corsair-hx1500i-2025-fully-modular"


@pytest.mark.parametrize("status,expected", [
    ("Active", True), ("active", True), ("ACTIVE", True),
    ("Inactive", False), ("Paused", False), ("", False),
])
def test_map_is_active(status, expected):
    assert _map_is_active(status) is expected


@pytest.mark.parametrize("category,expected", [
    ("Service", "service"), ("service", "service"),
    ("Hardware", "product"), ("", "product"), ("Anything Else", "product"),
])
def test_map_category(category, expected):
    assert _map_category(category) == expected


class _RaisingSiteConfig:
    def get(self, key, default=""):
        return default

    def get_int(self, key, default=0):
        return default


async def test_derive_display_and_keywords_fails_open_on_llm_error():
    with patch(
        "modules.content.affiliate_import.ollama_chat_text",
        AsyncMock(side_effect=RuntimeError("ollama unreachable")),
    ):
        display_text, keywords = await _derive_display_and_keywords(
            title="Widget Pro 9000 Long Marketing Title Here",
            description="x", site_config=_RaisingSiteConfig(), pool=object(),
        )
    assert display_text == "Widget Pro 9000 Long Marketing Title Here"[:60]
    assert keywords == []


class _FakePool:
    def __init__(self, existing_codes=()):
        self._existing = set(existing_codes)

    async def fetchval(self, sql, code):
        return 1 if code in self._existing else None


def _write_csv(tmp_path, rows):
    path = tmp_path / "sheet.csv"
    fieldnames = [
        "Status", "Product Name", "Category", "Platform",
        "Commission Rate", "Affiliate Link", "Description", "Promo Code",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{k: "" for k in fieldnames}, **row})
    return str(path)


async def test_import_creates_new_row_with_llm_keywords(tmp_path):
    csv_path = _write_csv(tmp_path, [{
        "Status": "Active", "Product Name": "Widget Pro 9000",
        "Category": "Hardware", "Platform": "Amazon",
        "Affiliate Link": "https://amazon.com/x", "Description": "A great widget.",
    }])
    pool = _FakePool()
    calls = {}

    async def _fake_add_link(pool, **kwargs):
        calls["add_link"] = kwargs

    async def _fake_set_active(pool, code, active):
        calls["set_active"] = (code, active)
        return True

    with patch(
        "modules.content.affiliate_import._derive_display_and_keywords",
        AsyncMock(return_value=("Widget Pro", ["Widget Pro", "Pro 9000"])),
    ), patch("modules.content.affiliate_links.add_link", _fake_add_link), \
       patch("modules.content.affiliate_links.set_active", _fake_set_active):
        report = await import_csv(pool, csv_path, site_config=object())

    assert len(report.created) == 1
    assert calls["add_link"]["keywords"] == ["Widget Pro", "Pro 9000"]
    assert calls["add_link"]["platform"] == "Amazon"
    assert calls["add_link"]["category"] == "product"
    assert calls["set_active"] == ("widget-pro-9000", True)


async def test_import_skips_existing_code_by_default(tmp_path):
    csv_path = _write_csv(tmp_path, [{
        "Status": "Active", "Product Name": "Widget Pro 9000", "Description": "",
    }])
    pool = _FakePool(existing_codes=["widget-pro-9000"])
    report = await import_csv(pool, csv_path, site_config=object())
    assert len(report.skipped) == 1
    assert report.created == []


async def test_import_force_overwrites_existing_code(tmp_path):
    csv_path = _write_csv(tmp_path, [{
        "Status": "Active", "Product Name": "Widget Pro 9000", "Description": "Updated.",
    }])
    pool = _FakePool(existing_codes=["widget-pro-9000"])
    calls = {}

    async def _fake_add_link(pool, **kwargs):
        calls["add_link"] = kwargs

    async def _fake_set_active(pool, code, active):
        return True

    with patch(
        "modules.content.affiliate_import._derive_display_and_keywords",
        AsyncMock(return_value=("Widget Pro", ["Widget"])),
    ), patch("modules.content.affiliate_links.add_link", _fake_add_link), \
       patch("modules.content.affiliate_links.set_active", _fake_set_active):
        report = await import_csv(pool, csv_path, site_config=object(), force=True)

    assert len(report.created) == 1
    assert calls["add_link"] is not None


async def test_import_uses_fallback_keyword_when_derivation_returns_none(tmp_path):
    csv_path = _write_csv(tmp_path, [{
        "Status": "Active", "Product Name": "Widget Pro 9000", "Description": "x",
    }])
    pool = _FakePool()
    calls = {}

    async def _fake_add_link(pool, **kwargs):
        calls["add_link"] = kwargs

    async def _fake_set_active(pool, code, active):
        return True

    with patch(
        "modules.content.affiliate_import._derive_display_and_keywords",
        AsyncMock(return_value=("Widget Pro 9000", [])),
    ), patch("modules.content.affiliate_links.add_link", _fake_add_link), \
       patch("modules.content.affiliate_links.set_active", _fake_set_active):
        report = await import_csv(pool, csv_path, site_config=object())

    assert len(report.created) == 1
    assert calls["add_link"]["keywords"] == ["Widget Pro 9000"]


async def test_import_add_link_failure_produces_error_row_not_batch_abort(tmp_path):
    csv_path = _write_csv(tmp_path, [
        {"Status": "Active", "Product Name": "Widget One", "Description": "x"},
        {"Status": "Active", "Product Name": "Widget Two", "Description": "y"},
    ])
    pool = _FakePool()

    async def _failing_add_link(pool, **kwargs):
        if kwargs["code"] == "widget-one":
            raise RuntimeError("db blip")

    async def _fake_set_active(pool, code, active):
        return True

    with patch(
        "modules.content.affiliate_import._derive_display_and_keywords",
        AsyncMock(return_value=("Name", ["kw"])),
    ), patch("modules.content.affiliate_links.add_link", _failing_add_link), \
       patch("modules.content.affiliate_links.set_active", _fake_set_active):
        report = await import_csv(pool, csv_path, site_config=object())

    assert len(report.errors) == 1
    assert report.errors[0].code == "widget-one"
    assert len(report.created) == 1
    assert report.created[0].code == "widget-two"


async def test_import_skips_blank_product_name(tmp_path):
    csv_path = _write_csv(tmp_path, [{"Status": "Active", "Product Name": "", "Description": "x"}])
    report = await import_csv(_FakePool(), csv_path, site_config=object())
    assert report.rows == []
