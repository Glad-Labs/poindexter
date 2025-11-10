"""
Database initialization and connection management.

Sets up SQLAlchemy engine, session factory, and database utilities.
Supports PostgreSQL for production with connection pooling and retry logic.
"""

import os
import logging
from typing import Generator, Optional
from contextlib import contextmanager

from sqlalchemy import create_engine, text, event, pool, exc
from sqlalchemy.orm import sessionmaker, Session as SQLSession
from sqlalchemy.pool import Pool

from models import Base

logger = logging.getLogger(__name__)


# ============================================================================
# MEMORY TABLE SCHEMAS (PostgreSQL)
# ============================================================================

# SQL definitions for AI memory system tables
# These are created during database initialization
MEMORY_TABLE_SCHEMAS = """
-- Memories table: Core persistent memory storage
CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    memory_type VARCHAR(50) NOT NULL,
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    tags TEXT[],
    related_memories UUID[],
    metadata JSONB,
    embedding bytea,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);

-- Knowledge clusters table: Grouped related memories
CREATE TABLE IF NOT EXISTS knowledge_clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    memories UUID[],
    confidence REAL NOT NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    importance_score REAL NOT NULL,
    topics TEXT[],
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_clusters_importance ON knowledge_clusters(importance_score DESC);

-- Learning patterns table: Patterns discovered from interactions
CREATE TABLE IF NOT EXISTS learning_patterns (
    pattern_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_type VARCHAR(50) NOT NULL,
    description TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    confidence REAL NOT NULL,
    examples TEXT[],
    discovered_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User preferences table: Persistent user preferences
CREATE TABLE IF NOT EXISTS user_preferences (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL,
    confidence REAL NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source VARCHAR(100)
);

-- Conversation sessions table: Track conversation history
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    message_count INTEGER DEFAULT 0,
    topics TEXT[],
    summary TEXT,
    importance REAL DEFAULT 0.5
);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON conversation_sessions(started_at DESC);
"""


# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================

def get_database_url() -> str:
    """
    Get PostgreSQL database URL from environment variables.
    
    PostgreSQL is REQUIRED - no fallback to SQLite.
    
    Environment Variables:
        DATABASE_URL: Full PostgreSQL URL (takes precedence)
        DATABASE_HOST: Hostname/IP (default: localhost)
        DATABASE_PORT: Port number (default: 5432)
        DATABASE_NAME: Database name (default: glad_labs_dev)
        DATABASE_USER: Username (REQUIRED if no DATABASE_URL)
        DATABASE_PASSWORD: Password (optional)
    
    Returns:
        str: PostgreSQL connection URL
    
    Raises:
        ValueError: If DATABASE_URL not set and components missing
    """
    
    # Check for full URL first (Railway pattern)
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        if 'postgresql' not in database_url:
            raise ValueError(
                f"❌ FATAL: Invalid database URL. PostgreSQL is REQUIRED."
                f"\n   Got: {database_url[:50]}..."
                f"\n   Expected: postgresql://user:password@host:port/database"
            )
        logger.info("✅ Using DATABASE_URL from environment (PostgreSQL)")
        return database_url
    
    # Component-based configuration (all must be PostgreSQL)
    host = os.getenv('DATABASE_HOST', 'localhost')
    port = os.getenv('DATABASE_PORT', '5432')
    name = os.getenv('DATABASE_NAME', 'glad_labs_dev')
    user = os.getenv('DATABASE_USER')
    password = os.getenv('DATABASE_PASSWORD')
    
    # Validate required components
    if not user:
        raise ValueError(
            f"❌ FATAL: DATABASE_USER is REQUIRED"
            f"\n   PostgreSQL connection requires DATABASE_USER environment variable"
            f"\n   Either set DATABASE_URL or provide DATABASE_USER + DATABASE_HOST + DATABASE_PORT + DATABASE_NAME"
        )
    
    if password:
        url = f"postgresql://{user}:{password}@{host}:{port}/{name}"
    else:
        url = f"postgresql://{user}@{host}:{port}/{name}"
    
    logger.info(f"✅ Using PostgreSQL database: {host}:{port}/{name}")
    return url


