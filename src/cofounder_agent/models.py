"""
Database models for Settings Management and Authentication system.

Uses SQLAlchemy ORM with PostgreSQL backend (shared with Strapi).
All models include proper constraints, indexes, and audit tracking.
"""

from datetime import datetime, timedelta
from uuid import UUID
from typing import Optional, List, Dict, Any
from sqlalchemy import (
    Column, String, Boolean, Integer, DateTime, ForeignKey, Text, 
    JSON, Index, UniqueConstraint, CheckConstraint, Float,
    func, event, create_engine
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB, INET, ARRAY
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, validates
import uuid as uuid_lib

Base = declarative_base()


class User(Base):
    """User account model with authentication and 2FA support."""
    
    __tablename__ = "users"
    __table_args__ = (
        Index('idx_users_username', 'username'),
        Index('idx_users_email', 'email'),
        Index('idx_users_is_active', 'is_active'),
        CheckConstraint("email = LOWER(email)", name='email_lowercase'),
        CheckConstraint("username ~ '^[a-zA-Z0-9_-]+$'", name='username_format'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    first_name = Column(String(255))
    last_name = Column(String(255))
    
    # Authentication & Security
    is_active = Column(Boolean, default=True, index=True)
    is_locked = Column(Boolean, default=False)
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime)
    
    # Multi-Factor Authentication
    totp_secret = Column(String(255))
    totp_enabled = Column(Boolean, default=False)
    backup_codes = Column(ARRAY(String), default=[])
    
    # Tracking
    last_login = Column(DateTime)
    last_password_change = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    updated_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    
    # Metadata
    metadata_ = Column('metadata', JSONB, default={})
    
    # Relationships
    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan", foreign_keys="UserRole.user_id")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    created_users = relationship("User", remote_side=[created_by], foreign_keys=[created_by])
    
    @validates('email')
    def validate_email(self, key, value):
        """Ensure email is lowercase."""
        return value.lower() if value else value
    
    def is_account_locked(self) -> bool:
        """Check if account is locked and lock duration has expired."""
        if not self.is_locked:
            return False
        if self.locked_until and self.locked_until <= datetime.utcnow():
            self.is_locked = False
            self.locked_until = None
            self.failed_login_attempts = 0
            return False
        return True
    
    def increment_failed_login(self):
        """Increment failed login attempts and lock account if threshold exceeded."""
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= 5:
            self.is_locked = True
            self.locked_until = datetime.utcnow() + timedelta(minutes=30)
    
    def reset_failed_login(self):
        """Reset failed login attempts on successful login."""
        self.failed_login_attempts = 0
        self.is_locked = False
        self.locked_until = None
    
    def __repr__(self):
        return f"<User(id={self.id}, username={self.username})>"


class Role(Base):
    """Role definition model for RBAC system."""
    
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint("name ~ '^[A-Z_]+$'", name='valid_role_name'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text)
    is_system_role = Column(Boolean, default=False)  # System roles cannot be deleted
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    permissions = relationship("RolePermission", back_populates="role", cascade="all, delete-orphan")
    users = relationship("UserRole", back_populates="role", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Role(name={self.name})>"


class Permission(Base):
    """Permission definition model for RBAC system."""
    
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint('resource', 'action', name='uq_resource_action'),
        CheckConstraint("action IN ('read', 'write', 'delete', 'admin')", name='valid_action'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    resource = Column(String(100), nullable=False)  # 'settings', 'users', 'audit'
    action = Column(String(100), nullable=False)  # 'read', 'write', 'delete', 'admin'
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    roles = relationship("RolePermission", back_populates="permission", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Permission(resource={self.resource}, action={self.action})>"


class RolePermission(Base):
    """Association table for roles and permissions (many-to-many)."""
    
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint('role_id', 'permission_id', name='uq_role_permission'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    role_id = Column(PG_UUID(as_uuid=True), ForeignKey('roles.id', ondelete='CASCADE'), nullable=False)
    permission_id = Column(PG_UUID(as_uuid=True), ForeignKey('permissions.id', ondelete='CASCADE'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    role = relationship("Role", back_populates="permissions")
    permission = relationship("Permission", back_populates="roles")


class UserRole(Base):
    """Association table for users and roles (many-to-many)."""
    
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint('user_id', 'role_id', name='uq_user_role'),
        Index('idx_user_roles_user_id', 'user_id'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    role_id = Column(PG_UUID(as_uuid=True), ForeignKey('roles.id', ondelete='CASCADE'), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    assigned_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    
    # Relationships - explicitly specify foreign_keys to avoid ambiguity
    user = relationship("User", back_populates="roles", foreign_keys=[user_id])
    role = relationship("Role", back_populates="users")
    assigned_by_user = relationship("User", foreign_keys=[assigned_by])


class Session(Base):
    """User session model for tracking active sessions and tokens."""
    
    __tablename__ = "sessions"
    __table_args__ = (
        Index('idx_sessions_user_id', 'user_id'),
        Index('idx_sessions_token_jti', 'token_jti'),
        Index('idx_sessions_is_active', 'is_active'),
        Index('idx_sessions_expires_at', 'expires_at'),
        CheckConstraint('created_at <= expires_at', name='session_validity'),
        CheckConstraint(
            '(is_active AND revoked_at IS NULL) OR (NOT is_active AND revoked_at IS NOT NULL)',
            name='revocation_logic'
        ),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    token_jti = Column(String(255), unique=True, nullable=False)
    refresh_token_jti = Column(String(255), unique=True)
    
    # Device & Location
    ip_address = Column(INET)
    user_agent = Column(Text)
    device_name = Column(String(255))
    
    # Status
    is_active = Column(Boolean, default=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_activity = Column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = Column(DateTime)
    
    # Relationships
    user = relationship("User", back_populates="sessions")
    
    def is_expired(self) -> bool:
        """Check if session has expired."""
        return datetime.utcnow() >= self.expires_at
    
    def revoke(self):
        """Revoke the session."""
        self.is_active = False
        self.revoked_at = datetime.utcnow()
    
    def __repr__(self):
        return f"<Session(user_id={self.user_id}, is_active={self.is_active})>"


class Setting(Base):
    """Runtime configuration setting model with encryption support."""
    
    __tablename__ = "settings"
    __table_args__ = (
        Index('idx_settings_key', 'key'),
        Index('idx_settings_category', 'category'),
        Index('idx_settings_environment', 'environment'),
        Index('idx_settings_modified_at', 'modified_at'),
        UniqueConstraint('key', 'environment', name='uq_setting_key_env'),
        CheckConstraint("category IN ('ai_models', 'integrations', 'features', 'system', 'security', 'performance')", 
                       name='valid_category'),
        CheckConstraint("value_type IN ('string', 'number', 'boolean', 'json', 'secret')", 
                       name='valid_value_type'),
        CheckConstraint("environment IN ('development', 'staging', 'production')", 
                       name='valid_environment'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    
    # Identification
    key = Column(String(255), nullable=False)
    category = Column(String(100), nullable=False)
    display_name = Column(String(255))
    description = Column(Text)
    
    # Value & Type
    value = Column(Text)  # Encrypted if is_encrypted=True
    value_type = Column(String(50), default='string', nullable=False)
    
    # Security
    is_encrypted = Column(Boolean, default=False)
    is_sensitive = Column(Boolean, default=False)  # Masked in UI
    is_secret = Column(Boolean, default=False)  # Never logged in plaintext
    
    # Validation
    validation_rule = Column(JSONB, default={})
    allowed_values = Column(ARRAY(String))
    
    # Metadata
    is_active = Column(Boolean, default=True)
    requires_restart = Column(Boolean, default=False)
    requires_deployment = Column(Boolean, default=False)
    version = Column(Integer, default=1)
    
    # Tracking
    modified_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    modified_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Scoping
    environment = Column(String(50), default='production')
    
    # Relationships
    audit_logs = relationship("SettingAuditLog", back_populates="setting", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Setting(key={self.key}, category={self.category})>"


class SettingAuditLog(Base):
    """Immutable audit log for all setting changes."""
    
    __tablename__ = "settings_audit_log"
    __table_args__ = (
        Index('idx_audit_setting_id', 'setting_id'),
        Index('idx_audit_changed_by', 'changed_by'),
        Index('idx_audit_changed_at', 'changed_at'),
        Index('idx_audit_setting_changed', 'setting_id', 'changed_at'),
        CheckConstraint('changed_at <= NOW()', name='audit_immutable'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    
    # Reference
    setting_id = Column(PG_UUID(as_uuid=True), ForeignKey('settings.id', ondelete='RESTRICT'), nullable=False)
    
    # Change Details
    old_value = Column(Text)  # Encrypted if was_encrypted=True
    new_value = Column(Text)  # Encrypted if is_encrypted=True
    change_description = Column(Text)
    
    # Metadata
    was_encrypted = Column(Boolean)
    is_encrypted = Column(Boolean)
    
    # Audit Trail
    changed_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Request Context
    ip_address = Column(INET)
    user_agent = Column(Text)
    request_id = Column(String(255))
    
    # Recovery
    change_reason = Column(Text)
    can_rollback = Column(Boolean, default=True)
    
    # Relationships
    setting = relationship("Setting", back_populates="audit_logs")
    
    def __repr__(self):
        return f"<SettingAuditLog(setting_id={self.setting_id}, changed_at={self.changed_at})>"


class FeatureFlag(Base):
    """Feature flag model for gradual rollout of features."""
    
    __tablename__ = "feature_flags"
    __table_args__ = (
        Index('idx_flags_name', 'flag_name'),
        Index('idx_flags_enabled', 'is_enabled'),
        CheckConstraint('percentage >= 0 AND percentage <= 100', name='valid_percentage'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    
    # Identification
    flag_name = Column(String(255), unique=True, nullable=False)
    description = Column(Text)
    
    # Status
    is_enabled = Column(Boolean, default=False, index=True)
    percentage = Column(Integer, default=0)  # 0-100: percentage of users with flag
    
    # Targeting
    target_users = Column(ARRAY(PG_UUID(as_uuid=True)), default=[])
    target_roles = Column(ARRAY(String), default=[])
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    updated_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    
    def __repr__(self):
        return f"<FeatureFlag(flag_name={self.flag_name}, is_enabled={self.is_enabled})>"


class APIKey(Base):
    """API key model for programmatic access."""
    
    __tablename__ = "api_keys"
    __table_args__ = (
        Index('idx_api_keys_key_hash', 'key_hash'),
        Index('idx_api_keys_user_id', 'user_id'),
        Index('idx_api_keys_is_active', 'is_active'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    
    # Identification
    name = Column(String(255))
    key_hash = Column(String(255), unique=True, nullable=False)
    key_prefix = Column(String(10), nullable=False)  # First 10 chars for display
    
    # Ownership
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    
    # Permissions
    permissions = Column(ARRAY(String), default=[])
    allowed_ips = Column(ARRAY(INET), default=[])
    
    # Status
    is_active = Column(Boolean, default=True, index=True)
    
    # Limits
    rate_limit_per_hour = Column(Integer, default=1000)
    
    # Tracking
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime)
    expires_at = Column(DateTime)
    revoked_at = Column(DateTime)
    
    # Relationships
    user = relationship("User", back_populates="api_keys")
    
    def is_expired(self) -> bool:
        """Check if API key has expired."""
        return self.expires_at and datetime.utcnow() >= self.expires_at
    
    def revoke(self):
        """Revoke the API key."""
        self.is_active = False
        self.revoked_at = datetime.utcnow()
    
    def __repr__(self):
        return f"<APIKey(user_id={self.user_id}, prefix={self.key_prefix})>"


# Event listeners for audit logging
@event.listens_for(Setting, 'before_update')
def receive_before_update(mapper, connection, target):
    """Automatically track setting versions."""
    target.version = target.version + 1 if target.version else 2


# ============================================================================
# OPERATIONAL MODELS (Replaced Firestore Collections with PostgreSQL)
# ============================================================================
# These models replace Google Cloud Firestore collections:
# - tasks: Firestore 'tasks' collection
# - logs: Firestore 'logs' collection
# - financial_entries: Firestore 'financials' collection
# - agent_status: Firestore 'agents' collection
# - health_checks: Firestore 'health' collection

class Task(Base):
    """
    Task model - replaces Firestore 'tasks' collection
    
    Represents content creation and operational tasks queued for agents
    to process. Tracks status through pipeline: queued → running → completed/failed
    """
    __tablename__ = "tasks"
    __table_args__ = (
        Index('idx_status_created_at', 'status', 'created_at'),
        Index('idx_agent_id_status', 'agent_id', 'status'),
    )
    
    # Primary key
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    
    # Task metadata
    task_name = Column(String(255), nullable=False)
    agent_id = Column(String(255), nullable=False, index=True)
    status = Column(String(50), default='queued')  # queued, pending, running, completed, failed
    
    # Content details
    topic = Column(String(255), nullable=False)
    primary_keyword = Column(String(255))
    target_audience = Column(String(255))
    category = Column(String(255))
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, 
                       nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, 
                       onupdate=datetime.utcnow, nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    
    # Flexible metadata and results
    task_metadata = Column(JSONB, default={})
    result = Column(JSONB)  # Task result/output
    
    def __repr__(self):
        return f"<Task(id={self.id}, topic='{self.topic}', status='{self.status}')>"


class Log(Base):
    """
    Log model - replaces Firestore 'logs' collection
    
    Structured logging for operations, audit trail, and debugging.
    Includes context metadata for better observability.
    """
    __tablename__ = "logs"
    __table_args__ = (
        Index('idx_log_level_timestamp', 'level', 'timestamp'),
        Index('idx_log_timestamp_desc', 'timestamp'),
    )
    
    # Primary key
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    
    # Log details
    level = Column(String(20), nullable=False)  # debug, info, warning, error, critical
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, 
                      nullable=False, server_default=func.now())
    
    # Optional task reference
    task_id = Column(PG_UUID(as_uuid=True), ForeignKey('tasks.id'))
    
    # Optional agent reference
    agent_id = Column(String(255))
    
    # Context metadata
    log_metadata = Column(JSONB, default={})
    
    def __repr__(self):
        return f"<Log(level='{self.level}', message='{self.message[:50]}...')>"


class FinancialEntry(Base):
    """
    Financial Entry model - replaces Firestore 'financials' collection
    
    Tracks expenses, costs, and financial metrics for burn rate tracking,
    cost optimization, and budget forecasting.
    """
    __tablename__ = "financial_entries"
    __table_args__ = (
        Index('idx_financial_timestamp_category', 'timestamp', 'category'),
        Index('idx_financial_timestamp_desc', 'timestamp'),
    )
    
    # Primary key
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    
    # Financial details
    amount = Column(Float, nullable=False)
    category = Column(String(255), nullable=False)  # model_usage, storage, compute, etc.
    
    # Timestamps
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, 
                      nullable=False, server_default=func.now())
    
    # Optional task reference
    task_id = Column(PG_UUID(as_uuid=True), ForeignKey('tasks.id'))
    
    # Flexible metadata
    financial_metadata = Column(JSONB, default={})
    
    def __repr__(self):
        return f"<FinancialEntry(amount=${self.amount}, category='{self.category}')>"


class AgentStatus(Base):
    """
    Agent Status model - replaces Firestore 'agents' collection
    
    Tracks agent health, availability, and status for orchestration
    and monitoring purposes.
    """
    __tablename__ = "agent_status"
    __table_args__ = (
        Index('idx_status', 'status'),
        Index('idx_last_heartbeat', 'last_heartbeat'),
    )
    
    # Primary key (agent name is unique identifier)
    agent_name = Column(String(255), primary_key=True)
    
    # Status tracking
    status = Column(String(50), nullable=False)  # online, offline, busy, error
    
    # Heartbeat tracking
    last_heartbeat = Column(DateTime(timezone=True), default=datetime.utcnow, 
                          nullable=False, server_default=func.now())
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, 
                       nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, 
                       onupdate=datetime.utcnow, nullable=False, server_default=func.now())
    
    # Service info
    service_version = Column(String(50))
    
    # Flexible metadata
    agent_metadata = Column(JSONB, default={})
    
    def __repr__(self):
        return f"<AgentStatus(agent='{self.agent_name}', status='{self.status}')>"


class HealthCheck(Base):
    """
    Health Check model - replaces Firestore 'health' collection
    
    Periodic health check records for monitoring system availability
    and performance metrics.
    """
    __tablename__ = "health_checks"
    __table_args__ = (
        Index('idx_healthcheck_timestamp_desc', 'timestamp'),
        Index('idx_healthcheck_service', 'service'),
    )
    
    # Primary key
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    
    # Service and status
    service = Column(String(255), nullable=False)  # cofounder, content-agent, etc.
    status = Column(String(50), nullable=False)  # healthy, degraded, unhealthy
    
    # Timestamp
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, 
                      nullable=False, server_default=func.now())
    
    # Response time and metadata
    response_time_ms = Column(Float)
    health_metadata = Column(JSONB, default={})
    
    def __repr__(self):
        return f"<HealthCheck(service='{self.service}', status='{self.status}')>"


# ============================================================================
# CONTENT MANAGEMENT MODELS (Replaces Strapi)
# ============================================================================

class Author(Base):
    """Author model for blog content."""
    __tablename__ = "authors"
    __table_args__ = (
        Index('idx_authors_slug', 'slug'),
        UniqueConstraint('slug', name='unique_author_slug'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    email = Column(String(255))
    bio = Column(Text)
    avatar_url = Column(String(500))
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    posts = relationship("Post", back_populates="author", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Author(name='{self.name}', slug='{self.slug}')>"


class Category(Base):
    """Category model for organizing posts."""
    __tablename__ = "categories"
    __table_args__ = (
        Index('idx_categories_slug', 'slug'),
        UniqueConstraint('slug', name='unique_category_slug'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    posts = relationship("Post", back_populates="category")
    
    def __repr__(self):
        return f"<Category(name='{self.name}', slug='{self.slug}')>"


class Tag(Base):
    """Tag model for tagging posts."""
    __tablename__ = "tags"
    __table_args__ = (
        Index('idx_tags_slug', 'slug'),
        UniqueConstraint('slug', name='unique_tag_slug'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text)
    color = Column(String(7))  # Hex color for UI
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<Tag(name='{self.name}', slug='{self.slug}')>"


class Post(Base):
    """Blog post model."""
    __tablename__ = "posts"
    __table_args__ = (
        Index('idx_posts_slug', 'slug'),
        Index('idx_posts_status', 'status'),
        Index('idx_posts_published_at', 'published_at'),
        Index('idx_posts_author_id', 'author_id'),
        Index('idx_posts_category_id', 'category_id'),
        UniqueConstraint('slug', name='unique_post_slug'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    
    # Content
    title = Column(String(500), nullable=False)
    slug = Column(String(500), nullable=False, unique=True, index=True)
    content = Column(Text, nullable=False)  # Markdown content
    excerpt = Column(String(1000))
    
    # Media
    featured_image_url = Column(String(500))
    cover_image_url = Column(String(500))
    
    # Relations
    author_id = Column(PG_UUID(as_uuid=True), ForeignKey('authors.id'), nullable=False)
    category_id = Column(PG_UUID(as_uuid=True), ForeignKey('categories.id'))
    
    # Tags (many-to-many via JSON for simplicity, can be normalized later)
    tag_ids = Column(ARRAY(PG_UUID(as_uuid=True)), default=[])
    
    # SEO
    seo_title = Column(String(255))
    seo_description = Column(String(500))
    seo_keywords = Column(String(500))
    
    # Status & Publishing
    status = Column(String(50), default='draft', index=True)  # draft, published, archived
    published_at = Column(DateTime, index=True)
    
    # Metadata
    view_count = Column(Integer, default=0)
    metadata_ = Column('metadata', JSONB, default={})
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    updated_by = Column(PG_UUID(as_uuid=True), ForeignKey('users.id'))
    
    # Relationships
    author = relationship("Author", back_populates="posts")
    category = relationship("Category", back_populates="posts")
    
    def __repr__(self):
        return f"<Post(title='{self.title}', status='{self.status}')>"


class ContentMetric(Base):
    """Content performance metrics."""
    __tablename__ = "content_metrics"
    __table_args__ = (
        Index('idx_metrics_post_id', 'post_id'),
        Index('idx_metrics_date', 'metric_date'),
    )
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid_lib.uuid4)
    post_id = Column(PG_UUID(as_uuid=True), ForeignKey('posts.id'), nullable=False)
    
    # Metrics
    metric_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    views = Column(Integer, default=0)
    unique_visitors = Column(Integer, default=0)
    engagement_time_seconds = Column(Float, default=0.0)
    bounce_rate = Column(Float, default=0.0)
    shares = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    
    # Calculated fields
    engagement_score = Column(Float, default=0.0)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<ContentMetric(post_id='{self.post_id}', date='{self.metric_date}')>"


__all__ = [
    'Base',
    'User',
    'Role',
    'Permission',
    'RolePermission',
    'UserRole',
    'Session',
    'Setting',
    'SettingAuditLog',
    'FeatureFlag',
    'APIKey',
    # Operational models (PostgreSQL replacements for Firestore)
    'Task',
    'Log',
    'FinancialEntry',
    'AgentStatus',
    'HealthCheck',
    # Content management models (replaces Strapi)
    'Author',
    'Category',
    'Tag',
    'Post',
    'ContentMetric',
]
