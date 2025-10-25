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
    JSON, ARRAY, INET, Index, UniqueConstraint, CheckConstraint,
    func, event, create_engine
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
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
    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    created_users = relationship("User", remote_side=[created_by])
    
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
    
    # Relationships
    user = relationship("User", back_populates="roles")
    role = relationship("Role", back_populates="users")


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
]
