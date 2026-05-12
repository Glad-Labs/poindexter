"""Unit tests for IGDBSource.

No real HTTP, no real DB. Mocks ``httpx.AsyncClient`` to return canned
Twitch + IGDB responses, and ``plugins.secrets.get_secret`` to return
test credentials. The token cache is cleared between tests so each
test starts from a known state.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.topic_source import TopicSource
from services.topic_sources import igdb as igdb_mod
from services.topic_sources.igdb import IGDBSource


def _make_pool() -> Any:
    """Minimal asyncpg pool whose acquire() yields a no-op connection.

    The IGDBSource uses the conn only via plugins.secrets.get_secret,
    which we monkeypatch directly — so the conn methods don't need
    real behaviour, just need to support ``async with``.
    """
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _make_client(
    *,
    token_payload: dict[str, Any] | None = None,
    token_status: int = 200,
    igdb_payload: list[dict[str, Any]] | dict[str, Any] | None = None,
    igdb_status: int = 200,
):
    """Fake httpx.AsyncClient routing POST calls by URL.

    - POST to oauth2/token → token_payload
    - POST to /games → igdb_payload
    """
    client = AsyncMock()

    async def post(url: str, *, params=None, headers=None, content=None, timeout=None):
        resp = MagicMock()
        if "oauth2/token" in url:
            resp.status_code = token_status
            resp.json = MagicMock(return_value=token_payload or {})
            if token_status >= 400:
                from httpx import HTTPStatusError, Request, Response
                resp.raise_for_status = MagicMock(
                    side_effect=HTTPStatusError(
                        "401", request=Request("POST", url),
                        response=Response(status_code=token_status),
                    ),
                )
            else:
                resp.raise_for_status = MagicMock()
        else:
            resp.status_code = igdb_status
            resp.json = MagicMock(return_value=igdb_payload or [])
            resp.text = ""
            resp.raise_for_status = MagicMock()
        return resp

    client.post = AsyncMock(side_effect=post)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.fixture(autouse=True)
def _clear_token_cache():
    """Each test starts with an empty Twitch token cache."""
    igdb_mod._TOKEN_CACHE.clear()
    yield
    igdb_mod._TOKEN_CACHE.clear()


def _patch_secrets(client_id: str = "test-client-id", client_secret: str = "test-client-secret"):
    """Patch plugins.secrets.get_secret to return canned credentials."""
    async def _get_secret(_conn, key):
        if key == "igdb_twitch_client_id":
            return client_id
        if key == "igdb_twitch_client_secret":
            return client_secret
        return None
    return patch("plugins.secrets.get_secret", new=_get_secret)


class TestIGDBSource:
    def test_conforms_to_topic_source_protocol(self):
        assert isinstance(IGDBSource(), TopicSource)
        assert IGDBSource().name == "igdb"

    @pytest.mark.asyncio
    async def test_skips_when_pool_is_none(self, caplog):
        topics = await IGDBSource().extract(pool=None, config={})
        assert topics == []
        assert any("pool unavailable" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_skips_when_credentials_missing(self, caplog):
        pool = _make_pool()
        with _patch_secrets(client_id="", client_secret=""):
            topics = await IGDBSource().extract(pool=pool, config={})
        assert topics == []
        assert any("not configured" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_returns_topics_on_happy_path(self):
        pool = _make_pool()
        now = int(time.time())
        games = [
            {
                "name": "Tunic",
                "summary": "A small fox in a big mystery.",
                "url": "https://www.igdb.com/games/tunic",
                "first_release_date": now - 7 * 86400,  # released a week ago
                "genres": [{"name": "Adventure"}],
                "themes": [{"name": "Indie"}],
                "platforms": [{"name": "PC"}, {"name": "Switch"}],
            },
            {
                "name": "Stray",
                "summary": "Cat in cyberpunk.",
                "url": "https://www.igdb.com/games/stray",
                "first_release_date": now - 14 * 86400,
                "themes": [{"name": "Indie"}],
            },
        ]
        ctx, _ = _make_client(
            token_payload={"access_token": "tok123", "expires_in": 60 * 86400},
            igdb_payload=games,
        )
        with _patch_secrets(), patch("httpx.AsyncClient", return_value=ctx):
            topics = await IGDBSource().extract(pool=pool, config={})
        assert len(topics) == 2
        # All from the igdb source.
        assert {t.source for t in topics} == {"igdb"}
        # Both titles surface the game name.
        joined = " ".join(t.title for t in topics)
        assert "Tunic" in joined
        assert "Stray" in joined
        # source_url propagated from IGDB.
        assert topics[0].source_url.startswith("https://www.igdb.com/")
        # Newer game ranks higher than older one (release-freshness score).
        assert topics[0].relevance_score >= topics[1].relevance_score

    @pytest.mark.asyncio
    async def test_token_cached_across_calls(self):
        pool = _make_pool()
        ctx, client = _make_client(
            token_payload={"access_token": "tok-abc", "expires_in": 60 * 86400},
            igdb_payload=[],
        )
        with _patch_secrets(), patch("httpx.AsyncClient", return_value=ctx):
            await IGDBSource().extract(pool=pool, config={})
            await IGDBSource().extract(pool=pool, config={})
        # Two extract() calls, but only ONE token POST should have happened
        # because the second call hits the in-process cache.
        token_calls = [
            c for c in client.post.await_args_list
            if "oauth2/token" in c.args[0]
        ]
        assert len(token_calls) == 1, (
            f"expected 1 token POST, got {len(token_calls)} — token cache not honoured"
        )

    @pytest.mark.asyncio
    async def test_401_clears_cache_and_returns_empty(self):
        pool = _make_pool()
        # Seed a known-bad token in the cache first.
        igdb_mod._TOKEN_CACHE["test-client-id"] = ("expired-token", time.time() + 999999)
        ctx, _ = _make_client(
            token_payload={"access_token": "tok-bad", "expires_in": 60 * 86400},
            igdb_status=401,
        )
        with _patch_secrets(), patch("httpx.AsyncClient", return_value=ctx):
            topics = await IGDBSource().extract(pool=pool, config={})
        assert topics == []
        # Cache cleared so next cycle refreshes.
        assert "test-client-id" not in igdb_mod._TOKEN_CACHE

    @pytest.mark.asyncio
    async def test_token_endpoint_failure_returns_empty(self, caplog):
        pool = _make_pool()
        ctx, _ = _make_client(
            token_payload={},
            token_status=401,
        )
        with _patch_secrets(), patch("httpx.AsyncClient", return_value=ctx):
            topics = await IGDBSource().extract(pool=pool, config={})
        assert topics == []
        assert any("Twitch OAuth" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_igdb_non_200_returns_empty(self):
        pool = _make_pool()
        ctx, _ = _make_client(
            token_payload={"access_token": "tok", "expires_in": 60 * 86400},
            igdb_payload={"error": "rate limited"},
            igdb_status=429,
        )
        with _patch_secrets(), patch("httpx.AsyncClient", return_value=ctx):
            topics = await IGDBSource().extract(pool=pool, config={})
        assert topics == []

    @pytest.mark.asyncio
    async def test_handles_games_without_release_date(self):
        pool = _make_pool()
        # Production data: occasional records have first_release_date=null
        # (TBA games), no themes, etc.
        games = [
            {"name": "Mystery Indie Game", "url": "https://www.igdb.com/games/x"},
        ]
        ctx, _ = _make_client(
            token_payload={"access_token": "tok", "expires_in": 60 * 86400},
            igdb_payload=games,
        )
        with _patch_secrets(), patch("httpx.AsyncClient", return_value=ctx):
            topics = await IGDBSource().extract(pool=pool, config={})
        assert len(topics) == 1
        # Score floor of 0.5 holds even with no release date.
        assert topics[0].relevance_score == 0.5
