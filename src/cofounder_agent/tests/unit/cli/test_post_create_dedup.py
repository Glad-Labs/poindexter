"""Click CLI tests for the ``poindexter posts create`` semantic dedup guard.

The MCP/HTTP create path got a pre-enqueue dedup guard (glad-labs-stack#1823).
``posts create`` is a SEPARATE model — it inserts a manual draft `posts` shell
directly — but it's still a manual-injection entry point, so a caller-supplied
topic that near-duplicates an already-published post should be refused there too
(with ``--force`` to override). Both paths share the same service:
``services.topic_dedup_guard.assert_topic_not_duplicate``.

These tests patch ``asyncpg`` + ``SiteConfig`` the same way
``test_post_create_media_validation`` does (no real DB), and patch the dedup
guard at its source module so the test controls block/allow without an
embedding model. Idempotency is disabled so the only DB op is the INSERT —
``conn.fetchrow`` call count is the "did it insert?" signal.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.posts import post_group

# ---------------------------------------------------------------------------
# Fixtures (mirror test_post_create_media_validation)
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _make_conn() -> MagicMock:
    conn = MagicMock()
    # The INSERT ... RETURNING is the only fetchrow when idempotency is off.
    conn.fetchrow = AsyncMock(
        return_value={
            "id": "11111111-2222-3333-4444-555555555555",
            "slug": "x-aabbcc",
            "title": "x",
            "status": "draft",
        }
    )
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def fake_asyncpg(fake_dsn):
    conn = _make_conn()
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock(return_value=None)

    async def _create_pool(_dsn, **_kwargs):
        return pool

    asyncpg = MagicMock()
    asyncpg.create_pool = _create_pool

    with patch.dict("sys.modules", {"asyncpg": asyncpg}):
        yield {"pool": pool, "conn": conn}


def _patch_site_config():
    """Idempotency disabled so the only DB op is the INSERT."""
    from services.site_config import SiteConfig

    class _StubSiteConfig(SiteConfig):
        def __init__(self, *_args, pool=None, **_kwargs):
            super().__init__(
                initial_config={"cli_post_create_idempotency_enabled": "false"},
                pool=pool,
            )

        async def load(self, _pool):
            return 0

    return patch("services.site_config.SiteConfig", _StubSiteConfig)


def _patch_guard(*, duplicate: bool):
    """Patch the shared guard at its source module (the CLI lazy-imports it).

    Returns ``(patch_context_manager, calls_dict)``.
    """
    import services.topic_dedup_guard as guard

    calls: dict = {}

    async def _fake(topic, *, site_config=None, force=False, **_kw):
        calls["topic"] = topic
        calls["force"] = force
        if duplicate:
            match = types.SimpleNamespace(
                similarity=0.82,
                metadata={"title": "The VRAM Currency Problem"},
                source_id="post/bb10de87",
            )
            raise guard.DuplicateTopicError(
                topic=topic, match=match, threshold=0.75
            )

    return patch.object(guard, "assert_topic_not_duplicate", _fake), calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPostsCreateDedup:
    def test_duplicate_topic_blocks_and_skips_insert(self, runner, fake_asyncpg):
        guard_patch, _calls = _patch_guard(duplicate=True)
        with _patch_site_config(), guard_patch:
            result = runner.invoke(
                post_group,
                ["create", "--topic", "Quantization and VRAM for LLMs", "--json"],
            )
        assert result.exit_code != 0
        combined = (result.output or "") + str(result.exception or "")
        # Names the colliding post and the --force remediation.
        assert "VRAM Currency Problem" in combined
        assert "--force" in combined
        # The near-duplicate is NOT inserted.
        assert fake_asyncpg["conn"].fetchrow.await_count == 0

    def test_distinct_topic_inserts(self, runner, fake_asyncpg):
        guard_patch, calls = _patch_guard(duplicate=False)
        with _patch_site_config(), guard_patch:
            result = runner.invoke(
                post_group,
                ["create", "--topic", "A novel angle on edge caching", "--json"],
            )
        assert result.exit_code == 0, result.output
        # Guard ran (allowed) and the row was inserted.
        assert calls.get("topic") == "A novel angle on edge caching"
        assert fake_asyncpg["conn"].fetchrow.await_count == 1

    def test_force_bypasses_dedup(self, runner, fake_asyncpg):
        guard_patch, calls = _patch_guard(duplicate=True)
        with _patch_site_config(), guard_patch:
            result = runner.invoke(
                post_group,
                [
                    "create",
                    "--topic",
                    "Quantization and VRAM for LLMs",
                    "--force",
                    "--json",
                ],
            )
        assert result.exit_code == 0, result.output
        # --force short-circuits before the guard runs, and still inserts.
        assert calls == {}
        assert fake_asyncpg["conn"].fetchrow.await_count == 1
