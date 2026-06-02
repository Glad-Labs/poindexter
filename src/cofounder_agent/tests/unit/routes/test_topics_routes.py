"""Unit tests for ``routes/topics_routes.py`` (Glad-Labs/poindexter#647).

Behavioral coverage of the URL-seeding endpoints:

- POST /api/topics/from-url   — one URL → one content task
- POST /api/topics/from-urls  — many URLs → top-N ranked tasks

Each endpoint's success / auth / error paths are pinned. The
``URLScraper`` is patched so no real HTTP fetch happens; ``add_task``
is mocked. The auth path uses the real ``verify_api_token`` dependency
(no override) so an unauthenticated request 401s.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.topics_routes import router
from services.url_scraper import URLScrapeError
from utils.route_utils import get_database_dependency, get_site_config_dependency


def _make_db():
    db = MagicMock()
    db.add_task = AsyncMock(return_value="task-id-123")
    return db


def _make_site_config():
    sc = MagicMock()
    # pick_target_length reads weighted settings off site_config; return
    # the default for any key so it resolves to a concrete int internally.
    sc.get = MagicMock(side_effect=lambda key, default=None: default)
    return sc


def _build_app(db=None, *, authed=True):
    db = db if db is not None else _make_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: db
    app.dependency_overrides[get_site_config_dependency] = lambda: _make_site_config()
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


def _scraped(title="A Great Article", **extra):
    base = {
        "title": title,
        "content_type": "article",
        "content_preview": "Some preview text",
        "author": "Jane Doe",
        "published_at": "2026-01-01",
        "word_count": 1200,
        "excerpt": "An excerpt",
    }
    base.update(extra)
    return base


def _patch_scraper(scrape_side_effect=None, scrape_return=None):
    """Patch URLScraper so scrape_url is controllable. Returns the patcher."""
    scraper_instance = MagicMock()
    scraper_instance.scrape_url = AsyncMock(
        side_effect=scrape_side_effect, return_value=scrape_return,
    )
    return patch(
        "routes.topics_routes.URLScraper", return_value=scraper_instance,
    ), scraper_instance


# ---------------------------------------------------------------------------
# POST /api/topics/from-url
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFromUrl:
    def test_success_returns_201_and_queues_task(self):
        db = _make_db()
        patcher, _scraper = _patch_scraper(scrape_return=_scraped())
        with patcher, patch(
            "routes.topics_routes.pick_target_length", return_value=1200,
        ):
            client = TestClient(_build_app(db))
            resp = client.post(
                "/api/topics/from-url", json={"url": "https://example.com/post"},
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["task_id"] == "task-id-123"
        assert data["topic"] == "A Great Article"
        assert data["status"] == "pending"
        db.add_task.assert_awaited_once()

    def test_scrape_error_returns_400(self):
        patcher, _scraper = _patch_scraper(
            scrape_side_effect=URLScrapeError("could not fetch"),
        )
        with patcher:
            client = TestClient(_build_app())
            resp = client.post(
                "/api/topics/from-url", json={"url": "https://broken.example"},
            )
        assert resp.status_code == 400
        assert "Could not scrape URL" in resp.json()["detail"]

    def test_unexpected_scrape_crash_returns_500(self):
        patcher, _scraper = _patch_scraper(
            scrape_side_effect=RuntimeError("boom"),
        )
        with patcher:
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.post(
                "/api/topics/from-url", json={"url": "https://crash.example"},
            )
        assert resp.status_code == 500

    def test_untitled_scrape_returns_422(self):
        # A scrape that yields no real title can't seed a topic.
        patcher, _scraper = _patch_scraper(scrape_return=_scraped(title="Untitled"))
        with patcher:
            client = TestClient(_build_app())
            resp = client.post(
                "/api/topics/from-url", json={"url": "https://notitle.example"},
            )
        assert resp.status_code == 422

    def test_missing_url_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/topics/from-url", json={})
        assert resp.status_code == 422

    def test_unauthenticated_returns_401(self):
        client = TestClient(_build_app(authed=False), raise_server_exceptions=False)
        resp = client.post(
            "/api/topics/from-url", json={"url": "https://example.com/post"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/topics/from-urls
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFromUrls:
    def test_success_queues_top_n_ranked(self):
        db = _make_db()
        # Two scrapeable URLs; richer one ranks first.
        scraper_instance = MagicMock()

        async def _scrape(u):
            if "rich" in u:
                return _scraped(title="Rich Post", word_count=3000)
            return _scraped(title="Thin Post", word_count=100, excerpt=None, author=None)

        scraper_instance.scrape_url = AsyncMock(side_effect=_scrape)
        with patch(
            "routes.topics_routes.URLScraper", return_value=scraper_instance,
        ), patch(
            "routes.topics_routes.pick_target_length", return_value=1200,
        ):
            client = TestClient(_build_app(db))
            resp = client.post(
                "/api/topics/from-urls",
                json={
                    "urls": ["https://rich.example", "https://thin.example"],
                    "max_topics": 2,
                },
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["queued"] == 2
        assert data["requested"] == 2
        # Richest post ranked first.
        assert data["tasks"][0]["topic"] == "Rich Post"
        assert db.add_task.await_count == 2

    def test_unscrapeable_urls_become_errors_not_tasks(self):
        db = _make_db()
        scraper_instance = MagicMock()

        async def _scrape(u):
            raise URLScrapeError("nope")

        scraper_instance.scrape_url = AsyncMock(side_effect=_scrape)
        with patch(
            "routes.topics_routes.URLScraper", return_value=scraper_instance,
        ):
            client = TestClient(_build_app(db))
            resp = client.post(
                "/api/topics/from-urls",
                json={"urls": ["https://broken.example"]},
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["queued"] == 0
        assert len(data["errors"]) == 1
        db.add_task.assert_not_awaited()

    def test_empty_url_list_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/topics/from-urls", json={"urls": []})
        assert resp.status_code == 422

    def test_unauthenticated_returns_401(self):
        client = TestClient(_build_app(authed=False), raise_server_exceptions=False)
        resp = client.post(
            "/api/topics/from-urls", json={"urls": ["https://example.com"]},
        )
        assert resp.status_code == 401