def create_db_engine():
    """
    Create SQLAlchemy database engine with PostgreSQL configuration.
    
    PostgreSQL only - no SQLite fallback.
    Uses asyncpg driver for async support.
    
    Returns:
        Engine: Configured SQLAlchemy engine for PostgreSQL
    
    Raises:
        ValueError: If database URL is invalid or PostgreSQL connection fails
    """
    
    database_url = get_database_url()
    
    # Validate PostgreSQL
    if 'postgresql' not in database_url:
        raise ValueError(
            f"❌ FATAL: Only PostgreSQL supported. Got: {database_url[:50]}..."
        )
    
    # Convert postgresql:// to postgresql+asyncpg:// for async support
    if '+' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://')
        logger.info("🔄 Using PostgreSQL with asyncpg driver (async support)")
    
    # Engine configuration for PostgreSQL with asyncpg
    engine_kwargs = {
        'echo': os.getenv('SQL_ECHO', 'false').lower() == 'true',
        'pool_pre_ping': True,  # Verify connections before using
        'poolclass': pool.NullPool,  # asyncpg requires NullPool (no connection pooling)
        'connect_args': {}
    }
    
    # SSL configuration for production
    if os.getenv('DATABASE_SSL_MODE') == 'require':
        engine_kwargs['connect_args']['sslmode'] = 'require'
        logger.info("🔒 SSL required for database connection")
    
    try:
        engine = create_engine(database_url, **engine_kwargs)
        logger.info(f"✅ PostgreSQL engine created: {database_url.split('@')[-1]}")
    except Exception as e:
        logger.error(f"❌ FATAL: Failed to create database engine: {e}")
        raise ValueError(
            f"Failed to create PostgreSQL engine:\n{e}\n"
            f"Check DATABASE_URL or component variables (HOST, PORT, NAME, USER, PASSWORD)"
        )
    
    # Set up connection event listeners
    setup_engine_listeners(engine)
    
    return engine


def setup_engine_listeners(engine):
    """
    Set up event listeners for PostgreSQL connection handling.
    
    Args:
        engine: SQLAlchemy engine instance
    """
    
    @event.listens_for(Pool, "checkout")
    def receive_checkout(dbapi_conn, connection_record, connection_proxy):
        """Validate PostgreSQL connection before checkout."""
        try:
            dbapi_conn.execute(text("SELECT 1"))
        except exc.DBAPIError as e:
            logger.warning(f"⚠️ PostgreSQL connection validation failed: {e}")
            raise


# ============================================================================
# SESSION MANAGEMENT
# ============================================================================

# Lazy initialization - engine created on first use, not at import time
# This prevents asyncpg connection issues during startup
_engine = None
_SessionLocal = None


def get_db_engine():
    """Get or create the database engine (lazy initialization)."""
    global _engine
    if _engine is None:
        _engine = create_db_engine()
        logger.info("Database engine initialized on first use")
    return _engine


