"""
OAuth-Only User Model Updates and New OAuthAccount Model

This file demonstrates the changes needed to models.py for OAuth-only authentication.
The User model is simplified to remove all password-based auth fields.
A new OAuthAccount model is added to store OAuth provider connections.
"""

# ============================================================================

# UPDATED User Model (OAuth-Only Version)

# ============================================================================

#

# Remove these fields from User model:

# - password_hash (no password auth)

# - first_name, last_name (not always provided by OAuth)

# - is_locked, failed_login_attempts, locked_until (no password brute force)

# - totp_secret, totp_enabled, backup_codes (OAuth provider handles 2FA)

# - last_password_change (not relevant)

# - created_by, updated_by (audit tracking - optional for MVP)

# - sessions, api_keys relationships (simplify for OAuth)

#

# Keep these fields:

# - id (PG_UUID primary key)

# - username (from OAuth provider)

# - email (primary contact)

# - is_active (can deactivate accounts)

# - last_login (track when user last logged in)

# - created_at, updated_at (timestamps)

# - metadata\_ (JSONB for flexible data)

# - roles relationship (RBAC still applies)

#

# Add:

# - oauth_accounts relationship (new OAuthAccount model)

# BEFORE: ~200 lines with auth complexity

# AFTER: ~80 lines, clean and simple

from datetime import datetime
from uuid import UUID
from typing import Optional, List, Dict, Any
from sqlalchemy import (
Column, String, Boolean, DateTime, ForeignKey, Text,
Index, CheckConstraint, func, event
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship, validates
import uuid as uuid_lib

class User(Base):
"""Simplified user model for OAuth-only authentication.

    This model stores basic user information obtained from OAuth providers.
    All authentication is handled externally by OAuth providers.
    Authorization is managed through the RBAC system (roles/permissions).
    """

    __tablename__ = "users"
    __table_args__ = (
        Index('idx_users_username', 'username'),
        Index('idx_users_email', 'email'),
        Index('idx_users_is_active', 'is_active'),
        Index('idx_users_created_at', 'created_at'),
        CheckConstraint("email = LOWER(email)", name='email_lowercase'),
    )

    # Core fields
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)

    # Activity tracking
    is_active = Column(Boolean, default=True, index=True)
    last_login = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Flexible metadata for future extensions
    metadata_ = Column('metadata', JSONB, default={})

    # Relationships
    oauth_accounts = relationship(
        "OAuthAccount",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="OAuthAccount.user_id"
    )
    roles = relationship(
        "UserRole",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserRole.user_id"
    )

    @validates('email')
    def validate_email(self, key, value):
        """Ensure email is lowercase."""
        return value.lower() if value else value

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, email={self.email})>"

# ============================================================================

# NEW OAuthAccount Model

# ============================================================================

#

# This model stores OAuth provider connections for each user.

# A user can have multiple OAuth accounts (GitHub + Google, etc.)

#

# Structure:

# - id: Primary key

# - user_id: FK to users table

# - provider: OAuth provider name ('github', 'google', 'facebook', etc.)

# - provider_user_id: User's ID from the OAuth provider

# - provider_data: JSON data from OAuth provider (name, avatar, etc.)

# - created_at: When the account was linked

#

# Unique constraint on (provider, provider_user_id) ensures:

# - Same OAuth account can't be linked twice

# - But a user can link multiple OAuth providers

#

# Example data:

# {

# "user_id": "550e8400-e29b-41d4-a716-446655440000",

# "provider": "github",

# "provider_user_id": "12345678",

# "provider_data": {

# "username": "octocat",

# "email": "octocat@github.com",

# "avatar_url": "https://avatars.githubusercontent.com/u/12345678",

# "bio": "I'm a software developer",

# "location": "San Francisco",

# "public_repos": 42,

# "followers": 100

# }

# }

class OAuthAccount(Base):
"""OAuth provider account linking for users.

    Each user can have multiple OAuth accounts (GitHub, Google, Facebook, etc.)
    This model tracks which OAuth providers are linked to each user.
    """

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        Index('idx_oauth_accounts_user_id', 'user_id'),
        Index('idx_oauth_accounts_provider', 'provider'),
        UniqueConstraint(
            'provider',
            'provider_user_id',
            name='uq_oauth_provider_user_id'
        ),  # Ensure same OAuth account can't be linked twice
    )

    # Core fields
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # OAuth provider info
    provider = Column(
        String(50),
        nullable=False,
        index=True
    )  # 'github', 'google', 'facebook', etc.
    provider_user_id = Column(String(255), nullable=False)  # ID from OAuth provider

    # Provider data (cached from OAuth provider)
    provider_data = Column(JSONB, default={})  # username, email, avatar_url, etc.

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    last_used = Column(DateTime, default=datetime.utcnow, nullable=False)  # Track usage

    # Relationships
    user = relationship("User", back_populates="oauth_accounts", foreign_keys=[user_id])

    def __repr__(self):
        return f"<OAuthAccount(provider={self.provider}, user_id={self.user_id})>"

# ============================================================================

# Migration Instructions for database.py

# ============================================================================

#

# In database.py, add the OAuthAccount table definition to ensure it's

# created when the database is initialized:

#

# from .models import OAuthAccount

#

# # In create_all() or engine initialization:

# OAuthAccount.**table**.create(engine, checkfirst=True)

#

# For existing deployments, you'd create a database migration:

#

# 1. Drop: sessions, api_keys tables (no longer needed)

# 2. Modify: users table (remove password/2FA fields)

# 3. Create: oauth_accounts table

#

# This can be done with Alembic or raw SQL depending on your setup.
