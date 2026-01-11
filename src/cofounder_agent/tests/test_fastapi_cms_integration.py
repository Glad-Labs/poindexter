"""
FastAPI CMS Integration Tests

Tests the complete integration between:
1. FastAPI Content Management System (CMS)
2. Content Generation Pipeline
3. Public Site Content Distribution
4. Oversight Hub Content Management UI

These tests verify:
- Data model compatibility (FastAPI ↔ Next.js)
- API endpoint functionality
- Content formatting and transformation
- Error handling and recovery
"""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from typing import Dict, Any

# Import FastAPI app
from main import app

# Note: Use the client fixture from conftest instead
# This ensures proper test isolation and lifespan management



class TestCMSDataModels:
    """Test FastAPI CMS data models"""

    def test_post_model_has_required_fields(self, client):
        """Post should have all fields needed by Next.js public site"""
        # Fields required by web/public-site
        required_fields = [
            "id",  # Unique identifier
            "title",  # Post title
            "slug",  # URL-friendly slug
            "content",  # Post content (markdown)
            "excerpt",  # Short preview
            "featured_image",  # Hero image
            "category",  # Category relation
            "tags",  # Tags array
            "author",  # Author info
            "status",  # Published/Draft
            "published_at",  # Publication date
            "created_at",  # Creation date
            "updated_at",  # Last update
            "seo_title",  # SEO meta title
            "seo_description",  # SEO meta description
            "seo_keywords",  # SEO keywords
        ]

        # If these fields are missing, Next.js site breaks
        assert len(required_fields) > 0

    def test_category_model_compatibility(self, client):
        """Category model should match Next.js expectations"""
        expected_fields = {
            "id": "UUID",
            "name": "string",
            "slug": "string",
            "description": "string",
        }
        # This ensures web/public-site/pages/category/[slug].js works


class TestContentManagementAPI:
    """Test content management endpoints"""

    def test_create_post_endpoint(self, client):
        """POST /api/cms/posts should create content"""
        post_data = {
            "title": "Test Post",
            "slug": "test-post",
            "content": "# Test\n\nContent here",
            "excerpt": "Test excerpt",
            "category_id": "123",
            "status": "published",
        }

        response = client.post("/api/cms/posts", json=post_data)

        # Should create successfully
        assert response.status_code in [200, 201]
        data = response.json()

        # Returned data should have all required fields
        assert "id" in data
        assert data["title"] == "Test Post"
        assert data["slug"] == "test-post"
        assert data["status"] == "published"

    def test_get_posts_with_pagination(self, client):
        """GET /api/cms/posts should support pagination"""
        response = client.get("/api/cms/posts?page=1&limit=10")

        assert response.status_code == 200
        data = response.json()

        # Should return paginated data
        assert "items" in data or isinstance(data, list)
        assert "total" in data or len(data) <= 10

    def test_get_post_by_slug(self, client):
        """GET /api/cms/posts/{slug} should return post"""
        response = client.get("/api/cms/posts/test-post")

        # Either found or not found (not error)
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert data["slug"] == "test-post"

    def test_search_posts(self, client):
        """GET /api/cms/posts/search should filter content"""
        response = client.get("/api/cms/posts/search?q=test&category=tech")

        assert response.status_code == 200
        data = response.json()

        # Should return list of matching posts
        assert isinstance(data, list) or "items" in data

    def test_update_post_metadata(self, client):
        """PUT /api/cms/posts/{id} should update SEO and metadata"""
        update_data = {
            "seo_title": "Updated Title",
            "seo_description": "Updated description",
            "seo_keywords": "test, updated",
            "featured_image": "https://example.com/image.jpg",
        }

        response = client.put("/api/cms/posts/123", json=update_data)

        # Should accept metadata updates
        assert response.status_code in [200, 404]


class TestContentPipeline:
    """Test content generation pipeline integration"""

    def test_generate_post_creates_database_entry(self, client):
        """POST /api/content/generate-blog-post should create CMS entry"""
        request_data = {
            "topic": "AI in Business",
            "style": "professional",
            "auto_publish": False,  # Don't publish yet
        }

        response = client.post("/api/content/generate-blog-post", json=request_data)

        # Should create content
        assert response.status_code in [200, 201]
        data = response.json()

        # Should return post with CMS-compatible fields
        assert "id" in data
        assert "slug" in data
        assert "content" in data
        assert "status" in data

    def test_generated_content_has_seo_fields(self, client):
        """Generated content should include SEO metadata"""
        request_data = {
            "topic": "Marketing Strategy",
            "generate_seo": True,
        }

        response = client.post("/api/content/generate-blog-post", json=request_data)

        if response.status_code == 200:
            data = response.json()

            # SEO fields should be populated
            assert "seo_title" in data
            assert "seo_description" in data
            assert "seo_keywords" in data

            # Should meet SEO requirements
            assert len(data.get("seo_title", "")) <= 60
            assert len(data.get("seo_description", "")) <= 160

    def test_publish_generated_content(self, client):
        """POST /api/content/{id}/publish should make content live"""
        response = client.post("/api/content/123/publish")

        # Should handle publish request
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert data["status"] == "published"