def _get_session_factory():
    """Get or create the session factory (lazy initialization)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_db_engine())
    return _SessionLocal


def get_session() -> SQLSession:
    """
    Get a database session.
    
    Returns:
        Session: SQLAlchemy session instance
    """
    return _get_session_factory()()


@contextmanager
def get_db_context() -> Generator[SQLSession, None, None]:
    """
    Context manager for database sessions.
    
    Usage:
        with get_db_context() as db:
            user = db.query(User).filter_by(username='admin').first()
    
    Yields:
        Session: SQLAlchemy session instance
    """
    session = _get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[SQLSession, None, None]:
    """
    Dependency for FastAPI to inject database session.
    
    Usage in FastAPI routes:
        @app.get("/users")
        async def list_users(db: Session = Depends(get_db)):
            return db.query(User).all()
    
    Yields:
        Session: SQLAlchemy session instance
    """
    db = _get_session_factory()()
    try:
        yield db
    finally:
        db.close()


# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_db():
    """
    Initialize database: create all tables and seed initial data.
    
    This should be called during application startup if the database
    is new or during migrations.
    """
    
    logger.info("Initializing database...")
    
    try:
        # Create all tables
        Base.metadata.create_all(bind=get_db_engine())
        logger.info("Database tables created successfully")
        
        # Create memory system tables
        init_memory_tables()
        
        # Seed initial data
        seed_initial_data()
        
        logger.info("Database initialization complete")
    
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


def init_memory_tables():
    """
    Initialize memory system tables in PostgreSQL.
    
    Creates tables for:
    - memories: Core persistent memory storage
    - knowledge_clusters: Grouped related memories
    - learning_patterns: Discovered interaction patterns
    - user_preferences: Persistent user preferences
    - conversation_sessions: Conversation history tracking
    
    Called automatically during database initialization.
    Safe to call multiple times (uses IF NOT EXISTS).
    """
    
    try:
        engine = get_db_engine()
        
        with engine.begin() as connection:
            # Execute all memory table creation statements
            for statement in MEMORY_TABLE_SCHEMAS.split(';'):
                statement = statement.strip()
                if statement:
                    connection.execute(text(statement))
            
        logger.info("✅ Memory system tables initialized successfully")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize memory tables: {e}")
        # Don't raise - memory tables might already exist
        # This is especially true if the schema was created by alembic migrations


def seed_initial_data():
    """
    Seed database with initial data (roles, permissions, system settings).
    
    This creates the basic RBAC structure and system settings needed
    for the application to function.
    """
    
    from models import Role, Permission, RolePermission, Setting
    from datetime import datetime
    import uuid
    
    logger.info("Seeding initial data...")
    
    with get_db_context() as db:
        
        # Skip if roles already exist (already seeded)
        if db.query(Role).filter_by(name='ADMIN').first():
            logger.info("Initial data already seeded, skipping")
            return
        
        # ===== CREATE ROLES =====
        roles = [
            Role(
                id=uuid.uuid4(),
                name='ADMIN',
                description='Full system access',
                is_system_role=True
            ),
            Role(
                id=uuid.uuid4(),
                name='MANAGER',
                description='Can manage settings and users',
                is_system_role=True
            ),
            Role(
                id=uuid.uuid4(),
                name='OPERATOR',
                description='Can view and use settings',
                is_system_role=True
            ),
            Role(
                id=uuid.uuid4(),
                name='VIEWER',
                description='Read-only access',
                is_system_role=True
            ),
        ]
        
        for role in roles:
            db.add(role)
        
        db.flush()  # Get role IDs
        
        # ===== CREATE PERMISSIONS =====
        permissions = [
            Permission(id=uuid.uuid4(), resource='settings', action='read', description='Read settings'),
            Permission(id=uuid.uuid4(), resource='settings', action='write', description='Modify settings'),
            Permission(id=uuid.uuid4(), resource='settings', action='delete', description='Delete settings'),
            Permission(id=uuid.uuid4(), resource='settings', action='admin', description='Administer settings'),
            
            Permission(id=uuid.uuid4(), resource='users', action='read', description='View users'),
            Permission(id=uuid.uuid4(), resource='users', action='write', description='Modify users'),
            Permission(id=uuid.uuid4(), resource='users', action='delete', description='Delete users'),
            Permission(id=uuid.uuid4(), resource='users', action='admin', description='Administer users'),
            
            Permission(id=uuid.uuid4(), resource='audit', action='read', description='View audit logs'),
            Permission(id=uuid.uuid4(), resource='audit', action='admin', description='Administer audit logs'),
            
            Permission(id=uuid.uuid4(), resource='roles', action='read', description='View roles'),
            Permission(id=uuid.uuid4(), resource='roles', action='write', description='Modify roles'),
            Permission(id=uuid.uuid4(), resource='roles', action='admin', description='Administer roles'),
        ]
        
        for perm in permissions:
            db.add(perm)
        
        db.flush()  # Get permission IDs
        
        # ===== ASSIGN PERMISSIONS TO ROLES =====
        admin_role = db.query(Role).filter_by(name='ADMIN').first()
        manager_role = db.query(Role).filter_by(name='MANAGER').first()
        operator_role = db.query(Role).filter_by(name='OPERATOR').first()
        viewer_role = db.query(Role).filter_by(name='VIEWER').first()
        
        # Admin: all permissions
        for perm in permissions:
            db.add(RolePermission(
                id=uuid.uuid4(),
                role_id=admin_role.id,
                permission_id=perm.id
            ))
        
        # Manager: read/write/delete (not admin) on settings and users
        manager_perms = db.query(Permission).filter(
            Permission.resource.in_(['settings', 'users']),
            Permission.action.in_(['read', 'write', 'delete'])
        ).all()
        for perm in manager_perms:
            db.add(RolePermission(
                id=uuid.uuid4(),
                role_id=manager_role.id,
                permission_id=perm.id
            ))
        
        # Operator: read/write on settings
        operator_perms = db.query(Permission).filter(
            Permission.resource == 'settings',
            Permission.action.in_(['read', 'write'])
        ).all()
        for perm in operator_perms:
            db.add(RolePermission(
                id=uuid.uuid4(),
                role_id=operator_role.id,
                permission_id=perm.id
            ))
        
        # Viewer: read-only on settings and audit
        viewer_perms = db.query(Permission).filter(
            (Permission.resource.in_(['settings', 'audit'])) &
            (Permission.action == 'read')
        ).all()
        for perm in viewer_perms:
            db.add(RolePermission(
                id=uuid.uuid4(),
                role_id=viewer_role.id,
                permission_id=perm.id
            ))
        
        db.commit()
        
        logger.info(f"Created {len(roles)} roles, {len(permissions)} permissions")


def reset_db():
    """
    Drop all tables and recreate database.
    
    WARNING: This will delete all data!
    """
    
    logger.warning("RESETTING DATABASE - ALL DATA WILL BE DELETED")
    
    if os.getenv('ENVIRONMENT') == 'production':
        raise RuntimeError("Cannot reset database in production!")
    
    Base.metadata.drop_all(bind=get_db_engine())
    logger.info("All tables dropped")
    
    init_db()
    logger.info("Database recreated with initial data")


def healthcheck_db() -> bool:
    """
    Check if database connection is healthy.
    
    Returns:
        bool: True if database is healthy, False otherwise
    """
    
    try:
        with get_db_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    'get_db_engine',
    'get_session',
    'get_db_context',
    'get_db',
    'init_db',
    'seed_initial_data',
    'reset_db',
    'healthcheck_db',
]
