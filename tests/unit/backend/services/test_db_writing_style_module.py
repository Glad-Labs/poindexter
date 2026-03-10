"""Tests for WritingStyleDatabase module with correct method signatures."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from src.cofounder_agent.services.writing_style_db import WritingStyleDatabase


@pytest.fixture
def writing_style_db(mock_pool):
    """Create WritingStyleDatabase instance with mocked connection pool."""
    return WritingStyleDatabase(mock_pool)


class TestWritingStyleDatabaseCreation:
    """Tests for writing sample creation."""

    @pytest.mark.asyncio
    async def test_create_writing_sample_with_dict(self, writing_style_db, mock_pool):
        """Test create_writing_sample with proper parameters."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        # Mock fetchrow (not fetchval) - create_writing_sample calls fetchrow
        mock_conn.fetchrow.return_value = {
            "id": 1,
            "user_id": "user_123",
            "title": "Professional Blog Post",
            "description": "",
            "content": "This is a professional blog post about AI...",
            "is_active": True,
            "word_count": 8,
            "char_count": 48,
            "created_at": now,
            "updated_at": now
        }
        
        result = await writing_style_db.create_writing_sample(
            user_id="user_123",
            title="Professional Blog Post",
            content="This is a professional blog post about AI..."
        )
        
        assert result is not None
        assert isinstance(result, dict)
        assert result["id"] == "1"  # Converted to string by _format_sample
        assert result["user_id"] == "user_123"
        assert mock_conn.fetchrow.called  # For inserting the sample

    @pytest.mark.asyncio
    async def test_create_writing_sample_with_features(self, writing_style_db, mock_pool):
        """Test create_writing_sample with optional features parameter."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "id": 2,
            "user_id": "user_456",
            "title": "Marketing Copy",
            "description": "Marketing style sample",
            "content": "Discover the power of...",
            "is_active": False,
            "word_count": 4,
            "char_count": 26,
            "created_at": now,
            "updated_at": now
        }
        
        result = await writing_style_db.create_writing_sample(
            user_id="user_456",
            title="Marketing Copy",
            content="Discover the power of...",
            description="Marketing style sample",
            set_as_active=False
        )
        
        assert result is not None
        assert isinstance(result, dict)
        assert result["description"] == "Marketing style sample"


class TestWritingStyleDatabaseRetrieval:
    """Tests for writing sample retrieval."""

    @pytest.mark.asyncio
    async def test_get_writing_sample_returns_optional_dict(self, writing_style_db, mock_pool):
        """Test get_writing_sample returns Optional[Dict[str, Any]]."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "id": 1,
            "user_id": "user_123",
            "title": "Professional Blog Post",
            "description": "",
            "content": "This is a professional blog post...",
            "is_active": True,
            "word_count": 6,
            "char_count": 40,
            "created_at": now,
            "updated_at": now
        }
        
        sample = await writing_style_db.get_writing_sample(sample_id="1")
        
        assert sample is not None
        assert isinstance(sample, dict)
        assert sample["title"] == "Professional Blog Post"
        assert mock_conn.fetchrow.called

    @pytest.mark.asyncio
    async def test_get_writing_sample_not_found(self, writing_style_db, mock_pool):
        """Test get_writing_sample returns None when not found."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = None
        
        sample = await writing_style_db.get_writing_sample(sample_id="99999")
        
        assert sample is None


class TestWritingStyleDatabaseUpdates:
    """Tests for writing sample updates."""

    @pytest.mark.asyncio
    async def test_update_writing_sample(self, writing_style_db, mock_pool):
        """Test updating a writing sample."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "id": 1,
            "user_id": "user_123",
            "title": "Updated Title",
            "description": "",
            "content": "Updated content...",
            "is_active": True,
            "word_count": 2,
            "char_count": 18,
            "created_at": now,
            "updated_at": now
        }
        
        # Check if update_writing_sample method exists
        if hasattr(writing_style_db, "update_writing_sample"):
            result = await writing_style_db.update_writing_sample(
                sample_id="1",
                user_id="user_123",
                title="Updated Title",
                content="Updated content..."
            )
            assert result is not None


class TestWritingStyleDatabaseDeletion:
    """Tests for writing sample deletion."""

    @pytest.mark.asyncio
    async def test_delete_writing_sample_requires_user_id(self, writing_style_db, mock_pool):
        """Test delete_writing_sample requires both sample_id and user_id parameters."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        # Mock execute to return "DELETE 1" (rows affected)
        mock_conn.execute.return_value = "DELETE 1"
        
        result = await writing_style_db.delete_writing_sample(
            sample_id="1",
            user_id="user_123"
        )
        
        assert isinstance(result, bool)
        assert result is True
        assert mock_conn.execute.called

    @pytest.mark.asyncio
    async def test_delete_writing_sample_not_found(self, writing_style_db, mock_pool):
        """Test delete_writing_sample returns False when sample doesn't exist."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        # Mock execute to return "DELETE 0" (no rows affected)
        mock_conn.execute.return_value = "DELETE 0"
        
        result = await writing_style_db.delete_writing_sample(
            sample_id="99999",
            user_id="user_123"
        )
        
        assert isinstance(result, bool)
        assert result is False


class TestWritingStyleDatabaseOtherMethods:
    """Tests for other WritingStyleDatabase methods."""

    @pytest.mark.asyncio
    async def test_method_existence(self, writing_style_db, mock_pool):
        """Test that expected methods exist on WritingStyleDatabase."""
        expected_methods = [
            "create_writing_sample",
            "get_writing_sample",
            "delete_writing_sample",
            "get_user_writing_samples",
        ]
        
        for method in expected_methods:
            assert hasattr(writing_style_db, method), f"WritingStyleDatabase should have {method} method"

    @pytest.mark.asyncio
    async def test_get_user_writing_samples(self, writing_style_db, mock_pool):
        """Test retrieving all samples for a user."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetch.return_value = [
            {
                "id": 1,
                "user_id": "user_123",
                "title": "Sample 1",
                "description": "",
                "content": "Sample 1 content...",
                "is_active": True,
                "word_count": 3,
                "char_count": 20,
                "created_at": now,
                "updated_at": now
            },
            {
                "id": 2,
                "user_id": "user_123",
                "title": "Sample 2",
                "description": "",
                "content": "Sample 2 content...",
                "is_active": False,
                "word_count": 3,
                "char_count": 20,
                "created_at": now,
                "updated_at": now
            }
        ]
        
        if hasattr(writing_style_db, "get_user_writing_samples"):
            samples = await writing_style_db.get_user_writing_samples(user_id="user_123")
            assert isinstance(samples, list)
            assert len(samples) == 2
            samples = await writing_style_db.get_user_writing_samples(user_id="user_123")
            assert isinstance(samples, list)
            assert len(samples) == 2
            assert mock_conn.fetch.called
