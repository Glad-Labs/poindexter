"""Tests for ``poindexter.services.local_site``, the settings side of local mode.

The resolver's defaults carry the upgrade safety: a missing or blank
``storage_provider`` row must mean ``s3``, or a flow run that loads new code
before the pinning migration runs would move a live site's uploads to a folder.
The identity fill must only fill empty values, and only in local mode.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from poindexter.services import local_site
from poindexter.services.local_site import (
    DEFAULT_LOCAL_DIR,
    LOCAL_SITE_MOUNT_PATH,
    PLACEHOLDER_SITE_NAME,
    fill_local_site_identity,
    is_local,
    local_object_store,
    local_public_base,
    local_site_url,
    read_local_object,
    storage_provider,
)
from poindexter.services.site_config import SiteConfig

_PKG = Path(local_site.__file__).resolve().parents[1]


def _cfg(**values: str) -> SiteConfig:
    return SiteConfig(initial_config=dict(values))


# ---------------------------------------------------------------------------
# storage_provider
# ---------------------------------------------------------------------------


def test_missing_row_means_s3(monkeypatch):
    monkeypatch.delenv("STORAGE_PROVIDER", raising=False)
    assert storage_provider(_cfg()) == "s3"


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_value_means_s3(raw):
    assert storage_provider(_cfg(storage_provider=raw)) == "s3"


@pytest.mark.parametrize(
    "raw,expected", [("local", "local"), (" LOCAL ", "local"), ("s3", "s3"), ("S3", "s3")]
)
def test_valid_values_are_normalised(raw, expected):
    assert storage_provider(_cfg(storage_provider=raw)) == expected


def test_unknown_value_logs_once_and_behaves_as_s3(caplog):
    local_site._reported_invalid.discard("minio")
    cfg = _cfg(storage_provider="minio")
    with caplog.at_level(logging.ERROR):
        assert storage_provider(cfg) == "s3"
        assert storage_provider(cfg) == "s3"
    errors = [r for r in caplog.records if "storage_provider" in r.getMessage()]
    assert len(errors) == 1


def test_a_non_string_config_value_is_s3():
    """A bare MagicMock site_config (common in older tests) must not flip mode."""
    assert storage_provider(MagicMock()) == "s3"
    assert is_local(MagicMock()) is False


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def test_local_site_url_is_api_url_plus_mount():
    assert local_site_url(_cfg(api_url="http://localhost:8002/")) == "http://localhost:8002/site"
    assert LOCAL_SITE_MOUNT_PATH == "/site"


def test_local_site_url_is_empty_without_api_url():
    assert local_site_url(_cfg()) == ""


@pytest.mark.parametrize(
    "values,expected",
    [
        (
            {
                "storage_public_url": "https://cdn.example/x/",
                "public_site_url": "https://b",
                "site_url": "https://c",
                "api_url": "http://d",
            },
            "https://cdn.example/x",
        ),
        (
            {"public_site_url": "https://b/", "site_url": "https://c", "api_url": "http://d"},
            "https://b",
        ),
        ({"site_url": "https://c", "api_url": "http://d"}, "https://c"),
        ({"api_url": "http://d"}, "http://d/site"),
        ({}, ""),
    ],
)
def test_public_base_precedence(values, expected):
    assert local_public_base(_cfg(**values)) == expected


def test_store_root_defaults_when_the_setting_is_blank(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    store = local_object_store(_cfg(storage_local_dir="  ", api_url="http://a"))
    assert store.root == Path(DEFAULT_LOCAL_DIR).expanduser()


def test_store_uses_the_configured_root_and_base(tmp_path):
    store = local_object_store(_cfg(storage_local_dir=str(tmp_path), site_url="http://h/site"))
    assert store.root == tmp_path
    assert store.url_for("a.json") == "http://h/site/a.json"


# ---------------------------------------------------------------------------
# read_local_object
# ---------------------------------------------------------------------------


@pytest.fixture
def local_cfg(tmp_path) -> SiteConfig:
    return _cfg(
        storage_provider="local",
        storage_local_dir=str(tmp_path / "site"),
        api_url="http://localhost:8002",
    )


@pytest.mark.asyncio
async def test_read_local_object_reads_a_stored_file(local_cfg, tmp_path):
    target = tmp_path / "site" / "images" / "inline" / "a.webp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"RIFF1234WEBP")
    data = await read_local_object("http://localhost:8002/site/images/inline/a.webp", local_cfg)
    assert data == b"RIFF1234WEBP"


@pytest.mark.asyncio
async def test_read_local_object_ignores_urls_outside_the_base(local_cfg):
    assert await read_local_object("https://images.pexels.com/photo.jpeg", local_cfg) is None


@pytest.mark.asyncio
async def test_read_local_object_is_none_for_a_missing_object(local_cfg):
    assert (
        await read_local_object("http://localhost:8002/site/images/missing.webp", local_cfg) is None
    )


@pytest.mark.asyncio
async def test_read_local_object_does_nothing_in_s3_mode(tmp_path):
    target = tmp_path / "site" / "a.webp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    cfg = _cfg(
        storage_provider="s3",
        storage_local_dir=str(tmp_path / "site"),
        api_url="http://localhost:8002",
    )
    assert await read_local_object("http://localhost:8002/site/a.webp", cfg) is None


@pytest.mark.asyncio
async def test_read_local_object_refuses_traversal(local_cfg, tmp_path):
    (tmp_path / "secret.txt").write_text("nope")
    assert await read_local_object("http://localhost:8002/site/../secret.txt", local_cfg) is None


# ---------------------------------------------------------------------------
# fill_local_site_identity (fake pool; the SQL WHERE is exercised for real in
# tests/integration_db/test_local_storage_provider.py)
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, settings: dict[str, str]):
        self.settings = settings
        self.updates: list[tuple[str, str]] = []

    async def fetch(self, sql, keys):
        return [{"key": k, "value": self.settings[k]} for k in keys if k in self.settings]

    async def fetchval(self, sql, key, value):
        assert "COALESCE(value, '') = ''" in sql, "must only ever fill empty values"
        self.updates.append((key, value))
        if self.settings.get(key, None) == "":
            self.settings[key] = value
            return key
        return None


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_fill_writes_the_preview_url_and_placeholder_name_in_local_mode():
    conn = _FakeConn(
        {
            "storage_provider": "local",
            "api_url": "http://localhost:8002/",
            "site_url": "",
            "site_name": "",
        }
    )
    filled = await fill_local_site_identity(_FakePool(conn))
    assert filled == {"site_url": "http://localhost:8002/site", "site_name": PLACEHOLDER_SITE_NAME}
    assert conn.settings["site_url"] == "http://localhost:8002/site"


@pytest.mark.asyncio
async def test_fill_never_overwrites_a_value_that_is_set():
    conn = _FakeConn(
        {
            "storage_provider": "local",
            "api_url": "http://localhost:8002",
            "site_url": "https://blog.example",
            "site_name": "Mine",
        }
    )
    assert await fill_local_site_identity(_FakePool(conn)) == {}
    assert conn.settings["site_url"] == "https://blog.example"
    assert conn.settings["site_name"] == "Mine"


@pytest.mark.parametrize("provider", ["s3", "", "minio"])
@pytest.mark.asyncio
async def test_fill_does_nothing_outside_local_mode(provider):
    conn = _FakeConn(
        {
            "storage_provider": provider,
            "api_url": "http://localhost:8002",
            "site_url": "",
            "site_name": "",
        }
    )
    assert await fill_local_site_identity(_FakePool(conn)) == {}
    assert conn.updates == []


@pytest.mark.asyncio
async def test_fill_does_nothing_without_the_provider_row():
    conn = _FakeConn({"api_url": "http://localhost:8002", "site_url": "", "site_name": ""})
    assert await fill_local_site_identity(_FakePool(conn)) == {}
    assert conn.updates == []


@pytest.mark.asyncio
async def test_fill_warns_and_skips_when_api_url_is_empty(caplog):
    conn = _FakeConn({"storage_provider": "local", "api_url": "", "site_url": "", "site_name": ""})
    with caplog.at_level(logging.WARNING):
        assert await fill_local_site_identity(_FakePool(conn)) == {}
    assert conn.updates == []
    assert any("api_url is empty" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# The placeholders agree with the free-tier brain seed and the reference seed
# ---------------------------------------------------------------------------


def _brain_seed() -> dict[str, str]:
    raw = json.loads((_PKG / "brain" / "seed_app_settings.json").read_text())
    items = raw if isinstance(raw, list) else raw.get("settings") or []
    return {it["key"]: it["value"] for it in items}


def _baseline_value(key: str) -> str:
    sql = (_PKG / "services" / "migrations" / "0000_baseline.seeds.sql").read_text()
    m = re.search(rf"VALUES \('{re.escape(key)}', '((?:[^']|'')*)'", sql)
    assert m, f"{key} missing from the baseline seed"
    return m.group(1)


def test_brain_seed_identity_matches_what_the_fill_writes():
    """Both install paths must land on the same preview: `docker compose up`
    (brain seeds first) and `poindexter setup` (the worker fills at boot)."""
    seed = _brain_seed()
    preview = _baseline_value("api_url").rstrip("/") + LOCAL_SITE_MOUNT_PATH
    assert seed["site_url"] == preview
    assert seed["public_site_url"] == preview
    assert seed["site_name"] == PLACEHOLDER_SITE_NAME


def test_defaults_seed_local_mode_and_the_folder():
    from poindexter.services.settings_defaults import DEFAULTS, METADATA

    assert DEFAULTS["storage_provider"] == "local"
    assert DEFAULTS["storage_local_dir"] == DEFAULT_LOCAL_DIR
    assert METADATA["storage_provider"]["value_type"] == "string"
    assert METADATA["storage_local_dir"]["value_type"] == "string"


def test_new_keys_are_categorised_with_the_rest_of_storage():
    from poindexter.services.settings_categories import resolve_category

    assert resolve_category("storage_provider") == "integrations"
    assert resolve_category("storage_local_dir") == "integrations"
