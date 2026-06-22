"""Click CLI tests for ``poindexter post create --media`` normalization.

Regression cover for the silent-drop bug where ``--media short`` was
stored verbatim into ``posts.media_to_generate`` while every downstream
consumer (``publish_service._wants_short``, ``backfill_videos`` filter,
``media_reconciliation``, ``media_approval_service._VALID_MEDIA``) keys
off the canonical flavor ``video_short``. The alias never matched, so the
operator's short-video request silently no-op'd — a textbook
``feedback_no_silent_defaults`` violation.

The fix mirrors the existing ``--gates`` validation: aliases are
normalized (``short`` → ``video_short``), then each resolved flavor is
checked against ``CANONICAL_MEDIA_NAMES`` with a loud error on a typo.

These tests patch ``asyncpg`` + ``SiteConfig`` + the dedup guard so the suite
never touches a real DB. ``posts create`` is now a manual write/upload command
(see ``test_post_create_manual_upload``): a body is required (piped via stdin
here) and the INSERT's positional args are ``(query, title, slug, content,
excerpt, status, media, metadata, key)`` — ``media`` is ``args[6]``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli.posts import (
    CANONICAL_MEDIA_NAMES,
    _normalize_media,
    post_group,
)

# ---------------------------------------------------------------------------
# Fixtures (mirror test_post_create_idempotency)
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_dsn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _make_conn(*, fetchrow_results: list[dict | None]) -> MagicMock:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=list(fetchrow_results))
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def fake_asyncpg(fake_dsn):
    """Patch ``asyncpg.create_pool`` so the CLI never reaches a real DB."""
    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=_make_conn(
        fetchrow_results=[None],
    ))
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock(return_value=None)

    async def _create_pool(_dsn, **_kwargs):
        return pool

    asyncpg = MagicMock()
    asyncpg.create_pool = _create_pool

    with patch.dict("sys.modules", {"asyncpg": asyncpg}):
        yield {"pool": pool, "asyncpg": asyncpg}


def _patch_site_config(initial: dict[str, str] | None = None):
    from services.site_config import SiteConfig

    class _StubSiteConfig(SiteConfig):
        def __init__(self, *_args, pool=None, **_kwargs):
            super().__init__(initial_config=dict(initial or {}), pool=pool)

        async def load(self, _pool):
            return 0

    return patch("services.site_config.SiteConfig", _StubSiteConfig)


def _run_create(runner, fake_asyncpg, media_arg: str):
    """Invoke ``posts create --media <media_arg>`` against a scripted INSERT.

    Idempotency is disabled so the path is a single INSERT (no lookup),
    keeping the INSERT call at ``fetchrow.await_args_list[0]``. A body is
    piped via stdin (now required) and the dedup guard is no-op'd so the
    only DB op is the INSERT.
    """
    import services.topic_dedup_guard as guard

    new_conn = _make_conn(fetchrow_results=[
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "slug": "x-aabbcc",
            "title": "x",
            "status": "draft",
        },
    ])
    fake_asyncpg["pool"].acquire.return_value.__aenter__ = AsyncMock(
        return_value=new_conn
    )
    site_patch = _patch_site_config(
        initial={"cli_post_create_idempotency_enabled": "false"}
    )
    guard_patch = patch.object(guard, "assert_topic_not_duplicate", AsyncMock())
    with site_patch, guard_patch:
        result = runner.invoke(
            post_group,
            ["create", "--title", "x", "--media", media_arg, "--json"],
            input="# x\n\nManual body.",
        )
    return result, new_conn


# ---------------------------------------------------------------------------
# _normalize_media — pure function
# ---------------------------------------------------------------------------


class TestNormalizeMedia:
    def test_short_alias_maps_to_video_short(self):
        assert _normalize_media(["short"]) == ["video_short"]

    def test_canonical_names_pass_through(self):
        assert _normalize_media(["podcast", "video", "video_short"]) == [
            "podcast", "video", "video_short",
        ]

    def test_dedupes_alias_and_canonical_collisions(self):
        # short + video_short collapse to a single entry.
        assert _normalize_media(["short", "video_short"]) == ["video_short"]

    def test_preserves_order(self):
        assert _normalize_media(["video", "podcast"]) == ["video", "podcast"]

    def test_empty_list(self):
        assert _normalize_media([]) == []


# ---------------------------------------------------------------------------
# CLI: --media short is normalized into the stored array
# ---------------------------------------------------------------------------


class TestMediaNormalizedInInsert:
    def test_short_stored_as_video_short(self, runner, fake_asyncpg):
        result, conn = _run_create(runner, fake_asyncpg, "short")
        assert result.exit_code == 0, result.output
        insert_call = conn.fetchrow.await_args_list[0]
        stored_media = insert_call.args[6]
        assert "video_short" in stored_media
        assert "short" not in stored_media

    def test_video_short_passes_through(self, runner, fake_asyncpg):
        result, conn = _run_create(runner, fake_asyncpg, "video_short")
        assert result.exit_code == 0, result.output
        insert_call = conn.fetchrow.await_args_list[0]
        assert insert_call.args[6] == ["video_short"]


# ---------------------------------------------------------------------------
# CLI: unknown media flavor fails loud (mirrors --gates validation)
# ---------------------------------------------------------------------------


class TestUnknownMediaFailsLoud:
    def test_typo_raises_nonzero_with_valid_list(self, runner, fake_asyncpg):
        result, _conn = _run_create(runner, fake_asyncpg, "viddeo")
        assert result.exit_code != 0
        # The error names the offending flavor and the canonical set.
        combined = (result.output or "") + str(result.exception or "")
        assert "viddeo" in combined
        for name in CANONICAL_MEDIA_NAMES:
            assert name in combined


def test_canonical_media_names_drops_video_long():
    """#1460: the type-valued video_long is collapsed into video. The CLI's
    accepted-media set must not offer it any more."""
    assert "video_long" not in CANONICAL_MEDIA_NAMES
    assert CANONICAL_MEDIA_NAMES == ("podcast", "video", "video_short")
