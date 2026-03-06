"""Tests for ContentDatabase module with correct method signatures."""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock

from src.cofounder_agent.schemas.database_response_models import PostResponse
from src.cofounder_agent.services.content_db import ContentDatabase


@pytest.fixture
def content_db(mock_pool):
    """Create ContentDatabase instance with mocked connection pool."""
    return ContentDatabase(mock_pool)


class TestContentDatabaseCreation:
    """Tests for content creation functionality."""

    @pytest.mark.asyncio
    async def test_create_post_with_dict_data(self, content_db, mock_pool):
        """Test create_post requires Dict[str, Any] and returns PostResponse."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.utcnow()
        mock_conn.fetchrow.return_value = {
            "id": 1,
            "title": "AI in 2024",
            "slug": "ai-in-2024",
            "content": "Content here...",
            "author_id": "author_123",
            "status": "draft",
            "quality_score": None,
            "created_at": now,
            "updated_at": now
        }
        
        post_data = {
            "title": "AI in 2024",
            "slug": "ai-in-2024",
            "content": "Content here...",
            "author_id": "author_123"
        }
        
        result = await content_db.create_post(post_data)
        
        assert result is not None
        # Result is PostResponse Pydantic model
        assert hasattr(result, 'title') or isinstance(result, dict)
        assert mock_conn.fetchrow.called or mock_conn.execute.called

    @pytest.mark.asyncio
    async def test_create_post_with_metadata(self, content_db, mock_pool):
        """Test create_post handles comprehensive metadata."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.utcnow()
        mock_conn.fetchrow.return_value = {
            "id": 2,
            "title": "Marketing Guide",
            "slug": "marketing-guide",
            "content": "Guide content...",
            "author_id": "author_456",
            "tags": ["marketing", "guide"],
            "created_at": now,
            "updated_at": now
        }
        
        post_data = {
            "title": "Marketing Guide",
            "slug": "marketing-guide",
            "content": "Guide content...",
            "author_id": "author_456",
            "tags": ["marketing", "guide"],
            "meta_description": "Learn marketing"
        }
        
        result = await content_db.create_post(post_data)
        
        assert result is not None
        assert mock_conn.execute.called


class TestContentDatabaseRetrieval:
    """Tests for content retrieval functionality."""

    @pytest.mark.asyncio
    async def test_get_post_by_slug_returns_post_response(self, content_db, mock_pool):
        """Test get_post_by_slug returns Optional[PostResponse]."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.utcnow()
        mock_conn.fetchrow.return_value = {
            "id": 1,
            "title": "AI in 2024",
            "slug": "ai-in-2024",
            "content": "Content here...",
            "author_id": "author_123",
            "created_at": now,
            "updated_at": now
        }
        
        post = await content_db.get_post_by_slug(slug="ai-in-2024")
        
        assert post is not None
        assert hasattr(post, 'slug') or isinstance(post, dict)
        assert mock_conn.fetchrow.called

    @pytest.mark.asyncio
    async def test_get_post_by_slug_not_found(self, content_db, mock_pool):
        """Test get_post_by_slug returns None when not found."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = None
        
        post = await content_db.get_post_by_slug(slug="nonexistent")
        
        assert post is None


class TestContentDatabaseUpdates:
    """Tests for content update functionality."""

    @pytest.mark.asyncio
    async def test_update_post_with_dict_updates(self, content_db, mock_pool):
        """Test update_post requires Dict[str, Any] updates and returns bool."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchval.return_value = 1  # rows affected
        
        updates = {
            "title": "Updated Title",
            "content": "Updated content..."
        }
        
        result = await content_db.update_post(post_id=1, updates=updates)
        
        assert isinstance(result, bool)
        assert result is True
        assert mock_conn.execute.called

    @pytest.mark.asyncio
    async def test_update_post_no_changes(self, content_db, mock_pool):
        """Test update_post with empty updates dict."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchval.return_value = 0  # no rows affected
        
        result = await content_db.update_post(post_id=1, updates={})
        
        assert isinstance(result, bool)


class TestContentDatabaseQuality:
    """Tests for quality evaluation functionality."""

    @pytest.mark.asyncio
    async def test_create_quality_evaluation(self, content_db, mock_pool):
        """Test creating quality evaluation record."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchval.return_value = 1  # evaluation ID
        
        evaluation_data = {
            "post_id": 1,
            "score": 8.5,
            "evaluator": "qa_agent",
            "feedback": "Good content"
        }
        
        result = await content_db.create_quality_evaluation(evaluation_data)
        
        # Should return int ID or success indicator
        assert result is not None
        assert mock_conn.execute.called

    @pytest.mark.asyncio
    async def test_create_quality_evaluation_with_metrics(self, content_db, mock_pool):
        """Test quality evaluation with detailed metrics."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchval.return_value = 2
        
        evaluation_data = {
            "post_id": 2,
            "score": 7.8,
            "evaluator": "creative_agent",
            "feedback": "Clear messaging",
            "metrics": {
                "readability": 8.0,
                "engagement": 7.5,
                "seo_score": 8.2
            }
        }
        
        result = await content_db.create_quality_evaluation(evaluation_data)
        
        assert result is not None
        assert mock_conn.execute.called
