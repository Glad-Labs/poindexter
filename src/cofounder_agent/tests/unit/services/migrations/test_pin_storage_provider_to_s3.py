"""Migration 20260928_174425: installs with a bucket stay on ``s3``.

The new ``storage_provider`` key defaults to ``local``. If the migration missed
an install that has a bucket, that live site would quietly start publishing to
a folder, so the key list it checks is derived from what ``R2UploadService``
actually reads rather than typed out a second time. The real SQL (ON CONFLICT
upgrade of a seeded ``local`` row) runs against Postgres in
``tests/integration_db/test_local_storage_provider.py``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parents[4] / "poindexter" / "services"
_MIGRATION = (
    _SERVICES
    / "migrations"
    / "20260928_174425_pin_storage_provider_to_s3_on_installs_with_a_configured_object_store.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("pin_storage_provider_unit", _MIGRATION)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_checks_every_object_store_key_the_uploader_reads_in_both_spellings():
    src = (_SERVICES / "r2_upload_service.py").read_text()
    suffixes = set(re.findall(r'self\._storage(?:_secret)?\("([a-z_]+)"', src))
    assert {"access_key", "secret_key", "endpoint", "bucket", "public_url"} <= suffixes
    keys = set(_load().OBJECT_STORE_KEYS)
    for suffix in suffixes:
        assert f"storage_{suffix}" in keys, suffix
        assert f"cloudflare_r2_{suffix}" in keys, suffix


class _Conn:
    def __init__(self, configured: list[str]):
        self.configured = configured
        self.inserts: list[str] = []

    async def fetch(self, sql, keys):
        assert "COALESCE(value, '') <> ''" in sql
        return [{"key": k} for k in self.configured if k in keys]

    async def fetchval(self, sql):
        self.inserts.append(sql)
        return "s3"


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_fresh_install_is_left_on_the_seeded_default():
    conn = _Conn(configured=[])
    await _load().up(_Pool(conn))
    assert conn.inserts == []


@pytest.mark.parametrize("key", ["storage_endpoint", "storage_secret_key", "cloudflare_r2_bucket"])
@pytest.mark.asyncio
async def test_any_configured_key_pins_s3(key):
    conn = _Conn(configured=[key])
    await _load().up(_Pool(conn))
    [sql] = conn.inserts
    assert "'storage_provider', 's3'" in sql
    # Upgrades only the seeded default; never another value.
    assert "WHERE app_settings.value = 'local'" in sql


@pytest.mark.asyncio
async def test_down_is_a_deliberate_no_op():
    conn = _Conn(configured=["storage_endpoint"])
    assert await _load().down(_Pool(conn)) is None
    assert conn.inserts == []
