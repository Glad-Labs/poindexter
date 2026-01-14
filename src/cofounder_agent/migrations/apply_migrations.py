#!/usr/bin/env python3
"""
Database Migration Runner
Applies SQL migrations from the migrations/ directory to the PostgreSQL database.
"""

import os
import sys
import psycopg2
import glob
from pathlib import Path
from urllib.parse import urlparse

def get_database_url():
    """Get DATABASE_URL from environment"""
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("❌ DATABASE_URL environment variable not set")
        print("Please set DATABASE_URL to your PostgreSQL connection string")
        sys.exit(1)
    return db_url

def parse_db_url(db_url):
    """Parse PostgreSQL connection URL"""
    parsed = urlparse(db_url)
    return {
        'host': parsed.hostname,
        'port': parsed.port or 5432,
        'database': parsed.path.lstrip('/'),
        'user': parsed.username,
        'password': parsed.password,
    }

def apply_migrations():
    """Apply all SQL migration files"""
    db_url = get_database_url()
    db_config = parse_db_url(db_url)
    
    print("🔄 Applying database migrations...")
    print(f"📍 Target: {db_config['host']}:{db_config['port']}/{db_config['database']}")
    
    # Connect to database
    try:
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        sys.exit(1)
    
    # Get migrations directory
    migrations_dir = Path(__file__).parent
    migration_files = sorted(glob.glob(str(migrations_dir / "*.sql")))
    
    if not migration_files:
        print("⚠️  No migration files found")
        cursor.close()
        conn.close()
        return
    
    # Apply each migration
    for migration_file in migration_files:
        migration_name = Path(migration_file).name
        print(f"\n📝 Applying migration: {migration_name}")
        
        try:
            with open(migration_file, 'r') as f:
                sql = f.read()
            
            cursor.execute(sql)
            conn.commit()
            print(f"✅ Migration completed: {migration_name}")
        except Exception as e:
            conn.rollback()
            print(f"❌ Migration failed: {migration_name}")
            print(f"   Error: {e}")
            cursor.close()
            conn.close()
            sys.exit(1)
    
    cursor.close()
    conn.close()
    print("\n✅ All database migrations applied successfully!")

if __name__ == '__main__':
    apply_migrations()
