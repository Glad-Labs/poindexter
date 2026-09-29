"""Integration: local publishing mode against real Postgres.

Three things only a real database proves:

* the pinning migration's ``ON CONFLICT … WHERE value = 'local'`` upgrades a
  seeded row and leaves every other value alone;
* ``fill_local_site_identity``'s ``WHERE COALESCE(value, '') = ''`` fills
  empty identity and nothing else;
* a published post, exported with ``storage_provider=local``, becomes a folder
  the ``/site`` mount serves, with the real export queries run against the
  real schema.

Everything runs inside the rolled-back ``test_txn``.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "poindexter"
    / "services"
    / "migrations"
    / "20260928_174425_pin_storage_provider_to_s3_on_installs_with_a_configured_object_store.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("pin_storage_provider_int", _MIGRATION)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class _TxnPool:
    """Hands the rolled-back test connection to code that expects a pool."""

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def fetch(self, *args, **kwargs):
        return await self._conn.fetch(*args, **kwargs)

    async def fetchrow(self, *args, **kwargs):
        return await self._conn.fetchrow(*args, **kwargs)

    async def fetchval(self, *args, **kwargs):
        return await self._conn.fetchval(*args, **kwargs)

    async def execute(self, *args, **kwargs):
        return await self._conn.execute(*args, **kwargs)


async def _set(conn, key: str, value: str) -> None:
    await conn.execute(
        """
        INSERT INTO app_settings (key, value, category)
        VALUES ($1, $2, 'testing')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        key,
        value,
    )


async def _get(conn, key: str) -> str | None:
    return await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", key)


async def _clear_object_store(conn) -> None:
    await conn.execute(
        "UPDATE app_settings SET value = '' WHERE key = ANY($1::text[])",
        list(_load().OBJECT_STORE_KEYS),
    )
    await conn.execute("DELETE FROM app_settings WHERE key = 'storage_provider'")


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


async def test_install_with_a_bucket_is_pinned_to_s3(test_txn) -> None:
    await _clear_object_store(test_txn)
    await _set(test_txn, "storage_endpoint", "https://account.r2.example")
    await _load().up(_TxnPool(test_txn))
    assert await _get(test_txn, "storage_provider") == "s3"


async def test_a_seeded_local_row_is_upgraded_when_a_bucket_exists(test_txn) -> None:
    await _clear_object_store(test_txn)
    await _set(test_txn, "storage_provider", "local")
    await _set(test_txn, "cloudflare_r2_bucket", "legacy-bucket")
    await _load().up(_TxnPool(test_txn))
    assert await _get(test_txn, "storage_provider") == "s3"


async def test_fresh_install_keeps_local(test_txn) -> None:
    await _clear_object_store(test_txn)
    await _set(test_txn, "storage_provider", "local")
    await _load().up(_TxnPool(test_txn))
    assert await _get(test_txn, "storage_provider") == "local"


async def test_fresh_install_without_the_row_gets_nothing_written(test_txn) -> None:
    await _clear_object_store(test_txn)
    await _load().up(_TxnPool(test_txn))
    assert await _get(test_txn, "storage_provider") is None


# ---------------------------------------------------------------------------
# The identity fill
# ---------------------------------------------------------------------------


async def test_identity_fill_fills_only_empty_values(test_txn) -> None:
    from poindexter.services.local_site import PLACEHOLDER_SITE_NAME, fill_local_site_identity

    await _set(test_txn, "storage_provider", "local")
    await _set(test_txn, "api_url", "http://localhost:8002")
    await _set(test_txn, "site_url", "")
    await _set(test_txn, "site_name", "Already Named")

    filled = await fill_local_site_identity(_TxnPool(test_txn))

    assert filled == {"site_url": "http://localhost:8002/site"}
    assert await _get(test_txn, "site_url") == "http://localhost:8002/site"
    assert await _get(test_txn, "site_name") == "Already Named"
    # A second boot changes nothing.
    assert await fill_local_site_identity(_TxnPool(test_txn)) == {}
    assert PLACEHOLDER_SITE_NAME != "Already Named"


async def test_identity_fill_is_a_no_op_in_s3_mode(test_txn) -> None:
    from poindexter.services.local_site import fill_local_site_identity

    await _set(test_txn, "storage_provider", "s3")
    await _set(test_txn, "api_url", "http://localhost:8002")
    await _set(test_txn, "site_url", "")
    assert await fill_local_site_identity(_TxnPool(test_txn)) == {}
    assert await _get(test_txn, "site_url") == ""


# ---------------------------------------------------------------------------
# Publish -> folder -> /site
# ---------------------------------------------------------------------------


async def test_a_published_post_exports_to_a_folder_the_site_mount_serves(
    test_txn, tmp_path
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from poindexter.services.site_config import SiteConfig
    from poindexter.services.static_export_service import export_post
    from poindexter.utils.local_site_mount import mount_local_site

    # The test makes its own post, inside the rolled-back transaction. It used to
    # export "the newest published post", which in a full tier run is whichever
    # post an earlier test committed: test_flag_bot_page_views_job leaves a
    # published, contentless, NULL-published_at row ("hot"), and Postgres sorts
    # NULLs FIRST under ORDER BY ... DESC, so that row won and the content
    # assertion below saw an empty string. Passing in isolation proved nothing.
    title = "Local Site Export Test"
    slug = f"local-site-export-{uuid.uuid4().hex[:8]}"
    await test_txn.execute(
        """
        INSERT INTO posts (id, title, slug, content, excerpt, status, published_at)
        VALUES (gen_random_uuid(), $1, $2, $3, 'An excerpt.', 'published', NOW())
        """,
        title,
        slug,
        "## A section\n\nBody with **bold** text.\n",
    )

    root = tmp_path / "site"
    cfg = SiteConfig(
        initial_config={
            "storage_provider": "local",
            "storage_local_dir": str(root),
            "api_url": "http://localhost:8002",
            "site_url": "http://localhost:8002/site",
            "site_name": "Integration Site",
        },
    )

    assert await export_post(_TxnPool(test_txn), slug, site_config=cfg) is True

    post = json.loads((root / "static" / "posts" / f"{slug}.json").read_text())
    assert post["slug"] == slug
    assert post["title"] == title
    # The markdown body is exported as HTML, not passed through.
    assert "<h2" in post["content"]
    assert "<strong>bold</strong>" in post["content"]
    index = json.loads((root / "static" / "posts" / "index.json").read_text())
    assert slug in [p["slug"] for p in index["posts"]]
    manifest = json.loads((root / "static" / "manifest.json").read_text())
    assert manifest["site_name"] == "Integration Site"
    assert manifest["site_url"] == "http://localhost:8002/site"
    for name in ("feed.json", "sitemap.json"):
        assert (root / "static" / name).is_file(), name
    sitemap = json.loads((root / "static" / "sitemap.json").read_text())
    assert f"http://localhost:8002/site/posts/{slug}" in [u["url"] for u in sitemap["urls"]]

    app = FastAPI()
    app.state.container = SimpleNamespace(site_config=cfg)
    mount_local_site(app)
    client = TestClient(app)
    served = client.get(f"/site/static/posts/{slug}.json")
    assert served.status_code == 200
    assert served.json()["slug"] == slug
    page = client.get(f"/site/posts/{slug}")
    assert page.status_code == 200
    assert "viewer.js" in page.text
