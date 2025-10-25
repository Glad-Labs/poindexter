"""
Alembic migrations configuration for database schema management.

This is the initial migration that creates all 10 tables for the
Settings Management and Authentication system.

Run with:
    alembic upgrade head
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid


def upgrade() -> None:
    """
    Create all tables for Settings Management system.
    
    Tables created:
    - users: User accounts with authentication
    - roles: RBAC roles (ADMIN, MANAGER, OPERATOR, VIEWER)
    - permissions: Resource-action permissions
    - role_permissions: Role-permission mapping (many-to-many)
    - user_roles: User-role mapping (many-to-many)
    - sessions: Active user sessions
    - settings: Runtime configuration values
    - settings_audit_log: Immutable audit trail
    - feature_flags: Feature flag management
    - api_keys: API key authentication
    """
    
    # ========================================================================
    # 1. USERS TABLE
    # ========================================================================
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('username', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('first_name', sa.String(255), nullable=True),
        sa.Column('last_name', sa.String(255), nullable=True),
        
        # Authentication & Security
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_locked', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
        
        # Multi-Factor Authentication
        sa.Column('totp_secret', sa.String(255), nullable=True),
        sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('backup_codes', postgresql.ARRAY(sa.String()), nullable=True),
        
        # Tracking
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.Column('last_password_change', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        
        # Metadata
        sa.Column('metadata', postgresql.JSONB(), nullable=False, server_default='{}'),
        
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username', name='uq_users_username'),
        sa.UniqueConstraint('email', name='uq_users_email'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_users_created_by'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name='fk_users_updated_by'),
        sa.CheckConstraint('email = LOWER(email)', name='email_lowercase'),
        sa.CheckConstraint("username ~ '^[a-zA-Z0-9_-]+$'", name='username_format'),
    )
    op.create_index('idx_users_username', 'users', ['username'])
    op.create_index('idx_users_email', 'users', ['email'])
    op.create_index('idx_users_is_active', 'users', ['is_active'])
    
    # ========================================================================
    # 2. ROLES TABLE
    # ========================================================================
    op.create_table(
        'roles',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_system_role', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_roles_name'),
        sa.CheckConstraint("name ~ '^[A-Z_]+$'", name='valid_role_name'),
    )
    
    # ========================================================================
    # 3. PERMISSIONS TABLE
    # ========================================================================
    op.create_table(
        'permissions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('resource', sa.String(100), nullable=False),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('resource', 'action', name='uq_resource_action'),
        sa.CheckConstraint("action IN ('read', 'write', 'delete', 'admin')", name='valid_action'),
    )
    
    # ========================================================================
    # 4. ROLE_PERMISSIONS TABLE (Many-to-Many)
    # ========================================================================
    op.create_table(
        'role_permissions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('permission_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('role_id', 'permission_id', name='uq_role_permission'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name='fk_role_permissions_role_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], name='fk_role_permissions_permission_id', ondelete='CASCADE'),
    )
    
    # ========================================================================
    # 5. USER_ROLES TABLE (Many-to-Many)
    # ========================================================================
    op.create_table(
        'user_roles',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('assigned_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('assigned_by', postgresql.UUID(as_uuid=True), nullable=True),
        
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'role_id', name='uq_user_role'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_user_roles_user_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name='fk_user_roles_role_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name='fk_user_roles_assigned_by'),
    )
    op.create_index('idx_user_roles_user_id', 'user_roles', ['user_id'])
    
    # ========================================================================
    # 6. SESSIONS TABLE
    # ========================================================================
    op.create_table(
        'sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('token_jti', sa.String(255), nullable=False),
        sa.Column('refresh_token_jti', sa.String(255), nullable=True),
        
        # Device & Location
        sa.Column('ip_address', postgresql.INET(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('device_name', sa.String(255), nullable=True),
        
        # Status
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_activity', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_jti', name='uq_sessions_token_jti'),
        sa.UniqueConstraint('refresh_token_jti', name='uq_sessions_refresh_token_jti'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_sessions_user_id', ondelete='CASCADE'),
        sa.CheckConstraint('created_at <= expires_at', name='session_validity'),
    )
    op.create_index('idx_sessions_user_id', 'sessions', ['user_id'])
    op.create_index('idx_sessions_token_jti', 'sessions', ['token_jti'])
    op.create_index('idx_sessions_is_active', 'sessions', ['is_active'])
    op.create_index('idx_sessions_expires_at', 'sessions', ['expires_at'])
    
    # ========================================================================
    # 7. SETTINGS TABLE
    # ========================================================================
    op.create_table(
        'settings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Identification
        sa.Column('key', sa.String(255), nullable=False),
        sa.Column('category', sa.String(100), nullable=False),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        
        # Value & Type
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('value_type', sa.String(50), nullable=False, server_default='string'),
        
        # Security
        sa.Column('is_encrypted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_sensitive', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_secret', sa.Boolean(), nullable=False, server_default='false'),
        
        # Validation
        sa.Column('validation_rule', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('allowed_values', postgresql.ARRAY(sa.String()), nullable=True),
        
        # Metadata
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('requires_restart', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('requires_deployment', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        
        # Tracking
        sa.Column('modified_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('modified_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        
        # Scoping
        sa.Column('environment', sa.String(50), nullable=False, server_default='production'),
        
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key', 'environment', name='uq_setting_key_env'),
        sa.ForeignKeyConstraint(['modified_by'], ['users.id'], name='fk_settings_modified_by'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_settings_created_by'),
        sa.CheckConstraint(
            "category IN ('ai_models', 'integrations', 'features', 'system', 'security', 'performance')",
            name='valid_category'
        ),
        sa.CheckConstraint(
            "value_type IN ('string', 'number', 'boolean', 'json', 'secret')",
            name='valid_value_type'
        ),
        sa.CheckConstraint(
            "environment IN ('development', 'staging', 'production')",
            name='valid_environment'
        ),
    )
    op.create_index('idx_settings_key', 'settings', ['key'])
    op.create_index('idx_settings_category', 'settings', ['category'])
    op.create_index('idx_settings_environment', 'settings', ['environment'])
    op.create_index('idx_settings_modified_at', 'settings', ['modified_at'])
    
    # ========================================================================
    # 8. SETTINGS_AUDIT_LOG TABLE (Immutable)
    # ========================================================================
    op.create_table(
        'settings_audit_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Reference
        sa.Column('setting_id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Change Details
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('change_description', sa.Text(), nullable=True),
        
        # Metadata
        sa.Column('was_encrypted', sa.Boolean(), nullable=True),
        sa.Column('is_encrypted', sa.Boolean(), nullable=True),
        
        # Audit Trail
        sa.Column('changed_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('changed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        
        # Request Context
        sa.Column('ip_address', postgresql.INET(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('request_id', sa.String(255), nullable=True),
        
        # Recovery
        sa.Column('change_reason', sa.Text(), nullable=True),
        sa.Column('can_rollback', sa.Boolean(), nullable=False, server_default='true'),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['setting_id'], ['settings.id'], name='fk_audit_setting_id', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['changed_by'], ['users.id'], name='fk_audit_changed_by', ondelete='RESTRICT'),
        sa.CheckConstraint('changed_at <= NOW()', name='audit_immutable'),
    )
    op.create_index('idx_audit_setting_id', 'settings_audit_log', ['setting_id'])
    op.create_index('idx_audit_changed_by', 'settings_audit_log', ['changed_by'])
    op.create_index('idx_audit_changed_at', 'settings_audit_log', ['changed_at'])
    op.create_index('idx_audit_setting_changed', 'settings_audit_log', ['setting_id', 'changed_at'])
    
    # ========================================================================
    # 9. FEATURE_FLAGS TABLE
    # ========================================================================
    op.create_table(
        'feature_flags',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Identification
        sa.Column('flag_name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        
        # Status
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('percentage', sa.Integer(), nullable=False, server_default='0'),
        
        # Targeting
        sa.Column('target_users', postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column('target_roles', postgresql.ARRAY(sa.String()), nullable=True),
        
        # Metadata
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('flag_name', name='uq_feature_flags_name'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_flags_created_by'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name='fk_flags_updated_by'),
        sa.CheckConstraint('percentage >= 0 AND percentage <= 100', name='valid_percentage'),
    )
    op.create_index('idx_flags_name', 'feature_flags', ['flag_name'])
    op.create_index('idx_flags_enabled', 'feature_flags', ['is_enabled'])
    
    # ========================================================================
    # 10. API_KEYS TABLE
    # ========================================================================
    op.create_table(
        'api_keys',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Identification
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('key_hash', sa.String(255), nullable=False),
        sa.Column('key_prefix', sa.String(10), nullable=False),
        
        # Ownership
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Permissions
        sa.Column('permissions', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('allowed_ips', postgresql.ARRAY(postgresql.INET()), nullable=True),
        
        # Status
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        
        # Limits
        sa.Column('rate_limit_per_hour', sa.Integer(), nullable=False, server_default='1000'),
        
        # Tracking
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key_hash', name='uq_api_keys_key_hash'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_api_keys_user_id', ondelete='CASCADE'),
    )
    op.create_index('idx_api_keys_key_hash', 'api_keys', ['key_hash'])
    op.create_index('idx_api_keys_user_id', 'api_keys', ['user_id'])
    op.create_index('idx_api_keys_is_active', 'api_keys', ['is_active'])


def downgrade() -> None:
    """
    Drop all tables (reverse migration).
    
    WARNING: This will delete all data!
    """
    
    # Drop tables in reverse order (respecting foreign keys)
    op.drop_table('api_keys')
    op.drop_table('feature_flags')
    op.drop_table('settings_audit_log')
    op.drop_table('settings')
    op.drop_table('sessions')
    op.drop_table('user_roles')
    op.drop_table('role_permissions')
    op.drop_table('permissions')
    op.drop_table('roles')
    op.drop_table('users')
