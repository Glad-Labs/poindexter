#!/usr/bin/env python3
"""
Migration Runner: Add Approval Workflow Fields to content_tasks Table

This script runs the migration to add approval workflow columns to the content_tasks table.
Required for Phase 5 approval gate implementation.

Usage:
    python run_migration.py
    python run_migration.py --database-url postgresql://user:pass@localhost/dbname
    python run_migration.py --dry-run  # Show what would be run without executing
"""

import os
import sys
import argparse
import logging
from pathlib import Path
import psycopg2
from psycopg2 import sql
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_database_url():
    """Get database URL from environment or arguments"""
    # Check environment variable
    db_url = os.getenv('DATABASE_URL')
    if db_url:
        return db_url
    
    # Default to localhost
    logger.warning("⚠️ DATABASE_URL not set, using default localhost connection")
    return "postgresql://postgres:postgres@localhost:5432/glad_labs_dev"


def read_migration_file(migration_path: str) -> str:
    """Read migration SQL file"""
    if not os.path.exists(migration_path):
        raise FileNotFoundError(f"Migration file not found: {migration_path}")
    
    with open(migration_path, 'r') as f:
        return f.read()


def parse_database_url(db_url: str) -> dict:
    """Parse PostgreSQL connection URL"""
    # Format: postgresql://user:password@host:port/database
    import re
    pattern = r'postgresql://(?:([^:]+)(?::([^@]+))?@)?([^:/]+)(?::(\d+))?/(.+)'
    match = re.match(pattern, db_url)
    
    if not match:
        raise ValueError(f"Invalid database URL format: {db_url}")
    
    user, password, host, port, database = match.groups()
    
    return {
        'user': user or 'postgres',
        'password': password or '',
        'host': host or 'localhost',
        'port': int(port) if port else 5432,
        'database': database,
    }


def run_migration(db_url: str, migration_file: str, dry_run: bool = False):
    """Run migration against PostgreSQL database"""
    
    logger.info("=" * 70)
    logger.info("🔄 Content Tasks Approval Workflow Migration")
    logger.info("=" * 70)
    
    # Parse database URL
    logger.info(f"📍 Connecting to database...")
    db_config = parse_database_url(db_url)
    logger.info(f"   Host: {db_config['host']}:{db_config['port']}")
    logger.info(f"   Database: {db_config['database']}")
    logger.info(f"   User: {db_config['user']}")
    
    # Read migration SQL
    logger.info(f"\n📄 Reading migration file: {migration_file}")
    migration_sql = read_migration_file(migration_file)
    logger.info(f"   SQL statements to execute: {len(migration_sql.split(';'))}")
    
    if dry_run:
        logger.info("\n⏭️  DRY RUN MODE - SQL that would be executed:")
        logger.info("-" * 70)
        logger.info(migration_sql)
        logger.info("-" * 70)
        logger.info("✅ Dry run complete (no changes made)")
        return True
    
    # Connect to database
    try:
        conn = psycopg2.connect(
            user=db_config['user'],
            password=db_config['password'],
            host=db_config['host'],
            port=db_config['port'],
            database=db_config['database'],
        )
        logger.info("✅ Connected to database")
        
        # Execute migration
        logger.info("\n🚀 Executing migration...")
        cursor = conn.cursor()
        
        try:
            cursor.execute(migration_sql)
            conn.commit()
            logger.info("✅ Migration executed successfully!")
            
            # Get table info
            cursor.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'content_tasks'
                AND column_name IN ('approval_status', 'qa_feedback', 'human_feedback', 
                                   'approved_by', 'approval_timestamp', 'approval_notes')
                ORDER BY ordinal_position;
            """)
            
            logger.info("\n📋 Verification - Approval columns added:")
            columns = cursor.fetchall()
            for col_name, col_type, is_nullable in columns:
                nullable = "NULL" if is_nullable == 'YES' else "NOT NULL"
                logger.info(f"   ✅ {col_name}: {col_type} {nullable}")
            
            if len(columns) == 6:
                logger.info("\n✅ All 6 approval columns verified!")
            else:
                logger.warning(f"\n⚠️  Only {len(columns)}/6 columns found. Check migration output.")
            
            # Get index info
            cursor.execute("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'content_tasks'
                AND indexname LIKE 'idx_content_tasks%';
            """)
            
            logger.info("\n📑 Verification - Indexes created:")
            indexes = cursor.fetchall()
            for (idx_name,) in indexes:
                logger.info(f"   ✅ {idx_name}")
            
            logger.info("\n" + "=" * 70)
            logger.info("🎉 MIGRATION COMPLETE - Database ready for Phase 5!")
            logger.info("=" * 70)
            
            return True
            
        except Exception as e:
            conn.rollback()
            logger.error(f"❌ Migration failed: {e}")
            raise
        
        finally:
            cursor.close()
            conn.close()
    
    except psycopg2.Error as e:
        logger.error(f"❌ Database connection failed: {e}")
        logger.error(f"   Check DATABASE_URL and verify PostgreSQL is running")
        return False


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Run database migration to add approval workflow fields'
    )
    parser.add_argument(
        '--database-url',
        help='PostgreSQL connection URL (or use DATABASE_URL env var)',
        default=None
    )
    parser.add_argument(
        '--migration-file',
        default='migrations/001_add_approval_workflow_fields.sql',
        help='Path to migration SQL file'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show SQL without executing (dry run mode)'
    )
    
    args = parser.parse_args()
    
    # Get database URL
    db_url = args.database_url or get_database_url()
    
    # Build migration file path
    script_dir = Path(__file__).parent
    migration_file = script_dir / args.migration_file
    
    try:
        success = run_migration(db_url, str(migration_file), args.dry_run)
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"\n❌ ERROR: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
