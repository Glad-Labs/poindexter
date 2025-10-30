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
# DATABASE CONFIGURATION
# ============================================================================

def get_database_url() -> str:
    """
    Get database URL from environment variables.
    
    Environment Variables:
        DATABASE_URL: Full database URL (takes precedence)
        DATABASE_CLIENT: 'postgres' or 'sqlite' (default: postgres)
        DATABASE_HOST: Hostname/IP
        DATABASE_PORT: Port number
        DATABASE_NAME: Database name
        DATABASE_USER: Username
        DATABASE_PASSWORD: Password
        DATABASE_FILENAME: For SQLite (if using local development)
    
    Returns:
        str: Database connection URL
    
    Raises:
        ValueError: If required environment variables are missing
    """
    
    # Check for full URL first (Railway pattern)
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        logger.info("Using DATABASE_URL from environment")
        return database_url
    
    # Fallback to component-based configuration
    db_client = os.getenv('DATABASE_CLIENT', 'postgres')
    
    if db_client == 'sqlite':
        # SQLite for local development
        db_filename = os.getenv('DATABASE_FILENAME', '.tmp/data.db')
        os.makedirs(os.path.dirname(db_filename), exist_ok=True)
        url = f"sqlite:///{db_filename}"
        logger.info(f"Using SQLite database: {db_filename}")
        return url
    
    elif db_client == 'postgres':
        # PostgreSQL for production
        host = os.getenv('DATABASE_HOST', 'localhost')
        port = os.getenv('DATABASE_PORT', '5432')
        name = os.getenv('DATABASE_NAME', 'glad_labs')
        user = os.getenv('DATABASE_USER', 'postgres')
        password = os.getenv('DATABASE_PASSWORD', '')
        
        if not user:
            raise ValueError("DATABASE_USER environment variable is required")
        
        if password:
            url = f"postgresql://{user}:{password}@{host}:{port}/{name}"
        else:
            url = f"postgresql://{user}@{host}:{port}/{name}"
        
        logger.info(f"Using PostgreSQL database: {host}:{port}/{name}")
        return url
    
    else:
        raise ValueError(f"Unsupported DATABASE_CLIENT: {db_client}")


def create_db_engine():
    """
    Create SQLAlchemy database engine with appropriate configuration.
    
    Returns:
        Engine: Configured SQLAlchemy engine
    """
    
    database_url = get_database_url()
    
    # Detect if PostgreSQL and convert to asyncpg dialect if needed
    is_postgres = 'postgresql' in database_url
    
    # Convert postgresql:// to postgresql+asyncpg:// to use asyncpg driver
    # This avoids psycopg2 dependency in Railway build environment
    if is_postgres and '+' not in database_url:
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://')
        logger.info("Using PostgreSQL with asyncpg driver (async support)")
    
    # Engine configuration
    engine_kwargs = {
        'echo': os.getenv('SQL_ECHO', 'false').lower() == 'true',
        'pool_pre_ping': True,  # Verify connections before using
        'connect_args': {}
    }
    
    if is_postgres:
        # PostgreSQL-specific configuration
        engine_kwargs.update({
            'poolclass': pool.QueuePool,
            'pool_size': int(os.getenv('DATABASE_POOL_SIZE', '20')),
            'max_overflow': int(os.getenv('DATABASE_MAX_OVERFLOW', '40')),
            'pool_recycle': 3600,  # Recycle connections after 1 hour
            'pool_timeout': 30,
        })
        
        # SSL configuration for production
        if os.getenv('DATABASE_SSL_MODE') == 'require':
            engine_kwargs['connect_args']['sslmode'] = 'require'
    
    else:
        # SQLite configuration
        engine_kwargs.update({
            'poolclass': pool.StaticPool,
            'connect_args': {'check_same_thread': False}
        })
    
    engine = create_engine(database_url, **engine_kwargs)
    
    # Set up connection event listeners
    setup_engine_listeners(engine)
    
    logger.info(f"Database engine created: {database_url}")
    return engine


def setup_engine_listeners(engine):
    """
    Set up event listeners for database connection handling.
    
    Args:
        engine: SQLAlchemy engine instance
    """
    
    is_postgres = 'postgresql' in engine.url.drivername
    
    if is_postgres:
        @event.listens_for(Pool, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            """Configure PostgreSQL connection."""
            # Enable connection extras for better error messages
            pass
        
        @event.listens_for(Pool, "checkout")
        def receive_checkout(dbapi_conn, connection_record, connection_proxy):
            """Validate connection before checkout."""
            try:
                dbapi_conn.execute(text("SELECT 1"))
            except exc.DBAPIError as e:
                logger.warning(f"Database connection validation failed: {e}")
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
        
        # Seed initial data
        seed_initial_data()
        
        logger.info("Database initialization complete")
    
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


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
