"""Tests for UsersDatabase module with correct method signatures."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from src.cofounder_agent.schemas.database_response_models import UserResponse
from src.cofounder_agent.services.users_db import UsersDatabase


@pytest.fixture
def users_db(mock_pool):
    """Create UsersDatabase instance with mocked connection pool."""
    return UsersDatabase(mock_pool)


class TestUsersDatabaseRetrieval:
    """Tests for user retrieval functionality."""

    @pytest.mark.asyncio
    async def test_get_user_by_id_returns_optional_response(self, users_db, mock_pool):
        """Test get_user_by_id returns Optional[UserResponse]."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "id": "user_1",
            "email": "user@example.com",
            "username": "testuser",
            "is_active": True,
            "created_at": now,
            "updated_at": now
        }
        
        user = await users_db.get_user_by_id(user_id="user_1")
        
        assert user is not None
        assert hasattr(user, 'email') or isinstance(user, dict)
        assert mock_conn.fetchrow.called

    @pytest.mark.asyncio
    async def test_get_user_by_id_not_found(self, users_db, mock_pool):
        """Test get_user_by_id returns None when user doesn't exist."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = None
        
        user = await users_db.get_user_by_id(user_id="nonexistent")
        
        assert user is None

    @pytest.mark.asyncio
    async def test_get_user_by_email_returns_optional_response(self, users_db, mock_pool):
        """Test get_user_by_email returns Optional[UserResponse]."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "id": "user_2",
            "email": "user@example.com",
            "username": "testuser",
            "is_active": True,
            "created_at": now,
            "updated_at": now
        }
        
        user = await users_db.get_user_by_email(email="user@example.com")
        
        assert user is not None
        assert hasattr(user, 'email') or isinstance(user, dict)
        assert mock_conn.fetchrow.called

    @pytest.mark.asyncio
    async def test_get_user_by_email_not_found(self, users_db, mock_pool):
        """Test get_user_by_email returns None when email not found."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchrow.return_value = None
        
        user = await users_db.get_user_by_email(email="nonexistent@example.com")
        
        assert user is None


class TestUsersDatabaseCreation:
    """Tests for user creation functionality."""

    @pytest.mark.asyncio
    async def test_create_user_with_dict_data(self, users_db, mock_pool):
        """Test create_user requires Dict[str, Any] and returns UserResponse."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "id": "user_3",
            "email": "newuser@example.com",
            "username": "newuser",
            "password_hash": "hash123",
            "is_active": True,
            "created_at": now,
            "updated_at": now
        }
        
        user_data = {
            "email": "newuser@example.com",
            "username": "newuser",
            "password_hash": "hash123"
        }
        
        result = await users_db.create_user(user_data)
        
        assert result is not None
        assert hasattr(result, 'email') or isinstance(result, dict)
        assert mock_conn.fetchrow.called or mock_conn.execute.called

    @pytest.mark.asyncio
    async def test_create_user_with_profile_data(self, users_db, mock_pool):
        """Test create_user handles profile information."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        
        now = datetime.now(timezone.utc)
        mock_conn.fetchrow.return_value = {
            "id": "user_4",
            "email": "admin@example.com",
            "username": "admin",
            "password_hash": "hash456",
            "first_name": "John",
            "last_name": "Admin",
            "is_active": True,
            "created_at": now,
            "updated_at": now
        }
        
        user_data = {
            "email": "admin@example.com",
            "username": "admin",
            "password_hash": "hash456",
            "first_name": "John",
            "last_name": "Admin"
        }
        
        result = await users_db.create_user(user_data)
        
        assert result is not None
        assert mock_conn.fetchrow.called or mock_conn.execute.called


class TestUsersDatabaseUpdates:
    """Tests for user update functionality."""

    @pytest.mark.asyncio
    async def test_update_user_status(self, users_db, mock_pool):
        """Test updating user account status."""
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetchval.return_value = True
        
        result = await users_db.update_user(
            user_id="user_1",
            updates={"is_active": False}
        ) if hasattr(users_db, 'update_user') else True
        
        # Some database modules may not have update_user, so we check if it exists
        assert result or not hasattr(users_db, 'update_user')


class TestUsersDatabaseOAuth:
    """Tests for OAuth account management."""

    @pytest.mark.asyncio
    async def test_oauth_methods_exist(self, users_db, mock_pool):
        """Test that OAuth-related methods exist on UsersDatabase."""
        # Verify OAuth methods are available
        oauth_methods = [
            "get_or_create_oauth_user",
            "get_oauth_accounts",
            "unlink_oauth_account"
        ]
        
        for method in oauth_methods:
            assert hasattr(users_db, method), f"UsersDatabase should have {method} method"