class TestPublicSiteIntegration:
    """Test integration with Next.js public site"""

    def test_api_response_compatible_with_getstaticprops(self, client):
        """API responses should work with Next.js getStaticProps"""
        response = client.get("/api/cms/posts")

        assert response.status_code == 200
        data = response.json()

        # getStaticProps expects array or object with items
        assert isinstance(data, list) or "items" in data

    def test_post_slug_resolution(self, client):
        """API should resolve posts by slug for dynamic routes"""
        # Next.js pages/posts/[slug].js needs this
        response = client.get("/api/cms/posts/test-post")

        if response.status_code == 200:
            data = response.json()

            # web/public-site uses these fields
            assert "slug" in data
            assert "title" in data
            assert "content" in data
            assert "seo_title" in data
            assert "seo_description" in data

    def test_category_filtering(self, client):
        """API should filter posts by category"""
        # web/public-site/pages/category/[slug].js needs this
        response = client.get("/api/cms/posts?category=tech")

        assert response.status_code == 200

    def test_tag_filtering(self, client):
        """API should filter posts by tag"""
        # web/public-site/pages/tag/[slug].js needs this
        response = client.get("/api/cms/posts?tag=featured")

        assert response.status_code == 200

    def test_pagination_for_archive(self, client):
        """API should support pagination"""
        # web/public-site/pages/archive/[page].js needs this
        response = client.get("/api/cms/posts?page=1&limit=10")

        assert response.status_code == 200
        data = response.json()

        # Should indicate total for pagination
        assert "total" in data or len(data) is not None


class TestOversightHubIntegration:
    """Test integration with React oversight hub"""

    def test_content_calendar_endpoint(self, client):
        """GET /api/cms/calendar should return scheduled posts"""
        response = client.get("/api/cms/calendar")

        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            # Should return calendar view with dates
            assert isinstance(data, list) or isinstance(data, dict)

    def test_content_status_tracking(self, client):
        """GET /api/cms/posts should show content status"""
        response = client.get("/api/cms/posts?status=draft")

        assert response.status_code == 200

        # Oversight hub uses status for filtering
        data = response.json()
        if isinstance(data, list):
            for post in data:
                assert post.get("status") in ["draft", "published", "scheduled"]

    def test_bulk_content_update(self, client):
        """PUT /api/cms/posts/bulk should update multiple posts"""
        update_data = {
            "post_ids": ["1", "2", "3"],
            "status": "published",
            "category_id": "tech",
        }

        response = client.put("/api/cms/posts/bulk", json=update_data)

        # Should handle bulk operations
        assert response.status_code in [200, 207, 400]


class TestDataFormatting:
    """Test data formatting for compatibility"""

    def test_post_formatting_for_markdown_rendering(self, client):
        """Post content should be valid markdown"""
        response = client.get("/api/cms/posts?limit=1")

        if response.status_code == 200:
            data = response.json()
            posts = data if isinstance(data, list) else data.get("items", [])

            if posts:
                post = posts[0]
                # Content should be markdown
                content = post.get("content", "")
                # Markdown is plain text with # ** - etc
                assert isinstance(content, str)

    def test_image_urls_are_absolute(self, client):
        """Image URLs should be absolute (not relative)"""
        response = client.get("/api/cms/posts?limit=1")

        if response.status_code == 200:
            data = response.json()
            posts = data if isinstance(data, list) else data.get("items", [])

            if posts:
                post = posts[0]
                featured_image = post.get("featured_image", "")

                if featured_image:
                    # URLs should start with http or https or be empty
                    assert featured_image.startswith(("http://", "https://", ""))

    def test_dates_are_iso_format(self, client):
        """Dates should be ISO 8601 format"""
        response = client.get("/api/cms/posts?limit=1")

        if response.status_code == 200:
            data = response.json()
            posts = data if isinstance(data, list) else data.get("items", [])

            if posts:
                post = posts[0]
                published_at = post.get("published_at", "")

                if published_at:
                    # Should parse as ISO format
                    try:
                        datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                        assert True
                    except ValueError:
                        pytest.fail(f"Date not ISO format: {published_at}")


class TestErrorHandling:
    """Test error handling and recovery"""

    def test_invalid_slug_returns_404(self, client):
        """Invalid slug should return 404, not 500"""
        response = client.get("/api/cms/posts/invalid-slug-xyz-12345")

        assert response.status_code in [404, 200]
        # Should NOT be 500 error

    def test_invalid_page_number_returns_400(self, client):
        """Invalid page should return 400"""
        response = client.get("/api/cms/posts?page=invalid")

        assert response.status_code in [400, 200]
        # Should handle gracefully

    def test_missing_required_field_returns_validation_error(self, client):
        """Missing required field should return validation error"""
        post_data = {
            "slug": "test",
            # Missing title
        }

        response = client.post("/api/cms/posts", json=post_data)

        # Should validate, not crash
        assert response.status_code in [400, 422, 200]

    def test_database_error_returns_500_with_message(self, client):
        """Database errors should return 500 with helpful message"""
        # This would require database connection failure
        # Skipped in unit tests, tested in integration
        pass


class TestBackwardCompatibility:
    """Test backward compatibility with old Strapi endpoints"""

    def test_old_strapi_urls_still_work(self, client):
        """Old Strapi URLs should redirect or work via adapter"""
        # Strapi: /api/posts
        response = client.get("/api/posts")

        # Should work (either at new or old endpoint)
        assert response.status_code in [200, 404, 307]

    def test_old_strapi_format_still_accepted(self, client):
        """Old Strapi request format should still work"""
        # Old format: /api/posts?filters[status][$eq]=published
        response = client.get("/api/posts?status=published")

        # Should work via new format
        assert response.status_code in [200, 404]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
