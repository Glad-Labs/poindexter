"""
Unit tests for routes/social_routes.py.

Tests cover:
- GET  /api/social/platforms            — get_platforms
- POST /api/social/connect              — connect_platform
- GET  /api/social/posts                — get_posts
- POST /api/social/posts               — create_post
- DELETE /api/social/posts/{id}         — delete_post
- GET  /api/social/posts/{id}/analytics — get_post_analytics
- POST /api/social/generate             — generate_content
- GET  /api/social/trending             — get_trending_topics
- POST /api/social/cross-post          — cross_post

Note: social_routes uses module-level _posts_store / _platform_connections.
Tests clear state via the imported module references so results are isolated.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.social_routes as social_module
from middleware.api_token_auth import verify_api_token
from routes.social_routes import social_router
from tests.unit.routes.conftest import TEST_USER

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_social_state():
    """Reset in-memory stores before every test for isolation."""
    social_module._posts_store.clear()
    for key in social_module._platform_connections:
        social_module._platform_connections[key] = False
    yield
    social_module._posts_store.clear()
    for key in social_module._platform_connections:
        social_module._platform_connections[key] = False


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(social_router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


VALID_POST_PAYLOAD = {
    "content": "This is a test post about AI and machine learning trends today.",
    "platforms": ["twitter"],
    "tone": "professional",
    "include_hashtags": True,
    "include_emojis": False,
}

VALID_GENERATE_PAYLOAD = {
    "topic": "AI trends in 2026",
    "platform": "twitter",
    "tone": "professional",
    "include_hashtags": True,
    "include_emojis": False,
}

VALID_CROSS_POST_PAYLOAD = {
    "content": "This is a cross-post test about AI and innovation in technology.",
    "platforms": ["twitter", "linkedin"],
}


# ---------------------------------------------------------------------------
# GET /api/social/platforms
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPlatforms:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/social/platforms")
        assert resp.status_code == 200

    def test_response_contains_known_platforms(self):
        client = TestClient(_build_app())
        data = client.get("/api/social/platforms").json()
        assert "twitter" in data
        assert "linkedin" in data
        assert "facebook" in data

    def test_default_platforms_are_disconnected(self):
        client = TestClient(_build_app())
        data = client.get("/api/social/platforms").json()
        assert data["twitter"]["connected"] is False


# ---------------------------------------------------------------------------
# POST /api/social/connect
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnectPlatform:
    def test_connect_twitter_returns_201(self):
        client = TestClient(_build_app())
        resp = client.post("/api/social/connect", json={"platform": "twitter"})
        assert resp.status_code == 201

    def test_connect_response_has_success_true(self):
        client = TestClient(_build_app())
        data = client.post("/api/social/connect", json={"platform": "twitter"}).json()
        assert data["success"] is True
        assert data["connected"] is True

    def test_connect_updates_platform_status(self):
        client = TestClient(_build_app())
        client.post("/api/social/connect", json={"platform": "linkedin"})
        data = client.get("/api/social/platforms").json()
        assert data["linkedin"]["connected"] is True

    def test_invalid_platform_returns_422(self):
        """Pydantic validates against SocialPlatformEnum."""
        client = TestClient(_build_app())
        resp = client.post("/api/social/connect", json={"platform": "myspace"})
        assert resp.status_code == 422

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(social_router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/social/connect", json={"platform": "twitter"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/social/posts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPosts:
    def test_empty_store_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/social/posts")
        assert resp.status_code == 200

    def test_empty_store_returns_empty_list(self):
        client = TestClient(_build_app())
        data = client.get("/api/social/posts").json()
        assert data["posts"] == []
        assert data["analytics"]["total_posts"] == 0

    def test_response_has_analytics(self):
        client = TestClient(_build_app())
        data = client.get("/api/social/posts").json()
        assert "analytics" in data
        assert "total_posts" in data["analytics"]

    def test_requires_auth(self):
        app = FastAPI()
        app.include_router(social_router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/social/posts")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/social/posts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreatePost:
    def test_create_post_returns_201(self):
        client = TestClient(_build_app())
        resp = client.post("/api/social/posts", json=VALID_POST_PAYLOAD)
        assert resp.status_code == 201

    def test_create_post_response_has_post_id(self):
        client = TestClient(_build_app())
        data = client.post("/api/social/posts", json=VALID_POST_PAYLOAD).json()
        assert "post_id" in data
        assert data["success"] is True

    def test_created_post_appears_in_list(self):
        client = TestClient(_build_app())
        client.post("/api/social/posts", json=VALID_POST_PAYLOAD)
        data = client.get("/api/social/posts").json()
        assert data["analytics"]["total_posts"] == 1

    def test_post_with_scheduled_time_has_scheduled_status(self):
        client = TestClient(_build_app())
        payload = {**VALID_POST_PAYLOAD, "scheduled_time": "2026-12-01T10:00:00"}
        data = client.post("/api/social/posts", json=payload).json()
        assert data["status"] == "scheduled"

    def test_post_without_scheduled_time_has_published_status(self):
        client = TestClient(_build_app())
        data = client.post("/api/social/posts", json=VALID_POST_PAYLOAD).json()
        assert data["status"] == "published"

    def test_missing_content_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/social/posts", json={"platforms": ["twitter"]})
        assert resp.status_code == 422

    def test_content_too_short_returns_422(self):
        client = TestClient(_build_app())
        payload = {**VALID_POST_PAYLOAD, "content": "short"}
        resp = client.post("/api/social/posts", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/social/posts/{post_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeletePost:
    def test_delete_existing_post_returns_204(self):
        client = TestClient(_build_app())
        created = client.post("/api/social/posts", json=VALID_POST_PAYLOAD).json()
        post_id = created["post_id"]
        resp = client.delete(f"/api/social/posts/{post_id}")
        assert resp.status_code == 204

    def test_delete_nonexistent_post_returns_404(self):
        client = TestClient(_build_app())
        resp = client.delete("/api/social/posts/nonexistent-id")
        assert resp.status_code == 404

    def test_deleted_post_removed_from_list(self):
        client = TestClient(_build_app())
        created = client.post("/api/social/posts", json=VALID_POST_PAYLOAD).json()
        post_id = created["post_id"]
        client.delete(f"/api/social/posts/{post_id}")
        data = client.get("/api/social/posts").json()
        assert data["analytics"]["total_posts"] == 0


# ---------------------------------------------------------------------------
# GET /api/social/posts/{post_id}/analytics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPostAnalytics:
    def test_existing_post_returns_200(self):
        client = TestClient(_build_app())
        created = client.post("/api/social/posts", json=VALID_POST_PAYLOAD).json()
        post_id = created["post_id"]
        resp = client.get(f"/api/social/posts/{post_id}/analytics")
        assert resp.status_code == 200

    def test_analytics_response_has_required_fields(self):
        client = TestClient(_build_app())
        created = client.post("/api/social/posts", json=VALID_POST_PAYLOAD).json()
        post_id = created["post_id"]
        data = client.get(f"/api/social/posts/{post_id}/analytics").json()
        for field in ["post_id", "likes", "shares", "comments", "engagement_rate"]:
            assert field in data

    def test_nonexistent_post_returns_404(self):
        client = TestClient(_build_app())
        resp = client.get("/api/social/posts/nonexistent/analytics")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/social/generate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateContent:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.post("/api/social/generate", json=VALID_GENERATE_PAYLOAD)
        assert resp.status_code == 200

    def test_response_has_content(self):
        client = TestClient(_build_app())
        data = client.post("/api/social/generate", json=VALID_GENERATE_PAYLOAD).json()
        assert data["success"] is True
        assert "content" in data
        assert len(data["content"]) > 0

    def test_missing_topic_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/social/generate", json={"platform": "twitter"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/social/trending
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTrendingTopics:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/social/trending")
        assert resp.status_code == 200

    def test_default_platform_is_twitter(self):
        client = TestClient(_build_app())
        data = client.get("/api/social/trending").json()
        assert data["platform"] == "twitter"
        assert len(data["topics"]) > 0

    def test_explicit_platform_returns_topics(self):
        client = TestClient(_build_app())
        data = client.get("/api/social/trending?platform=linkedin").json()
        assert data["platform"] == "linkedin"

    def test_unknown_platform_returns_empty_topics(self):
        client = TestClient(_build_app())
        data = client.get("/api/social/trending?platform=myspace").json()
        assert data["topics"] == []


# ---------------------------------------------------------------------------
# POST /api/social/cross-post
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCrossPost:
    def test_cross_post_two_platforms_returns_201(self):
        client = TestClient(_build_app())
        resp = client.post("/api/social/cross-post", json=VALID_CROSS_POST_PAYLOAD)
        assert resp.status_code == 201

    def test_cross_post_response_has_post_ids(self):
        client = TestClient(_build_app())
        data = client.post("/api/social/cross-post", json=VALID_CROSS_POST_PAYLOAD).json()
        assert data["success"] is True
        assert "post_ids" in data
        assert len(data["post_ids"]) == 2

    def test_single_platform_returns_error(self):
        """Schema enforces min_items=2 — single platform returns 422 (Pydantic validation).
        The route's manual len < 2 check is unreachable but the schema guard is sufficient."""
        client = TestClient(_build_app())
        payload = {**VALID_CROSS_POST_PAYLOAD, "platforms": ["twitter"]}
        resp = client.post("/api/social/cross-post", json=payload)
        assert resp.status_code in (400, 422)

    def test_cross_post_creates_posts_in_store(self):
        client = TestClient(_build_app())
        client.post("/api/social/cross-post", json=VALID_CROSS_POST_PAYLOAD)
        data = client.get("/api/social/posts").json()
        assert data["analytics"]["total_posts"] == 2
